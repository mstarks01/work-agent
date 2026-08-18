"""Candidate-trigger recall: did deterministic analysis see the lead at all?

A separate number from finding recall, and it answers a narrower question. For
each reference claim the corpus says a working tool must report, this asks
whether :mod:`stride_service.candidates` fired a rule *in that claim's lane* on
*at least one of the elements it is about*. It says nothing about whether the
agent then found the claim — that is finding recall, and it is scored elsewhere
over produced reports.

**Per framework, over each package's own rules and its own reference set.** A
lane is a package's vocabulary and a rule belongs to whichever package declares
it, so one pooled figure would divide one package's firings by another's
references. ASVS's 17 rules matter here more than STRIDE's 11, not less: #160
measured that its predicates are *presence tests*, which is exactly the kind of
rule that quietly stops firing when extraction changes what it records.

**A claim naming no element is not scoreable and is excluded by name.** Most
ASVS requirements address a coding practice with no position in the graph, so
:class:`~evals.harness.reference.ReferenceRequirement` keeps the neutral empty
default — and "fired on an element it is about" has no answer for one. Counting
those as misses would report the rules failing at a question nobody asked them;
counting them as hits would inflate the rate. They are reported as their own
count, so the exclusion cannot silently shrink a denominator.

**Full trigger recall is not the target, and chasing it would be a mistake.**
The candidate layer exists to make structural enumeration cheap, not to encode
threat scenarios; a threat that turns on what a submitter *said* rather than on
what the model's shape *is* has no structural trigger by construction, and
adding one would mean writing a rule that pretends to a judgement it cannot
make. A miss here is a fact about which threats are structural, and the agents
remain responsible for the rest.

Nothing here needs a provider. It runs over the corpus's blessed models alone,
which is why it can gate on every PR while the LLM metrics cannot.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from evals.harness.reference import GoldenCase, ReferenceClaim
from stride_service.candidates import generate_candidates
from stride_service.frameworks import package_for
from stride_service.report import FrameworkName
from stride_service.system_model import SystemModel

__all__ = [
    "CaseTriggerRecall",
    "TriggerHit",
    "by_framework",
    "case_trigger_recall",
    "corpus_recall",
]


@dataclass(frozen=True)
class TriggerHit:
    """One reference threat, and the rules that fired on its elements.

    ``rule_ids`` empty means no structural lead existed for this threat — the
    honest and expected outcome for a threat grounded in what someone said.
    """

    claim: str
    framework: FrameworkName
    lane: str
    tier: str
    element_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]

    @property
    def scoreable(self) -> bool:
        """Whether the question has an answer for this claim at all."""
        return bool(self.element_ids)

    @property
    def triggered(self) -> bool:
        return bool(self.rule_ids)


@dataclass(frozen=True)
class CaseTriggerRecall:
    """One case's hits, with the two counts a threshold would read."""

    case_id: str
    framework: FrameworkName
    hits: tuple[TriggerHit, ...]

    @property
    def scoreable(self) -> tuple[TriggerHit, ...]:
        return tuple(hit for hit in self.hits if hit.scoreable)

    @property
    def unscoreable(self) -> int:
        """Claims naming no element, excluded from both counts below."""
        return len(self.hits) - len(self.scoreable)

    @property
    def triggered(self) -> int:
        return sum(1 for hit in self.scoreable if hit.triggered)

    @property
    def total(self) -> int:
        return len(self.scoreable)

    @property
    def must_find_triggered(self) -> int:
        return sum(
            1 for hit in self.scoreable if hit.triggered and hit.tier == "must-find"
        )

    @property
    def must_find_total(self) -> int:
        return sum(1 for hit in self.scoreable if hit.tier == "must-find")


def case_trigger_recall(
    case: GoldenCase, framework: FrameworkName
) -> CaseTriggerRecall:
    """Score one case's reference set for one framework against its fired rules."""
    return CaseTriggerRecall(
        case_id=case.id,
        framework=framework,
        hits=tuple(
            _hit(framework, reference, case.model)
            for reference in case.references.get(framework) or ()
        ),
    )


def corpus_recall(cases: Iterable[GoldenCase]) -> tuple[CaseTriggerRecall, ...]:
    """Every case scored for every framework it declares, in corpus order.

    Driven by the declaration rather than by the registry: a case a package's
    **Precondition** refuses carries no reference set for it, and scoring the
    empty one would report zero trigger recall for a package that correctly did
    nothing.
    """
    return tuple(
        case_trigger_recall(case, declared.name)
        for case in cases
        for declared in case.meta.frameworks
    )


def _hit(
    framework: FrameworkName, reference: ReferenceClaim, model: SystemModel
) -> TriggerHit:
    """The rules that fired in this claim's lane on any element it names.

    Matching is *lane plus element overlap*, deliberately loose. A candidate
    naming the flow a claim is about has surfaced the lead even when the claim's
    own element list is wider, and a stricter subset rule would score the
    reference set's editorial choices rather than the rules' reach.

    The lane comes off the record's own :attr:`~evals.harness.reference.
    ReferenceClaim.lane`, so nothing here spells ``category`` or ``chapter``.
    """
    # This package's own rules against this package's own reference set: a rule
    # belongs to whichever package declares it, and a lane name is only
    # meaningful inside that package.
    package = package_for(framework)
    fired = generate_candidates(model, package.lanes, package.rules).get(reference.lane)
    targets = set(reference.affected_element_ids)
    rule_ids: tuple[str, ...] = ()
    if fired is not None:
        rule_ids = tuple(
            sorted(
                {
                    candidate.rule_id
                    for candidate in fired.candidates
                    if targets & set(candidate.element_ids)
                }
            )
        )
    return TriggerHit(
        claim=reference.claim,
        framework=framework,
        lane=reference.lane,
        tier=reference.tier,
        element_ids=reference.affected_element_ids,
        rule_ids=rule_ids,
    )


def summarize(results: Sequence[CaseTriggerRecall]) -> dict[str, float | int]:
    """Totals over the results given, for a report line or a CI threshold.

    **Filter to one framework before calling this.** Pooling two packages would
    divide one's firings by the other's references; :func:`by_framework` is the
    shape a caller wants.
    """
    triggered = sum(result.triggered for result in results)
    total = sum(result.total for result in results)
    must_triggered = sum(result.must_find_triggered for result in results)
    must_total = sum(result.must_find_total for result in results)
    return {
        "cases": len(results),
        "references": total,
        "unscoreable": sum(result.unscoreable for result in results),
        "triggered": triggered,
        "recall": round(triggered / total, 4) if total else 0.0,
        "must_find_references": must_total,
        "must_find_triggered": must_triggered,
        "must_find_recall": round(must_triggered / must_total, 4)
        if must_total
        else 0.0,
    }


def by_framework(
    results: Sequence[CaseTriggerRecall],
) -> dict[FrameworkName, dict[str, float | int]]:
    """One :func:`summarize` per framework the results carry."""
    frameworks = sorted({result.framework for result in results})
    return {
        framework: summarize(
            [result for result in results if result.framework == framework]
        )
        for framework in frameworks
    }
