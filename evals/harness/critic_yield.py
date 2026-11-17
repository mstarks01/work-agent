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
scored outcomes, which is the state the critic faced.

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

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args

from analysis_service.claims import FrameworkAnalysis, VerdictStatus
from analysis_service.frameworks.stride.record import DraftThreat, StrideCategory
from evals.harness.identity import Matcher
from evals.harness.ledger import Ledger
from evals.harness.reference import GoldenCase
from evals.harness.scorer import (
    CaseScore,
    Rate,
    VoteStatus,
    candidate_claim,
    percent,
    score_case,
)

# What one produced threat turned out to be, in the pass that scored it. The
# first two are hits against the reference set, and the rest are the ways of
# missing it.
#
# **Composed from ``VoteStatus`` rather than restating it.** The ways an unmatched
# finding can stand are the scorer's vocabulary, and a second copy here would
# disagree with it the first time one was added. So the four names below are
# the ones this module owns, and every standing arrives with the scorer's own
# type.
ScoredOutcome = (
    Literal["matched-must-find", "matched-expected", "lane-error", "unscored"]
    | VoteStatus
)

#: What the critic's pass did with one draft, read off the block it returned.
#: ``needs-info`` is a qualification: it is neither a confirmation nor a
#: rejection. ``omitted`` is a draft the block carries in none of its three
#: lists.
CriticFate = VerdictStatus | Literal["returned", "omitted"]
OMITTED: CriticFate = "omitted"
FATES: tuple[CriticFate, ...] = tuple(
    value for part in get_args(CriticFate) for value in get_args(part)
)

#: Which input population a draft joins.
Population = Literal["real", "not-real", "unlabelled"]

#: The population each scored outcome puts a draft in.
#:
#: ``real`` rests on a reference the draft matched, or on a current vote that
#: pools it: both are relative to the reference set and the ledger, not proof
#: that the draft is true. ``not-real`` rests on a current substance
#: down-vote. Everything else is unlabelled: ``open`` is a reviewer who was
#: unsure, ``stale`` a vote on another argument (ADR 0029), ``unvoted`` no
#: vote, ``lane-error`` a reference in another lane, and ``unscored`` a draft
#: the scorer did not key, such as one that names an element the corpus model
#: does not hold.
LABEL_OF: Mapping[ScoredOutcome, Population] = MappingProxyType(
    {
        "matched-must-find": "real",
        "matched-expected": "real",
        "pooled": "real",
        "rejected": "not-real",
        "open": "unlabelled",
        "stale": "unlabelled",
        "unvoted": "unlabelled",
        "lane-error": "unlabelled",
        "unscored": "unlabelled",
    }
)

#: The names of the rates :attr:`CriticPopulations.rates` gives.
PopulationRate = Literal[
    "real_rejected", "real_needs_info", "not_real_confirmed", "not_real_needs_info"
]

#: The names of the rates :attr:`CriticYield.rates` gives.
YieldRate = Literal[
    "kill_rate", "rejected_kill_rate", "matched_kill_rate", "kill_precision"
]

#: Why the not-real population is empty: some draft reads ``stale``, or none
#: does. :data:`GAP_WORDS` says what each one means.
NotRealGap = Literal["stale-drafts", "no-down-vote"]

#: What a report prints for each :data:`NotRealGap`.
GAP_WORDS: Mapping[NotRealGap, str] = MappingProxyType(
    {
        "stale-drafts": "no draft carries a current down-vote, and some carry"
        " only a vote on another argument: a vote judges a reported claim with"
        " its verdict (ADR 0029), and a draft has no verdict",
        "no-down-vote": "no draft carries a current down-vote",
    }
)


