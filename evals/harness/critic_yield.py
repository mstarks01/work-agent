"""Both sides of the critic, scored from one run.

The critic is the most expensive node in the graph — one strong-tier pass over
every category agent's output — and this module is the evidence that it earns that
cost. It is deliberately two-sided: a critic that kills unsupported threats is
earning its cost, and the *same* critic killing threats that matched a
reference is destroying real findings. Only the pair means anything. A kill
count alone can be read as either.

Comparators, for reading the numbers against something: Semgrep's assistant
kills ~20% of findings at 92-96% agreement with human triage, and unfiltered
LLM threat enumeration runs ~86% raw false positives.

**How it works.** The critic returns exactly the drafts it was given
(:func:`~stride_service.critic.assemble_threats` enforces it), so a killed
draft is one carrying a ``rejected`` verdict, and the two sides are a superset
and its subset. That makes the whole instrument a matter of scoring the same
case twice through the shipped scorer — no second metric implementation, no
special-cased matching — and reading the killed drafts' *pre-critic*
dispositions, which is the state the critic actually faced.

Two properties hold this together:

* **The judged task is identical on both sides.** Both passes go through
  :func:`~evals.harness.scorer.score_case`, whose claim is the threat's
  ``title`` either way. A different claim string on one side makes the two
  numbers incomparable and the yield meaningless.
* **The pre-critic pass runs first**, sharing a
  :class:`~evals.harness.judge.MemoJudge` with the post-critic pass. The
  subset's questions are then already answered, so the second pass is close to
  free — and identical answers to identical questions are guaranteed rather
  than hoped for.

Credential-free, like the rest of the scorer: it takes plain data and a
:class:`~evals.harness.judge.Judge`.

**Non-gating.** This is an instrument. Any threshold over these numbers comes
from observed spread across the baseline sweeps, not from a guess here — and
the number that could *veto* the generator-critic pattern outright is
``matched_killed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from evals.harness.judge import Judge, MemoJudge
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore, candidate_claim, ratio, score_case
from stride_service.frameworks.stride.record import DraftThreat, StrideCategory

# What one produced threat turned out to be, in the pass that scored it. The
# first two are hits against ground truth, the last four are the ways of
# missing it; ``needs-info`` is post-critic only, since it is a verdict.
Disposition = Literal[
    "matched-must-find",
    "matched-expected",
    "lane-error",
    "unsupported",
    "valid-unlisted",
    "noise",
    "needs-info",
    "unscored",
]


@dataclass(frozen=True)
class KilledDraft:
    """One draft the critic rejected, and what it was before it was rejected.

    The audit trail under the aggregate counts: a ``matched-must-find`` line
    here is a real finding the critic destroyed, named, with the reference it
    was answering — which is what makes the veto number actionable instead of
    merely alarming.
    """

    threat_id: str
    category: StrideCategory
    claim: str
    disposition: Disposition
    reference_index: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "category": self.category,
            "claim": self.claim,
            "disposition": self.disposition,
            "reference_index": self.reference_index,
        }


@dataclass(frozen=True)
class CriticYield:
    """What one case's critic pass cost and bought."""

    case_id: str
    drafts_in: int
    threats_out: int
    killed: tuple[KilledDraft, ...]
    unsupported_before: int
    unsupported_after: int
    matched_before: int
    matched_after: int
    must_find_before: int
    must_find_after: int
    must_find_total: int

    # --- the two numbers that matter -------------------------------------

    @property
    def unsupported_killed(self) -> int:
        """The critic earning its cost: unsupported drafts it removed."""
        return sum(1 for draft in self.killed if draft.disposition == "unsupported")

    @property
    def matched_killed(self) -> int:
        """The critic destroying real findings — the number that can veto the
        pattern. A draft that had matched a reference, killed anyway."""
        return sum(
            1 for draft in self.killed if draft.disposition.startswith("matched")
        )

    @property
    def must_find_killed(self) -> int:
        """The sharpest form of the veto: a killed draft that had answered a
        reference the Tier 2 gate depends on."""
        return sum(
            1 for draft in self.killed if draft.disposition == "matched-must-find"
        )

    # --- rates, all judge-relative ---------------------------------------

    @property
    def kill_count(self) -> int:
        return len(self.killed)

    @property
    def kill_rate(self) -> float:
        """Share of drafts the critic rejected — the Semgrep ~20% comparator."""
        return ratio(self.kill_count, self.drafts_in)

    @property
    def unsupported_kill_rate(self) -> float:
        """Of the unsupported drafts it was handed, the share it caught."""
        return ratio(self.unsupported_killed, self.unsupported_before)

    @property
    def matched_kill_rate(self) -> float:
        """Of the drafts that had matched a reference, the share it killed."""
        return ratio(self.matched_killed, self.matched_before)

    @property
    def kill_precision(self) -> float:
        """Of what it killed, the share that was unsupported.

        Deliberately *not* the inverse of :attr:`matched_kill_rate`: a killed
        ``valid-unlisted`` draft is neither a win nor a loss here, because the
        reference sets are non-exhaustive by construction and the corpus
        feedback loop, not this instrument, is what settles those.
        """
        return ratio(self.unsupported_killed, self.kill_count)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "counts": {
                "drafts_in": self.drafts_in,
                "threats_out": self.threats_out,
                "killed": self.kill_count,
                "unsupported_before": self.unsupported_before,
                "unsupported_after": self.unsupported_after,
                "unsupported_killed": self.unsupported_killed,
                "matched_before": self.matched_before,
                "matched_after": self.matched_after,
                "matched_killed": self.matched_killed,
                "must_find_before": self.must_find_before,
                "must_find_after": self.must_find_after,
                "must_find_total": self.must_find_total,
                "must_find_killed": self.must_find_killed,
            },
            "metrics": {
                "kill_rate": round(self.kill_rate, 3),
                "unsupported_kill_rate": round(self.unsupported_kill_rate, 3),
                "matched_kill_rate": round(self.matched_kill_rate, 3),
                "kill_precision": round(self.kill_precision, 3),
            },
            "killed": [draft.to_json() for draft in self.killed],
        }


