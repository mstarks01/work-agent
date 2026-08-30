"""ASVS's scorer: an applicability confusion matrix over a finite catalog.

STRIDE's claim set is open, so grading it needs a rule that composes an identity
from what a claim carries — a lane, an action verb and the elements it names.
**ASVS's is closed**, and that one difference removes even the rule: the catalog
is finite, a claim carries the standard's own identifier, and two claims about
one requirement compare by string. #167 settled that, and this module is what it
looks like.

So the shape of the answer differs too. STRIDE's scorer reports recall against an
open list and asks a person about whatever else a run produced, because "the
corpus did not list it" cannot mean "it is wrong". Here it can: a run at level ``L`` rules on
:func:`~analysis_service.frameworks.asvs.catalog.requirements_for` and nothing
else, so the complement of the reference set is a real negative and the four
cells of a confusion matrix are all reachable.

**What counts as applied.** An ASVS claim never reports a pass — verification
needs source code and the people who built the system, and a job here carries
prose. Its three verdicts split two ways for this purpose: ``confirmed`` and
``needs-info`` both assert the requirement *applies*, and ``rejected`` is the
critic ruling that it does not. So the matrix is over applicability, which is the
only question this package answers.

Security: nothing here reads a claim's prose. Every value is an identifier the
catalog already holds or a verdict from a closed vocabulary, so a claim's text
cannot steer a number (OWASP LLM01).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from analysis_service.frameworks.asvs.catalog import requirements_for
from analysis_service.frameworks.asvs.record import requirement_of
from analysis_service.report import (
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    RuledClaim,
    ScopeEntry,
)
from analysis_service.sources import CARRIED_EVIDENCE_KINDS
from evals.harness.reference import (
    DISPOSITION_FOR_EVIDENCE,
    CaseFramework,
    GoldenCase,
    ReferenceRequirement,
)

#: The verdicts that assert a requirement applies to this system. ``rejected``
#: is the critic ruling it does not, which is the negative answer rather than a
#: missing one — so it is read, not ignored.
APPLIES: frozenset[str] = frozenset({"confirmed", "needs-info"})

#: This scorer's package, at the closed type the corpus is keyed by.
FRAMEWORK: FrameworkName = "asvs"


class ApplicabilityError(ValueError):
    """A case cannot be scored for ASVS at all."""


@dataclass(frozen=True)
class ApplicabilityScore:
    """One case's ASVS answer, as the four cells and the two recall figures.

    ``excluded`` is a count where the other cells are lists. It is the bulk of
    the matrix — a level-2 run rules on far more requirements than any case
    expects a ruling on — and naming several hundred identifiers a run correctly
    said nothing about would bury the three lists a reader is there for.

    ``off_catalog`` is its own cell rather than folded into ``over_applied``.
    A claim naming a requirement outside the level's set is a different defect
    from one naming a real requirement the case did not expect: the first is the
    package composing an identifier its own catalog does not hold at this level,
    which is a bug, and the second is a judgement this corpus disagrees with.
    """

    case: str
    level: int
    #: Whether this case is near the architectures *this package's* exemplars
    #: demonstrate. On the ``(case, framework)`` pair because exemplars live at
    #: ``frameworks/<name>/lanes/<lane>/exemplars.md`` — a case near STRIDE's
    #: payments exemplar is near nothing of ASVS's.
    exemplar_proximity: str
    #: Whether the case read this framework's records as complete against the
    #: model. It decides whether :attr:`precision` is defined at all, because
    #: the complement of a sample is not a set of negatives.
    reference_set: str
    universe: int
    expected: tuple[str, ...]
    must_find: tuple[str, ...]
    matched: tuple[str, ...]
    missed: tuple[str, ...]
    #: Expected requirements the run applied only through a
    #: ``needs-other-evidence`` scope entry: a lane raised each, and the service
    #: withheld the claim for want of evidence the job does not carry. Matched,
    #: because the entry asserts the requirement applies (#454); listed apart,
    #: because the report carries no claim for it.
    matched_by_deferral: tuple[str, ...]
    must_find_missed: tuple[str, ...]
    over_applied: tuple[str, ...]
    rejected: tuple[str, ...]
    off_catalog: tuple[str, ...]
    excluded: int

    @property
    def recall(self) -> float:
        """Of the requirements this case expects a ruling on, how many arrived."""
        return len(self.matched) / len(self.expected) if self.expected else 0.0

    @property
    def must_find_recall(self) -> float:
        """The same over the tier a gate would read."""
        if not self.must_find:
            return 0.0
        return (len(self.must_find) - len(self.must_find_missed)) / len(self.must_find)

    @property
    def precision(self) -> float | None:
        """Of what the run said applies, how much the case expected.

        ``off_catalog`` counts against this: a claim naming a requirement the
        level does not carry is still a claim the reader has to read.

        **``None`` on a sampled reference set**, which is every case until a
        **Case Sitting** clears one. The figure divides by the requirements the
        run applied that the case did not list, and reading those as wrong
        needs a set somebody read as complete: on a sample, an unlisted
        requirement the run applied may be correct and merely unrecorded.
        ``evals/harness/scorer.py`` refuses the same inference for STRIDE, and
        for the same reason — scoring it punishes finding real things and
        pushes every tuning cycle toward under-reporting.

        ``over_applied`` stays a list either way. It is what
        :mod:`evals.harness.pairing` reads and what
        :func:`over_applied_for_promotion` feeds back, and both are readings a
        person settles rather than a rate anything gates on.
        """
        if self.reference_set != "exhaustive":
            return None
        applied = len(self.matched) + len(self.over_applied) + len(self.off_catalog)
        return len(self.matched) / applied if applied else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framework": FRAMEWORK,
            "level": self.level,
            "exemplar_proximity": self.exemplar_proximity,
            "reference_set": self.reference_set,
            "universe": self.universe,
            "expected": len(self.expected),
            "must_find": len(self.must_find),
            "matched": list(self.matched),
            "missed": list(self.missed),
            "matched_by_deferral": list(self.matched_by_deferral),
            "must_find_missed": list(self.must_find_missed),
            "over_applied": list(self.over_applied),
            "rejected": list(self.rejected),
            "off_catalog": list(self.off_catalog),
            "excluded": self.excluded,
            "recall": round(self.recall, 4),
            "must_find_recall": round(self.must_find_recall, 4),
            "precision": None if self.precision is None else round(self.precision, 4),
        }


def declared(case: GoldenCase) -> CaseFramework:
    """This case's ASVS declaration, or a refusal naming what is missing."""
    for entry in case.meta.frameworks:
        if entry.name == FRAMEWORK:
            return entry
    raise ApplicabilityError(f"{case.id}: case.json does not declare asvs")


