"""Rule-vs-label agreement: the machinery, and the fixtures behind it.

The scoreboard prices the shipped identity rule against the recorded labels,
offline and free. These tests cover the machinery around the number: that the
fixtures are loadable and balanced, that agreement is computed the way the
decision states, that a refusal and an ``unclear`` label are each counted
rather than scored, and that a matcher below the bar fails rather than passes
with a note.

They also cover the merge direction over both scored candidate negatives and
the corpus's own distinct claims.
"""

from __future__ import annotations

import collections
import json

import pytest

from analysis_service.frameworks import PACKAGES
from evals.harness.calibration import (
    AGREEMENT_BAR,
    IDENTITY_VALIDATION,
    SCORED_LABELS,
    CalibrationError,
    load_pairs,
    measure_agreement,
    measure_merges,
)
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.reference import load_corpus
from evals.harness.run import _flows_by_case
from tests.eval_factories import LabelReplayMatcher


@pytest.fixture(scope="module")
def pairs():
    return load_pairs()


@pytest.fixture(scope="module")
def corpus_and_flows():
    corpus = load_corpus("evals/corpus")
    return corpus, _flows_by_case(corpus)


@pytest.fixture(scope="module")
def scored(pairs):
    """The pairs carrying an answer. ``label_match`` raises on the rest."""
    return [pair for pair in pairs if pair.is_scored]


@pytest.fixture(scope="module")
def labels(scored):
    return {
        (pair.reference_claim, pair.candidate_claim): pair.label_match
        for pair in scored
    }


def test_fixture_set_is_large_and_balanced(pairs, scored):
    # Agreement measured on a lopsided fixture set is not informative, and the
    # bar was sized against ~100 pairs. Balance is read over the scored half:
    # an undecided pair belongs to neither side.
    assert len(pairs) >= 100
    matches = sum(1 for pair in scored if pair.label_match)
    assert 0.3 <= matches / len(scored) <= 0.7


def test_a_matcher_that_replays_the_labels_agrees_completely(pairs, labels):
    result = measure_agreement(LabelReplayMatcher(labels), pairs)

    assert result.agreement == 1.0
    assert result.meets_bar
    assert result.false_matches == ()
    assert result.false_non_matches == ()


def test_agreement_below_the_bar_does_not_pass(pairs, labels):
    # Flip every fifth pair: 80% agreement, comfortably under the bar.
    flipped = {pair.candidate_claim for pair in pairs[::5]}
    matcher = LabelReplayMatcher(
        labels, flip=lambda pair: pair.candidate_claim in flipped
    )

    result = measure_agreement(matcher, pairs)

    assert result.agreement < AGREEMENT_BAR
    assert not result.meets_bar


def test_disagreements_are_split_by_direction(pairs, labels):
    # A bare percentage says nothing about which distinction the rule misses:
    # false matches inflate recall, false non-matches deflate it.
    first = pairs[0]
    matcher = LabelReplayMatcher(
        labels, flip=lambda pair: pair.candidate_claim == first.candidate_claim
    )

    result = measure_agreement(matcher, pairs)

    if first.label_match:
        assert len(result.false_non_matches) == 1
        assert result.false_matches == ()
    else:
        assert len(result.false_matches) == 1
        assert result.false_non_matches == ()


def test_result_serializes_every_disagreement(pairs, labels):
    matcher = LabelReplayMatcher(
        labels, flip=lambda pair: pair.candidate_claim == pairs[0].candidate_claim
    )

    payload = measure_agreement(matcher, pairs).to_json()

    assert payload["bar"] == AGREEMENT_BAR
    disagreements = payload["false_matches"] + payload["false_non_matches"]
    assert len(disagreements) == 1
    assert disagreements[0]["label_note"]
    assert disagreements[0]["matcher_rationale"]


def test_reference_claims_stay_attached_to_the_corpus(pairs):
    # build_pairs.py pulls reference claims from claims/stride.json by index, so a
    # reworded reference cannot silently detach a fixture from what it labels.
    from pathlib import Path

    from evals.harness.reference import load_corpus

    corpus_dir = Path(__file__).resolve().parents[1] / "evals" / "corpus"
    claims_by_case = {
        case.id: {reference.claim for reference in case.claims_for("stride")}
        for case in load_corpus(corpus_dir)
    }
    for pair in pairs:
        assert pair.reference_claim in claims_by_case[pair.case]


def test_malformed_fixtures_fail_closed(tmp_path):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([{"case": "01", "label": "match"}]))

    with pytest.raises(CalibrationError, match="malformed"):
        load_pairs(path)


def test_unknown_label_fails_closed(tmp_path, pairs):
    path = tmp_path / "pairs.json"
    entry = {
        "case": pairs[0].case,
        "category": pairs[0].category,
        "reference_claim": pairs[0].reference_claim,
        "candidate_claim": pairs[0].candidate_claim,
        "label": "probably",
        "note": "",
    }
    path.write_text(json.dumps([entry]))

    with pytest.raises(CalibrationError, match="label"):
        load_pairs(path)


