"""Both sides of the critic, scored from one run.

The critic is the most expensive node in the graph, at one strong-tier pass over
every category agent's output, and this module is the evidence that it earns
that cost. It is deliberately two-sided. A critic that rejects down-voted drafts
is earning its cost, and the same critic rejecting drafts that matched a
reference is destroying real findings. Only the pair means anything, because a
kill count alone can be read as either.

Two comparators help in reading the numbers against something. Semgrep's
assistant kills about 20% of findings at 92% to 96% agreement with human triage.
Unfiltered LLM threat enumeration runs at about 86% raw false positives.

Here is how it works. Each post-critic claim is a draft the critic kept, so
the two sides are a superset and its subset. A killed draft is a draft that is
not in the post-critic output, and its fate says whether the critic rejected it
or the block holds no verdict for it. Both sides are produced sets, so the
whole instrument is a matter of scoring the same case twice through the shipped
scorer, with no second metric implementation and no special-cased matching,
and then reading each draft's pre-critic scored outcome beside its fate in the
block, which is the state the critic faced and what it did.

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
``matched_killed``, which counts every reference-matched draft that left the
report, with a verdict or not.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args

from typing_extensions import TypeIs

from analysis_service.claims import FrameworkAnalysis, RejectionStep, VerdictStatus
from analysis_service.frameworks.stride.record import DraftThreat, StrideCategory
from evals.harness.identity import Matcher
from evals.harness.ledger import Ledger
from evals.harness.rates import Rate
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore, VoteStatus, candidate_claim, score_case

#: The scored outcomes that matched a reference.
MatchedOutcome = Literal["matched-must-find", "matched-expected"]

# What one produced threat turned out to be, in the pass that scored it.
# ``MatchedOutcome`` gives the two hits against the reference set, and the rest
# are the ways of missing it.
#
# **Composed from ``VoteStatus`` rather than restating it.** The ways an unmatched
# finding can stand are the scorer's vocabulary, and a second copy here would
# disagree with it the first time one was added. So ``MatchedOutcome`` and the
# two misses below are the names this module owns, and every standing arrives
# with the scorer's own type.
ScoredOutcome = MatchedOutcome | Literal["lane-error", "unscored"] | VoteStatus

#: The fates that hold no verdict status in the block. ``returned`` is a claim
#: the critic kept and assembly sent back to the deferred set, so it is not in
#: the report. ``omitted`` is a draft the block carries in none of its three
#: lists.
NoVerdictFate = Literal["returned", "omitted"]
#: What the critic's pass did with one draft, read off the block it returned.
#: ``needs-info`` is a qualification: it is neither a confirmation nor a
#: rejection.
CriticFate = VerdictStatus | NoVerdictFate
OMITTED: Literal["omitted"] = "omitted"
FATES: tuple[CriticFate, ...] = tuple(
    value for part in get_args(CriticFate) for value in get_args(part)
)
#: The fates that are a critic's verdict on the block.
VERDICT_STATUSES: frozenset[VerdictStatus] = frozenset(get_args(VerdictStatus))
#: The fates that hold no verdict, in the order a count of them names them.
NO_VERDICT_FATES: tuple[NoVerdictFate, ...] = tuple(sorted(get_args(NoVerdictFate)))


def has_verdict(fate: str) -> TypeIs[VerdictStatus]:
    """Whether ``fate`` is a verdict status the block records.

    A draft with no verdict is in no population rate, no yield rate and no
    agreement count: ``critic_yield`` and ``critic_replay`` each count it on its
    own line, which :func:`no_verdict_line` words.
    """
    return fate in VERDICT_STATUSES


def no_verdict_line(apart: int, fates: Sequence[str]) -> str:
    """The line that counts ``apart`` drafts with no archived verdict.

    It names ``fates``, which holds two or more fates, as each caller's
    constant does.
    """
    counted = "1 draft has" if apart == 1 else f"{apart} drafts have"
    *first, last = fates
    return f"{counted} no archived verdict ({', '.join(first)} or {last})"


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

#: Why the not-real population has no draft with a verdict: each draft with a
#: current down-vote has no verdict, some draft reads ``stale``, or neither.
#: :data:`GAP_WORDS` says what each one means.
NotRealGap = Literal["no-verdict", "stale-drafts", "no-down-vote"]

#: What a report prints for each :data:`NotRealGap`.
GAP_WORDS: Mapping[NotRealGap, str] = MappingProxyType(
    {
        "no-verdict": "each draft with a current down-vote has no archived"
        " verdict, so it is counted apart",
        "stale-drafts": "no draft carries a current down-vote, and some carry"
        " only a vote on another argument: a vote judges a reported claim with"
        " its verdict (ADR 0029), and a draft is not yet a reported claim",
        "no-down-vote": "no draft carries a current down-vote",
    }
)


@dataclass(frozen=True)
class CriticPopulations:
    """The drafts the critic was handed, by scored outcome and by fate.

    Each rate reads only its own population. ``real_rejected`` divides by the
    real drafts with a verdict, ``not_real_confirmed`` by the not-real drafts
    with a verdict, and a ``needs-info`` fate counts in neither: it has a
    rate of its own.

    A draft with no verdict (:func:`has_verdict`) is in no denominator, and
    :attr:`no_verdict` counts it. A population with no draft that has a verdict
    gives a :class:`~evals.harness.rates.Rate` whose value is ``None``, which is
    a gap and not a zero; :attr:`not_real_gap` names the reason for the
    not-real population.
    """

    counts: Mapping[tuple[ScoredOutcome, CriticFate], int]

    @classmethod
    def pooled(cls, yields: Sequence[CriticYield]) -> CriticPopulations:
        """The populations of several cases, summed."""
        total: Counter[tuple[ScoredOutcome, CriticFate]] = Counter()
        for entry in yields:
            total.update(entry.populations.counts)
        return cls(dict(total))

    def count(self, label: Population) -> int:
        """The drafts of one population, with a verdict or not."""
        return sum(n for (outcome, _), n in self.counts.items() if _in(label)(outcome))

    def rate(self, label: Population, fate: VerdictStatus) -> Rate:
        """The share of one population's drafts with a verdict that met one fate."""
        return Rate(
            self.met(fate, _in(label)),
            self.with_verdict(_in(label)),
        )

    def with_verdict(self, outcome_matches: Callable[[ScoredOutcome], bool]) -> int:
        """The drafts with a verdict whose scored outcome ``outcome_matches``."""
        return sum(
            n
            for (outcome, fate), n in self.counts.items()
            if outcome_matches(outcome) and has_verdict(fate)
        )

    def met(
        self, fate: VerdictStatus, outcome_matches: Callable[[ScoredOutcome], bool]
    ) -> int:
        """The drafts that met ``fate`` whose scored outcome ``outcome_matches``."""
        return sum(
            n
            for (outcome, held), n in self.counts.items()
            if held == fate and outcome_matches(outcome)
        )

    @property
    def no_verdict(self) -> int:
        """The drafts with no verdict, which no rate divides by."""
        return sum(n for (_, fate), n in self.counts.items() if not has_verdict(fate))

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
        """Why the not-real population has no draft with a verdict, or ``None``."""
        if self.with_verdict(_in("not-real")):
            return None
        if self.count("not-real"):
            return "no-verdict"
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
            "no_verdict": self.no_verdict,
        }