def declared_level(case: GoldenCase) -> int:
    """The ASVS level this case declares, which decides its universe.

    Read off the case rather than passed in: an option that changes which
    requirements are in play changes the reference set, so it travels with the
    reference set. A case that declares ASVS without one cannot be scored, and
    saying so here beats scoring it against the wrong catalog slice.
    """
    level = declared(case).options.get("level")
    if not isinstance(level, int):
        raise ApplicabilityError(
            f"{case.id}: case.json declares asvs with no integer level, so"
            " the requirement set it rules on is undefined"
        )
    return level


def applied_requirements(claims: Sequence[RuledClaim]) -> tuple[set[str], set[str]]:
    """One block's claims split into what applies and what the critic rejected.

    Hand it **every** ruled claim — :meth:`FrameworkAnalysis.all_claims` — because
    the split it makes is by verdict, and a block keeps its rejections in a
    second array. Handed ``claims`` alone it can only ever return an empty
    rejected set, which reads as a run that rejected nothing.

    Returns ``(applied, rejected)`` as sets of the standard's identifiers.
    A claim whose ID does not parse contributes to neither: the block's own
    checks report a malformed ID, and counting it here as a miss would charge one
    defect to a metric that is not about it.
    """
    applied: set[str] = set()
    rejected: set[str] = set()
    for claim in claims:
        requirement = requirement_of(claim.id)
        if not requirement:
            continue
        target = applied if claim.verdict.status in APPLIES else rejected
        target.add(requirement)
    return applied, rejected


def score_applicability(
    case: GoldenCase, block: FrameworkAnalysis
) -> ApplicabilityScore:
    """One case's ASVS block against its reference records. No model call."""
    level = declared_level(case)
    universe = {requirement.id for requirement in requirements_for(level)}

    references = [
        reference
        for reference in case.references.get(FRAMEWORK) or ()
        if isinstance(reference, ReferenceRequirement)
    ]
    expected = {reference.requirement for reference in references}
    must_find = {
        reference.requirement for reference in references if reference.must_find
    }

    # Both arrays, because the critic's rejections land in ``rejected_claims``
    # and the report validator forbids one sitting in ``claims``. Reading
    # ``claims`` alone left the negative cell permanently empty: a requirement
    # the critic ruled inapplicable reached neither ``rejected`` here nor the
    # block's ``scope``, so it was missed with nothing saying why.
    applied, rejected = applied_requirements(block.all_claims())
    off_catalog = applied - universe
    # A ``needs-other-evidence`` scope entry is the service's other way of
    # saying a requirement applies: a lane raised it, and the claim was withheld
    # because settling it needs evidence the job does not carry (#417). The
    # matrix here is over applicability, so the entry counts as applied beside
    # a ``confirmed`` or ``needs-info`` claim (#454). It is listed apart because
    # the report carries no claim for it — that is the policy cost
    # `CARRIED_EVIDENCE_KINDS` sets.
    deferred_units = {
        entry.unit for entry in block.scope if entry.state == "needs-other-evidence"
    }
    in_universe = (applied | deferred_units) & universe

    matched = expected & in_universe
    missed = expected - in_universe
    over_applied = in_universe - expected
    matched_by_deferral = matched & (deferred_units - applied)

    return ApplicabilityScore(
        case=case.id,
        level=level,
        exemplar_proximity=declared(case).exemplar_proximity,
        reference_set=declared(case).reference_set,
        universe=len(universe),
        expected=tuple(sorted(expected)),
        must_find=tuple(sorted(must_find)),
        matched=tuple(sorted(matched)),
        missed=tuple(sorted(missed)),
        matched_by_deferral=tuple(sorted(matched_by_deferral)),
        must_find_missed=tuple(sorted(must_find - in_universe)),
        over_applied=tuple(sorted(over_applied)),
        rejected=tuple(sorted(rejected)),
        off_catalog=tuple(sorted(off_catalog)),
        # The fourth cell: in the universe, not expected, and not applied.
        excluded=len(universe - expected - in_universe),
    )