@dataclass(frozen=True)
class ScoredCase:
    """One case scored on both sides of the critic."""

    score: CaseScore
    pre_critic: CaseScore
    critic_yield: CriticYield


def score_case_with_yield(
    case: GoldenCase,
    drafts: Sequence[DraftThreat],
    produced: Sequence[DraftThreat],
    judge: Judge,
) -> ScoredCase:
    """Score both sides of the critic, sharing one memo of judge rulings.

    ``score`` is the post-critic score the rest of the harness already
    reports — identical to what :func:`score_case` alone would have returned,
    because the memo only ever replays answers to questions the pre-critic pass
    already asked.

    Order matters for cost, not for correctness: the pre-critic set is the
    superset, so asking it first is what makes the second pass nearly free.
    """
    memo = MemoJudge(judge)
    pre = score_case(case, drafts, memo)
    post = score_case(case, produced, memo)
    return ScoredCase(
        score=post, pre_critic=pre, critic_yield=_yield(pre, post, drafts)
    )


def _yield(
    pre: CaseScore, post: CaseScore, drafts: Sequence[DraftThreat]
) -> CriticYield:
    surviving = set(post.produced_ids)
    dispositions = _dispositions(pre)
    killed = tuple(
        KilledDraft(
            threat_id=draft.id,
            category=draft.category,
            claim=candidate_claim(draft),
            disposition=dispositions[draft.id][0],
            reference_index=dispositions[draft.id][1],
        )
        for draft in drafts
        if draft.id not in surviving
    )
    return CriticYield(
        case_id=pre.case_id,
        drafts_in=pre.produced_count,
        threats_out=post.produced_count,
        killed=killed,
        unsupported_before=pre.bucket_counts["unsupported"],
        unsupported_after=post.bucket_counts["unsupported"],
        matched_before=len(pre.matched),
        matched_after=len(post.matched),
        must_find_before=pre.must_find_matched,
        must_find_after=post.must_find_matched,
        must_find_total=pre.must_find_total,
    )


def _dispositions(score: CaseScore) -> dict[str, tuple[Disposition, int | None]]:
    """Every produced threat's fate in one scoring pass, keyed by threat ID.

    Threat IDs are unique within a run — :func:`~stride_service.critic.
    join_drafts` fails closed if two category agents reuse one — so a dict is a safe
    index. The scorer's outcomes are mutually exclusive by construction: a
    threat is matched, or a lane error, or ``needs-info``, or adjudicated into
    exactly one bucket. ``unscored`` should be unreachable, and is here so that
    a future scorer path which stops covering the produced set surfaces as a
    visible label in the artifact rather than a ``KeyError`` mid-sweep.
    """
    records: dict[str, tuple[Disposition, int | None]] = {}
    for pair in score.matched:
        matched: Disposition = (
            "matched-must-find" if pair.tier == "must-find" else "matched-expected"
        )
        records[pair.threat_id] = (matched, pair.reference_index)
    for error in score.lane_errors:
        records[error.threat_id] = ("lane-error", error.reference_index)
    for entry in score.adjudicated:
        records[entry.threat_id] = (entry.bucket, None)
    for threat_id in score.needs_info_unmatched:
        records[threat_id] = ("needs-info", None)
    for threat_id in score.produced_ids:
        records.setdefault(threat_id, ("unscored", None))
    return records


def aggregate_yield(yields: Sequence[CriticYield]) -> dict[str, Any]:
    """The corpus-wide view, pooled rather than averaged over cases.

    Pooled on purpose: these are small counts per case, and a mean of per-case
    rates would let a case that drafted three threats outweigh one that drafted
    forty.
    """
    drafts_in = sum(entry.drafts_in for entry in yields)
    killed = sum(entry.kill_count for entry in yields)
    unsupported_before = sum(entry.unsupported_before for entry in yields)
    unsupported_killed = sum(entry.unsupported_killed for entry in yields)
    matched_before = sum(entry.matched_before for entry in yields)
    matched_killed = sum(entry.matched_killed for entry in yields)
    must_find_killed = sum(entry.must_find_killed for entry in yields)
    return {
        "cases": len(yields),
        "drafts_in": drafts_in,
        "threats_out": sum(entry.threats_out for entry in yields),
        "killed": killed,
        "unsupported_killed": unsupported_killed,
        "matched_killed": matched_killed,
        "must_find_killed": must_find_killed,
        "kill_rate": round(ratio(killed, drafts_in), 3),
        "unsupported_kill_rate": round(
            ratio(unsupported_killed, unsupported_before), 3
        ),
        "matched_kill_rate": round(ratio(matched_killed, matched_before), 3),
        "kill_precision": round(ratio(unsupported_killed, killed), 3),
    }
