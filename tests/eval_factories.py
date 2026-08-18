"""Scripted judges and threat builders for the offline eval tests.

The whole scorer is exercised with **zero provider calls**: the judge is a
seam, so a stand-in replaying the corpus's recorded labels plays its part exactly.
That is what the credential-free PR job runs.
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
from stride_service.frameworks.stride.record import (
    STRIDE_VERSION,
    DraftThreat,
    StrideCategory,
    Threat,
)
from stride_service.report import (
    Ground,
    Rating,
    Severity,
    UnknownRef,
    Verdict,
)
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
    """Replays the recorded labels, optionally disagreeing on the first ``flip``."""

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


def draft_threat(
    sequence: int,
    category: StrideCategory,
    title: str,
    *,
    element_ids: Iterable[str] = ("entity:shopper",),
    likelihood: Rating = "high",
    impact: Rating = "high",
) -> DraftThreat:
    """One category agent's draft, as ``merge_drafts`` parks it for the critic.

    No verdict and no confidence: those are the critic's, and critic yield
    exists to measure what the critic did with drafts exactly this shape.

    ``grounds`` is an ``unknown-attribute`` on the first cited element rather
    than a quote, because nothing on the eval side scores grounds and a quote
    would need a source to be verifiable against. The eval-side reference set
    carries no grounds at all — a hand-authored one would be graded by nothing.
    """
    return DraftThreat(
        id=f"{CATEGORY_LETTERS[category]}-{sequence:02d}",
        framework="stride",
        framework_version=STRIDE_VERSION,
        category=category,
        title=title,
        description=f"{title} Details for the scorer's adjudication step.",
        affected_element_ids=list(element_ids),
        grounds=[
            Ground(
                kind="unknown-attribute",
                element_id=next(iter(element_ids)),
                attribute="authentication",
            )
        ],
        severity=Severity(
            likelihood=likelihood, impact=impact, justification="scripted"
        ),
    )


def produced_threat(
    sequence: int,
    category: StrideCategory,
    title: str,
    *,
    element_ids: Iterable[str] = ("entity:shopper",),
    likelihood: Rating = "high",
    impact: Rating = "high",
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
                UnknownRef(
                    element_id=next(iter(element_ids)), attribute="authentication"
                )
            ],
        )
    )
    draft = draft_threat(
        sequence,
        category,
        title,
        element_ids=element_ids,
        likelihood=likelihood,
        impact=impact,
    )
    return promote(draft, verdict=verdict)


def promote(draft: DraftThreat, *, verdict: Verdict | None = None) -> Threat:
    """The draft as the critic would return it: same claim, plus its rulings."""
    return Threat(
        **draft.model_dump(),
        confidence="high",
        verdict=verdict or Verdict(status="confirmed"),
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
