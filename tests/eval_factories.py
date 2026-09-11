"""Scripted matchers and threat builders for the offline eval tests.

The whole scorer runs with **zero provider calls** in production too, but the
tests script the matcher anyway: a stand-in that replays recorded labels lets a
test state a matching outcome directly instead of reverse-engineering element
IDs and verbs that produce it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from analysis_service.actions import ActionVerb
from analysis_service.claims import (
    Ground,
    Rating,
    Severity,
    UnknownRef,
    Verdict,
)
from analysis_service.frameworks.stride.record import (
    STRIDE_VERSION,
    DraftThreat,
    StrideCategory,
    Threat,
)
from evals.harness import ledger
from evals.harness.identity import ClaimPair, ClaimRuling
from evals.harness.ledger import Vote
from evals.harness.reference import ReferenceThreat

CATEGORY_LETTERS = {
    "spoofing": "S",
    "tampering": "T",
    "repudiation": "R",
    "information-disclosure": "I",
    "denial-of-service": "D",
    "elevation-of-privilege": "E",
}


class ScriptedMatcher:
    """Answers from recorded data, and counts what it was asked.

    ``matching_pairs`` holds ``(reference_claim, candidate_claim)`` tuples a
    label called equivalent; anything else is a non-match.
    """

    def __init__(self, matching_pairs: Iterable[tuple[str, str]] = ()) -> None:
        self.matching_pairs = set(matching_pairs)
        self.claim_calls: list[ClaimPair] = []

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        self.claim_calls.append(pair)
        match = (pair.reference_claim, pair.candidate_claim) in self.matching_pairs
        return ClaimRuling(match=match, rationale="scripted")


class LabelReplayMatcher:
    """Replays the recorded labels, optionally disagreeing where ``flip`` says."""

    def __init__(
        self,
        labels: dict[tuple[str, str], bool],
        flip: Callable[[ClaimPair], bool] | None = None,
    ) -> None:
        self._labels = labels
        self._flip = flip or (lambda _pair: False)

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        label = self._labels[(pair.reference_claim, pair.candidate_claim)]
        match = not label if self._flip(pair) else label
        return ClaimRuling(match=match, rationale="replayed label")


def draft_threat(
    sequence: int,
    category: StrideCategory,
    title: str,
    *,
    element_ids: Iterable[str] = ("entity:shopper",),
    verb: ActionVerb = "impersonate",
    likelihood: Rating = "high",
    impact: Rating = "high",
) -> DraftThreat:
    """One category agent's draft, as ``merge_drafts`` parks it for the critic.

    No verdict and no confidence: those are the critic's, and critic yield
    exists to measure what the critic did with drafts exactly this shape.

    ``grounds`` is a scripted quote, because nothing on the eval side scores
    grounds and a quote is the one kind no model is consulted for: an
    ``unknown-attribute`` must be one the evidence catalog derives from the
    case's model, and a helper that knows no model cannot write one that is.
    The eval-side reference set carries no grounds at all — a hand-authored
    one would be graded by nothing.
    """
    return DraftThreat(
        id=f"{CATEGORY_LETTERS[category]}-{sequence:02d}",
        framework="stride",
        framework_version=STRIDE_VERSION,
        category=category,
        title=title,
        description=f"{title} Details for the scorer's adjudication step.",
        affected_element_ids=list(element_ids),
        # Overridable, because a scorer test that wants two drafts to be one
        # finding — or two — sets exactly this and the elements beside it.
        verb=verb,
        grounds=[Ground(kind="quote", text="scripted", source_label="scripted")],
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


#: What a test's vote read, where the test is about something else. A real vote
#: carries the two digests of what the reviewer was shown (ADR 0029), and the
#: ledger requires both; a test asking about a reason code or a filename needs
#: *a* value and not a particular one.
SAMPLE_CONTENT = "s1:0000000000000000"
SAMPLE_PROSE = "p1:0000000000000000"


def cast(*args: object, **kwargs: object) -> Vote:
    """:func:`evals.harness.ledger.cast` with the two digests defaulted.

    Shadows the ledger's own name on purpose, so a test that does not care what
    was voted on reads exactly as it did before the fields existed. A test
    *about* the digests passes them itself, or calls the ledger directly.
    """
    kwargs.setdefault("content", SAMPLE_CONTENT)
    kwargs.setdefault("prose", SAMPLE_PROSE)
    return ledger.cast(*args, **kwargs)  # type: ignore[arg-type]


def other_content(marker: str = "1") -> str:
    """A structural digest that is not :data:`SAMPLE_CONTENT`."""
    return f"s1:{marker * 16}"


def other_prose(marker: str = "1") -> str:
    """A prose digest that is not :data:`SAMPLE_PROSE`."""
    return f"p1:{marker * 16}"
