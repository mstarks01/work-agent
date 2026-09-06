"""Scoring produced threats against a golden case's reference set.

It is mechanical everywhere, and a human answers the one question code cannot:

1. Lane prefilter, mechanical. A produced threat is only ever a candidate for
   same-category references. That is not a shortcut: the scorer rejects misfiled
   threats and never recategorizes them. It cuts the pair count about sixfold
   for free.
2. Per-pair claim equivalence, mechanical. This is the identity rule from
   :mod:`evals.harness.identity`, which is endpoint subset plus one action, with
   one ruling per pair that is individually auditable in the run artifact. It
   refuses a pair it cannot read rather than guessing, so a reference claim
   without a verb fails the sweep loudly.
3. One-to-one assignment, mechanical. This is deterministic maximum bipartite
   matching, so each produced threat consumes at most one reference. Without it,
   recall is inflatable and stops meaning anything.
4. Standing of the unmatched, voted. Unmatched is not a false positive.
   References are non-exhaustive by construction, so that rule would punish
   finding real threats and push every tuning cycle toward under-reporting. The
   scorer keys each unmatched threat by its fingerprint and looks it up in the
   vote ledger. A substance down-vote rejects it and gates. A vote that joins
   the pool marks it real but unlisted. A finding nobody answered is
   ``unvoted``, which is visible, non-gating, and exactly what the review queue
   serves.
5. Severity calibration, mechanical. ``derive_severity_level`` is shipped
   arithmetic, and comparing bands needs no vote.

Two things this module deliberately does not do. It scores element agreement at
the citation level and never uses it as a prefilter: the endpoint resolution
inside the rule handles the flow-versus-process spelling, and the per-pair
Jaccard here reports citation quality on matched pairs. It also never treats a
``needs-info`` threat as a false positive. Those are the designed behaviour the
third exemplar per category teaches, and they get their own bucket.

Every number here is rule-relative and ledger-relative. It is valid for tracking
movement and comparing configurations. It is not an absolute, and it is not
comparable to published figures from other tools. The matching half is
deterministic, so it cannot move between two runs of one configuration. The
standing half moves only when a person votes.

The scorer takes :class:`~analysis_service.frameworks.stride.record.DraftThreat` rather than
:class:`~analysis_service.frameworks.stride.record.Threat`, so the same function scores the
pre-critic union and the post-critic report. Nothing is promoted to make that
work. ``verdict`` and ``confidence`` are the critic's outputs, and synthesizing
them to measure the critic would decide the answer by fiat. The one field a
draft cannot supply is the ``needs-info`` standing bypass, and it is simply
inactive before the critic has ruled, which is the honest reading of a set
nobody has ruled on yet.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from analysis_service.frameworks.stride.record import (
    DraftThreat,
    StrideCategory,
    Threat,
)
from analysis_service.report import SeverityLevel, derive_severity_level
from evals.harness.fingerprint import components_for, fingerprint, version_for
from evals.harness.identity import ClaimPair, Matcher
from evals.harness.ledger import Ledger
from evals.harness.reference import GoldenCase, ReferenceThreat


def candidate_claim(threat: DraftThreat) -> str:
    """The produced threat's claim, as a pair carries it.

    The ``title``: one scannable line naming the attacker action and its
    target, which is exactly the register a ``ReferenceThreat.claim`` is
    written in. Grading the 4000-character ``description`` instead would grade
    prose no one asked the model to reproduce, and it is what the recorded
    calibration fixtures were written against — so the matching task offline
    and the matching task in a live run are the same task. It is defined on the
    *draft* base class for the same reason: a draft and the threat it becomes
    are matched on the same string, or critic yield compares two numbers that
    were never comparable.
    """
    return threat.title


def _is_needs_info(threat: DraftThreat) -> bool:
    """Whether a produced threat carries the critic's ``needs-info`` verdict.

    Only a ruled :class:`Threat` can: a draft has no verdict yet, so the
    decision-9 bypass is inactive on the pre-critic side rather than guessed at.
    """
    return isinstance(threat, Threat) and threat.verdict.status == "needs-info"


@dataclass(frozen=True)
class PairRuling:
    """One ruled pair, kept whole for the run artifact."""

    reference_index: int
    threat_id: str
    category: StrideCategory
    cross_lane: bool
    match: bool
    rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "reference_index": self.reference_index,
            "threat_id": self.threat_id,
            "category": self.category,
            "cross_lane": self.cross_lane,
            "match": self.match,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class MatchedPair:
    """A reference and the produced threat assigned to it."""

    reference_index: int
    threat_id: str
    tier: str
    element_overlap: bool
    element_jaccard: float
    reference_level: SeverityLevel
    produced_level: SeverityLevel
    likelihood_agrees: bool
    impact_agrees: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "reference_index": self.reference_index,
            "threat_id": self.threat_id,
            "tier": self.tier,
            "element_overlap": self.element_overlap,
            "element_jaccard": round(self.element_jaccard, 3),
            "reference_level": self.reference_level,
            "produced_level": self.produced_level,
            "likelihood_agrees": self.likelihood_agrees,
            "impact_agrees": self.impact_agrees,
        }


#: Where an unmatched produced threat stands with the reviewers.
#:
#: ``rejected`` — a current vote carries a substance reason, so a person read
#: the finding and said the analysis is wrong. The one gating standing.
#: ``pooled`` — a current vote puts it in the pool: real, just not in the
#: reference set yet. ``promotion_candidates`` surfaces these.
#: ``open`` — somebody answered ``unsure`` or ``needs-evidence``; neither
#: gates nor pools.
#: ``unvoted`` — nobody answered. Non-gating and visible, and exactly the set
#: the review queue serves.
Standing = Literal["rejected", "pooled", "open", "unvoted"]


@dataclass(frozen=True)
class UnlistedThreat:
    """An unmatched produced threat, its fingerprint, and its standing.

    The fingerprint is the join key to the vote ledger and the review queue:
    the standing recorded here is the ledger's answer at scoring time, and the
    same key re-reads the ledger after the next sitting with no re-run.
    """

    threat_id: str
    category: StrideCategory
    claim: str
    fingerprint: str
    standing: Standing

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "category": self.category,
            "claim": self.claim,
            "fingerprint": self.fingerprint,
            "standing": self.standing,
        }


@dataclass(frozen=True)
class LaneError:
    """A produced threat that matches a reference in a *different* lane.

    The only way lane accuracy is observable at all: in-lane matching cannot
    see a misfiled threat by construction. Recorded here, and deliberately
    **not** counted as a recall hit — misfiled threats are rejected rather than
    recategorized, so the reference stays a miss.
    """

    threat_id: str
    produced_category: StrideCategory
    reference_index: int
    reference_category: StrideCategory
    rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "produced_category": self.produced_category,
            "reference_index": self.reference_index,
            "reference_category": self.reference_category,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CaseScore:
    """Everything one case's scoring produced, metrics and evidence alike."""

    case_id: str
    exemplar_proximity: str
    produced_ids: tuple[str, ...]
    produced_count: int
    reference_count: int
    must_find_total: int
    matched: tuple[MatchedPair, ...]
    missed: tuple[int, ...]
    lane_errors: tuple[LaneError, ...]
    unlisted: tuple[UnlistedThreat, ...]
    needs_info_unmatched: tuple[str, ...]
    #: Unmatched threats citing an element the blessed model does not hold.
    #: Only an ``end-to-end`` run can fill this: its lanes cite the IDs a live
    #: extraction spelled, and the references cite the blessed ones, so such a
    #: threat can equal no reference however right its analysis is. Kept apart
    #: from ``unlisted`` because a fingerprint over a foreign ID is a key no
    #: vote can land on, and apart from ``missed`` because the number it feeds
    #: says how much of the gap is spelling rather than analysis (#431).
    foreign: tuple[str, ...]
    rulings: tuple[PairRuling, ...] = field(repr=False)
    severity_confusion: dict[str, int] = field(default_factory=dict)

    # --- Tier 2: the gate-in-waiting -------------------------------------

    @property
    def must_find_matched(self) -> int:
        return sum(1 for pair in self.matched if pair.tier == "must-find")

    @property
    def must_find_recall(self) -> float:
        return ratio(self.must_find_matched, self.must_find_total)

    @property
    def expected_recall(self) -> float:
        expected_total = self.reference_count - self.must_find_total
        matched = sum(1 for pair in self.matched if pair.tier == "expected")
        return ratio(matched, expected_total)

    @property
    def recall(self) -> float:
        return ratio(len(self.matched), self.reference_count)

    # --- Tier 3: tracked, never absolute ---------------------------------

    @property
    def lane_accuracy(self) -> float:
        """Of the produced threats that correspond to a reference at all, the
        fraction filed in the right lane. Categorization is a documented
        failure mode (arXiv:2505.04101) and otherwise invisible."""
        return ratio(len(self.matched), len(self.matched) + len(self.lane_errors))

    @property
    def element_accuracy(self) -> float:
        """Of matched pairs, the fraction citing at least one shared element.
        Traceability is the property the whole report schema exists for."""
        return ratio(
            sum(1 for pair in self.matched if pair.element_overlap), len(self.matched)
        )

    @property
    def element_jaccard(self) -> float:
        return ratio(
            sum(pair.element_jaccard for pair in self.matched), len(self.matched)
        )

    @property
    def standing_counts(self) -> dict[str, int]:
        counts = Counter(entry.standing for entry in self.unlisted)
        return {standing: counts.get(standing, 0) for standing in _STANDINGS}

    @property
    def rejected_rate(self) -> float:
        """The one gating Tier 3 number: hallucination is what destroys trust.

        Human-confirmed only: the numerator is findings a person voted down
        for substance. An unvoted finding cannot raise it, so a sweep over a
        cold ledger reads 0.0 — beside an ``unvoted`` count that says how much
        of the answer is still waiting on a sitting.
        """
        return ratio(self.standing_counts["rejected"], self.produced_count)

    @property
    def unvoted_count(self) -> int:
        """How much of the unlisted set no person has answered yet."""
        return self.standing_counts["unvoted"]

    @property
    def severity_exact_rate(self) -> float:
        exact = sum(
            1 for pair in self.matched if pair.reference_level == pair.produced_level
        )
        return ratio(exact, len(self.matched))

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "exemplar_proximity": self.exemplar_proximity,
            "counts": {
                "produced": self.produced_count,
                "references": self.reference_count,
                "matched": len(self.matched),
                "missed": len(self.missed),
                "must_find_total": self.must_find_total,
                "must_find_matched": self.must_find_matched,
                "needs_info_unmatched": len(self.needs_info_unmatched),
                "foreign": len(self.foreign),
                "lane_errors": len(self.lane_errors),
                **self.standing_counts,
            },
            "metrics": {
                "must_find_recall": round(self.must_find_recall, 3),
                "expected_recall": round(self.expected_recall, 3),
                "recall": round(self.recall, 3),
                "lane_accuracy": round(self.lane_accuracy, 3),
                "element_accuracy": round(self.element_accuracy, 3),
                "element_jaccard": round(self.element_jaccard, 3),
                "rejected_rate": round(self.rejected_rate, 3),
                "severity_exact_rate": round(self.severity_exact_rate, 3),
            },
            "severity_confusion": self.severity_confusion,
            "matched": [pair.to_json() for pair in self.matched],
            "missed": list(self.missed),
            "lane_errors": [error.to_json() for error in self.lane_errors],
            "unlisted": [entry.to_json() for entry in self.unlisted],
            "needs_info_unmatched": list(self.needs_info_unmatched),
            "foreign": list(self.foreign),
            "rulings": [ruling.to_json() for ruling in self.rulings],
        }