@dataclass(frozen=True)
class CriticPopulations:
    """The drafts the critic was handed, by scored outcome and by fate.

    Each rate reads only its own population. ``real_rejected`` divides by the
    real drafts, ``not_real_confirmed`` by the not-real ones, and a
    ``needs-info`` fate counts in neither: it has a rate of its own.
    An empty population gives a :class:`~evals.harness.scorer.Rate` whose value
    is ``None``, which is a gap in the labels and not a zero.
    """

    counts: Mapping[tuple[ScoredOutcome, CriticFate], int]

    @classmethod
    def pooled(cls, yields: Sequence[CriticYield]) -> CriticPopulations:
        """The populations of several cases, summed."""
        total: Counter[tuple[ScoredOutcome, CriticFate]] = Counter()
        for entry in yields:
            total.update(entry.populations.counts)
        return cls(dict(total))

    def count(self, label: Population, among: Sequence[CriticFate] = FATES) -> int:
        """The drafts of one population, met by any fate in ``among``."""
        return sum(
            n
            for (outcome, fate), n in self.counts.items()
            if LABEL_OF[outcome] == label and fate in among
        )

    def rate(self, label: Population, fate: CriticFate) -> Rate:
        """The share of one population that met one fate."""
        return Rate(self.count(label, (fate,)), self.count(label))

    @property
    def rates(self) -> dict[PopulationRate, Rate]:
        """The four rates a report prints, keyed by name."""
        return {
            "real_rejected": self.rate("real", "rejected"),
            "real_needs_info": self.rate("real", "needs-info"),
            "not_real_confirmed": self.rate("not-real", "confirmed"),
            "not_real_needs_info": self.rate("not-real", "needs-info"),
        }

    @property
    def stale_drafts(self) -> int:
        """The drafts whose votes judged a different argument (ADR 0029)."""
        return sum(n for (outcome, _), n in self.counts.items() if outcome == "stale")

    @property
    def not_real_gap(self) -> NotRealGap | None:
        """Why the not-real population is empty, or ``None`` where it is not."""
        if self.count("not-real"):
            return None
        return "stale-drafts" if self.stale_drafts else "no-down-vote"

    def to_json(self) -> dict[str, Any]:
        return {
            "counts": [
                {"outcome": outcome, "fate": fate, "drafts": n}
                for (outcome, fate), n in sorted(self.counts.items())
            ],
            **{name: rate.to_json() for name, rate in self.rates.items()},
            "not_real_gap": self.not_real_gap,
            "stale_drafts": self.stale_drafts,
        }


@dataclass(frozen=True)
class CriticAudit:
    """The critic's two sides over a sweep: its input populations and its loss.

    ``populations`` counts drafts. ``reference_loss`` counts references: its
    denominator is the gross count of reference-matched drafts the critic
    killed, and its numerator the kills whose reference no reported claim
    matched. Another retained draft can still carry a killed draft's
    reference, so the two counts differ.

    Neither half measures quote membership, ruling validity or agreement
    between critic runs. Both are relative to the reference set and the
    ledger.
    """

    populations: CriticPopulations
    reference_loss: Rate

    @classmethod
    def of(cls, yields: Sequence[CriticYield]) -> CriticAudit:
        """The audit of several cases' yields, pooled."""
        return cls(
            populations=CriticPopulations.pooled(yields),
            reference_loss=Rate(
                sum(entry.matched_lost for entry in yields),
                sum(entry.matched_killed for entry in yields),
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "populations": self.populations.to_json(),
            "reference_loss": self.reference_loss.to_json(),
        }

    def render(self) -> None:
        """The population table, its four rates, its gap and the loss line."""
        populations = self.populations
        print(
            "critic input populations, in drafts (labels are relative to the"
            " reference set and the ledger). Rows are scored outcomes, where"
            " 'rejected' is a person's down-vote; columns are the critic's"
            " fates, where 'rejected' is the critic's verdict:"
        )
        print(
            f"  {'scored outcome':<18} {'label':<12}"
            + "".join(f"{fate:>11}" for fate in FATES)
        )
        for outcome in sorted({outcome for outcome, _ in populations.counts}):
            cells = "".join(
                f"{populations.counts.get((outcome, fate), 0):>11}" for fate in FATES
            )
            print(f"  {outcome:<18} {LABEL_OF[outcome]:<12}{cells}")
        for name, rate in populations.rates.items():
            print(f"  {name:<24} {rate.shown()}")
        gap = populations.not_real_gap
        if gap is not None:
            print(
                f"  not-real population empty ({gap}, stale drafts:"
                f" {populations.stale_drafts}): {GAP_WORDS[gap]}"
            )
        loss = self.reference_loss
        print(
            f"  gross: {loss.denominator} reference-matched drafts killed;"
            f" net: {loss.numerator} of their references no reported claim took"
        )


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
    outcome: ScoredOutcome
    reference_index: int | None
    #: The step the critic said it rejected the draft at, off the block's own
    #: verdict: ``lane``, ``reasoning``, ``duplicate``, ``evidence`` and the
    #: rest of :data:`~analysis_service.claims.RejectionStep`. ``None`` where
    #: the block carries no verdict for the draft, which is a kill the record
    #: cannot explain and is counted under its own key rather than folded into
    #: a step the critic never named.
    cause: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "category": self.category,
            "claim": self.claim,
            "disposition": self.outcome,
            "reference_index": self.reference_index,
            "cause": self.cause,
        }


