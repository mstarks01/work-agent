"""Both sides of the critic, scored from one run.

The critic is the most expensive node in the graph, at one strong-tier pass over
every category agent's output, and this module is the evidence that it earns
that cost. It is deliberately two-sided. A critic that kills rejected threats is
earning its cost, and the same critic killing threats that matched a reference
is destroying real findings. Only the pair means anything, because a kill count
alone can be read as either.

Two comparators help in reading the numbers against something. Semgrep's
assistant kills about 20% of findings at 92% to 96% agreement with human triage.
Unfiltered LLM threat enumeration runs at about 86% raw false positives.

Here is how it works. The critic returns exactly the drafts it was given, which
:func:`~analysis_service.critic.assemble_claims` enforces, so a killed draft is
one carrying a ``rejected`` verdict, and the two sides are a superset and its
subset. The whole instrument is therefore a matter of scoring the same case
twice through the shipped scorer, with no second metric implementation and no
special-cased matching, and then reading the killed drafts' pre-critic
dispositions, which is the state the critic faced.

One property holds this together: the scored task is identical on both sides.
Both passes go through :func:`~evals.harness.scorer.score_case`, whose claim is
the threat's ``title`` either way, ruled by the same deterministic matcher
against the same ledger. Identical questions get identical answers by
construction, because the rule is a pure function, so nothing has to be memoized
to guarantee it.

It is credential-free, like the rest of the scorer. It takes plain data, a
:class:`~evals.harness.identity.Matcher` and a
:class:`~evals.harness.ledger.Ledger`.

Nothing here gates. This is an instrument. Any threshold over these numbers
comes from observed spread across the baseline sweeps rather than from a guess
here, and the number that could veto the generator-critic pattern outright is
``matched_killed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from analysis_service.frameworks.stride.record import DraftThreat, StrideCategory
from evals.harness.identity import Matcher
from evals.harness.ledger import Ledger
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore, candidate_claim, ratio, score_case

# What one produced threat turned out to be, in the pass that scored it. The
# first two are hits against the reference set, the last four are the ways of
# missing it; ``needs-info`` is post-critic only, since it is a verdict.
Disposition = Literal[
    "matched-must-find",
    "matched-expected",
    "lane-error",
    "rejected",
    "pooled",
    "open",
    "unvoted",
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
    rejected_before: int
    rejected_after: int
    matched_before: int
    matched_after: int
    must_find_before: int
    must_find_after: int
    must_find_total: int

    # --- the two numbers that matter -------------------------------------

    @property
    def rejected_killed(self) -> int:
        """The critic earning its cost: drafts it removed that a person had
        already rejected for substance."""
        return sum(1 for draft in self.killed if draft.disposition == "rejected")

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

    # --- rates, all rule-and-ledger-relative ------------------------------

    @property
    def kill_count(self) -> int:
        return len(self.killed)

    @property
    def kill_rate(self) -> float:
        """Share of drafts the critic rejected — the Semgrep ~20% comparator."""
        return ratio(self.kill_count, self.drafts_in)

    @property
    def rejected_kill_rate(self) -> float:
        """Of the human-rejected drafts it was handed, the share it caught."""
        return ratio(self.rejected_killed, self.rejected_before)

    @property
    def matched_kill_rate(self) -> float:
        """Of the drafts that had matched a reference, the share it killed."""
        return ratio(self.matched_killed, self.matched_before)

    @property
    def kill_precision(self) -> float:
        """Of what it killed, the share a person had rejected for substance.

        Deliberately *not* the inverse of :attr:`matched_kill_rate`: a killed
        ``pooled`` or ``unvoted`` draft is neither a win nor a loss here,
        because the reference sets are non-exhaustive by construction and the
        corpus feedback loop, not this instrument, is what settles those. Over
        a cold ledger this reads 0.0 for the same reason the scorer's
        ``rejected_rate`` does — the denominator of trust is votes, and there
        are none yet.
        """
        return ratio(self.rejected_killed, self.kill_count)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "counts": {
                "drafts_in": self.drafts_in,
                "threats_out": self.threats_out,
                "killed": self.kill_count,
                "rejected_before": self.rejected_before,
                "rejected_after": self.rejected_after,
                "rejected_killed": self.rejected_killed,
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
                "rejected_kill_rate": round(self.rejected_kill_rate, 3),
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
    matcher: Matcher,
    votes: Ledger,
) -> ScoredCase:
    """Score both sides of the critic through one matcher and one ledger.

    ``score`` is the post-critic score the rest of the harness already
    reports — identical to what :func:`score_case` alone would have returned,
    because the rule is deterministic and both passes read one ledger.
    """
    pre = score_case(case, drafts, matcher, votes)
    post = score_case(case, produced, matcher, votes)
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
        rejected_before=pre.standing_counts["rejected"],
        rejected_after=post.standing_counts["rejected"],
        matched_before=len(pre.matched),
        matched_after=len(post.matched),
        must_find_before=pre.must_find_matched,
        must_find_after=post.must_find_matched,
        must_find_total=pre.must_find_total,
    )


def _dispositions(score: CaseScore) -> dict[str, tuple[Disposition, int | None]]:
    """Every produced threat's fate in one scoring pass, keyed by threat ID.

    Threat IDs are unique within a run — :func:`~analysis_service.critic.
    join_drafts` fails closed if two category agents reuse one — so a dict is a safe
    index. The scorer's outcomes are mutually exclusive by construction: a
    threat is matched, or a lane error, or ``needs-info``, or unlisted with
    exactly one standing. ``unscored`` should be unreachable, and is here so that
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
    for entry in score.unlisted:
        records[entry.threat_id] = (entry.standing, None)
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
    rejected_before = sum(entry.rejected_before for entry in yields)
    rejected_killed = sum(entry.rejected_killed for entry in yields)
    matched_before = sum(entry.matched_before for entry in yields)
    matched_killed = sum(entry.matched_killed for entry in yields)
    must_find_killed = sum(entry.must_find_killed for entry in yields)
    return {
        "cases": len(yields),
        "drafts_in": drafts_in,
        "threats_out": sum(entry.threats_out for entry in yields),
        "killed": killed,
        "rejected_killed": rejected_killed,
        "matched_killed": matched_killed,
        "must_find_killed": must_find_killed,
        "kill_rate": round(ratio(killed, drafts_in), 3),
        "rejected_kill_rate": round(ratio(rejected_killed, rejected_before), 3),
        "matched_kill_rate": round(ratio(matched_killed, matched_before), 3),
        "kill_precision": round(ratio(rejected_killed, killed), 3),
    }


def render(yields: Sequence[CriticYield]) -> None:
    """Both sides of the critic, always printed together.

    ``killed-real`` is deliberately on the same line as ``killed-rejected``:
    a kill count read on its own says nothing about whether the critic removes
    noise or destroys findings.
    """
    for entry in yields:
        print(
            f"{entry.case_id:<26} critic {entry.drafts_in}->{entry.threats_out}"
            f"  killed-rejected {entry.rejected_killed}/{entry.rejected_before}"
            f"  killed-real {entry.matched_killed}/{entry.matched_before}"
            f"  (must-find {entry.must_find_killed})"
        )
    if yields:
        totals = aggregate_yield(yields)
        print(
            f"critic yield: killed {totals['killed']}/{totals['drafts_in']}"
            f" ({totals['kill_rate']:.0%}),"
            f" rejected caught {totals['rejected_kill_rate']:.0%},"
            f" real destroyed {totals['matched_kill_rate']:.0%}"
            " (instrument, non-gating)"
        )


def artifact(yields: Sequence[CriticYield]) -> dict[str, Any]:
    """This instrument's artifact keys."""
    return {
        "critic_yield": [entry.to_json() for entry in yields],
        "critic_yield_aggregate": aggregate_yield(yields) if yields else None,
    }
