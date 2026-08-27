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
:func:`~stride_service.frameworks.asvs.catalog.requirements_for` and nothing
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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evals.harness.reference import CaseFramework, GoldenCase, ReferenceRequirement
from stride_service.frameworks.asvs.catalog import requirements_for
from stride_service.frameworks.asvs.record import requirement_of
from stride_service.report import (
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    RuledClaim,
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
    universe: int
    expected: tuple[str, ...]
    must_find: tuple[str, ...]
    matched: tuple[str, ...]
    missed: tuple[str, ...]
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
    def precision(self) -> float:
        """Of what the run said applies, how much the case expected.

        ``off_catalog`` counts against this: a claim naming a requirement the
        level does not carry is still a claim the reader has to read.
        """
        applied = len(self.matched) + len(self.over_applied) + len(self.off_catalog)
        return len(self.matched) / applied if applied else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framework": FRAMEWORK,
            "level": self.level,
            "exemplar_proximity": self.exemplar_proximity,
            "universe": self.universe,
            "expected": len(self.expected),
            "must_find": len(self.must_find),
            "matched": list(self.matched),
            "missed": list(self.missed),
            "must_find_missed": list(self.must_find_missed),
            "over_applied": list(self.over_applied),
            "rejected": list(self.rejected),
            "off_catalog": list(self.off_catalog),
            "excluded": self.excluded,
            "recall": round(self.recall, 4),
            "must_find_recall": round(self.must_find_recall, 4),
            "precision": round(self.precision, 4),
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

    applied, rejected = applied_requirements(block.claims)
    off_catalog = applied - universe
    in_universe = applied & universe

    matched = expected & in_universe
    missed = expected - in_universe
    over_applied = in_universe - expected

    return ApplicabilityScore(
        case=case.id,
        level=level,
        exemplar_proximity=declared(case).exemplar_proximity,
        universe=len(universe),
        expected=tuple(sorted(expected)),
        must_find=tuple(sorted(must_find)),
        matched=tuple(sorted(matched)),
        missed=tuple(sorted(missed)),
        must_find_missed=tuple(sorted(must_find - in_universe)),
        over_applied=tuple(sorted(over_applied)),
        rejected=tuple(sorted(rejected)),
        off_catalog=tuple(sorted(off_catalog)),
        # The fourth cell: in the universe, not expected, and not applied.
        excluded=len(universe - expected - in_universe),
    )


def published(blocks: Mapping[str, Any], metric: str) -> float | None:
    """One pooled ASVS number, for the comparison table."""
    aggregate = blocks.get("applicability_aggregate")
    if not isinstance(aggregate, Mapping):
        return None
    value = aggregate.get(metric)
    return float(value) if isinstance(value, int | float) else None


def pooled(scores: Sequence[ApplicabilityScore]) -> Mapping[str, Any]:
    """The corpus-wide figures, pooled over claims rather than averaged per case.

    A per-case mean would weight a case carrying 6 records the same as one
    carrying 17, which is the arithmetic that makes a small case's miss vanish.
    """
    expected = sum(len(score.expected) for score in scores)
    matched = sum(len(score.matched) for score in scores)
    must_find = sum(len(score.must_find) for score in scores)
    must_find_missed = sum(len(score.must_find_missed) for score in scores)
    applied = matched + sum(
        len(score.over_applied) + len(score.off_catalog) for score in scores
    )
    return {
        "cases": len(scores),
        "expected": expected,
        "matched": matched,
        "recall": round(matched / expected, 4) if expected else 0.0,
        "must_find": must_find,
        "must_find_recall": (
            round((must_find - must_find_missed) / must_find, 4) if must_find else 0.0
        ),
        "precision": round(matched / applied, 4) if applied else 0.0,
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
        f" {'miss':>5} {'over':>5} {'rej':>5} {'off':>4}"
    )
    for score in scores:
        print(
            f"{score.case:<26} {score.level:>3} {score.recall:>6.0%}"
            f" {score.must_find_recall:>6.0%} {score.precision:>6.0%}"
            f" {len(score.missed):>5} {len(score.over_applied):>5}"
            f" {len(score.rejected):>5} {len(score.off_catalog):>4}"
        )
    totals = pooled(scores)
    print(
        f"pooled over {totals['cases']} cases: recall {totals['recall']:.0%}"
        f" ({totals['matched']}/{totals['expected']}),"
        f" must-find {totals['must_find_recall']:.0%},"
        f" precision {totals['precision']:.0%},"
        f" off-catalog {totals['off_catalog']}"
        " (instrument, non-gating)"
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
        # Both sides of this framework's critic, from the block the run already
        # produced: no second scoring pass, because both sides are
        # requirement identifiers.
        "applicability_yield": score_yield(case, block, drafts),
    }
