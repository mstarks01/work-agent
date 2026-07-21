"""Scripted judges and threat builders for the offline eval tests.

The whole scorer is exercised with **zero Vertex calls** (ticket 009 decision
17): the judge is a seam, so a stand-in replaying the SME's recorded labels
plays its part exactly. That is what the credential-free PR job runs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from evals.harness.judge import (
    Bucket,
    BucketRuling,
    ClaimPair,
    ClaimRuling,
    UnmatchedThreat,
)
from evals.harness.reference import ReferenceThreat
from stride_service.report import Severity, StrideCategory, Threat, Verdict
from stride_service.system_model import SystemModel

CATEGORY_LETTERS = {
    "spoofing": "S",
    "tampering": "T",
    "repudiation": "R",
    "information-disclosure": "I",
    "denial-of-service": "D",
    "elevation-of-privilege": "E",
}


class ScriptedJudge:
    """Answers from recorded data, and counts what it was asked.

    ``matching_pairs`` holds ``(reference_claim, candidate_claim)`` tuples the
    human called equivalent; anything else is a non-match. ``buckets`` maps a
    threat ID to its adjudicated bucket, defaulting to ``valid-unlisted`` —
    the bucket that is explicitly *not* a failure.
    """

    def __init__(
        self,
        matching_pairs: Iterable[tuple[str, str]] = (),
        buckets: dict[str, Bucket] | None = None,
    ) -> None:
        self.matching_pairs = set(matching_pairs)
        self.buckets = buckets or {}
        self.claim_calls: list[ClaimPair] = []
        self.adjudication_calls: list[UnmatchedThreat] = []

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        self.claim_calls.append(pair)
        match = (pair.reference_claim, pair.candidate_claim) in self.matching_pairs
        return ClaimRuling(match=match, rationale="scripted")

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling:
        self.adjudication_calls.append(threat)
        return BucketRuling(
            bucket=self.buckets.get(threat.threat_id, "valid-unlisted"),
            rationale="scripted",
        )


class LabelReplayJudge:
    """Replays the hand labels, optionally disagreeing on the first ``flip``."""

    def __init__(
        self,
        labels: dict[tuple[str, str], bool],
        flip: Callable[[ClaimPair], bool] | None = None,
    ) -> None:
        self._labels = labels
        self._flip = flip or (lambda _pair: False)

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        human = self._labels[(pair.reference_claim, pair.candidate_claim)]
        match = not human if self._flip(pair) else human
        return ClaimRuling(match=match, rationale="replayed label")

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling:
        raise AssertionError("calibration never adjudicates")


def produced_threat(
    sequence: int,
    category: StrideCategory,
    title: str,
    *,
    element_ids: Iterable[str] = ("entity:shopper",),
    likelihood: str = "high",
    impact: str = "high",
    verdict_status: str = "confirmed",
) -> Threat:
    """One threat as the graph would emit it, titled with its claim."""
    verdict = (
        Verdict(status="confirmed")
        if verdict_status == "confirmed"
        else Verdict(
            status="needs-info",
            reason="authentication on this flow is unknown",
            related_unknowns=[
                {"element_id": next(iter(element_ids)), "attribute": "authentication"}
            ],
        )
    )
    return Threat(
        id=f"{CATEGORY_LETTERS[category]}-{sequence:02d}",
        category=category,
        title=title,
        description=f"{title} Details for the scorer's adjudication step.",
        affected_element_ids=list(element_ids),
        severity=Severity(
            likelihood=likelihood, impact=impact, justification="scripted"
        ),
        confidence="high",
        verdict=verdict,
    )


def threat_for(reference: ReferenceThreat, sequence: int, title: str) -> Threat:
    """A produced threat aimed at one reference, citing the same elements."""
    return produced_threat(
        sequence,
        reference.category,
        title,
        element_ids=reference.affected_element_ids,
        likelihood=reference.severity.likelihood,
        impact=reference.severity.impact,
    )
