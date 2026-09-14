"""Run-to-run stability: how much of a sweep's recall survives re-running it.

Every other number in this harness comes from one sweep, and a single sweep
cannot tell a real movement from sampling noise. Two sweeps of the same corpus
under the same execution identities can. The references that matched in both are
what the system reliably finds. The ones that matched in one are the spread any
comparison of two other numbers has to clear before it means anything.

The instrument is the reference index rather than the threat text. A produced
threat gets a fresh ID every run and its wording moves, so text can only be
compared as prose. The reference it was matched to is a corpus coordinate that
means the same thing in every sweep. This module therefore measures set overlap
over ``scores[].matched[].reference_index``, and needs no re-scoring, no
provider and no credentials. It reads finished artifacts, the way ``promote``
does.

Two runs agreeing on a reference is not two runs producing the same threat.
The same reference can be matched by threats that differ in severity, elements
and wording, so beside recall this reads two more things where the record
holds them. **Cause stability**: for a reference every run missed, whether the
loss instrument charged it to one cause each time or to several — a fix is
priced on a cause, and a cause that moves between runs is a ceiling that moves
with it. **Content stability**: for a reference two or more runs matched,
whether the matching claims kept one severity band and one resolved place
across the runs. Five case 01 runs matched reference 17 every time and rated
it high four times and critical once; recall called that stable. Both read
what an artifact and its report bundle already hold, and each says
``unread`` where a run predates the block it needs rather than reporting a
zero. Wording is not compared: it moves every run, and comparing it as prose
would measure the tokenizer.

It does not gate, like the rest of the instruments. The spread this reports is
the input to any future threshold rather than a threshold itself.

Two kinds of difference between the runs, handled two ways. A difference that
changes what the spread *means* — another mode, another model — is reported by
:func:`comparability_warnings` and the comparison goes ahead, because a chosen
comparison across configurations is a real question. A difference that makes
the arithmetic meaningless is refused by :func:`compare_runs`: a reference
index is a coordinate into one corpus, so two runs scored against two corpus
digests, or two counts of one case's references, share no coordinate system
and would have produced a spread — or a negative ``never`` — out of nothing.
"""

from __future__ import annotations

import itertools
import json
import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis_service.claims import FrameworkName
from analysis_service.system_model import ModelIndex, SystemModel
from evals.harness.artifact import EvalArtifact, load_artifact
from evals.harness.bundle import reports_dir
from evals.harness.fingerprint import IDENTIFIER_OF
from evals.harness.identity import endpoint_form
from evals.harness.provenance import ProvenanceError
from evals.harness.reference import MUST_FIND
from evals.harness.scorer import ratio

#: One case of one framework. Stability is per framework because the two
#: instruments answer over different sets — STRIDE's open claim set through a
#: composed identity, ASVS's finite catalog by string compare — so pooling their spread
#: would report one volatility figure over two populations.
Scope = tuple[FrameworkName, str]


@dataclass(frozen=True)
class MustFindFate:
    """One run's answer about one case's must-find references.

    ``matched`` and ``missed`` are both recorded because the two packages name
    different halves. A package whose claims compose an identity marks the tier
    on each *matched* row and cannot name a reference no run ever matched; a
    package identified by a catalog requirement lists the *missed* ones by
    identifier. :func:`band` needs neither half to be complete, because a
    reference every run agreed on contributes nothing to a spread.
    """

    matched: frozenset[str] = frozenset()
    missed: frozenset[str] = frozenset()
    #: Whether ``matched`` is already narrowed to must-finds. A record that
    #: marks the tier on each matched row answers ``True``, and its matched set
    #: names must-finds outright. A record that marks no tier and lists the
    #: missed must-finds instead answers ``False``: its matched set holds every
    #: reference it matched, so the must-finds among them are the ones some
    #: other run named as missed.
    #:
    #: A property of the record rather than of the package that wrote it, so a
    #: package added tomorrow answers by saying which half its block names.
    names_the_tier: bool = True