@dataclass(frozen=True)
class CriticAudit:
    """The critic's two sides over a sweep: its input populations and its loss.

    ``populations`` counts drafts. ``reference_loss`` counts references: its
    denominator is the gross count of reference-matched drafts that left the
    report, and its numerator the kills whose reference no reported claim
    matched. It counts every kill, with a verdict or not, as
    :func:`~evals.harness.losses.attribute_case` charges each one to the
    critic. Another retained draft can still carry a killed draft's
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
        """Print the population table, its rates and the loss line.

        The gap line and the no-verdict line print where they apply.
        """
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
                f"  not-real rates unavailable ({gap}, stale drafts:"
                f" {populations.stale_drafts}): {GAP_WORDS[gap]}"
            )
        if populations.no_verdict:
            print(f"  {no_verdict_line(populations.no_verdict, NO_VERDICT_FATES)}")
        loss = self.reference_loss
        print(
            f"  gross: {loss.denominator} reference-matched drafts left the report;"
            f" net: {loss.numerator} of their references no reported claim took"
        )


@dataclass(frozen=True)
class KilledDraft:
    """One draft that left the report, and what it was before the critic.

    ``fate`` says whether the critic rejected it or the block holds no verdict
    for it.

    The audit trail under the aggregate counts: a ``matched-must-find`` line
    here is a real finding that left the report, named, with the reference it
    was answering — which is what makes the veto number actionable instead of
    merely alarming.
    """

    threat_id: str
    category: StrideCategory
    claim: str
    outcome: ScoredOutcome
    reference_index: int | None
    #: What the block did with the draft: ``rejected``, or a fate with no
    #: verdict (:data:`NO_VERDICT_FATES`), which no population rate or yield
    #: rate counts.
    fate: CriticFate
    #: The step the critic said it rejected the draft at, off the block's own
    #: verdict: ``lane``, ``reasoning``, ``duplicate``, ``evidence`` and the
    #: rest of :data:`~analysis_service.claims.RejectionStep`. ``None`` where
    #: the verdict names no step, or the block carries no verdict for the
    #: draft. :attr:`CriticYield.killed_by_cause` counts the first under
    #: :data:`UNSTATED` and the second under its fate, so neither is folded
    #: into a step the critic never named.
    cause: RejectionStep | None

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "category": self.category,
            "claim": self.claim,
            "disposition": self.outcome,
            "reference_index": self.reference_index,
            "fate": self.fate,
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
    matched_after_indices: frozenset[int]

    # --- the gross kill counts -------------------------------------------

    @property
    def rejected_killed(self) -> int:
        """The count of every down-voted draft that left the report.

        It holds drafts with a verdict or not (:attr:`KilledDraft.fate` tells
        which) that a person had already rejected for substance.
        """
        return sum(1 for draft in self.killed if LABEL_OF[draft.outcome] == "not-real")

    @property
    def matched_killed(self) -> int:
        """The gross veto number: reference-matched drafts that left the report.

        A draft that had matched a reference and left the report, with a
        verdict or not: the critic destroying real findings.
        """
        return sum(1 for draft in self.killed if _matched(draft.outcome))

    @property
    def matched_lost(self) -> int:
        """The kills of :attr:`matched_killed` whose reference no surviving claim took.

        This is the net destruction. A killed draft's reference can be matched
        again by another draft after the critic, so the gross count overstates
        the loss: such a kill reads as a destroyed finding while the report
        still carries it. ``matched_killed`` is the gross count.
        """
        return sum(self._lost(draft) for draft in self.killed)

    def _lost(self, draft: KilledDraft) -> bool:
        """Whether ``draft`` had matched a reference that no surviving claim took."""
        return (
            _matched(draft.outcome)
            and draft.reference_index not in self.matched_after_indices
        )

    @property
    def must_find_lost(self) -> int:
        """The net form of :attr:`must_find_killed`."""
        return sum(
            self._lost(draft)
            for draft in self.killed
            if draft.outcome == "matched-must-find"
        )

    @property
    def must_find_killed(self) -> int:
        """The sharpest form of the veto: must-find drafts that left the report.

        Each had answered a reference the Tier 2 gate depends on, and left with
        a verdict or not.
        """
        return sum(1 for draft in self.killed if draft.outcome == "matched-must-find")

    @property
    def killed_by_cause(self) -> dict[KillCause, dict[str, int]]:
        """Every kill by its step or fate, and what each removed.

        A kill is keyed by the step its verdict names, by ``unstated`` for a
        rejection that names no step, or by its fate where it has no verdict.

        Per key, it gives the kills, the ones that had matched a reference, and
        the ones that had answered a must-find, so a prompt change that moves
        the critic's reasoning kills is visible as that and not as a bare count.
        """
        by_cause: dict[KillCause, dict[str, int]] = {}
        for draft in self.killed:
            entry = by_cause.setdefault(
                _cause_key(draft), {"killed": 0, "matched": 0, "must_find": 0}
            )
            entry["killed"] += 1
            entry["matched"] += _matched(draft.outcome)
            entry["must_find"] += draft.outcome == "matched-must-find"
        return dict(sorted(by_cause.items()))

    @property
    def kill_count(self) -> int:
        return len(self.killed)

    # --- rates, all rule-and-ledger-relative ------------------------------

    @property
    def rates(self) -> dict[YieldRate, Rate]:
        """Each rate with its counts, keyed by the rate's name.

        Each numerator counts drafts the critic rejected, not every kill.
        ``kill_rate`` is the share of drafts the critic rejected, the Semgrep
        ~20% comparator. ``rejected_kill_rate`` is the share of the drafts a
        person rejected that the critic rejected too, and ``matched_kill_rate``
        the share of reference-matched drafts it rejected. ``kill_precision``
        is the share of its rejections that a person rejected for substance.
        It is not the inverse of ``matched_kill_rate``: a rejected ``pooled``
        or ``unvoted`` draft is neither a win nor a loss here, because the
        reference sets are non-exhaustive by construction. A draft with no
        verdict is in no rate, as a numerator or a denominator. A rate whose
        population holds no draft with a verdict has no value.
        """
        return _critic_rates(self.populations)

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
            "rates": {name: rate.to_json() for name, rate in self.rates.items()},
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

    ``block`` is the report's own framework block, read for the two facts the
    drafts do not carry: each draft's fate, and the step the critic said it
    rejected each killed draft at.
    """
    pre = score_case(case, drafts, matcher, votes, rows)
    post = score_case(case, produced, matcher, votes, rows)
    return ScoredCase(score=post, critic_yield=_yield(pre, post, drafts, block))


