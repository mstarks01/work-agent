"""Scoring produced threats against a golden case's reference set.

Mechanical first, judgement only where nothing else will do:

1. **Lane prefilter** (mechanical). A produced threat is only ever a candidate
   for same-category references. Not a shortcut: misfiled threats are rejected,
   never recategorized. Cuts pair count ~6x for free.
2. **Per-pair claim equivalence** (judged). Binary, within lane, one line of
   rationale each, individually auditable in the run artifact.
3. **One-to-one assignment** (mechanical). Deterministic maximum bipartite
   matching, so each produced threat consumes at most one reference; without it
   recall is inflatable and stops meaning anything.
4. **Adjudication of the unmatched** (judged, three buckets). Unmatched is
   *not* treated as a false positive: references are non-exhaustive by
   construction, so that rule would punish finding real threats and push every
   tuning cycle toward under-reporting. Only ``unsupported`` gates.
5. **Severity calibration** (mechanical). ``derive_severity_level`` is shipped
   arithmetic; comparing bands needs no judge.

Two things this module deliberately does *not* do. Element agreement is
**scored, never used as a prefilter**: a correct threat may cite the data flow
where the SME cited the process at its endpoint, and filtering on it would
score a hit as a miss. And ``needs-info`` threats are never false positives —
they are the designed behaviour the third exemplar per category teaches, and
get their own bucket.

Every number here is **judge-relative**. Valid for tracking movement and
comparing configurations; not an absolute, and not comparable to published
figures from other tools.

The scorer takes :class:`~stride_service.report.DraftThreat`, not
:class:`~stride_service.report.Threat`, so the *same* function scores the
pre-critic union and the post-critic report. Nothing is promoted to make that
work: ``verdict`` and ``confidence`` are the critic's outputs, and synthesizing
them to measure the critic would decide the answer by fiat. The one field a
draft cannot supply — the ``needs-info`` adjudication bypass — is therefore
simply inactive before the critic has ruled, which is the honest reading of a
set nobody has ruled on yet.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.harness.judge import (
    Bucket,
    ClaimPair,
    Judge,
    UnmatchedThreat,
)
from evals.harness.reference import GoldenCase, ReferenceThreat
from stride_service.report import DraftThreat, SeverityLevel, StrideCategory, Threat


def candidate_claim(threat: DraftThreat) -> str:
    """The produced threat's claim, as the judge sees it.

    The ``title``: one scannable line naming the attacker action and its
    target, which is exactly the register a ``ReferenceThreat.claim`` is
    written in. Grading the 4000-character ``description`` instead would grade
    prose no one asked the model to reproduce, and it is what the hand-labelled
    calibration fixtures were written against — so the judged task offline and
    the judged task in a live run are the same task. It is defined on the
    *draft* base class for the same reason: a draft and the threat it becomes
    are judged on the same string, or critic yield compares two numbers that
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
    """One judged pair, kept whole for the run artifact."""

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


@dataclass(frozen=True)
class AdjudicatedThreat:
    """An unmatched produced threat and the bucket it landed in."""

    threat_id: str
    category: StrideCategory
    claim: str
    bucket: Bucket
    rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "category": self.category,
            "claim": self.claim,
            "bucket": self.bucket,
            "rationale": self.rationale,
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
    adjudicated: tuple[AdjudicatedThreat, ...]
    needs_info_unmatched: tuple[str, ...]
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
    def bucket_counts(self) -> dict[str, int]:
        counts = Counter(entry.bucket for entry in self.adjudicated)
        return {bucket: counts.get(bucket, 0) for bucket in _BUCKETS}

    @property
    def unsupported_rate(self) -> float:
        """The one gating Tier 3 number: hallucination is what destroys trust."""
        return ratio(self.bucket_counts["unsupported"], self.produced_count)

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
                "lane_errors": len(self.lane_errors),
                **self.bucket_counts,
            },
            "metrics": {
                "must_find_recall": round(self.must_find_recall, 3),
                "expected_recall": round(self.expected_recall, 3),
                "recall": round(self.recall, 3),
                "lane_accuracy": round(self.lane_accuracy, 3),
                "element_accuracy": round(self.element_accuracy, 3),
                "element_jaccard": round(self.element_jaccard, 3),
                "unsupported_rate": round(self.unsupported_rate, 3),
                "severity_exact_rate": round(self.severity_exact_rate, 3),
            },
            "severity_confusion": self.severity_confusion,
            "matched": [pair.to_json() for pair in self.matched],
            "missed": list(self.missed),
            "lane_errors": [error.to_json() for error in self.lane_errors],
            "adjudicated": [entry.to_json() for entry in self.adjudicated],
            "needs_info_unmatched": list(self.needs_info_unmatched),
            "rulings": [ruling.to_json() for ruling in self.rulings],
        }


_BUCKETS: tuple[Bucket, ...] = ("unsupported", "valid-unlisted", "noise")


def ratio(numerator: float, denominator: float) -> float:
    """Zero denominators are 0.0, never a crash and never a silent 100%."""
    return numerator / denominator if denominator else 0.0


