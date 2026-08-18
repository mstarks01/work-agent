"""Candidate-trigger recall: did deterministic analysis see the lead at all?

A separate number from finding recall, and it answers a narrower question. For
each reference threat the corpus says a working tool must report, this asks whether
:mod:`stride_service.candidates` fired a rule *in that threat's category* on
*at least one of the elements it is about*. It says nothing about whether the
agent then found the threat — that is finding recall, and it is scored
elsewhere over produced reports.

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

from evals.harness.reference import GoldenCase, ReferenceThreat
from stride_service.candidates import generate_candidates
from stride_service.frameworks import package_for
from stride_service.system_model import SystemModel

__all__ = ["CaseTriggerRecall", "TriggerHit", "case_trigger_recall", "corpus_recall"]


@dataclass(frozen=True)
class TriggerHit:
    """One reference threat, and the rules that fired on its elements.

    ``rule_ids`` empty means no structural lead existed for this threat — the
    honest and expected outcome for a threat grounded in what someone said.
    """

    claim: str
    category: str
    tier: str
    element_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]

    @property
    def triggered(self) -> bool:
        return bool(self.rule_ids)


@dataclass(frozen=True)
class CaseTriggerRecall:
    """One case's hits, with the two counts a threshold would read."""

    case_id: str
    hits: tuple[TriggerHit, ...]

    @property
    def triggered(self) -> int:
        return sum(1 for hit in self.hits if hit.triggered)

    @property
    def total(self) -> int:
        return len(self.hits)

    @property
    def must_find_triggered(self) -> int:
        return sum(1 for hit in self.hits if hit.triggered and hit.tier == "must-find")

    @property
    def must_find_total(self) -> int:
        return sum(1 for hit in self.hits if hit.tier == "must-find")


def case_trigger_recall(case: GoldenCase) -> CaseTriggerRecall:
    """Score one golden case's reference threats against the fired rules."""
    return CaseTriggerRecall(
        case_id=case.id,
        hits=tuple(_hit(reference, case.model) for reference in case.stride_claims()),
    )


def corpus_recall(cases: Iterable[GoldenCase]) -> tuple[CaseTriggerRecall, ...]:
    """Every case scored, in corpus order."""
    return tuple(case_trigger_recall(case) for case in cases)


def _hit(reference: ReferenceThreat, model: SystemModel) -> TriggerHit:
    """The rules that fired in this threat's lane on any element it names.

    Matching is *lane plus element overlap*, deliberately loose. A candidate
    naming the flow a threat is about has surfaced the lead even when the
    threat's own element list is wider, and a stricter subset rule would score
    the reference set's editorial choices rather than the rules' reach.
    """
    # STRIDE's own rules: trigger recall measures this package's deterministic
    # reach against this package's reference set, and a lane name is only
    # meaningful inside the package that declares it.
    stride = package_for("stride")
    fired = generate_candidates(model, stride.lanes, stride.rules).get(
        reference.category
    )
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
        category=reference.category,
        tier=reference.tier,
        element_ids=reference.affected_element_ids,
        rule_ids=rule_ids,
    )


def summarize(results: Sequence[CaseTriggerRecall]) -> dict[str, float | int]:
    """Corpus totals, for a report line or a CI threshold."""
    triggered = sum(result.triggered for result in results)
    total = sum(result.total for result in results)
    must_triggered = sum(result.must_find_triggered for result in results)
    must_total = sum(result.must_find_total for result in results)
    return {
        "cases": len(results),
        "references": total,
        "triggered": triggered,
        "recall": round(triggered / total, 4) if total else 0.0,
        "must_find_references": must_total,
        "must_find_triggered": must_triggered,
        "must_find_recall": round(must_triggered / must_total, 4)
        if must_total
        else 0.0,
    }