@dataclass(frozen=True)
class CriticYield:
    """What one case's critic pass removed, and what it gained."""

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
    populations: CriticPopulations
    #: The references the post-critic score matched, so a kill can be read as
    #: gross (the draft that had matched died) or net (nothing took its place).
    matched_after_indices: frozenset[int] = frozenset()

    # --- the two numbers that matter -------------------------------------

    @property
    def rejected_killed(self) -> int:
        """The critic earning its cost: drafts it removed that a person had
        already rejected for substance."""
        return sum(1 for draft in self.killed if draft.outcome == "rejected")

    @property
    def matched_killed(self) -> int:
        """The critic destroying real findings — the number that can veto the
        pattern. A draft that had matched a reference, killed anyway."""
        return sum(1 for draft in self.killed if draft.outcome.startswith("matched"))

    @property
    def matched_lost(self) -> int:
        """Of :attr:`matched_killed`, the kills whose reference no surviving
        claim took: the net destruction. A killed draft's reference can be
        matched again by another draft after the critic, so the gross count
        overstates the loss: such a kill reads as a destroyed finding while the
        report still carries it. ``matched_killed`` stays the gross count."""
        return sum(
            1
            for draft in self.killed
            if draft.outcome.startswith("matched")
            and draft.reference_index not in self.matched_after_indices
        )

    @property
    def must_find_lost(self) -> int:
        """The net form of :attr:`must_find_killed`."""
        return sum(
            1
            for draft in self.killed
            if draft.outcome == "matched-must-find"
            and draft.reference_index not in self.matched_after_indices
        )

    @property
    def must_find_killed(self) -> int:
        """The sharpest form of the veto: a killed draft that had answered a
        reference the Tier 2 gate depends on."""
        return sum(1 for draft in self.killed if draft.outcome == "matched-must-find")

    # --- rates, all rule-and-ledger-relative ------------------------------

    @property
    def killed_by_cause(self) -> dict[str, dict[str, int]]:
        """Every kill by the step the critic named, and what each step removed.

        The critic states why it rejected a draft, and nothing counted it: the
        second Baseline's four must-find kills were three ``reasoning`` and one
        ``lane``, read by hand. Per step, the kills, the ones that had matched a
        reference, and the ones that had answered a must-find, so a prompt
        change that moves the critic's reasoning kills is visible as that and
        not as a bare count. An unexplained kill is keyed ``unstated``.
        """
        by_cause: dict[str, dict[str, int]] = {}
        for draft in self.killed:
            entry = by_cause.setdefault(
                draft.cause or UNSTATED, {"killed": 0, "matched": 0, "must_find": 0}
            )
            entry["killed"] += 1
            entry["matched"] += draft.outcome.startswith("matched")
            entry["must_find"] += draft.outcome == "matched-must-find"
        return dict(sorted(by_cause.items()))

    @property
    def kill_count(self) -> int:
        return len(self.killed)

    @property
    def rates(self) -> dict[YieldRate, Rate]:
        """Each rate with its counts, keyed by the rate's name.

        ``kill_rate`` is the share of drafts the critic rejected, the Semgrep
        ~20% comparator. ``rejected_kill_rate`` is the share of the drafts a
        person rejected that the critic killed, and ``matched_kill_rate`` the
        share of reference-matched drafts it killed. ``kill_precision`` is the
        share of the kills that a person rejected for substance. It is not the
        inverse of ``matched_kill_rate``: a killed ``pooled`` or ``unvoted``
        draft is neither a win nor a loss here, because the reference sets are
        non-exhaustive by construction. A rate over an empty population has no
        value.
        """
        return _critic_rates(
            drafts_in=self.drafts_in,
            killed=self.kill_count,
            rejected_before=self.rejected_before,
            rejected_killed=self.rejected_killed,
            matched_before=self.matched_before,
            matched_killed=self.matched_killed,
        )

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
                "matched_lost": self.matched_lost,
                "must_find_lost": self.must_find_lost,
            },
            "metrics": {name: rate.rounded() for name, rate in self.rates.items()},
            "populations": self.populations.to_json(),
            "killed_by_cause": self.killed_by_cause,
            "killed": [draft.to_json() for draft in self.killed],
        }