_STANDINGS: tuple[Standing, ...] = ("rejected", "pooled", "open", "unvoted")


def ratio(numerator: float, denominator: float) -> float:
    """Zero denominators are 0.0, never a crash and never a silent 100%."""
    return numerator / denominator if denominator else 0.0


def score_case(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    matcher: Matcher,
    votes: Ledger,
) -> CaseScore:
    """Score one case's produced threats against its reference set.

    ``produced`` is a bare threat list rather than a report, and typed at the
    draft base class, so this one function scores both sides of the critic: the
    merged drafts going in, and the report's threats coming out.

    ``votes`` decides the standing of every unmatched threat. Passing the
    object rather than a path keeps this function pure: the caller loads the
    ledger once, and a test hands in one it built in memory.
    """
    rulings: list[PairRuling] = []
    in_lane = _match_in_lane(case, produced, matcher, rulings)
    references = case.stride_claims()
    assignment = _assign(in_lane, references)

    matched = tuple(
        _matched_pair(reference_index, references[reference_index], produced[pos])
        for reference_index, pos in sorted(assignment.items())
    )
    matched_threat_positions = set(assignment.values())
    matched_reference_indices = set(assignment)
    missed = tuple(
        index
        for index in range(len(references))
        if index not in matched_reference_indices
    )
    unmatched_positions = [
        position
        for position in range(len(produced))
        if position not in matched_threat_positions
    ]

    lane_errors = _find_lane_errors(
        case, produced, matcher, rulings, unmatched_positions, missed
    )
    misfiled = {error.threat_id for error in lane_errors}
    unlisted, needs_info, foreign = _standing_of_unmatched(
        case, produced, votes, unmatched_positions, misfiled
    )

    return CaseScore(
        case_id=case.id,
        exemplar_proximity=case.declaration("stride").exemplar_proximity,
        produced_ids=tuple(threat.id for threat in produced),
        produced_count=len(produced),
        reference_count=len(references),
        must_find_total=len(case.must_find_for("stride")),
        matched=matched,
        missed=missed,
        lane_errors=lane_errors,
        unlisted=unlisted,
        needs_info_unmatched=needs_info,
        foreign=foreign,
        rulings=tuple(rulings),
        severity_confusion=_severity_confusion(matched),
    )


