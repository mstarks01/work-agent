"""The scorer, offline, with zero provider calls — like the scorer itself.

Matching goes through a scripted stand-in so a test states an outcome
directly, and the standing of the unmatched comes from an in-memory vote
ledger. Under test: the lane prefilter, the one-to-one assignment, the
standing lookup, the severity arithmetic — the halves that must never drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.claims import Ground
from evals.harness.calibration import load_pairs
from evals.harness.content import structural
from evals.harness.fingerprint import components_for, version_for
from evals.harness.ledger import Ledger
from evals.harness.reference import load_case
from evals.harness.scorer import (
    candidate_claim,
    exemplar_delta,
    score_case,
    severity_axis_agreement,
    unlisted_for_promotion,
)
from tests.eval_factories import ScriptedMatcher, cast, produced_threat, threat_for

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"
CONTROL_CASE = CORPUS_DIR / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CONTROL_CASE)


@pytest.fixture(scope="module")
def labelled_pairs():
    return load_pairs()


@pytest.fixture()
def no_votes():
    return Ledger()


def vote_on(case, threat, verdict, reason=None, content=None):
    """One vote on exactly the fingerprint the scorer computes for ``threat``.

    ``content`` defaults to the threat's own words, which is the vote a
    reviewer cast on what they were shown. Passing another value is how a test
    says the finding has been rewritten since the vote.
    """
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    components = components_for(
        "stride",
        threat.category,
        tuple(threat.affected_element_ids),
        flows,
        verb=threat.verb,
    )
    return cast(
        components,
        case.id,
        verdict,
        voter="test-reviewer",
        content=content or structural(threat),
        reason=reason,
        version=version_for("stride"),
    )


def test_candidate_claim_is_the_title(case):
    threat = threat_for(
        case.claims_for("stride")[0], 1, "An attacker replays a session."
    )
    assert candidate_claim(threat) == "An attacker replays a session."


def test_matches_reference_via_recorded_labels(case, labelled_pairs, no_votes):
    # The fixtures define the expected matching behaviour: a produced threat
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
    matcher = ScriptedMatcher([(pair.reference_claim, pair.candidate_claim)])

    score = score_case(case, produced, matcher, no_votes)

    assert [entry.reference_index for entry in score.matched] == [reference_index]
    assert score.matched[0].element_overlap is True


def test_lane_prefilter_never_compares_across_lanes(case, no_votes):
    produced = [produced_threat(1, "denial-of-service", "A flood takes the API down.")]
    matcher = ScriptedMatcher()

    score_case(case, produced, matcher, no_votes)

    in_lane = [pair for pair in matcher.claim_calls if not _is_cross_lane(pair, case)]
    assert all(pair.category == "denial-of-service" for pair in in_lane)


def _is_cross_lane(pair, case) -> bool:
    reference = next(
        reference
        for reference in case.claims_for("stride")
        if reference.claim == pair.reference_claim
    )
    return reference.category != "denial-of-service"


def test_assignment_is_one_to_one(case, no_votes):
    # Two produced threats the matcher calls equivalent to the *same* reference
    # must consume one reference between them; without this recall inflates and
    # stops meaning anything.
    reference = case.claims_for("stride")[0]
    produced = [
        threat_for(reference, 1, "Claim one."),
        threat_for(reference, 2, "Claim two."),
    ]
    matcher = ScriptedMatcher(
        [(reference.claim, "Claim one."), (reference.claim, "Claim two.")]
    )

    score = score_case(case, produced, matcher, no_votes)

    assert len(score.matched) == 1
    assert score.produced_count == 2
    assert len(score.unlisted) == 1  # the loser keeps a standing, not discarded


def test_must_find_references_win_assignment_ties(case, no_votes):
    must_find = next(ref for ref in case.claims_for("stride") if ref.must_find)
    expected = next(
        ref
        for ref in case.claims_for("stride")
        if not ref.must_find and ref.category == must_find.category
    )
    produced = [threat_for(must_find, 1, "Ambiguous claim.")]
    matcher = ScriptedMatcher(
        [(must_find.claim, "Ambiguous claim."), (expected.claim, "Ambiguous claim.")]
    )

    score = score_case(case, produced, matcher, no_votes)

    assert score.must_find_matched == 1


def test_an_unvoted_unmatched_threat_is_visible_and_never_gates(case, no_votes):
    produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]

    score = score_case(case, produced, ScriptedMatcher(), no_votes)

    assert score.standing_counts == {
        "rejected": 0,
        "pooled": 0,
        "open": 0,
        "unvoted": 1,
        "stale": 0,
    }
    assert score.rejected_rate == 0.0
    assert score.unvoted_count == 1
    assert score.unlisted[0].fingerprint.startswith(f"v{version_for('stride')}:")
    assert unlisted_for_promotion([score]) == []


class TestTheRejectedRateCarriesItsDenominators:
    """A rate whose numerator comes from a subset of its denominator.

    ``rejected_rate`` divides by every produced finding, and only an unmatched,
    non-``needs-info``, in-lane finding citing a blessed element can ever raise
    it. So the number falls when a run produces more of what it cannot count,
    and read alone it looks like a false-positive rate. The three counts travel
    together now.
    """

    def test_a_matched_finding_is_produced_and_never_eligible(self, case, no_votes):
        listed = case.stride_claims()[0]
        produced = [
            produced_threat(1, listed.category, listed.claim),
            produced_threat(2, "spoofing", "A grounded but unlisted claim."),
        ]
        matcher = ScriptedMatcher([(listed.claim, listed.claim)])

        score = score_case(case, produced, matcher, no_votes)

        assert score.produced_count == 2
        assert len(score.matched) == 1
        assert score.eligible_count == 1, "the matched finding cannot be voted down"

    def test_nothing_reviewed_reads_as_zero_either_way(self, case, no_votes):
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]

        score = score_case(case, produced, ScriptedMatcher(), no_votes)

        assert score.eligible_count == 1
        assert score.reviewed_count == 0
        assert score.rejected_rate == 0.0
        assert score.rejected_rate_of_reviewed == 0.0, (
            "no reviewer answered, so there is no rate rather than a good one"
        )

    def test_the_reviewed_rate_reads_the_denominator_a_person_supplied(self, case):
        produced = [
            produced_threat(1, "spoofing", "An attacker abuses a made-up service."),
            produced_threat(
                2,
                "spoofing",
                "A second unlisted claim.",
                element_ids=("process:storefront-api",),
            ),
        ]
        votes = Ledger(
            [vote_on(case, produced[0], "down", reason="unsupported-by-the-model")]
        )

        score = score_case(case, produced, ScriptedMatcher(), votes)

        assert (score.eligible_count, score.reviewed_count) == (2, 1)
        assert score.rejected_rate == 0.5
        assert score.rejected_rate_of_reviewed == 1.0

    def test_the_artifact_carries_all_three(self, case, no_votes):
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]

        payload = score_case(case, produced, ScriptedMatcher(), no_votes).to_json()

        assert payload["counts"]["produced"] == 1
        assert payload["counts"]["eligible"] == 1
        assert payload["counts"]["reviewed"] == 0
        assert "rejected_rate_of_reviewed" in payload["metrics"]


def test_a_pooled_finding_feeds_promotion(case):
    produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
    votes = Ledger([vote_on(case, produced[0], "up")])

    score = score_case(case, produced, ScriptedMatcher(), votes)

    assert score.standing_counts["pooled"] == 1
    assert score.rejected_rate == 0.0
    promoted = unlisted_for_promotion([score])
    assert promoted[0]["claim"] == "A grounded but unlisted claim."
    assert promoted[0]["fingerprint"] == score.unlisted[0].fingerprint


class TestAStandingIsAboutTheArgumentAndNotTheKey:
    """An identity match is never on its own a human-validated finding.

    The 2026-09-09 audit rewrote every retained case 01 finding to assert the
    opposite of itself and the scorer still returned 12 matches. So a vote holds
    only against the argument it was cast on (ADR 0029).
    """

    @staticmethod
    def _reargued(threat):
        """The same topic, resting on a different ground."""
        return threat.model_copy(
            update={
                "grounds": [
                    Ground(
                        kind="unknown-attribute",
                        element_id="entity:customer",
                        attribute="authentication",
                    )
                ]
            }
        )

    def test_a_vote_on_this_argument_stands(self, case):
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
        votes = Ledger([vote_on(case, produced[0], "up")])

        score = score_case(case, produced, ScriptedMatcher(), votes)

        assert score.unlisted[0].standing == "pooled"

    def test_a_vote_on_an_earlier_argument_is_stale(self, case):
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
        votes = Ledger(
            [
                vote_on(
                    case,
                    produced[0],
                    "up",
                    content=structural(self._reargued(produced[0])),
                )
            ]
        )

        score = score_case(case, produced, ScriptedMatcher(), votes)

        assert score.unlisted[0].standing == "stale"
        assert score.standing_counts["stale"] == 1

    def test_a_stale_substance_rejection_does_not_gate(self, case):
        """It gates nothing, because the person judged another argument."""
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
        votes = Ledger(
            [
                vote_on(
                    case,
                    produced[0],
                    "down",
                    reason="not-a-threat",
                    content=structural(self._reargued(produced[0])),
                )
            ]
        )

        score = score_case(case, produced, ScriptedMatcher(), votes)

        assert score.unlisted[0].standing == "stale"
        assert score.rejected_rate == 0.0

    def test_a_stale_vote_does_not_feed_promotion(self, case):
        """Promotion writes this run's finding into the reference set, so a
        vote on another argument may not offer it."""
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
        votes = Ledger(
            [
                vote_on(
                    case,
                    produced[0],
                    "up",
                    content=structural(self._reargued(produced[0])),
                )
            ]
        )

        score = score_case(case, produced, ScriptedMatcher(), votes)

        assert unlisted_for_promotion([score]) == []

    def test_nobody_answering_is_still_unvoted(self, case):
        """`stale` and `unvoted` are different facts and stay apart."""
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]

        score = score_case(case, produced, ScriptedMatcher(), Ledger())

        assert score.unlisted[0].standing == "unvoted"

    def test_a_rewording_alone_leaves_the_standing_alive(self, case):
        """Prose moves every run, so binding a substance vote to it would
        expire every standing on every sweep."""
        produced = [produced_threat(1, "spoofing", "A grounded but unlisted claim.")]
        votes = Ledger([vote_on(case, produced[0], "up")])
        reworded = [produced[0].model_copy(update={"description": "Said again."})]

        score = score_case(case, reworded, ScriptedMatcher(), votes)

        assert score.unlisted[0].standing == "pooled"


def test_a_substance_down_vote_is_the_gating_standing(case):
    produced = [produced_threat(1, "spoofing", "An attacker abuses a made-up service.")]
    votes = Ledger(
        [vote_on(case, produced[0], "down", reason="unsupported-by-the-model")]
    )

    score = score_case(case, produced, ScriptedMatcher(), votes)

    assert score.standing_counts["rejected"] == 1
    assert score.rejected_rate == 1.0


def test_a_style_down_vote_pools_and_never_gates(case):
    # The reason split is the control for taste: a badly written finding is
    # still a real finding, so it joins the pool and moves no analysis number.
    produced = [produced_threat(1, "spoofing", "A real but poorly written claim.")]
    votes = Ledger([vote_on(case, produced[0], "down", reason="poorly-written")])

    score = score_case(case, produced, ScriptedMatcher(), votes)

    assert score.standing_counts["pooled"] == 1
    assert score.rejected_rate == 0.0


def test_a_rejection_wins_over_a_second_reviewers_pool_vote(case):
    # Reviewers disagree; the gate must not pass on the vote most favourable
    # to the tool. The disagreement is the queue's to surface, not scoring's
    # to settle.
    produced = [produced_threat(1, "spoofing", "A contested claim.")]
    votes = Ledger(
        [
            vote_on(case, produced[0], "up"),
            cast(
                components_for(
                    "stride",
                    produced[0].category,
                    tuple(produced[0].affected_element_ids),
                    {},
                    verb=produced[0].verb,
                ),
                case.id,
                "down",
                voter="second-reviewer",
                content=structural(produced[0]),
                reason="not-a-threat",
                version=version_for("stride"),
            ),
        ]
    )

    score = score_case(case, produced, ScriptedMatcher(), votes)

    assert score.standing_counts["rejected"] == 1


def test_an_unsure_vote_leaves_the_finding_open(case):
    produced = [produced_threat(1, "spoofing", "A claim the reviewer could not call.")]
    votes = Ledger([vote_on(case, produced[0], "unsure")])

    score = score_case(case, produced, ScriptedMatcher(), votes)

    assert score.standing_counts["open"] == 1
    assert score.rejected_rate == 0.0
    assert unlisted_for_promotion([score]) == []


def test_needs_info_threats_are_keyed_like_any_other(case, no_votes):
    """A conditional finding can be wrong too, so a person gets to say so.

    The verdict used to exempt it from the vote lookup, which left most of a
    report's unmatched output outside every number: 100 of 116 in the first
    Baseline.
    """
    produced = [
        produced_threat(
            1,
            "spoofing",
            "Callback authentication is unverified, so forgery may be possible.",
            element_ids=["process:storefront-api"],
            verdict_status="needs-info",
        )
    ]
    matcher = ScriptedMatcher()

    score = score_case(case, produced, matcher, no_votes)

    assert [entry.threat_id for entry in score.unlisted] == ["S-01"]
    assert score.standing_counts["unvoted"] == 1


def test_element_disagreement_is_scored_not_filtered(case, no_votes):
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
    matcher = ScriptedMatcher([(reference.claim, "Same action, different element.")])

    score = score_case(case, produced, matcher, no_votes)

    assert len(score.matched) == 1
    assert score.matched[0].element_overlap is False
    assert score.element_accuracy == 0.0
    assert score.recall > 0.0


def test_misfiled_threat_is_a_lane_error_and_not_a_recall_hit(case, no_votes):
    # Misfiled threats are rejected rather than recategorized, so the reference
    # stays missed while lane accuracy records the mistake.
    reference = next(
        ref for ref in case.claims_for("stride") if ref.category == "tampering"
    )
    produced = [produced_threat(1, "spoofing", "Filed in the wrong lane.")]
    matcher = ScriptedMatcher([(reference.claim, "Filed in the wrong lane.")])

    score = score_case(case, produced, matcher, no_votes)

    assert score.matched == ()
    assert len(score.lane_errors) == 1
    assert score.lane_errors[0].produced_category == "spoofing"
    assert score.lane_errors[0].reference_category == "tampering"
    assert score.lane_accuracy == 0.0
    assert score.unlisted == ()  # already accounted for, not keyed twice


def test_severity_calibration_is_arithmetic(case, no_votes):
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
    matcher = ScriptedMatcher([(reference.claim, "Right threat, softer severity.")])

    score = score_case(case, produced, matcher, no_votes)

    assert score.severity_confusion == {"critical->low": 1}
    assert score.severity_exact_rate == 0.0
    assert severity_axis_agreement(score.matched) == {"likelihood": 0.0, "impact": 0.0}


def test_recall_and_artifact_over_the_whole_labelled_set(
    case, labelled_pairs, no_votes
):
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
    matcher = ScriptedMatcher(
        (pair.reference_claim, pair.candidate_claim) for pair in matches
    )

    score = score_case(case, produced, matcher, no_votes)

    # The fixtures sample the reference set rather than covering it, so the
    # expected number of hits is the number of *distinct* references they
    # label — two candidates against one reference still consume one.
    covered = {pair.reference_claim for pair in matches}
    assert len(score.matched) == len(covered)
    assert score.recall == pytest.approx(len(covered) / len(case.claims_for("stride")))
    assert score.must_find_recall > 0.0
    artifact = score.to_json()
    assert artifact["counts"]["produced"] == len(produced)
    assert len(artifact["rulings"]) == len(matcher.claim_calls)
    assert all(ruling["rationale"] for ruling in artifact["rulings"])


def test_exemplar_delta_is_reported_near_minus_far(case):
    matcher = ScriptedMatcher()
    near = score_case(case, [], matcher, no_votes)
    far_case = load_case(CORPUS_DIR / "02-iot-fleet-telemetry")
    far = score_case(far_case, [], matcher, no_votes)

    delta = exemplar_delta([near, far])

    assert delta["delta"] == pytest.approx(delta["near_recall"] - delta["far_recall"])


def test_empty_production_scores_zero_without_crashing(case):
    score = score_case(case, [], ScriptedMatcher(), no_votes)

    assert score.recall == 0.0
    assert score.must_find_recall == 0.0
    assert score.element_accuracy == 0.0
    assert score.lane_accuracy == 0.0
    assert len(score.missed) == len(case.claims_for("stride"))


def test_a_threat_citing_an_id_the_blessed_model_lacks_is_foreign(case, no_votes):
    """#431: an end-to-end run's lanes cite the IDs a live extraction spelled.

    Such a threat can equal no reference, and a fingerprint over its IDs is a
    key no vote can land on, so it is counted apart from both ``unlisted`` and
    ``missed``.
    """
    produced = [
        produced_threat(
            1, "spoofing", "Spelled as extracted.", element_ids=("entity:shoppers",)
        ),
        produced_threat(
            2, "spoofing", "Spelled as blessed.", element_ids=("entity:shopper",)
        ),
    ]

    score = score_case(case, produced, ScriptedMatcher(), no_votes)

    assert score.foreign == ("S-01",)
    assert [entry.threat_id for entry in score.unlisted] == ["S-02"]
    assert score.to_json()["counts"]["foreign"] == 1