@dataclass(frozen=True)
class ScoredCase:
    """One case scored on both sides of the critic."""

    score: CaseScore
    critic_yield: CriticYield


def score_case_with_yield(
    case: GoldenCase,
    drafts: Sequence[DraftThreat],
    produced: Sequence[DraftThreat],
    matcher: Matcher,
    votes: Ledger,
    block: FrameworkAnalysis,
    rows: Mapping[str, str],
) -> ScoredCase:
    """Score both sides of the critic through one matcher and one ledger.

    ``score`` is the post-critic score the rest of the harness already
    reports — identical to what :func:`score_case` alone would have returned,
    because the rule is deterministic and both passes read one ledger.

    ``block`` is the report's own framework block, read for the one fact the
    drafts do not carry: the step the critic said it rejected each killed draft
    at.
    """
    pre = score_case(case, drafts, matcher, votes, rows)
    post = score_case(case, produced, matcher, votes, rows)
    return ScoredCase(score=post, critic_yield=_yield(pre, post, drafts, block))


#: The cause a kill reads where its verdict names no step. **The one spelling
#: of a missing ``rejected_because``**: the critic yield and the loss
#: attribution both read it, so neither invents a step the critic never wrote.
UNSTATED = "unstated"


def rejection_steps(block: FrameworkAnalysis) -> dict[str, str]:
    """Each rejected claim's stated step, keyed by claim ID, off the block's verdicts.

    The one reader of ``rejected_because`` for this instrument. A rejected
    claim whose verdict names no step is absent here, so its kill reads
    :data:`UNSTATED` rather than a step the critic never wrote.
    """
    return {
        claim.id: claim.verdict.rejected_because
        for claim in block.rejected_claims
        if claim.verdict.rejected_because is not None
    }


def fate_by_claim(block: FrameworkAnalysis) -> dict[str, CriticFate]:
    """Each claim ID the block carries, and the list it carries it in.

    A draft the block does not carry has the fate :data:`OMITTED`, the
    report omission of :mod:`evals.harness.assembly`. The block's validation
    refuses one ID in both ``claims`` and ``rejected_claims``. It does not
    check ``returned_claims`` against them, and ``returned`` wins here, as it
    does in the assembly replay.
    """
    return {
        **{claim.id: "rejected" for claim in block.rejected_claims},
        **{claim.id: claim.verdict.status for claim in block.claims},
        **{entry.claim_id: "returned" for entry in block.returned_claims},
    }


def _yield(
    pre: CaseScore,
    post: CaseScore,
    drafts: Sequence[DraftThreat],
    block: FrameworkAnalysis,
) -> CriticYield:
    steps = rejection_steps(block)
    fate_of = fate_by_claim(block)
    surviving = set(post.produced_ids)
    outcomes = _scored_outcomes(pre)
    killed = tuple(
        KilledDraft(
            threat_id=draft.id,
            category=draft.category,
            claim=candidate_claim(draft),
            outcome=outcomes[draft.id][0],
            reference_index=outcomes[draft.id][1],
            cause=steps.get(draft.id),
        )
        for draft in drafts
        if draft.id not in surviving
    )
    return CriticYield(
        case_id=pre.case_id,
        drafts_in=pre.produced_count,
        threats_out=post.produced_count,
        killed=killed,
        rejected_before=pre.status_counts["rejected"],
        rejected_after=post.status_counts["rejected"],
        matched_before=len(pre.matched),
        matched_after=len(post.matched),
        must_find_before=pre.must_find_matched,
        must_find_after=post.must_find_matched,
        must_find_total=pre.must_find_total,
        populations=CriticPopulations(
            dict(
                Counter(
                    (outcomes[draft.id][0], fate_of.get(draft.id, OMITTED))
                    for draft in drafts
                )
            )
        ),
        matched_after_indices=frozenset(pair.reference_index for pair in post.matched),
    )