def _match_in_lane(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    matcher: Matcher,
    rulings: list[PairRuling],
) -> dict[int, list[int]]:
    """Step 1 and 2: same-lane pairs only, one ruled verdict each.

    Returns reference index -> the positions in ``produced`` the rule called
    equivalent, which is the bipartite graph step 3 assigns over.

    The pair carries both verbs, because the rule's verb half is what separates
    a read from a write against one store. A reference claim with no verb makes
    the rule raise, which fails the sweep and names the case — the corpus
    carries a verb on all 243 claims, so that failure means a new case arrived
    without one.
    """
    candidates: dict[int, list[int]] = {}
    for reference_index, reference in enumerate(case.stride_claims()):
        matches = []
        for position, threat in enumerate(produced):
            if threat.category != reference.category:
                continue
            ruling = matcher.equivalent(
                ClaimPair(
                    case=case.id,
                    category=reference.category,
                    reference_claim=reference.claim,
                    candidate_claim=candidate_claim(threat),
                    reference_element_ids=tuple(reference.affected_element_ids),
                    candidate_element_ids=tuple(threat.affected_element_ids),
                    reference_verb=reference.verb,
                    candidate_verb=threat.verb,
                )
            )
            rulings.append(
                PairRuling(
                    reference_index=reference_index,
                    threat_id=threat.id,
                    category=reference.category,
                    cross_lane=False,
                    match=ruling.match,
                    rationale=ruling.rationale,
                )
            )
            if ruling.match:
                matches.append(position)
        candidates[reference_index] = matches
    return candidates