def score_case(
    case: GoldenCase, produced: Sequence[DraftThreat], judge: Judge
) -> CaseScore:
    """Score one case's produced threats against its reference set.

    ``produced`` is a bare threat list rather than a report, and typed at the
    draft base class, so this one function scores both sides of the critic: the
    merged drafts going in, and the report's threats coming out.
    """
    rulings: list[PairRuling] = []
    in_lane = _judge_in_lane(case, produced, judge, rulings)
    assignment = _assign(in_lane, case.references)

    matched = tuple(
        _matched_pair(reference_index, case.references[reference_index], produced[pos])
        for reference_index, pos in sorted(assignment.items())
    )
    matched_threat_positions = set(assignment.values())
    matched_reference_indices = set(assignment)
    missed = tuple(
        index
        for index in range(len(case.references))
        if index not in matched_reference_indices
    )
    unmatched_positions = [
        position
        for position in range(len(produced))
        if position not in matched_threat_positions
    ]

    lane_errors = _find_lane_errors(
        case, produced, judge, rulings, unmatched_positions, missed
    )
    misfiled = {error.threat_id for error in lane_errors}
    adjudicated, needs_info = _adjudicate(
        case, produced, judge, unmatched_positions, misfiled
    )

    return CaseScore(
        case_id=case.id,
        exemplar_proximity=case.meta.exemplar_proximity,
        produced_ids=tuple(threat.id for threat in produced),
        produced_count=len(produced),
        reference_count=len(case.references),
        must_find_total=len(case.must_find),
        matched=matched,
        missed=missed,
        lane_errors=lane_errors,
        adjudicated=adjudicated,
        needs_info_unmatched=needs_info,
        rulings=tuple(rulings),
        severity_confusion=_severity_confusion(matched),
    )


def _judge_in_lane(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    judge: Judge,
    rulings: list[PairRuling],
) -> dict[int, list[int]]:
    """Step 1 and 2: same-lane pairs only, one judged verdict each.

    Returns reference index -> the positions in ``produced`` the judge called
    equivalent, which is the bipartite graph step 3 assigns over.
    """
    candidates: dict[int, list[int]] = {}
    for reference_index, reference in enumerate(case.references):
        matches = []
        for position, threat in enumerate(produced):
            if threat.category != reference.category:
                continue
            ruling = judge.equivalent(
                ClaimPair(
                    case=case.id,
                    category=reference.category,
                    reference_claim=reference.claim,
                    candidate_claim=candidate_claim(threat),
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

    Kuhn's augmenting-path algorithm over the judged adjacency. ``must-find``
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
        produced_level=threat.severity.level,
        likelihood_agrees=reference.severity.likelihood == threat.severity.likelihood,
        impact_agrees=reference.severity.impact == threat.severity.impact,
    )


def _find_lane_errors(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    judge: Judge,
    rulings: list[PairRuling],
    unmatched_positions: Sequence[int],
    missed: Sequence[int],
) -> tuple[LaneError, ...]:
    """The cross-lane pass, over what is unmatched on *both* sides only.

    Bounded on purpose: this is the second-largest source of judge calls in a
    run, and a produced threat that already consumed a reference cannot also be
    evidence of a lane error.
    """
    errors: list[LaneError] = []
    claimed_references: set[int] = set()
    for position in unmatched_positions:
        threat = produced[position]
        for reference_index in missed:
            reference = case.references[reference_index]
            already_used = reference_index in claimed_references
            if reference.category == threat.category or already_used:
                continue
            ruling = judge.equivalent(
                ClaimPair(
                    case=case.id,
                    category=reference.category,
                    reference_claim=reference.claim,
                    candidate_claim=candidate_claim(threat),
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


def _adjudicate(
    case: GoldenCase,
    produced: Sequence[DraftThreat],
    judge: Judge,
    unmatched_positions: Sequence[int],
    misfiled: set[str],
) -> tuple[tuple[AdjudicatedThreat, ...], tuple[str, ...]]:
    """Step 4: three buckets, and the ``needs-info`` set that bypasses them.

    A ``needs-info`` threat is never a false positive — it is the designed
    response to an unknown attribute — so it is counted, not judged. A threat
    already recorded as a lane error is accounted for too, and judging it again
    would double-count one mistake.
    """
    adjudicated: list[AdjudicatedThreat] = []
    needs_info: list[str] = []
    sibling_claims = tuple(candidate_claim(threat) for threat in produced)

    for position in unmatched_positions:
        threat = produced[position]
        if threat.id in misfiled:
            continue
        if _is_needs_info(threat):
            needs_info.append(threat.id)
            continue
        ruling = judge.adjudicate(
            UnmatchedThreat(
                threat_id=threat.id,
                category=threat.category,
                claim=candidate_claim(threat),
                description=threat.description,
                affected_element_ids=tuple(threat.affected_element_ids),
            ),
            case.model,
            tuple(
                claim for claim in sibling_claims if claim != candidate_claim(threat)
            ),
        )
        adjudicated.append(
            AdjudicatedThreat(
                threat_id=threat.id,
                category=threat.category,
                claim=candidate_claim(threat),
                bucket=ruling.bucket,
                rationale=ruling.rationale,
            )
        )
    return tuple(adjudicated), tuple(needs_info)


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
    """The corpus feedback loop.

    Grounded threats the reference set does not carry, surfaced for SME review
    and promotion at the next blessing pass. Non-exhaustive ground truth
    converges from real output; it never converges from someone trying to be
    exhaustive up front.
    """
    return [
        {
            "case": score.case_id,
            "category": entry.category,
            "claim": entry.claim,
            "rationale": entry.rationale,
        }
        for score in scores
        for entry in score.adjudicated
        if entry.bucket == "valid-unlisted"
    ]