def test_unknown_annotation_fails_closed(tmp_path, pairs):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([_entry(pairs[0], annotations=["interesting"])]))

    with pytest.raises(CalibrationError, match="annotations"):
        load_pairs(path)


def test_empty_fixture_set_is_refused():
    with pytest.raises(CalibrationError):
        measure_agreement(LabelReplayMatcher({}), [])


def test_the_shipped_rule_clears_the_bar_on_the_recorded_labels(pairs):
    """The admission gate, pinned where it can fail.

    94.5% over 311 scored pairs, with 28 set aside and no matcher refusals. A rule
    change that drops below the bar fails here, offline, before any sweep runs
    with it. The bar is not the measurement — the split and merge counts in
    ``tests/test_evals_identity.py`` are, and they bind harder.
    """
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus("evals/corpus")))

    result = measure_agreement(matcher, pairs)

    assert result.meets_bar
    assert result.refused == sum(
        1 for pair in pairs if pair.is_scored and pair.candidate_element_ids is None
    )
    assert result.total == len(pairs) - result.refused - sum(result.set_aside.values())


def test_the_shipped_fixtures_cause_no_matcher_refusals(pairs):
    """Non-comparable candidates are disposed before the matcher is called."""
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus("evals/corpus")))

    result = measure_agreement(matcher, pairs)

    assert result.refused == 0


def test_a_refusal_is_reported_and_never_scored(tmp_path, pairs):
    """A matcher that cannot read a scored pair must not buy accuracy with it."""
    path = tmp_path / "pairs.json"
    path.write_text(
        json.dumps(
            [
                _entry(
                    pairs[0],
                    candidate_element_ids=None,
                    candidate_verb=None,
                    label="match",
                )
            ]
        )
    )
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus("evals/corpus")))

    result = measure_agreement(matcher, load_pairs(path))

    assert result.refused == 1
    assert result.total == 0


def _entry(pair, **overrides):
    """One fixture pair as ``pairs.json`` spells it, for a tmp fixture file."""
    entry = {
        "case": pair.case,
        "category": pair.category,
        "reference_claim": pair.reference_claim,
        "candidate_claim": pair.candidate_claim,
        "reference_element_ids": list(pair.reference_element_ids),
        "candidate_element_ids": (
            None
            if pair.candidate_element_ids is None
            else list(pair.candidate_element_ids)
        ),
        "reference_verb": pair.reference_verb,
        "candidate_verb": pair.candidate_verb,
        "label": pair.label,
        "note": pair.note,
        "annotations": list(pair.annotations),
    }
    return entry | overrides


def test_an_unclear_label_loads(tmp_path, pairs):
    """A reader who cannot decide a pair records that, and the loader takes it.

    Before this the loader failed closed on any label but the two, which forced
    an undecidable pair into a binary the evidence does not support.
    """
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([_entry(pairs[0], label="unclear")]))

    loaded = load_pairs(path)

    assert loaded[0].label == "unclear"
    assert not loaded[0].is_scored


def test_an_unclear_pair_carries_no_answer_to_compare(tmp_path, pairs):
    """``label_match`` raises rather than reading "nobody knows" as no-match."""
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([_entry(pairs[0], label="unclear")]))
    (unclear,) = load_pairs(path)

    with pytest.raises(CalibrationError, match="no answer"):
        _ = unclear.label_match


def test_an_unclear_pair_is_counted_and_never_scored(tmp_path, pairs, labels):
    """It leaves the denominator, so it cannot move the agreement either way.

    The same treatment a refusal gets, counted apart: a refusal is the rule
    declining to read a pair, and ``unclear`` is the label declining to decide
    one.
    """
    decided = [pair for pair in pairs if pair.is_scored][:10]
    entries = [_entry(pair) for pair in decided]
    entries.append(_entry(decided[0], label="unclear", candidate_claim="undecided"))
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(entries))

    result = measure_agreement(LabelReplayMatcher(labels), load_pairs(path))

    assert result.set_aside == {"unclear": 1}
    assert result.total == len(decided)
    assert result.agreement == 1.0
    assert result.to_json()["set_aside"] == {"unclear": 1}


def test_an_invalid_claim_is_counted_and_never_scored(tmp_path, pairs, labels):
    decided = [pair for pair in pairs if pair.is_scored][:10]
    entries = [_entry(pair) for pair in decided]
    entries.append(
        _entry(decided[0], label="invalid-claim", candidate_claim="not a threat")
    )
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(entries))

    result = measure_agreement(LabelReplayMatcher(labels), load_pairs(path))

    assert result.set_aside == {"invalid-claim": 1}
    assert result.total == len(decided)
    assert result.agreement == 1.0