def _assign(
    candidates: dict[int, list[int]],
    references: Sequence[ReferenceThreat],
) -> dict[int, int]:
    """Step 3: maximum one-to-one assignment, deterministically.

    Kuhn's augmenting-path algorithm over the ruled adjacency. ``must-find``
    references are processed first so that when the matching is not unique, the
    references the gate depends on are the ones that get satisfied — the
    matching is still maximum either way, this only settles ties.
    """
    taken: dict[int, int] = {}  # position in produced -> reference index

    def augment(reference_index: int, visited: set[int]) -> bool:
        for position in candidates.get(reference_index, ()):
            if position in visited:
                continue
            visited.add(position)
            holder = taken.get(position)
            if holder is None or augment(holder, visited):
                taken[position] = reference_index
                return True
        return False

    order = sorted(
        range(len(references)),
        key=lambda index: (not references[index].must_find, index),
    )
    for reference_index in order:
        augment(reference_index, set())

    # ``taken`` is the authoritative view: an earlier reference may have been
    # bumped to a different position by a later augmenting path.
    return {reference_index: position for position, reference_index in taken.items()}


def _matched_pair(
    reference_index: int, reference: ReferenceThreat, threat: DraftThreat
) -> MatchedPair:
    reference_ids = set(reference.affected_element_ids)
    produced_ids = set(threat.affected_element_ids)
    shared = reference_ids & produced_ids
    return MatchedPair(
        reference_index=reference_index,
        threat_id=threat.id,
        tier=reference.tier,
        element_overlap=bool(shared),
        element_jaccard=ratio(len(shared), len(reference_ids | produced_ids)),
        reference_level=reference.severity.level,
        # The matrix rather than the field: ``Severity.level`` is optional
        # until the model validator derives it, and this is that same
        # derivation, which is what the band comparison is defined as.
        produced_level=derive_severity_level(
            threat.severity.likelihood, threat.severity.impact
        ),
        likelihood_agrees=reference.severity.likelihood == threat.severity.likelihood,
        impact_agrees=reference.severity.impact == threat.severity.impact,
    )