@dataclass(frozen=True)
class ScoredRun:
    """One finished sweep, reduced to what a stability comparison reads.

    ``matched`` holds **strings** for both frameworks, because what identifies a
    matched reference differs: STRIDE's scorer emits an index into the case's
    reference list, and ASVS's emits the standard's own requirement identifier.
    Rendering the index as a string keeps one set type and one Jaccard, and the
    identifiers never collide because the key carries the framework.
    """

    label: str
    #: The corpus every reference coordinate below indexes into.
    corpus_digest: str
    mode: str
    models: dict[str, Any]
    matched: dict[Scope, frozenset[str]]
    references: dict[Scope, int]
    recall: dict[Scope, float]
    #: Each missed reference's charged cause, per scope, off the ``losses``
    #: block. ``None`` where the artifact carries no such block, which every
    #: sweep before the instrument existed does: an absent record is not a run
    #: whose causes all agreed.
    causes: dict[Scope, dict[str, str]] | None = None
    #: Each matched reference's content, per scope: the matching claim's
    #: severity band and its endpoint-resolved place, off the report bundle.
    #: ``None`` where no bundle sits beside the artifact.
    content: dict[Scope, dict[str, tuple[str, frozenset[str]]]] | None = None
    #: This run's must-find fates per scope: which the run matched, and which
    #: it named as missed. Both packages answer, out of their own block, and
    #: the missed half is empty for a package whose record names only the
    #: matched ones — see :func:`band` for why that costs the reading nothing.
    must_find: dict[Scope, MustFindFate] = field(default_factory=dict)

    @property
    def cases(self) -> frozenset[Scope]:
        return frozenset(self.matched)


@dataclass(frozen=True)
class CaseStability:
    """One case's spread across the runs that scored it.

    ``always`` / ``sometimes`` / ``never`` partition the case's references, so
    they sum to ``references`` and the middle bucket is the whole finding:
    ``never`` is a coverage gap to work on and ``always`` is settled, while
    ``sometimes`` is the band in which a one-sweep recall number can move
    without anything having changed.
    """

    framework: FrameworkName
    case_id: str
    runs: int
    references: int
    recalls: tuple[float, ...]
    always: int
    sometimes: int
    never: int
    mean_jaccard: float
    #: Of the references every run missed, how many were charged to one cause
    #: in every run and how many to more than one. ``None`` for both where any
    #: run carries no loss rows, because an absent cause is not an agreeing one.
    cause_stable: int | None = None
    cause_moving: int | None = None
    #: Of the references two or more runs matched, how many kept one severity
    #: band across every run that matched them, and how many kept one
    #: resolved place. ``None`` where any run has no report bundle to read, or
    #: where the package's references name no place.
    severity_held: int | None = None
    severity_moved: int | None = None
    place_held: int | None = None
    place_moved: int | None = None

    @property
    def recall_spread(self) -> float:
        return max(self.recalls) - min(self.recalls)

    @property
    def volatile_rate(self) -> float:
        return ratio(self.sometimes, self.references)

    def to_json(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "case": self.case_id,
            "runs": self.runs,
            "references": self.references,
            "recalls": [round(recall, 3) for recall in self.recalls],
            "recall_spread": round(self.recall_spread, 3),
            "always_matched": self.always,
            "sometimes_matched": self.sometimes,
            "never_matched": self.never,
            "volatile_rate": round(self.volatile_rate, 3),
            "mean_jaccard": round(self.mean_jaccard, 3),
            "cause_stable": self.cause_stable,
            "cause_moving": self.cause_moving,
            "severity_held": self.severity_held,
            "severity_moved": self.severity_moved,
            "place_held": self.place_held,
            "place_moved": self.place_moved,
        }


