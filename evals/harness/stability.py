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
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis_service.claims import FrameworkName
from analysis_service.system_model import ModelIndex, SystemModel
from evals.harness.artifact import EvalArtifact, load_artifact
from evals.harness.bundle import reports_dir
from evals.harness.fingerprint import IDENTIFIER_OF
from evals.harness.identity import endpoint_form
from evals.harness.provenance import ProvenanceError
from evals.harness.scorer import ratio

#: One case of one framework. Stability is per framework because the two
#: instruments answer over different sets — STRIDE's open claim set through a
#: composed identity, ASVS's finite catalog by string compare — so pooling their spread
#: would report one volatility figure over two populations.
Scope = tuple[FrameworkName, str]


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
    try:
        for score in artifact.block("scores"):
            scope: Scope = ("stride", str(score["case"]))
            matched[scope] = frozenset(
                str(pair["reference_index"]) for pair in score["matched"]
            )
            references[scope] = int(score["counts"]["references"])
            recall[scope] = float(score["metrics"]["recall"])
        for entry in artifact.block("applicability"):
            scope = ("asvs", str(entry["case"]))
            matched[scope] = frozenset(str(item) for item in entry["matched"])
            references[scope] = int(entry["expected"])
            recall[scope] = float(entry["recall"])
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
    )


def _causes(artifact: EvalArtifact) -> dict[Scope, dict[str, str]] | None:
    """Each missed reference's charged cause, off the ``losses`` block, or ``None`` without one."""
    try:
        rows = artifact.block("losses")
    except KeyError:
        return None
    if rows is None:
        return None
    try:
        return {
            ("stride", str(row["case"])): {
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

    Read only for a package that composes its identity from an action and a
    place, by its own declaration: its scorer records which claim matched
    each reference, and its claims carry a severity. A catalog-identified
    package records matched identifiers alone, so there is nothing here to
    read for it. ``None`` where the bundle is absent, which is the shape of a
    sweep copied without its reports.
    """
    directory = reports_dir(artifact.path)
    if not directory.is_dir():
        return None
    content: dict[Scope, dict[str, tuple[str, frozenset[str]]]] = {}
    try:
        for score in artifact.block("scores"):
            scope: Scope = ("stride", str(score["case"]))
            if IDENTIFIER_OF[scope[0]] is not None:
                continue
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