def _find_lane_errors(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    matcher: Matcher,
    rulings: list[PairRuling],
    unmatched_positions: Sequence[int],
    missed: Sequence[int],
) -> tuple[LaneError, ...]:
    """The cross-lane pass, over what is unmatched on *both* sides only.

    Bounded on purpose: a produced threat that already consumed a reference
    cannot also be evidence of a lane error, and a reference that matched is
    not missing.
    """
    errors: list[LaneError] = []
    claimed_references: set[int] = set()
    for position in unmatched_positions:
        threat = produced[position]
        for reference_index in missed:
            reference = case.stride_claims()[reference_index]
            already_used = reference_index in claimed_references
            if reference.category == threat.category or already_used:
                continue
            ruling = matcher.equivalent(
                ClaimPair(
                    case=case.id,
                    category=reference.category,
                    reference_claim=reference.claim,
                    candidate_claim=candidate_claim(threat),
                    reference_element_ids=tuple(reference.affected_element_ids),
                    candidate_element_ids=tuple(threat.affected_element_ids),
                    reference_verb=reference.verb,
                    candidate_verb=threat.verb,
                )
            )
            rulings.append(
                PairRuling(
                    reference_index=reference_index,
                    threat_id=threat.id,
                    category=reference.category,
                    cross_lane=True,
                    match=ruling.match,
                    rationale=ruling.rationale,
                )
            )
            if not ruling.match:
                continue
            claimed_references.add(reference_index)
            errors.append(
                LaneError(
                    threat_id=threat.id,
                    produced_category=threat.category,
                    reference_index=reference_index,
                    reference_category=reference.category,
                    rationale=ruling.rationale,
                )
            )
            break
    return tuple(errors)


