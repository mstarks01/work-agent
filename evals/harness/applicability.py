"""ASVS's scorer: an applicability confusion matrix, with no judge anywhere.

STRIDE's claim set is open, so grading it needs a model to decide when two
sentences are one claim. **ASVS's is closed**, and that one difference removes
the judge entirely: the catalog is finite, a claim carries the standard's own
identifier, and two claims about one requirement compare by string. #167 settled
that, and this module is what it looks like.

So the shape of the answer differs too. STRIDE's scorer reports recall against an
open list and adjudicates whatever else a run produced, because "the corpus did
not list it" cannot mean "it is wrong". Here it can: a run at level ``L`` rules on
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
from stride_service.report import FrameworkAnalysis, FrameworkName, RuledClaim

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
    ``near_recall`` column pooling both would be an average of a judge-relative
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

    **Cheaper than STRIDE's, and worth saying why.** STRIDE needs a judge to
    separate a grounded unlisted threat from noise. Here the list falls out of
    set arithmetic, and ``off_catalog`` has already taken out the case that is a
    package bug rather than a judgement — so this costs nothing and carries no
    model's opinion.
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


def _mean(values: Iterable[float]) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0