# ---------------------------------------------------------------------------
# Disposition: what a run concluded, beside whether the requirement applies.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """What one report says about one requirement, at the grain a case judges.

    Six kinds, and they are the *report's* own vocabulary rather than the
    corpus's: three verdicts a claim can carry, two scope states that answer
    without one, and silence. The corpus speaks in
    :data:`~evals.harness.reference.AsvsDisposition`, and :func:`satisfies` is
    the only place the two meet — which is what keeps the mapping relative to
    what the job carries rather than hard-coded at each comparison.

    **``needs_evidence`` is not here, because it is not in the report.** The
    fan-in strips it once it has decided whether a proposal becomes a draft
    (``evidence._ROUTED_AWAY``), on the ground that the claim-versus-scope-entry
    split already carries the distinction. So everything below reads a verdict
    or a scope entry, which is all a consumer of the payload can read either.

    ``needs`` is set only for ``deferred``, where the scope entry names the kind
    of evidence that would settle the requirement.
    """

    kind: Literal[
        "confirmed", "needs-info", "rejected", "not-applicable", "deferred", "silent"
    ]
    needs: str = ""


#: The evidence kind each disposition asks for, inverted from the corpus table
#: so the two cannot drift. ``not-applicable`` and ``gap-from-prose`` are absent:
#: neither asks for evidence, and :func:`satisfies` answers them before it looks
#: here.
EVIDENCE_FOR_DISPOSITION: Mapping[str, str] = MappingProxyType(
    {disposition: kind for kind, disposition in DISPOSITION_FOR_EVIDENCE.items()}
)


def observe(
    requirement: str,
    applied_by_id: Mapping[str, RuledClaim],
    rejected_by_id: Mapping[str, RuledClaim],
    scope_by_unit: Mapping[str, ScopeEntry],
) -> Observation:
    """What the report says about one requirement.

    A requirement reaches a reader through exactly one of these: the fan-in
    hands ``scope_entries`` every ruled claim, so a requirement a claim covers
    gets no scope entry, and one the critic rejected is covered. The order below
    states that invariant rather than breaking a tie.

    **An ``applicable`` scope entry reads as silence, and that is a decision.**
    It is the state ``scope_entries`` gives every requirement no claim covers —
    *considered, and nothing raised*. For a case that listed the requirement,
    that is the miss the recall figure already charges, so naming it a seventh
    disposition would put one failure in two metrics. A case cannot expect it,
    which is why :data:`~evals.harness.reference.AsvsDisposition` has no word
    for it.
    """
    claim = applied_by_id.get(requirement)
    if claim is not None:
        settled = claim.verdict.status == "confirmed"
        return Observation("confirmed" if settled else "needs-info")
    if requirement in rejected_by_id:
        return Observation("rejected")
    entry = scope_by_unit.get(requirement)
    if entry is None:
        return Observation("silent")
    if entry.state == "needs-other-evidence":
        return Observation("deferred", entry.needs)
    if entry.state == "not-applicable":
        return Observation("not-applicable")
    return Observation("silent")


def satisfies(
    disposition: str,
    observation: Observation,
    carried: Collection[str] = CARRIED_EVIDENCE_KINDS,
) -> bool:
    """Whether one run's answer is the one this case expected.

    **``carried`` is read, never assumed**, and that is the whole reason this is
    a function rather than a lookup table. Which kinds of evidence become a
    **Scope Entry** is a policy
    :data:`~analysis_service.sources.CARRIED_EVIDENCE_KINDS` sets, not a
    property of the kinds: a job carrying source code would keep a ``needs-code``
    proposal as a claim and rule it, exactly as it keeps a ``prose`` one today.
    A scorer that hard-coded *code, config and people are deferred* would invert
    silently the day that tuple grows, and would fail a run for doing the newly
    correct thing.

    The cost of reading the policy is that two carried kinds are
    indistinguishable in the report — both arrive as ``needs-info``, because the
    field that told them apart is stripped before the payload. With one carried
    kind, which is what ships, there is nothing to confuse.
    """
    if disposition == "not-applicable":
        return observation.kind in {"rejected", "not-applicable"}
    if disposition == "gap-from-prose":
        return observation.kind == "confirmed"
    wanted = EVIDENCE_FOR_DISPOSITION[disposition]
    if wanted in carried:
        return observation.kind == "needs-info"
    return observation.kind == "deferred" and observation.needs == wanted


@dataclass(frozen=True)
class Judged:
    """One requirement a case judged, the run answered, and the scorer compared."""

    requirement: str
    expected: str
    observed: Observation
    ok: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "expected": self.expected,
            "observed": self.observed.kind,
            "needs": self.observed.needs,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class DispositionScore:
    """One case's evidence routing: did the run reach the right next action.

    Deliberately **not** folded into :class:`ApplicabilityScore`. That one
    answers *is this requirement in play*, and this one answers *what can this
    submission conclude about it* — two questions one number would average into
    something that moves for either reason. A run can score full applicability
    recall and still tell a submitter to send more description for a property
    only the source settles.

    ``unreached`` is the requirements a case judged that the run said nothing
    about. They are excluded from :attr:`accuracy` rather than counted wrong,
    because the recall figure beside this one already charges them, and charging
    them twice would make one miss move two metrics.

    ``unjudged`` counts records carrying no expected disposition. It is the
    corpus's own gap, and it rides in the artifact so a reader can tell a small
    denominator from a good score.
    """

    case: str
    carried: tuple[str, ...]
    judged: tuple[Judged, ...]
    unreached: tuple[str, ...]
    unjudged: int

    @property
    def correct(self) -> tuple[Judged, ...]:
        return tuple(entry for entry in self.judged if entry.ok)

    @property
    def wrong(self) -> tuple[Judged, ...]:
        return tuple(entry for entry in self.judged if not entry.ok)

    @property
    def accuracy(self) -> float:
        """Of the judged requirements the run answered, how many it answered right."""
        return len(self.correct) / len(self.judged) if self.judged else 0.0

    @property
    def needs_other_evidence(self) -> tuple[Judged, ...]:
        """The judged records only evidence this job cannot carry would settle.

        The denominator of the two rates below, and the only records on which
        either failure is reachable at all. A case expecting ``gap-from-prose``
        cannot produce a false prose request, so counting it in the denominator
        would report a flattering zero.
        """
        return tuple(
            entry
            for entry in self.judged
            if EVIDENCE_FOR_DISPOSITION.get(entry.expected, "")
            not in ("", *self.carried)
        )

    @property
    def false_prose_requests(self) -> tuple[Judged, ...]:
        """Asked for more description where no description could ever answer.

        The failure this instrument exists for. The requirement needs source,
        settings or a person; the run raised a ``needs-info`` claim instead,
        which reads to a submitter as *send more of what you already sent*.
        """
        return tuple(
            entry
            for entry in self.needs_other_evidence
            if entry.observed.kind == "needs-info"
        )

    @property
    def false_confirmed(self) -> tuple[Judged, ...]:
        """Ruled a deficiency from prose that only other evidence could establish."""
        return tuple(
            entry
            for entry in self.needs_other_evidence
            if entry.observed.kind == "confirmed"
        )

    @property
    def wrong_kind(self) -> tuple[Judged, ...]:
        """Deferred for the wrong kind of evidence: right refusal, wrong routing."""
        return tuple(
            entry
            for entry in self.needs_other_evidence
            if entry.observed.kind == "deferred" and not entry.ok
        )

    @property
    def false_not_applicable(self) -> tuple[Judged, ...]:
        """Ruled out a requirement the case says applies."""
        return tuple(
            entry
            for entry in self.wrong
            if entry.expected != "not-applicable"
            and entry.observed.kind in {"rejected", "not-applicable"}
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framework": FRAMEWORK,
            "carried": list(self.carried),
            "judged": len(self.judged),
            "correct": len(self.correct),
            "accuracy": round(self.accuracy, 4),
            "unreached": list(self.unreached),
            "unjudged": self.unjudged,
            "needs_other_evidence": len(self.needs_other_evidence),
            "wrong": [entry.to_json() for entry in self.wrong],
            "false_prose_requests": [
                entry.to_json() for entry in self.false_prose_requests
            ],
            "false_confirmed": [entry.to_json() for entry in self.false_confirmed],
            "false_not_applicable": [
                entry.to_json() for entry in self.false_not_applicable
            ],
            "wrong_kind": [entry.to_json() for entry in self.wrong_kind],
        }


def score_dispositions(
    case: GoldenCase,
    block: FrameworkAnalysis,
    carried: Collection[str] = CARRIED_EVIDENCE_KINDS,
) -> DispositionScore:
    """One case's expected dispositions against what the run actually said.

    Security: every value compared here is a closed-vocabulary token or a
    catalog identifier — a verdict status, a scope state, an evidence kind. No
    claim prose is read, so a model's text cannot move a number (OWASP LLM01).
    """
    references = [
        reference
        for reference in case.references.get(FRAMEWORK) or ()
        if isinstance(reference, ReferenceRequirement)
    ]
    applied_by_id = {
        requirement_of(claim.id): claim
        for claim in block.claims
        if requirement_of(claim.id)
    }
    rejected_by_id = {
        requirement_of(claim.id): claim
        for claim in block.rejected_claims
        if requirement_of(claim.id)
    }
    scope_by_unit = {entry.unit: entry for entry in block.scope}

    judged: list[Judged] = []
    unreached: list[str] = []
    unjudged = 0
    for reference in sorted(references, key=lambda ref: ref.requirement):
        if reference.disposition is None:
            unjudged += 1
            continue
        observation = observe(
            reference.requirement, applied_by_id, rejected_by_id, scope_by_unit
        )
        if observation.kind == "silent":
            unreached.append(reference.requirement)
            continue
        judged.append(
            Judged(
                requirement=reference.requirement,
                expected=reference.disposition,
                observed=observation,
                ok=satisfies(reference.disposition, observation, carried),
            )
        )

    return DispositionScore(
        case=case.id,
        carried=tuple(carried),
        judged=tuple(judged),
        unreached=tuple(unreached),
        unjudged=unjudged,
    )


def pooled_dispositions(scores: Sequence[DispositionScore]) -> Mapping[str, Any]:
    """The corpus-wide routing figures, pooled over requirements.

    Pooled rather than averaged per case, for the reason :func:`pooled` is: a
    case carrying two judged records would otherwise weigh as much as one
    carrying seventeen. Each rate carries its own denominator beside it, so a
    reader can tell a clean run from an unexercised one.
    """
    judged = sum(len(score.judged) for score in scores)
    correct = sum(len(score.correct) for score in scores)
    reachable = sum(len(score.needs_other_evidence) for score in scores)
    routed_right = sum(
        1 for score in scores for entry in score.needs_other_evidence if entry.ok
    )
    false_prose = sum(len(score.false_prose_requests) for score in scores)
    false_confirmed = sum(len(score.false_confirmed) for score in scores)
    wrong_kind = sum(len(score.wrong_kind) for score in scores)
    applicable = sum(
        1
        for score in scores
        for entry in score.judged
        if entry.expected != "not-applicable"
    )
    false_not_applicable = sum(len(score.false_not_applicable) for score in scores)
    return {
        "cases": len(scores),
        "judged": judged,
        "correct": correct,
        "accuracy": round(correct / judged, 4) if judged else 0.0,
        "unreached": sum(len(score.unreached) for score in scores),
        "unjudged": sum(score.unjudged for score in scores),
        "needs_other_evidence": reachable,
        "false_prose_requests": false_prose,
        "false_prose_request_rate": (
            round(false_prose / reachable, 4) if reachable else 0.0
        ),
        "false_confirmed": false_confirmed,
        "false_confirmed_rate": (
            round(false_confirmed / reachable, 4) if reachable else 0.0
        ),
        "wrong_kind": wrong_kind,
        # Counted from the records that were actually right, never from the
        # three named failures subtracted off the denominator. A record the run
        # ruled out entirely is none of those three and is still wrong, so
        # subtracting would report it as correctly routed.
        "evidence_kind_accuracy": (
            round(routed_right / reachable, 4) if reachable else 0.0
        ),
        "applicable_judged": applicable,
        "false_not_applicable": false_not_applicable,
        "false_not_applicable_rate": (
            round(false_not_applicable / applicable, 4) if applicable else 0.0
        ),
    }


def render_dispositions(scores: Sequence[DispositionScore]) -> None:
    """The routing table: what a run told the submitter to do next.

    Printed apart from the applicability table because it answers a different
    question, and a reader who reads one number off the wrong table draws the
    wrong conclusion about what to fix.
    """
    if not scores:
        return
    print("\nASVS disposition (mechanical, closed vocabulary)")
    print(
        f"{'case':<26} {'judged':>7} {'acc':>6} {'unjud':>6} {'unrch':>6}"
        f" {'prose!':>7} {'conf!':>6} {'kind!':>6} {'na!':>4}"
    )
    for score in scores:
        print(
            f"{score.case:<26} {len(score.judged):>7} {score.accuracy:>6.0%}"
            f" {score.unjudged:>6} {len(score.unreached):>6}"
            f" {len(score.false_prose_requests):>7}"
            f" {len(score.false_confirmed):>6} {len(score.wrong_kind):>6}"
            f" {len(score.false_not_applicable):>4}"
        )
    totals = pooled_dispositions(scores)
    print(
        f"pooled over {totals['cases']} cases:"
        f" accuracy {totals['accuracy']:.0%}"
        f" ({totals['correct']}/{totals['judged']}),"
        f" {totals['unjudged']} records carry no expected disposition"
        " (instrument, non-gating)"
    )
    if totals["needs_other_evidence"]:
        print(
            f"  of {totals['needs_other_evidence']} requirements needing evidence"
            " this job cannot carry:"
            f" {totals['false_prose_requests']} asked for more prose"
            f" ({totals['false_prose_request_rate']:.0%}),"
            f" {totals['false_confirmed']} were ruled from prose"
            f" ({totals['false_confirmed_rate']:.0%}),"
            f" {totals['wrong_kind']} named the wrong kind"
        )


def published(blocks: Mapping[str, Any], metric: str) -> float | None:
    """One pooled ASVS number, for the comparison table."""
    return _published(blocks, "applicability_aggregate", metric)


def published_disposition(blocks: Mapping[str, Any], metric: str) -> float | None:
    """One pooled routing number, for the comparison table."""
    return _published(blocks, "disposition_aggregate", metric)


def _published(
    blocks: Mapping[str, Any], aggregate_key: str, metric: str
) -> float | None:
    aggregate = blocks.get(aggregate_key)
    if not isinstance(aggregate, Mapping):
        return None
    value = aggregate.get(metric)
    return float(value) if isinstance(value, int | float) else None


def _rate(value: float | None) -> str:
    """One rate for a table cell, or ``n/a`` where the figure is undefined.

    Printed rather than defaulted to zero: a rate nobody can compute and a rate
    that came out at zero are different facts, and a column that spells them the
    same way reads the second as the first.
    """
    return "n/a" if value is None else f"{value:.0%}"


def pooled(scores: Sequence[ApplicabilityScore]) -> Mapping[str, Any]:
    """The corpus-wide figures, pooled over claims rather than averaged per case.

    A per-case mean would weight a case carrying 6 records the same as one
    carrying 17, which is the arithmetic that makes a small case's miss vanish.

    **Precision pools over the exhaustive cases alone**, for the reason
    :attr:`ApplicabilityScore.precision` gives, and ``precision_cases`` carries
    that denominator beside it: a figure over no case at all is ``None`` rather
    than a clean-reading zero.
    """
    expected = sum(len(score.expected) for score in scores)
    matched = sum(len(score.matched) for score in scores)
    must_find = sum(len(score.must_find) for score in scores)
    must_find_missed = sum(len(score.must_find_missed) for score in scores)
    read = [score for score in scores if score.reference_set == "exhaustive"]
    read_matched = sum(len(score.matched) for score in read)
    applied = read_matched + sum(
        len(score.over_applied) + len(score.off_catalog) for score in read
    )
    return {
        "cases": len(scores),
        "expected": expected,
        "matched": matched,
        "missed": sum(len(score.missed) for score in scores),
        "matched_by_deferral": sum(len(score.matched_by_deferral) for score in scores),
        "recall": round(matched / expected, 4) if expected else 0.0,
        "must_find": must_find,
        "must_find_recall": (
            round((must_find - must_find_missed) / must_find, 4) if must_find else 0.0
        ),
        "precision": round(read_matched / applied, 4) if applied else None,
        "precision_cases": len(read),
        "off_catalog": sum(len(score.off_catalog) for score in scores),
    }


def exemplar_delta(scores: Sequence[ApplicabilityScore]) -> dict[str, float]:
    """The near-vs-far applicability-recall delta: tracked, **non-gating**.

    The same question the STRIDE scorer's delta asks — does output get worse
    away from the architectures this package's exemplars demonstrate — over the
    number this package produces. It is a separate function rather than a shared
    one because the recall it averages is a different measurement, and a
    ``near_recall`` column pooling both would be an average of a rule-relative
    figure and a set comparison.
    """
    near = [score for score in scores if score.exemplar_proximity == "near"]
    far = [score for score in scores if score.exemplar_proximity == "far"]
    near_recall = _mean(score.recall for score in near)
    far_recall = _mean(score.recall for score in far)
    return {
        "near_recall": round(near_recall, 3),
        "far_recall": round(far_recall, 3),
        "delta": round(near_recall - far_recall, 3),
    }


def over_applied_for_promotion(
    scores: Sequence[ApplicabilityScore],
) -> list[dict[str, Any]]:
    """The corpus feedback loop, ASVS's half.

    A requirement a run ruled applicable that the case did not expect is either
    the run over-applying or the reference set being incomplete — the same
    distinction ``valid-unlisted`` draws for STRIDE, surfaced for the next
    reading session to settle.

    **Cheaper than STRIDE's, and worth saying why.** STRIDE needs a person to
    separate a grounded unlisted threat from noise, one vote per finding. Here
    the list falls out of set arithmetic, and ``off_catalog`` has already taken
    out the case that is a package bug rather than a judgement — so this costs
    no reviewer's attention at all.
    """
    return [
        {
            "case": score.case,
            "framework": FRAMEWORK,
            "level": score.level,
            "requirement": requirement,
        }
        for score in scores
        for requirement in score.over_applied
    ]


@dataclass(frozen=True)
class ApplicabilityYield:
    """What one case's ASVS critic pass cost and bought.

    The same two-sided instrument :mod:`evals.harness.critic_yield` is for, over
    the only destructive move this package's **Critic** has. STRIDE's critic
    kills drafts; ASVS's *rules applicability*, and its `rejected` verdict is
    the ruling that a requirement does not apply to a system of this shape. So
    "what did the critic destroy, and how much of it was real" reads:

    * ``earned`` — requirements it rejected that the case did not expect. The
      critic doing its job, and the direct counterpart of ``unsupported_killed``.
    * ``destroyed`` — requirements it rejected that the case *did* expect. **The
      number that can veto the pattern here**, exactly as ``matched_killed``
      does for STRIDE.

    Only the pair means anything. A rejection count alone reads as either.

    **No second scoring pass.** STRIDE's yield works by scoring one case twice
    through the identity rule, which is cheap because the rule is a pure
    function. Here both sides are requirement identifiers, so the whole
    instrument is set arithmetic over the block the run already produced.
    """

    case: str
    drafts: int
    confirmed: tuple[str, ...]
    rejected: tuple[str, ...]
    destroyed: tuple[str, ...]
    earned: tuple[str, ...]

    @property
    def rejection_rate(self) -> float:
        """Of what the critic was handed, the share it ruled inapplicable."""
        return len(self.rejected) / self.drafts if self.drafts else 0.0

    @property
    def destroyed_rate(self) -> float:
        """Of what it rejected, the share the case expected a ruling on."""
        return len(self.destroyed) / len(self.rejected) if self.rejected else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framework": FRAMEWORK,
            "drafts": self.drafts,
            "confirmed": len(self.confirmed),
            "rejected": len(self.rejected),
            "destroyed": list(self.destroyed),
            "earned": list(self.earned),
            "rejection_rate": round(self.rejection_rate, 3),
            "destroyed_rate": round(self.destroyed_rate, 3),
        }


def score_yield(
    case: GoldenCase, block: FrameworkAnalysis, drafts: Sequence[Claim]
) -> ApplicabilityYield:
    """One case's ASVS critic pass, against what the case expected.

    ``drafts`` is the pre-critic union the critic was handed, which is the
    denominator: the block's own ``claims`` and ``rejected_claims`` are what
    came back, and a draft the fan-in never delivered was never the critic's to
    rule on.
    """
    expected = {
        reference.requirement
        for reference in case.references.get(FRAMEWORK) or ()
        if isinstance(reference, ReferenceRequirement)
    }
    # Read from the list a claim sits in, not from its verdict: the two lists
    # *are* the critic's decision, and re-deriving it from the verdict would be
    # a second definition of the same split.
    confirmed = _requirements(block.claims)
    rejected = _requirements(block.rejected_claims)
    return ApplicabilityYield(
        case=case.id,
        drafts=len(drafts),
        confirmed=tuple(sorted(confirmed)),
        rejected=tuple(sorted(rejected)),
        destroyed=tuple(sorted(rejected & expected)),
        earned=tuple(sorted(rejected - expected)),
    )


def _requirements(claims: Sequence[RuledClaim]) -> set[str]:
    """Every standard identifier in a claim list, parseable ones only."""
    return {
        requirement for claim in claims if (requirement := requirement_of(claim.id))
    }


def aggregate_yield(yields: Sequence[ApplicabilityYield]) -> Mapping[str, Any]:
    """The corpus-wide view, pooled rather than averaged over cases.

    Pooled for the reason :func:`~evals.harness.critic_yield.aggregate_yield`
    gives: these are small per-case counts, and a mean of per-case rates lets a
    case carrying six records outweigh one carrying seventeen.
    """
    drafts = sum(entry.drafts for entry in yields)
    rejected = sum(len(entry.rejected) for entry in yields)
    destroyed = sum(len(entry.destroyed) for entry in yields)
    return {
        "cases": len(yields),
        "drafts": drafts,
        "confirmed": sum(len(entry.confirmed) for entry in yields),
        "rejected": rejected,
        "destroyed": destroyed,
        "earned": sum(len(entry.earned) for entry in yields),
        "rejection_rate": round(rejected / drafts, 3) if drafts else 0.0,
        "destroyed_rate": round(destroyed / rejected, 3) if rejected else 0.0,
    }


def _mean(values: Iterable[float]) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0


def render(scores: Sequence[ApplicabilityScore]) -> None:
    """ASVS's rows, then the pooled figures. Never folded into STRIDE's table.

    The two scorers answer different questions over different sets — an open
    claim set through a composed identity, and a finite catalog by string
    compare — so one combined recall column would be an average of two things
    nobody asked for.
    """
    if not scores:
        return
    print("\nASVS applicability (mechanical, catalog match)")
    print(
        f"{'case':<26} {'lvl':>3} {'rec':>6} {'must':>6} {'prec':>6}"
        f" {'miss':>5} {'defr':>5} {'over':>5} {'rej':>5} {'off':>4}"
    )
    for score in scores:
        print(
            f"{score.case:<26} {score.level:>3} {score.recall:>6.0%}"
            f" {score.must_find_recall:>6.0%} {_rate(score.precision):>6}"
            f" {len(score.missed):>5} {len(score.matched_by_deferral):>5}"
            f" {len(score.over_applied):>5}"
            f" {len(score.rejected):>5} {len(score.off_catalog):>4}"
        )
    totals = pooled(scores)
    print(
        f"pooled over {totals['cases']} cases: recall {totals['recall']:.0%}"
        f" ({totals['matched']}/{totals['expected']}),"
        f" must-find {totals['must_find_recall']:.0%},"
        f" precision {_rate(totals['precision'])}"
        f" (over {totals['precision_cases']} of {totals['cases']} cases read"
        " as exhaustive),"
        f" off-catalog {totals['off_catalog']}"
        " (instrument, non-gating)"
    )
    # Printed as its own line because it answers a different question from the
    # recall figure above it: not *how much was found* but *how much of it the
    # report withholds*, which is a policy `CARRIED_EVIDENCE_KINDS` sets rather
    # than a limit of the analysis.
    if totals["matched_by_deferral"]:
        print(
            f"  of {totals['matched']} matched,"
            f" {totals['matched_by_deferral']} reached the report only as a"
            " scope entry, deferred for want of another kind of evidence"
        )


def artifact(scores: Sequence[ApplicabilityScore]) -> dict[str, Any]:
    """This instrument's artifact keys.

    Its own keys rather than rows in ``scores``: a confusion matrix over a
    finite catalog and a rule-relative recall figure are not the same
    measurement, and one list would invite a reader to average them.

    ``over_applied_for_promotion`` is the corpus feedback loop's half of this
    instrument — requirements a run ruled applicable that the case did not
    expect, for the next reading session to settle. No vote needed, because the set
    arithmetic already separated the package-bug case into ``off_catalog``.
    """
    return {
        "applicability": [score.to_json() for score in scores],
        "applicability_aggregate": pooled(scores) if scores else None,
        "applicability_exemplar_delta": exemplar_delta(scores) if scores else None,
        "over_applied_for_promotion": over_applied_for_promotion(scores),
    }


def disposition_artifact(scores: Sequence[DispositionScore]) -> dict[str, Any]:
    """The routing instrument's own artifact keys, never rows in the matrix's.

    Separate keys for the reason :func:`artifact` gives: applicability and
    evidence routing are two measurements, and one list would invite a reader
    to average them.
    """
    return {
        "disposition": [score.to_json() for score in scores],
        "disposition_aggregate": pooled_dispositions(scores) if scores else None,
    }


def render_yield(yields: Sequence[ApplicabilityYield]) -> None:
    """This framework's critic, both sides, never one.

    ``destroyed`` is the number that can veto the pattern here — the critic
    ruling inapplicable a requirement the case says applies — and it is printed
    beside ``earned`` for the reason :mod:`evals.harness.critic_yield` prints
    its pair: a rejection count alone reads as the critic working or as the
    critic breaking things.
    """
    if not yields:
        return
    print("\nASVS critic yield (mechanical, catalog match)")
    print(
        f"{'case':<26} {'drafts':>7} {'confirmed':>10} {'rejected':>9} {'earned':>7} {'destroyed':>10}"
    )
    for entry in yields:
        print(
            f"{entry.case:<26} {entry.drafts:>7} {len(entry.confirmed):>10}"
            f" {len(entry.rejected):>9} {len(entry.earned):>7}"
            f" {len(entry.destroyed):>10}"
        )
    totals = aggregate_yield(yields)
    print(
        f"pooled: rejected {totals['rejected']}/{totals['drafts']}"
        f" ({totals['rejection_rate']:.0%}),"
        f" earned {totals['earned']}, destroyed {totals['destroyed']}"
        f" ({totals['destroyed_rate']:.0%} of rejections)"
        " (instrument, non-gating)"
    )


def artifact_yield(yields: Sequence[ApplicabilityYield]) -> dict[str, Any]:
    """This instrument's artifact keys.

    Its own keys beside STRIDE's for the reason the scores are: one column
    pooling a rule-relative kill count with set arithmetic would be a rate
    across both.
    """
    return {
        "applicability_yield": [entry.to_json() for entry in yields],
        "applicability_yield_aggregate": aggregate_yield(yields) if yields else None,
    }


def score_case(
    case: GoldenCase, block: FrameworkAnalysis, drafts: Sequence[Any]
) -> dict[str, Any]:
    """Both of this package's per-case rows, keyed by the instrument reading each.

    The keys are instrument names, so the sweep accumulates a row without
    knowing which package produced it and the per-case payload carries it under
    the same name the artifact does.

    Scored only where the case declared the framework: a case this package's
    **Precondition** refuses carries no reference set, and scoring the empty
    block it would still produce reports zero recall for a framework that
    correctly did nothing. The block's presence is that declaration.
    """
    return {
        "applicability": score_applicability(case, block),
        # What the run told the submitter to do next, which the matrix above
        # cannot see: a matched requirement is matched whether the report routed
        # it to the right kind of evidence or to the wrong one (#471).
        "disposition": score_dispositions(case, block),
        # Both sides of this framework's critic, from the block the run already
        # produced: no second scoring pass, because both sides are
        # requirement identifiers.
        "applicability_yield": score_yield(case, block, drafts),
    }