def test_every_set_aside_pair_says_which_axis_decided_it(pairs):
    """Nothing leaves the score without a disposition that names the reason.

    The counts are pinned so a disposition cannot drift into or out of the
    identity score unnoticed.
    """
    by_label = collections.Counter(pair.label for pair in pairs)

    assert by_label["unclear"] == 2
    assert by_label["unsupported"] == 25
    assert by_label["invalid-claim"] == 1
    assert set(by_label) - set(SCORED_LABELS) == {
        "unclear",
        "unsupported",
        "invalid-claim",
    }


def test_annotations_are_diagnostic_only(pairs):
    """Secondary observations never decide whether a pair enters the score."""
    for pair in pairs:
        assert pair.is_scored == (pair.label in SCORED_LABELS)


def test_recorded_annotation_counts_are_pinned(pairs):
    annotations = collections.Counter(
        annotation for pair in pairs for annotation in pair.annotations
    )

    assert annotations == {"mixed": 13, "misclassified-lane": 7}


def test_the_merge_direction_is_measured_over_distinct_reference_claims(pairs):
    """The direction the labels cannot answer, and why it reads the corpus.

    Both are real measurements since #511 assigned the ``no-match`` half its
    elements and verbs. They read different populations and neither replaces
    the other: the candidate pairs resemble what a live run emits, and every
    within-lane pair of reference claims is a pair the corpus already calls two
    findings, so every merge there is an error by construction.
    """
    corpus = load_corpus("evals/corpus")
    matcher = SubsetVerbIdentity(_flows_by_case(corpus))

    result = measure_agreement(matcher, pairs)
    merges = measure_merges(corpus, "stride", _flows_by_case(corpus))

    assert 0 < len(result.false_matches) < len(pairs), (
        "the candidate merge direction is measurable since #511; a zero here"
        " means the negatives lost their element and verb assignments"
    )
    assert 0 < len(merges.merges) < merges.within_lane_pairs
    assert merges.to_json()["merges"] == len(merges.merges)


def test_the_merge_direction_refuses_a_package_with_no_contract():
    """It raises for a package with no entry rather than answering zero.

    "Nothing was asked" must never read as "no collisions". Every package in
    ``PACKAGES`` has an entry — ``tests/test_framework_neutrality.py`` is what
    says so — and this covers the one that has not been written yet.
    """
    corpus = load_corpus("evals/corpus")

    with pytest.raises(CalibrationError, match="declares no identity validation"):
        measure_merges(corpus, "nomogram", _flows_by_case(corpus))


#: Each package's collision count over this corpus, asserted exactly rather
#: than against a floor. A corpus case or a re-cut element set moves them, and
#: the author who moves them has to say what the new number is.
#:
#: ASVS's denominator is small because the chapter separates almost everything
#: first: 448 within-case pairs of reference requirements, of which 20 share a
#: chapter, of which none shares a requirement identifier. That is the shape a
#: catalog claim set should have, and a rise in the third column would mean two
#: rulings on one requirement in one place — one vote answering for both.
PACKAGE_COLLISIONS = {
    "stride": {"comparable_pairs": 287, "collisions": 3},
    "asvs": {"comparable_pairs": 20, "collisions": 0},
}


def test_each_package_collision_count_is_pinned(corpus_and_flows):
    """Recorded, not thresholded, for every package rather than for STRIDE."""
    corpus, flows = corpus_and_flows

    measured = {
        package: {
            "comparable_pairs": measure_merges(
                corpus, package, flows
            ).within_lane_pairs,
            "collisions": len(measure_merges(corpus, package, flows).merges),
        }
        for package in PACKAGES
    }

    assert measured == PACKAGE_COLLISIONS, (
        f"the collision counts moved to {measured}. Update PACKAGE_COLLISIONS"
        " and re-quote them in docs/agents/claim-identity.md."
    )


def test_every_package_answers_the_collision_question(corpus_and_flows):
    """Runs every way rather than outward from STRIDE.

    A package cannot ship without a collision measurement: the identity a
    claim composes differs per package, and the cost of keying two distinct
    claims alike does not. Any collision must be recorded in that package's
    own exception table with a reason.
    """
    corpus, flows = corpus_and_flows

    for package in PACKAGES:
        contract = IDENTITY_VALIDATION[package]
        result = measure_merges(corpus, package, flows)

        assert result.within_lane_pairs > 0, (
            f"{package} has no comparable reference pairs in this corpus, so"
            " its collision count says nothing"
        )
        unrecorded = [
            merge
            for merge in result.merges
            if (merge.case, merge.lane) not in contract.recorded_collisions
        ]
        assert not unrecorded, (
            f"{package} collides on {[(m.case, m.lane) for m in unrecorded]} and"
            " the pairs are not recorded. Add each with its reason, or separate"
            " them."
        )