def _standing_of_unmatched(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    votes: Ledger,
    unmatched_positions: Sequence[int],
    misfiled: set[str],
) -> tuple[tuple[UnlistedThreat, ...], tuple[str, ...], tuple[str, ...]]:
    """Step 4: each unmatched threat's fingerprint, looked up in the ledger.

    Whether the **System Model** supports a claim nobody wrote down is a
    question about prose, and a person answers it — once per fingerprint,
    kept forever. This function only reads the answers. A ``needs-info``
    threat is never a false positive — it is the designed response to an
    unknown attribute — so it is counted, not keyed. A threat already recorded
    as a lane error is accounted for too, and keying it again would
    double-count one mistake. A threat citing an element the blessed model
    does not hold is counted as ``foreign`` and not keyed either: only an
    end-to-end run produces one, and its fingerprint would name an ID no
    reviewer can resolve.

    The fingerprint is computed exactly the way the review queue computes it,
    from the same components under the same per-framework version, so a vote
    cast from the queue lands on the finding scored here by construction.
    """
    unlisted: list[UnlistedThreat] = []
    needs_info: list[str] = []
    foreign: list[str] = []
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    blessed_ids = {element.id for element in case.model.elements()}
    version = version_for("stride")

    for position in unmatched_positions:
        threat = produced[position]
        if threat.id in misfiled:
            continue
        if _is_needs_info(threat):
            needs_info.append(threat.id)
            continue
        if not blessed_ids.issuperset(threat.affected_element_ids):
            foreign.append(threat.id)
            continue
        components = components_for(
            "stride",
            threat.category,
            tuple(threat.affected_element_ids),
            flows,
            verb=threat.verb,
        )
        value = fingerprint(components, version=version)
        unlisted.append(
            UnlistedThreat(
                threat_id=threat.id,
                category=threat.category,
                claim=candidate_claim(threat),
                fingerprint=value,
                standing=_standing(votes, value),
            )
        )
    return tuple(unlisted), tuple(needs_info), tuple(foreign)


def _standing(votes: Ledger, value: str) -> Standing:
    """One fingerprint's standing, from the current votes and nothing else.

    ``rejected`` wins over ``pooled`` when reviewers disagree: a standing that
    hid a substance objection behind a second reviewer's up-vote would let the
    gate pass on the vote most favourable to the tool. The disagreement itself
    is the queue's business to surface for a third opinion.
    """
    current = [vote for (key, _), vote in votes.current().items() if key == value]
    if any(vote.counts_against_analysis for vote in current):
        return "rejected"
    if any(vote.joins_the_pool for vote in current):
        return "pooled"
    if current:
        return "open"
    return "unvoted"


def _severity_confusion(matched: Iterable[MatchedPair]) -> dict[str, int]:
    """Step 5: the band confusion over matched pairs, purely arithmetic.

    Keyed ``reference->produced`` so a systematic bias (everything inflated one
    band) is readable straight off the artifact.
    """
    counts = Counter(
        f"{pair.reference_level}->{pair.produced_level}" for pair in matched
    )
    return dict(sorted(counts.items()))


def severity_axis_agreement(matched: Sequence[MatchedPair]) -> dict[str, float]:
    """Which axis the model gets wrong, since the band hides it."""
    return {
        "likelihood": ratio(
            sum(1 for pair in matched if pair.likelihood_agrees), len(matched)
        ),
        "impact": ratio(sum(1 for pair in matched if pair.impact_agrees), len(matched)),
    }


