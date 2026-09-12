"""Scripted matchers and threat builders for the offline eval tests.

The whole scorer runs with **zero provider calls** in production too, but the
tests script the matcher anyway: a stand-in that replays recorded labels lets a
test state a matching outcome directly instead of reverse-engineering element
IDs and verbs that produce it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from analysis_service.actions import ActionVerb
from analysis_service.claims import (
    Ground,
    Rating,
    Severity,
    UnknownRef,
    UnreconciledKind,
    UnreconciledRuling,
    Verdict,
)
from analysis_service.frameworks.stride.record import (
    STRIDE_VERSION,
    DraftThreat,
    StrideCategory,
    Threat,
)
from analysis_service.identity import IDENTITY_VERSION, build_identity
from analysis_service.sampling import TierSampling
from evals import verify_corpus
from evals.harness import ledger
from evals.harness.artifact import ARTIFACT_VERSION
from evals.harness.identity import ClaimPair, ClaimRuling
from evals.harness.ledger import Vote
from evals.harness.provenance import RunProvenance
from evals.harness.reference import GoldenCase, ReferenceThreat, load_corpus
from tests.factories import SAMPLE_INSTRUCTIONS, sample_fingerprint

#: The commit and the corpus digest a synthetic sweep names. Neither is real,
#: which is why no test here may reach the repository with them.
SWEEP_COMMIT = "c" * 40
SWEEP_CORPUS = "d" * 64


def sweep_sampling(temperature: float = 0.2) -> dict[str, TierSampling]:
    return {
        "base": TierSampling(temperature=temperature, seed=7),
        "strong": TierSampling(temperature=temperature, seed=7),
    }


def sweep_document(
    *,
    clean: bool = True,
    temperature: float = 0.2,
    strong_model: str = "openai/gpt-5.6",
    served_strong: str = "gpt-5.6-luna",
    frameworks: tuple[str, ...] = ("stride",),
    usage_nodes: tuple[str, ...] = ("extract", "critic"),
    cases: tuple[str, ...] = ("01-a-case",),
    seed: int = 1,
    charges: dict[str, float] | None = None,
    served_upstreams: tuple[str, ...] = (),
) -> dict:
    """One admissible artifact document; ``seed`` varies the bytes only.

    Synthetic but honest -- every fingerprint recomputes -- because
    :func:`~evals.harness.artifact.load_artifact` refuses anything less. Shared
    by the Baseline tests and the review app's, which read the same identity
    out of it through one reader.

    ``charges`` is what the providers said they charged, per node. Empty by
    default, which is what every sweep on a direct vendor records -- those
    report token counts and nothing else.

    ``served_upstreams`` is what a gateway said about who answered the strong
    node, one execution per entry, so two entries are one node served twice
    from two places. Empty by default, which is what a direct vendor records.
    """
    tiers = sweep_sampling(temperature)
    runs = {
        "extract": ("base", "openai/gpt-base", "gpt-base-001"),
        "critic": ("strong", strong_model, served_strong),
    }
    upstreams_of = {"critic": served_upstreams or (None,)}
    node_runs = {
        node: [
            {
                "node": node,
                "tier": tier,
                "requested_model": requested,
                "served_model": served,
                "instruction_sha256": SAMPLE_INSTRUCTIONS,
                "generation_fingerprint": sample_fingerprint(
                    served, tiers[tier], requested=requested
                ),
                "served_upstream": upstream,
            }
            for upstream in upstreams_of.get(node, (None,))
        ]
        for node, (tier, requested, served) in runs.items()
    }
    provenance = RunProvenance.model_validate(
        {
            "identity_version": IDENTITY_VERSION,
            "build": dict(build_identity()),
            "sampling_config_version": 1,
            "tiers_config_version": 1,
            "sampling": tiers,
            "node_runs": node_runs,
        }
    )
    usage = {
        node: {
            "prompt_tokens": 1000,
            "cached_prompt_tokens": 200,
            "completion_tokens": 300,
        }
        for node in usage_nodes
    }
    return {
        "artifact_version": ARTIFACT_VERSION,
        "mode": "end-to-end",
        "cases": list(cases),
        "trusted": False,
        "structural_failures": [],
        "repo_commit": {"commit": SWEEP_COMMIT, "clean": clean},
        "corpus_digest": SWEEP_CORPUS,
        "frameworks": list(frameworks),
        "certification": {"verdict": "uncertified", "seed": seed},
        "node_usage": usage,
        "node_charges": dict(charges or {}),
        "provenance": provenance.to_json(),
    }


def write_sweep_document(path: Path, document: dict | None = None) -> Path:
    """Lay one artifact document down at ``path`` and hand the path back."""
    path.write_text(
        json.dumps(document or sweep_document(), indent=2), encoding="utf-8"
    )
    return path


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


def corpus_case(case_id: str) -> GoldenCase:
    """One corpus case by ID, loaded through the shipped corpus verifier."""
    return next(
        entry for entry in load_corpus(verify_corpus.CORPUS_DIR) if entry.id == case_id
    )


def unreconciled(
    claim_id: str, kind: UnreconciledKind = "dropped"
) -> UnreconciledRuling:
    """A first-pass mark against one claim, with the message every test uses."""
    return UnreconciledRuling.of(
        claim_id=claim_id, kind=kind, message="the first pass got this wrong"
    )


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