def _scored_outcomes(score: CaseScore) -> dict[str, tuple[ScoredOutcome, int | None]]:
    """Every produced threat's scored outcome in one pass, keyed by threat ID.

    Threat IDs are unique within a run — :func:`~analysis_service.critic.
    join_drafts` fails closed if two category agents reuse one — so a dict is a safe
    index. The scorer's outcomes are mutually exclusive by construction: a
    threat is matched, or a lane error, or unlisted with exactly one
    standing. ``unscored`` should be unreachable, and is here so that
    a future scorer path which stops covering the produced set surfaces as a
    visible label in the artifact rather than a ``KeyError`` mid-sweep.
    """
    records: dict[str, tuple[ScoredOutcome, int | None]] = {}
    for pair in score.matched:
        matched: ScoredOutcome = (
            "matched-must-find" if pair.must_find else "matched-expected"
        )
        records[pair.threat_id] = (matched, pair.reference_index)
    for error in score.lane_errors:
        records[error.threat_id] = ("lane-error", error.reference_index)
    for entry in score.unlisted:
        records[entry.threat_id] = (entry.status, None)
    for threat_id in score.produced_ids:
        records.setdefault(threat_id, ("unscored", None))
    return records


def _critic_rates(
    *,
    drafts_in: int,
    killed: int,
    rejected_before: int,
    rejected_killed: int,
    matched_before: int,
    matched_killed: int,
) -> dict[YieldRate, Rate]:
    """The yield's four rates, from one case's counts or from pooled ones."""
    return {
        "kill_rate": Rate(killed, drafts_in),
        "rejected_kill_rate": Rate(rejected_killed, rejected_before),
        "matched_kill_rate": Rate(matched_killed, matched_before),
        "kill_precision": Rate(rejected_killed, killed),
    }


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
    matched_lost = sum(entry.matched_lost for entry in yields)
    must_find_lost = sum(entry.must_find_lost for entry in yields)
    by_cause: dict[str, Counter[str]] = {}
    for entry in yields:
        for cause, counts in entry.killed_by_cause.items():
            by_cause.setdefault(cause, Counter()).update(counts)
    return {
        "cases": len(yields),
        "drafts_in": drafts_in,
        "threats_out": sum(entry.threats_out for entry in yields),
        "killed": killed,
        "rejected_killed": rejected_killed,
        "matched_killed": matched_killed,
        "must_find_killed": must_find_killed,
        "matched_lost": matched_lost,
        "must_find_lost": must_find_lost,
        **{
            name: rate.rounded()
            for name, rate in _critic_rates(
                drafts_in=drafts_in,
                killed=killed,
                rejected_before=rejected_before,
                rejected_killed=rejected_killed,
                matched_before=matched_before,
                matched_killed=matched_killed,
            ).items()
        },
        # Every kill by the step the critic named, pooled; the per-step
        # ``matched`` and ``must_find`` are what a step removed.
        "killed_by_cause": {
            cause: dict(counts) for cause, counts in sorted(by_cause.items())
        },
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
            f"  (must-find {entry.must_find_killed}, lost for good"
            f" {entry.matched_lost})"
        )
    if yields:
        totals = aggregate_yield(yields)
        print(
            f"critic yield: killed {totals['killed']}/{totals['drafts_in']}"
            f" ({percent(totals['kill_rate'])}),"
            f" rejected caught {percent(totals['rejected_kill_rate'])},"
            f" real destroyed {percent(totals['matched_kill_rate'])}"
            f" of which lost for good {totals['matched_lost']}"
            f" (must-find {totals['must_find_lost']})"
            " (instrument, non-gating)"
        )
        if totals["killed_by_cause"]:
            print(
                "  by the step the critic named: "
                + ", ".join(
                    f"{cause} {counts['killed']} (matched {counts['matched']},"
                    f" must-find {counts['must_find']})"
                    for cause, counts in totals["killed_by_cause"].items()
                )
            )


def artifact(yields: Sequence[CriticYield]) -> dict[str, Any]:
    """This instrument's artifact keys."""
    return {
        "critic_yield": [entry.to_json() for entry in yields],
        "critic_yield_aggregate": aggregate_yield(yields) if yields else None,
    }