def _claim_scored_framework() -> FrameworkName:
    """The package the ``scores`` block holds rows for, read off the registry.

    A row in that block names a case and its matched reference indices and
    names no framework, so the block can carry one package's rows and no more.
    That package is the one whose claims compose an identity from an action and
    a place — :data:`~evals.harness.fingerprint.IDENTIFIER_OF` answers ``None``
    for it — and a package identified by a catalog requirement is scored in
    ``applicability`` instead.

    Asked of the registry rather than spelled at each site, so a second
    composed package is refused by name here rather than filed silently under
    this one's.
    """
    composed = sorted(
        name for name, identifier in IDENTIFIER_OF.items() if identifier is None
    )
    if len(composed) != 1:
        raise ProvenanceError(
            "the 'scores' block names no framework, so it holds one package's"
            f" rows; {composed} each compose a claim identity from an action and"
            " a place, so the block needs a framework field before this reads it"
        )
    return composed[0]


def read_run(artifact: EvalArtifact) -> ScoredRun:
    """Reduce a loaded artifact to its per-``(framework, case)`` matched sets.

    Both blocks, because both are recall against a reference set: ``scores`` is
    STRIDE's, and ``applicability`` is ASVS's.

    A sweep carrying neither measured no recall to compare and is refused here
    rather than reported as a run that matched nothing — an empty overlap and an
    unscored sweep are opposite facts about the system. **Carrying only
    ``applicability`` is enough**: a sweep over ASVS-only cases produces no
    STRIDE score block and its ASVS half still compares.
    """
    matched: dict[Scope, frozenset[str]] = {}
    references: dict[Scope, int] = {}
    recall: dict[Scope, float] = {}

    # Refused by name rather than raised through: an artifact whose blocks are
    # the wrong shape is a file to re-produce, and a KeyError out of a
    # comparison reads as a defect in the comparison.
    # Outside the try: a second composed package is a fact about the registry,
    # and the handler below names the artifact as malformed. A file that is
    # perfectly well formed would be blamed for a defect in the code.
    scored = _claim_scored_framework()
    must_find: dict[Scope, MustFindFate] = {}
    try:
        for score in artifact.block("scores"):
            scope: Scope = (scored, str(score["case"]))
            matched[scope] = frozenset(
                str(pair["reference_index"]) for pair in score["matched"]
            )
            references[scope] = int(score["counts"]["references"])
            recall[scope] = float(score["metrics"]["reference_coverage"])
            # This package marks the tier on each matched row and names no
            # missed reference, so the missed half stays empty.
            must_find[scope] = MustFindFate(
                matched=frozenset(
                    str(pair["reference_index"])
                    for pair in score["matched"]
                    if pair.get("tier") == MUST_FIND
                )
            )
        for entry in artifact.block("applicability"):
            scope = ("asvs", str(entry["case"]))
            matched[scope] = frozenset(str(item) for item in entry["matched"])
            references[scope] = int(entry["expected"])
            recall[scope] = float(entry["recall"])
            # This one names the missed must-finds by catalog identifier, and
            # marks no tier on a matched row — so the matched half is the
            # matched set narrowed to identifiers some run named as a must-find,
            # which ``band`` does once it holds every run.
            must_find[scope] = MustFindFate(
                matched=frozenset(str(item) for item in entry["matched"]),
                missed=frozenset(str(item) for item in entry["must_find_missed"]),
                names_the_tier=False,
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvenanceError(f"{artifact.path}: malformed score block: {exc}") from exc

    if not matched:
        raise ProvenanceError(
            f"{artifact.path}: no scores or applicability block, so this sweep"
            " measured no recall to compare — re-run it over a case that"
            " declares a scored framework"
        )
    return ScoredRun(
        label=artifact.path.name,
        corpus_digest=artifact.corpus_digest,
        mode=artifact.mode,
        models=dict(artifact.block("models")),
        matched=matched,
        references=references,
        recall=recall,
        causes=_causes(artifact),
        content=_content(artifact, matched),
        must_find=must_find,
    )


def _causes(artifact: EvalArtifact) -> dict[Scope, dict[str, str]] | None:
    """Each missed reference's charged cause, off the ``losses`` block, or ``None`` without one.

    Two artifacts have no causes to read and they are different files: one
    predates the instrument and omits the key, and one ran it over a mode that
    charges nothing and writes ``None``. Both read ``unread`` here, and neither
    may take the rest of the comparison down with it — recall and content need
    no losses block.
    """
    if not artifact.carries("losses"):
        return None
    rows = artifact.block("losses")
    if rows is None:
        return None
    # Outside the try, for the reason :func:`read_run` gives.
    scored = _claim_scored_framework()
    try:
        return {
            (scored, str(row["case"])): {
                str(loss["reference_index"]): str(loss["cause"])
                for loss in row["losses"]
            }
            for row in rows
        }
    except (KeyError, TypeError) as exc:
        raise ProvenanceError(
            f"{artifact.path}: malformed losses block: {exc}"
        ) from exc


def _content(
    artifact: EvalArtifact, matched: Mapping[Scope, frozenset[str]]
) -> dict[Scope, dict[str, tuple[str, frozenset[str]]]] | None:
    """Each matched reference's severity band and resolved place, off the report bundle.

    Read for the package :func:`_claim_scored_framework` names, which is the
    only one the ``scores`` block holds rows for: its scorer records which
    claim matched each reference, and its claims carry a severity. A
    catalog-identified package records matched identifiers alone, so there is
    nothing here to read for it. ``None`` where the bundle is absent, which is
    the shape of a sweep copied without its reports.
    """
    directory = reports_dir(artifact.path)
    if not directory.is_dir():
        return None
    content: dict[Scope, dict[str, tuple[str, frozenset[str]]]] = {}
    # Outside the try: a second composed package is a fact about the registry,
    # and the handler below names the artifact as malformed. A file that is
    # perfectly well formed would be blamed for a defect in the code.
    scored = _claim_scored_framework()
    try:
        for score in artifact.block("scores"):
            scope: Scope = (scored, str(score["case"]))
            path = directory / f"{scope[1]}.report.json"
            if not path.is_file():
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
            flows = ModelIndex.of(
                SystemModel.model_validate(raw["system_model"])
            ).flow_endpoints
            block = next(b for b in raw["analyses"] if b["framework"] == scope[0])
            claims = {claim["id"]: claim for claim in block["claims"]}
            content[scope] = {
                str(pair["reference_index"]): (
                    str(claims[pair["threat_id"]]["severity"]["level"]),
                    endpoint_form(
                        claims[pair["threat_id"]]["affected_element_ids"], flows
                    ),
                )
                for pair in score["matched"]
            }
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise ProvenanceError(
            f"{artifact.path}: cannot read matched content off its reports: {exc}"
        ) from exc
    return content


@dataclass(frozen=True)
class Band:
    """How far a sweep's must-find total moves when nothing about it changed.

    **The number every "is this fix worth a run" judgement rests on**, and the
    expensive way to get it is to sweep one configuration five times and take
    the sample deviation of five totals. That buys a deviation on four degrees
    of freedom, whose own 95% interval runs from about 0.6 to 2.9 times the
    truth — a wide answer for the price of five sweeps.

    This reads it off the fates instead. Each must-find reference is matched or
    missed in each run, so a reference matched in ``m`` of ``k`` runs
    contributes ``m(k-m)/(k(k-1))`` to the total's variance, unbiased. Summing
    over the references gives :attr:`floor_variance` from runs already paid
    for, and a pair of sweeps is enough because the sum is over the references
    rather than over the runs.

    **It is a floor and not the answer.** The sum assumes the references move
    independently, and they do not: a run that goes badly goes badly across
    several at once. :attr:`inflation` is that gap, measured rather than
    assumed — :func:`band` computes it wherever it is given repeat runs whose
    totals have a deviation of their own, and the two readings are then tested
    against each other rather than each against its own expectation.

    Without a calibration the reading is honest about being a floor.
    """

    #: Must-find references whose fate the runs can read. A reference every run
    #: agreed on contributes nothing, so a package whose record cannot name the
    #: ones nobody matched loses nothing by it.
    references: int
    #: Of those, the ones matched in some runs and not others. The whole of the
    #: floor comes from these.
    volatile: int
    runs: int
    floor_variance: float
    #: Observed variance over the calibration runs' own totals, and the floor
    #: over those same runs. ``None`` where nothing was given to calibrate on.
    observed_variance: float | None = None
    calibration_floor: float | None = None
    #: Degrees of freedom behind the calibration, which is what says how much
    #: to trust it: one repeat set of five runs carries four.
    calibration_freedom: int = 0

    @property
    def inflation(self) -> float | None:
        """How much wider the truth is than the floor, in variance. ``None`` uncalibrated."""
        if not self.observed_variance or not self.calibration_floor:
            return None
        return self.observed_variance / self.calibration_floor

    @property
    def variance(self) -> float:
        return self.floor_variance * (self.inflation or 1.0)

    @property
    def sd(self) -> float:
        return math.sqrt(self.variance)

    def runs_needed(self, effect: float) -> int:
        """Runs each side for an effect of ``effect`` must-finds to clear two deviations.

        The difference of two means of ``n`` runs has variance ``2v/n``, so the
        answer is ``n >= 8v/effect**2``. One run each side is the floor of the
        answer and never zero: a comparison needs a before and an after.
        """
        if effect <= 0:
            raise ValueError("an effect of nothing needs no measurement")
        return max(1, math.ceil(8 * self.variance / effect**2))

    def to_json(self) -> dict[str, Any]:
        return {
            "references": self.references,
            "volatile": self.volatile,
            "runs": self.runs,
            "floor_variance": round(self.floor_variance, 3),
            "floor_sd": round(math.sqrt(self.floor_variance), 3),
            "inflation": None if self.inflation is None else round(self.inflation, 3),
            "calibration_freedom": self.calibration_freedom,
            "sd": round(self.sd, 3),
            "runs_needed": {
                str(effect): self.runs_needed(effect) for effect in (3, 5, 10)
            },
        }


def _fates(
    runs: Sequence[ScoredRun], rows: Collection[tuple[str, str]] = ()
) -> dict[tuple[Scope, str], tuple[int, int]]:
    """Each must-find reference's ``(matched runs, runs that scored its case)``.

    A reference is a must-find where **any** run says so, which is how the two
    packages' half-answers combine: one marks the tier on a matched row, the
    other names the missed ones. A reference no run calls a must-find is not
    here, and one every run agreed on contributes nothing below.

    Restricted to the cases **every** run scored, which is the rule
    :func:`compare_runs` states and applies for the same reason: a case one run
    skipped has no second measurement. Folding it in reads the run that skipped
    it as a run that found nothing there, which fabricates a spread the size of
    the whole case.

    ``rows`` narrows to named ``(case, reference)`` pairs — the reading a fix
    that targets known references is priced on, rather than the corpus total.

    A reference no run in the set ever matched is absent, because no record
    names a must-find nobody found. It contributes no spread either, so the
    reading is right for the question it answers: how far the number moves
    **if the change does nothing**, which is what an effect has to clear.
    """
    shared = frozenset.intersection(*(frozenset(run.must_find) for run in runs))
    known: dict[Scope, set[str]] = {}
    for run in runs:
        for scope in shared:
            fate = run.must_find[scope]
            named = fate.missed | (fate.matched if fate.names_the_tier else frozenset())
            known.setdefault(scope, set()).update(named)
    if rows:
        wanted = set(rows)
        known = {
            scope: {ref for ref in refs if (scope[1], ref) in wanted}
            for scope, refs in known.items()
        }
    fates: dict[tuple[Scope, str], tuple[int, int]] = {}
    for scope, refs in known.items():
        for ref in refs:
            matched = sum(1 for run in runs if ref in run.must_find[scope].matched)
            fates[(scope, ref)] = (matched, len(runs))
    return fates


def _floor(fates: Mapping[tuple[Scope, str], tuple[int, int]]) -> float:
    """The variance the fates account for, summed over the references."""
    return sum(
        matched * (scoring - matched) / (scoring * (scoring - 1))
        for matched, scoring in fates.values()
        if scoring > 1
    )


def band(
    runs: Sequence[ScoredRun],
    calibration: Sequence[Sequence[ScoredRun]] = (),
    rows: Collection[tuple[str, str]] = (),
) -> Band:
    """The spread of the must-find total over ``runs``, off their fates.

    ``calibration`` is repeat sets — the same case or corpus run several times
    — whose totals carry a deviation of their own. Each one contributes its
    observed variance and its floor, and the ratio of the two sums is how much
    the independence assumption under-states. Three runs is the minimum that
    says anything, so a pair contributes nothing and is ignored rather than
    counted as agreement.
    """
    if len(runs) < 2:
        raise ValueError("a spread needs two runs or more")
    if not frozenset.intersection(*(frozenset(run.must_find) for run in runs)):
        raise ValueError("the runs share no scored case, so there is no spread to read")
    fates = _fates(runs, rows)
    observed = floor = 0.0
    freedom = 0
    for repeat in calibration:
        if len(repeat) < 3:
            continue
        # Over everything the repeat set holds, never over ``rows``. How much
        # the references co-move is a property of a run rather than of the
        # subset being priced, and a handful of rows carries too little of it
        # to measure — narrowing here read the inflation as absent and priced
        # a targeted fix against the floor alone.
        totals = _totals(repeat)
        mean = sum(totals) / len(totals)
        degrees = len(totals) - 1
        observed += sum((total - mean) ** 2 for total in totals)
        floor += _floor(_fates(repeat)) * degrees
        freedom += degrees
    return Band(
        references=len(fates),
        volatile=sum(1 for m, k in fates.values() if 0 < m < k),
        runs=len(runs),
        floor_variance=_floor(fates),
        observed_variance=observed / freedom if freedom else None,
        calibration_floor=floor / freedom if freedom else None,
        calibration_freedom=freedom,
    )


def _totals(
    runs: Sequence[ScoredRun], rows: Collection[tuple[str, str]] = ()
) -> list[int]:
    """Each run's must-find total, over the references this set can read.

    The same references the floor is summed over, so the two readings are of
    one population — the cases every run scored, and no other. A must-find
    every run matched is outside it for a record that names only the missed
    ones, and it is a constant, so it moves the mean and not the spread.
    """
    universe: dict[Scope, set[str]] = {}
    for scope, ref in _fates(runs, rows):
        universe.setdefault(scope, set()).add(ref)
    return [
        sum(
            len(refs & run.must_find[scope].matched) for scope, refs in universe.items()
        )
        for run in runs
    ]


def load_runs(paths: Iterable[Path | str]) -> list[ScoredRun]:
    """Load every artifact named, through the loader ``promote`` uses."""
    return [read_run(load_artifact(path)) for path in paths]


def _mean_jaccard(sets: Sequence[frozenset[str]]) -> float:
    """Mean pairwise Jaccard, with the empty-vs-empty pair scored 1.0.

    Two runs that both found nothing agree completely, and calling that 0.0
    would report the most stable outcome there is as the least stable one.
    """
    pairs = list(itertools.combinations(sets, 2))
    overlaps = [
        1.0 if not (left | right) else len(left & right) / len(left | right)
        for left, right in pairs
    ]
    return ratio(sum(overlaps), len(overlaps))


def compare_runs(runs: Sequence[ScoredRun]) -> list[CaseStability]:
    """Per-case stability over the cases **every** run scored.

    Restricted to the intersection on purpose: a case one sweep skipped has no
    second measurement, and folding its single run in as if it were stable
    would flatter exactly the number this exists to expose.
    """
    if len(runs) < 2:
        raise ValueError("stability needs at least two scored runs")
    refuse_incomparable(runs)
    shared = frozenset.intersection(*(run.cases for run in runs))
    if not shared:
        raise ValueError("the runs share no scored case, so nothing is comparable")

    stability = []
    for scope in sorted(shared):
        framework, case_id = scope
        sets = [run.matched[scope] for run in runs]
        references = _one_reference_count(runs, scope)
        always = len(frozenset.intersection(*sets))
        ever = len(frozenset.union(*sets))
        if ever > references:
            raise ValueError(
                f"{framework}/{case_id}: the runs match {ever} distinct references"
                f" and the case has {references}, so a matched index names a"
                " reference the case does not hold"
            )
        stability.append(
            CaseStability(
                framework=framework,
                case_id=case_id,
                runs=len(runs),
                references=references,
                recalls=tuple(run.recall[scope] for run in runs),
                always=always,
                sometimes=ever - always,
                never=references - ever,
                mean_jaccard=_mean_jaccard(sets),
                **_cause_stability(runs, scope, references, sets),
                **_content_stability(runs, scope, sets),
            )
        )
    return stability


def _cause_stability(
    runs: Sequence[ScoredRun],
    scope: Scope,
    references: int,
    sets: Sequence[frozenset[str]],
) -> dict[str, int | None]:
    """Over the references every run missed: one cause each time, or several."""
    if any(run.causes is None for run in runs):
        return {"cause_stable": None, "cause_moving": None}
    never = {str(index) for index in range(references)} - frozenset.union(*sets)
    stable = 0
    for reference in never:
        causes = set()
        for run in runs:
            assert run.causes is not None
            charged = run.causes.get(scope, {}).get(reference)
            if charged is None:
                raise ValueError(
                    f"{scope[0]}/{scope[1]}: {run.label} missed reference"
                    f" {reference} and its losses block charges it to nothing"
                )
            causes.add(charged)
        stable += len(causes) == 1
    return {"cause_stable": stable, "cause_moving": len(never) - stable}


def _content_stability(
    runs: Sequence[ScoredRun], scope: Scope, sets: Sequence[frozenset[str]]
) -> dict[str, int | None]:
    """Over the references two or more runs matched: one band and one place, or not."""
    unread: dict[str, int | None] = {
        "severity_held": None,
        "severity_moved": None,
        "place_held": None,
        "place_moved": None,
    }
    if IDENTIFIER_OF[scope[0]] is not None or any(run.content is None for run in runs):
        return unread
    counts = Counter(reference for matched in sets for reference in matched)
    severity_held = severity_moved = place_held = place_moved = 0
    for reference, seen in counts.items():
        if seen < 2:
            continue
        bands = set()
        places = set()
        for run in runs:
            assert run.content is not None
            entry = run.content.get(scope, {}).get(reference)
            if entry is not None:
                bands.add(entry[0])
                places.add(entry[1])
        if len(bands) == 1:
            severity_held += 1
        else:
            severity_moved += 1
        if len(places) == 1:
            place_held += 1
        else:
            place_moved += 1
    return {
        "severity_held": severity_held,
        "severity_moved": severity_moved,
        "place_held": place_held,
        "place_moved": place_moved,
    }


def refuse_incomparable(runs: Sequence[ScoredRun]) -> None:
    """The differences no warning can caveat: refused before any set is read.

    The same artifact named twice is one measurement counted as two, and it
    would report a case as perfectly stable on the strength of agreeing with
    itself. Two corpus digests are two coordinate systems: reference ``3`` of
    a case in one corpus is whichever claim sat third in *that* file, and the
    matched sets would be intersected as though the indices meant one thing.
    """
    labels = [run.label for run in runs]
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    if repeated:
        raise ValueError(
            f"the same artifact is named more than once: {', '.join(repeated)}"
        )
    digests = sorted({run.corpus_digest for run in runs})
    if len(digests) > 1:
        raise ValueError(
            "the runs were scored against different corpora"
            f" ({', '.join(digest[:12] for digest in digests)}), so a reference"
            " index in one names nothing in the other; re-run over one corpus"
        )


def _one_reference_count(runs: Sequence[ScoredRun], scope: Scope) -> int:
    """The case's reference count, which every run must agree on.

    One corpus digest should make this unanimous, and the check is here because
    "should" is not enough for the ``never`` bucket: reading the first run's
    count against a union over every run produces a negative number when the
    two counts are simply about different lists.
    """
    counts = sorted({run.references[scope] for run in runs})
    if len(counts) > 1:
        framework, case_id = scope
        raise ValueError(
            f"{framework}/{case_id}: the runs disagree about how many references"
            f" the case has ({', '.join(str(count) for count in counts)}), so"
            " their matched sets index different lists"
        )
    return counts[0]


def aggregate_stability(stability: Sequence[CaseStability]) -> dict[str, Any]:
    """The corpus-wide view, pooled over references rather than over cases."""
    references = sum(entry.references for entry in stability)
    always = sum(entry.always for entry in stability)
    sometimes = sum(entry.sometimes for entry in stability)

    def pooled(field: str) -> int | None:
        """A sum that stays ``None`` if any case could not read the field."""
        values = [getattr(entry, field) for entry in stability]
        return None if any(value is None for value in values) else sum(values)

    return {
        "cases": len(stability),
        "cause_stable": pooled("cause_stable"),
        "cause_moving": pooled("cause_moving"),
        "severity_held": pooled("severity_held"),
        "severity_moved": pooled("severity_moved"),
        "place_held": pooled("place_held"),
        "place_moved": pooled("place_moved"),
        "runs": max((entry.runs for entry in stability), default=0),
        "references": references,
        "always_matched": always,
        "sometimes_matched": sometimes,
        "never_matched": sum(entry.never for entry in stability),
        "always_rate": round(ratio(always, references), 3),
        "volatile_rate": round(ratio(sometimes, references), 3),
        "mean_jaccard": round(
            ratio(sum(entry.mean_jaccard for entry in stability), len(stability)), 3
        ),
        "worst_case_recall_spread": round(
            max((entry.recall_spread for entry in stability), default=0.0), 3
        ),
    }


def comparability_warnings(runs: Sequence[ScoredRun]) -> list[str]:
    """What makes these runs not a clean repeat of each other.

    Reported rather than refused, on the same principle
    :class:`~evals.harness.artifact.EvalArtifact` applies to a sweep with
    failures: comparing two modes or two configurations is sometimes exactly the
    question being asked, and the tool's job is to make sure it is a chosen
    comparison rather than an accidental one. Anything here means the spread
    below includes a configuration difference, not just sampling noise.
    """
    warnings = []
    modes = {run.mode for run in runs}
    if len(modes) > 1:
        warnings.append(f"runs are of different modes: {', '.join(sorted(modes))}")
    warnings += _model_warnings(runs)
    cases = [run.cases for run in runs]
    unshared = frozenset.union(*cases) - frozenset.intersection(*cases)
    if unshared:
        warnings.append(
            "cases scored by only some runs, excluded from the comparison:"
            f" {', '.join(f'{name}/{case}' for name, case in sorted(unshared))}"
        )
    return warnings


def _model_warnings(runs: Sequence[ScoredRun]) -> list[str]:
    """Every ``models`` field the runs disagree on, named individually.

    Field by field because the two disagreements mean different things: a
    changed generator is the thing under test, and a changed measurement
    silently re-measures every case that did not change at all.
    """
    fields = sorted({field for run in runs for field in run.models})
    return [
        f"runs disagree on {field}: {', '.join(sorted(values))}"
        for field, values in ((field, _values(runs, field)) for field in fields)
        if len(values) > 1
    ]


def _values(runs: Sequence[ScoredRun], field: str) -> set[str]:
    def rendered(value: Any) -> str:
        if isinstance(value, Mapping):
            return ", ".join(f"{key}={value[key]}" for key in sorted(value))
        return str(value)

    return {rendered(run.models.get(field)) for run in runs}