def exemplar_delta(scores: Sequence[CaseScore]) -> dict[str, float]:
    """The near-vs-far recall delta: tracked, deliberately **non-gating**.

    A large delta is a finding to act on — the exemplars live in their own
    subtree precisely so they can be diversified — not a build to break.

    "Near" is every architecture the exemplars demonstrate, so the number
    survives the exemplar set growing: with two worked systems and one corpus
    control apiece, this still asks the one question worth asking, which is
    whether recall depends on having been shown the architecture. What it can
    no longer do is attribute a gap to a *particular* exemplar system — for
    that, read the per-case recalls behind it.
    """
    near = [score for score in scores if score.exemplar_proximity == "near"]
    far = [score for score in scores if score.exemplar_proximity == "far"]
    near_recall = ratio(sum(score.recall for score in near), len(near))
    far_recall = ratio(sum(score.recall for score in far), len(far))
    return {
        "near_recall": round(near_recall, 3),
        "far_recall": round(far_recall, 3),
        "delta": round(near_recall - far_recall, 3),
    }


def unlisted_for_promotion(scores: Sequence[CaseScore]) -> list[dict[str, Any]]:
    """The corpus feedback loop: the pool feeds promotion.

    Findings a person voted into the pool — real, just not in the reference
    set — surfaced for the next blessing pass. A non-exhaustive reference set
    converges from real output a reviewer confirmed; it never converges from
    someone trying to be exhaustive up front. The fingerprint rides along so
    the blessing pass can retire the vote's pool membership when the claim
    becomes a reference.
    """
    return [
        {
            "case": score.case_id,
            "category": entry.category,
            "claim": entry.claim,
            "fingerprint": entry.fingerprint,
        }
        for score in scores
        for entry in score.unlisted
        if entry.standing == "pooled"
    ]


def render(scores: Sequence[CaseScore]) -> None:
    """Every case's row, then the near/far exemplar delta under them."""
    for score in scores:
        print(
            f"{score.case_id:<26} must-find {score.must_find_matched}/"
            f"{score.must_find_total}"
            f"  recall {score.recall:.2f}"
            f"  lane {score.lane_accuracy:.2f}"
            f"  element {score.element_accuracy:.2f}"
            f"  rejected {score.rejected_rate:.2f}"
            f"  unvoted {score.unvoted_count}"
            + (f"  foreign {len(score.foreign)}" if score.foreign else "")
        )
    if scores:
        delta = exemplar_delta(scores)
        print(
            f"exemplar delta: near {delta['near_recall']:.2f}"
            f" vs far {delta['far_recall']:.2f}"
            f" = {delta['delta']:+.2f} (tracked, non-gating)"
        )


def published(blocks: Mapping[str, Any], metric: str) -> float | None:
    """One metric's mean across this sweep's cases, for the comparison table.

    The knowledge that a STRIDE number lives at ``scores[].metrics`` stays
    here rather than in the generator, so a change to the block's shape is one
    edit in the module that owns it.
    """
    rows = blocks.get("scores") or []
    values = [
        row["metrics"][metric]
        for row in rows
        if isinstance(row, Mapping) and metric in row.get("metrics", {})
    ]
    return sum(values) / len(values) if values else None


def vote_coverage(blocks: Mapping[str, Any]) -> tuple[int, int]:
    """(answered, total) unmatched findings, so a reader can weigh the row."""
    rows = blocks.get("scores") or []
    total = answered = 0
    for row in rows:
        counts = row.get("counts", {}) if isinstance(row, Mapping) else {}
        here = sum(counts.get(name, 0) for name in ("rejected", "pooled", "open"))
        total += here + counts.get("unvoted", 0)
        answered += here
    return answered, total


def artifact(scores: Sequence[CaseScore]) -> dict[str, Any]:
    """This instrument's artifact keys.

    The aggregates carry the sweep's verdict alongside them on the envelope, so
    nothing downstream folds an uncertified run into a trusted number unaware.
    """
    return {
        "scores": [score.to_json() for score in scores],
        "exemplar_delta": exemplar_delta(scores) if scores else None,
        "unlisted_for_promotion": unlisted_for_promotion(scores),
    }