#: The cause of a kill whose verdict names no step.
Unstated = Literal["unstated"]

#: The cause a kill reads where its verdict names no step. **The one spelling
#: of a missing ``rejected_because``**: the critic yield and the loss
#: attribution both read it, so neither invents a step the critic never wrote.
UNSTATED: Unstated = "unstated"

#: What :attr:`CriticYield.killed_by_cause` counts a kill under: the step its
#: verdict names, :data:`UNSTATED`, or the fate of a kill with no verdict.
KillCause = RejectionStep | Unstated | NoVerdictFate


def rejection_steps(block: FrameworkAnalysis) -> dict[str, RejectionStep]:
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
    refuses one ID in two of its three lists, so each ID has one fate.
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
    fates = {draft.id: fate_of.get(draft.id, OMITTED) for draft in drafts}
    surviving = set(post.produced_ids)
    outcomes = _scored_outcomes(pre)
    killed = tuple(
        KilledDraft(
            threat_id=draft.id,
            category=draft.category,
            claim=candidate_claim(draft),
            outcome=outcomes[draft.id][0],
            reference_index=outcomes[draft.id][1],
            fate=fates[draft.id],
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
            dict(Counter((outcomes[draft.id][0], fates[draft.id]) for draft in drafts))
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


def _cause_key(draft: KilledDraft) -> KillCause:
    """The key a kill is counted under in :attr:`CriticYield.killed_by_cause`.

    It is the stated step, :data:`UNSTATED` for a rejection that names none,
    or the fate of a kill with no verdict.
    """
    if not has_verdict(draft.fate):
        return draft.fate
    return draft.cause or UNSTATED


def _matched(outcome: ScoredOutcome) -> bool:
    """Whether ``outcome`` matched a reference."""
    return outcome in get_args(MatchedOutcome)


def _any_outcome(_: ScoredOutcome) -> bool:
    """The predicate that admits every scored outcome."""
    return True


def _in(label: Population) -> Callable[[ScoredOutcome], bool]:
    """The predicate that a scored outcome puts a draft in ``label``."""
    return lambda outcome: LABEL_OF[outcome] == label


def _critic_rates(populations: CriticPopulations) -> dict[YieldRate, Rate]:
    """The yield's four rates, over the drafts with a verdict, of one case or pooled.

    Each numerator counts the drafts the critic rejected, read off the same
    population counts as each denominator.
    """
    rejected = populations.met("rejected", _any_outcome)
    down_voted_rejected = populations.met("rejected", _in("not-real"))
    return {
        "kill_rate": Rate(rejected, populations.with_verdict(_any_outcome)),
        "rejected_kill_rate": Rate(
            down_voted_rejected, populations.with_verdict(_in("not-real"))
        ),
        "matched_kill_rate": Rate(
            populations.met("rejected", _matched), populations.with_verdict(_matched)
        ),
        "kill_precision": Rate(down_voted_rejected, rejected),
    }


def aggregate_yield(yields: Sequence[CriticYield]) -> dict[str, Any]:
    """The corpus-wide view, pooled rather than averaged over cases.

    Pooled on purpose: these are small counts per case, and a mean of per-case
    rates would let a case that drafted three threats outweigh one that drafted
    forty.
    """
    populations = CriticPopulations.pooled(yields)
    rates = _critic_rates(populations)
    must_find_killed = sum(entry.must_find_killed for entry in yields)
    matched_lost = sum(entry.matched_lost for entry in yields)
    must_find_lost = sum(entry.must_find_lost for entry in yields)
    by_cause: dict[KillCause, Counter[str]] = {}
    for entry in yields:
        for cause, counts in entry.killed_by_cause.items():
            by_cause.setdefault(cause, Counter()).update(counts)
    return {
        "cases": len(yields),
        "drafts_in": sum(entry.drafts_in for entry in yields),
        "threats_out": sum(entry.threats_out for entry in yields),
        "killed": sum(entry.kill_count for entry in yields),
        "rejected_killed": sum(entry.rejected_killed for entry in yields),
        "matched_killed": sum(entry.matched_killed for entry in yields),
        "must_find_killed": must_find_killed,
        "matched_lost": matched_lost,
        "must_find_lost": must_find_lost,
        **{name: rate.rounded() for name, rate in rates.items()},
        # Each rate's counts: the kill counts above include the drafts with
        # no verdict, so they do not divide to a rate.
        "rates": {name: rate.to_json() for name, rate in rates.items()},
        "no_verdict": populations.no_verdict,
        # Every kill by the step its verdict names, or by its fate where it has
        # no verdict, pooled; the per-key ``matched`` and ``must_find`` are
        # what each removed.
        "killed_by_cause": {
            cause: dict(counts) for cause, counts in sorted(by_cause.items())
        },
    }


def render(yields: Sequence[CriticYield]) -> None:
    """Both sides of the critic, always printed together.

    ``killed-real`` is deliberately on the same line as ``killed-rejected``:
    a kill count read on its own says nothing about whether the critic removes
    noise or destroys findings. Each rate counts only drafts with a verdict,
    the "all kills" counts keep every draft that left the report, and a
    no-verdict line prints under a case and under the total where a draft has
    no verdict.
    """
    for entry in yields:
        rates = entry.rates
        apart = entry.populations.no_verdict
        print(
            f"{entry.case_id:<26} critic {entry.drafts_in}->{entry.threats_out}"
            f"  killed-rejected {rates['rejected_kill_rate'].shown()}"
            f"  killed-real {rates['matched_kill_rate'].shown()}"
            f"  (all kills: must-find {entry.must_find_killed}, lost for good"
            f" {entry.matched_lost})"
        )
        if apart:
            print(f"  {no_verdict_line(apart, NO_VERDICT_FATES)}")
    if yields:
        totals = aggregate_yield(yields)
        pooled = {name: Rate.from_json(rate) for name, rate in totals["rates"].items()}
        print(
            f"critic yield: killed {pooled['kill_rate'].shown()},"
            f" rejected caught {pooled['rejected_kill_rate'].shown()},"
            f" real destroyed {pooled['matched_kill_rate'].shown()};"
            f" all kills lost for good {totals['matched_lost']}"
            f" (must-find {totals['must_find_lost']})"
            " (instrument, non-gating)"
        )
        if totals["killed_by_cause"]:
            print(
                "  by the step its verdict names, or by fate: "
                + ", ".join(
                    f"{cause} {counts['killed']} (matched {counts['matched']},"
                    f" must-find {counts['must_find']})"
                    for cause, counts in totals["killed_by_cause"].items()
                )
            )
        if totals["no_verdict"]:
            print(f"  {no_verdict_line(totals['no_verdict'], NO_VERDICT_FATES)}")


def artifact(yields: Sequence[CriticYield]) -> dict[str, Any]:
    """This instrument's artifact keys."""
    return {
        "critic_yield": [entry.to_json() for entry in yields],
        "critic_yield_aggregate": aggregate_yield(yields) if yields else None,
    }
