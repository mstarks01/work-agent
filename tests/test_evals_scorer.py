"""The scorer, offline, with zero provider calls.

Every judgement call goes through a stand-in replaying the corpus's recorded
labels, so what is under test here is the mechanical half — the lane prefilter,
the one-to-one assignment, the buckets, the severity arithmetic — which is
exactly the half that must never drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.calibration import load_pairs
from evals.harness.reference import load_case
from evals.harness.scorer import (
    candidate_claim,
    exemplar_delta,
    score_case,
    severity_axis_agreement,
    unlisted_for_promotion,
)
from tests.eval_factories import ScriptedJudge, produced_threat, threat_for

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"
CONTROL_CASE = CORPUS_DIR / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CONTROL_CASE)


@pytest.fixture(scope="module")
def labelled_pairs():
    return load_pairs()


def test_candidate_claim_is_the_title(case):
    threat = threat_for(
        case.claims_for("stride")[0], 1, "An attacker replays a session."
    )
    assert candidate_claim(threat) == "An attacker replays a session."


def test_matches_reference_via_recorded_labels(case, labelled_pairs):
    # The fixtures are the ground truth for the judged half: a produced threat
    # titled with a labelled candidate claim must land on its reference.
    pair = next(
        pair
        for pair in labelled_pairs
        if pair.case == case.id and pair.label == "match"
    )
    reference_index = next(
        index
        for index, reference in enumerate(case.claims_for("stride"))
        if reference.claim == pair.reference_claim
    )
    produced = [
        threat_for(case.claims_for("stride")[reference_index], 1, pair.candidate_claim)
    ]
    judge = ScriptedJudge([(pair.reference_claim, pair.candidate_claim)])

    score = score_case(case, produced, judge)

    assert [entry.reference_index for entry in score.matched] == [reference_index]
    assert score.matched[0].element_overlap is True


def test_lane_prefilter_never_judges_across_lanes(case):
    produced = [produced_threat(1, "denial-of-service", "A flood takes the API down.")]
    judge = ScriptedJudge()

    score_case(case, produced, judge)

    in_lane = [pair for pair in judge.claim_calls if not _is_cross_lane(pair, case)]
    assert all(pair.category == "denial-of-service" for pair in in_lane)


def _is_cross_lane(pair, case) -> bool:
    reference = next(
        reference
        for reference in case.claims_for("stride")
        if reference.claim == pair.reference_claim
    )
    return reference.category != "denial-of-service"


def test_assignment_is_one_to_one(case):
    # Two produced threats the judge calls equivalent to the *same* reference
    # must consume one reference between them; without this recall inflates and
    # stops meaning anything.
    reference = case.claims_for("stride")[0]
    produced = [
        threat_for(reference, 1, "Claim one."),
        threat_for(reference, 2, "Claim two."),
    ]
    judge = ScriptedJudge(
        [(reference.claim, "Claim one."), (reference.claim, "Claim two.")]
    )

    score = score_case(case, produced, judge)

    assert len(score.matched) == 1
    assert score.produced_count == 2
    assert len(score.adjudicated) == 1  # the loser is adjudicated, not discarded


def test_must_find_references_win_assignment_ties(case):
    must_find = next(ref for ref in case.claims_for("stride") if ref.must_find)
    expected = next(
        ref
        for ref in case.claims_for("stride")
        if not ref.must_find and ref.category == must_find.category
    )
    produced = [threat_for(must_find, 1, "Ambiguous claim.")]
    judge = ScriptedJudge(
        [(must_find.claim, "Ambiguous claim."), (expected.claim, "Ambiguous claim.")]
    )

    score = score_case(case, produced, judge)

    assert score.must_find_matched == 1


def test_unmatched_is_never_counted_as_a_false_positive(case):
    produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
    judge = ScriptedJudge(buckets={"S-01": "valid-unlisted"})

    score = score_case(case, produced, judge)

    assert score.bucket_counts == {
        "unsupported": 0,
        "valid-unlisted": 1,
        "noise": 0,
    }
    assert score.unsupported_rate == 0.0
    assert unlisted_for_promotion([score])[0]["claim"] == (
        "A grounded but unlisted claim."
    )


def test_unsupported_is_the_gating_bucket(case):
    produced = [produced_threat(1, "spoofing", "An attacker abuses a made-up service.")]
    judge = ScriptedJudge(buckets={"S-01": "unsupported"})

    score = score_case(case, produced, judge)

    assert score.bucket_counts["unsupported"] == 1
    assert score.unsupported_rate == 1.0


def test_needs_info_threats_bypass_adjudication(case):
    produced = [
        produced_threat(
            1,
            "spoofing",
            "Callback authentication is unverified, so forgery may be possible.",
            element_ids=["process:storefront-api"],
            verdict_status="needs-info",
        )
    ]
    judge = ScriptedJudge()

    score = score_case(case, produced, judge)

    assert score.needs_info_unmatched == ("S-01",)
    assert judge.adjudication_calls == []
    assert score.adjudicated == ()


def test_element_disagreement_is_scored_not_filtered(case):
    # A correct threat may cite the process where the corpus cited the flow at its
    # endpoint. That must still match, and show up as an element-accuracy miss
    # rather than a recall miss.
    reference = case.claims_for("stride")[0]
    produced = [
        produced_threat(
            1,
            reference.category,
            "Same action, different element.",
            element_ids=["store:orders-db"],
        )
    ]
    judge = ScriptedJudge([(reference.claim, "Same action, different element.")])

    score = score_case(case, produced, judge)

    assert len(score.matched) == 1
    assert score.matched[0].element_overlap is False
    assert score.element_accuracy == 0.0
    assert score.recall > 0.0


def test_misfiled_threat_is_a_lane_error_and_not_a_recall_hit(case):
    # Misfiled threats are rejected rather than recategorized, so the reference
    # stays missed while lane accuracy records the mistake.
    reference = next(
        ref for ref in case.claims_for("stride") if ref.category == "tampering"
    )
    produced = [produced_threat(1, "spoofing", "Filed in the wrong lane.")]
    judge = ScriptedJudge([(reference.claim, "Filed in the wrong lane.")])

    score = score_case(case, produced, judge)

    assert score.matched == ()
    assert len(score.lane_errors) == 1
    assert score.lane_errors[0].produced_category == "spoofing"
    assert score.lane_errors[0].reference_category == "tampering"
    assert score.lane_accuracy == 0.0
    assert judge.adjudication_calls == []  # already accounted for, not double-counted


def test_severity_calibration_is_arithmetic(case):
    reference = next(
        ref
        for ref in case.claims_for("stride")
        if ref.severity.likelihood == "high" and ref.severity.impact == "high"
    )
    produced = [
        produced_threat(
            1,
            reference.category,
            "Right threat, softer severity.",
            element_ids=reference.affected_element_ids,
            likelihood="low",
            impact="medium",
        )
    ]
    judge = ScriptedJudge([(reference.claim, "Right threat, softer severity.")])

    score = score_case(case, produced, judge)

    assert score.severity_confusion == {"critical->low": 1}
    assert score.severity_exact_rate == 0.0
    assert severity_axis_agreement(score.matched) == {"likelihood": 0.0, "impact": 0.0}


def test_recall_and_artifact_over_the_whole_labelled_set(case, labelled_pairs):
    """The full offline pass: every ``match`` fixture for the control case."""
    matches = [
        pair
        for pair in labelled_pairs
        if pair.case == case.id and pair.label == "match"
    ]
    claims_by_reference = {
        reference.claim: reference for reference in case.claims_for("stride")
    }
    produced = [
        threat_for(
            claims_by_reference[pair.reference_claim], index + 1, pair.candidate_claim
        )
        for index, pair in enumerate(matches)
    ]
    judge = ScriptedJudge(
        (pair.reference_claim, pair.candidate_claim) for pair in matches
    )

    score = score_case(case, produced, judge)

    # The fixtures sample the reference set rather than covering it, so the
    # expected number of hits is the number of *distinct* references they
    # label — two candidates against one reference still consume one.
    covered = {pair.reference_claim for pair in matches}
    assert len(score.matched) == len(covered)
    assert score.recall == pytest.approx(len(covered) / len(case.claims_for("stride")))
    assert score.must_find_recall > 0.0
    artifact = score.to_json()
    assert artifact["counts"]["produced"] == len(produced)
    assert len(artifact["rulings"]) == len(judge.claim_calls)
    assert all(ruling["rationale"] for ruling in artifact["rulings"])


def test_exemplar_delta_is_reported_near_minus_far(case):
    judge = ScriptedJudge()
    near = score_case(case, [], judge)
    far_case = load_case(CORPUS_DIR / "02-iot-fleet-telemetry")
    far = score_case(far_case, [], judge)

    delta = exemplar_delta([near, far])

    assert delta["delta"] == pytest.approx(delta["near_recall"] - delta["far_recall"])


def test_empty_production_scores_zero_without_crashing(case):
    score = score_case(case, [], ScriptedJudge())

    assert score.recall == 0.0
    assert score.must_find_recall == 0.0
    assert score.element_accuracy == 0.0
    assert score.lane_accuracy == 0.0
    assert len(score.missed) == len(case.claims_for("stride"))
