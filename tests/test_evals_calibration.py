"""Rule-vs-label agreement: the machinery, and the fixtures behind it.

The scoreboard prices the shipped identity rule against the recorded labels,
offline and free. These tests cover the machinery around the number: that the
fixtures are loadable and balanced, that agreement is computed the way the
decision states, that a refusal is counted rather than scored, and that a
matcher below the bar fails rather than passes with a note.
"""

from __future__ import annotations

import json

import pytest

from evals.harness.calibration import (
    AGREEMENT_BAR,
    CalibrationError,
    load_pairs,
    measure_agreement,
)
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.reference import load_corpus
from evals.harness.run import _flows_by_case
from tests.eval_factories import LabelReplayMatcher


@pytest.fixture(scope="module")
def pairs():
    return load_pairs()


@pytest.fixture(scope="module")
def labels(pairs):
    return {
        (pair.reference_claim, pair.candidate_claim): pair.label_match for pair in pairs
    }


def test_fixture_set_is_large_and_balanced(pairs):
    # Agreement measured on a lopsided fixture set is not informative, and the
    # bar was sized against ~100 pairs.
    assert len(pairs) >= 100
    matches = sum(1 for pair in pairs if pair.label_match)
    assert 0.3 <= matches / len(pairs) <= 0.7


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


def test_empty_fixture_set_is_refused():
    with pytest.raises(CalibrationError):
        measure_agreement(LabelReplayMatcher({}), [])


def test_the_shipped_rule_clears_the_bar_on_the_recorded_labels(pairs):
    """The number the retirement decision rests on, pinned where it can fail.

    92.5% over the 200 pairs the rule can read, with the 139 it refuses
    counted beside the bar rather than inside it. A rule change that drops
    below the bar fails here, offline, before any sweep runs with it.
    """
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus("evals/corpus")))

    result = measure_agreement(matcher, pairs)

    assert result.meets_bar
    assert result.refused == sum(
        1 for pair in pairs if pair.candidate_element_ids is None
    )
    assert result.total == len(pairs) - result.refused
    assert result.false_matches == ()


def test_a_refusal_is_reported_and_never_scored(pairs, labels):
    """A matcher that cannot read a pair must not buy accuracy with it."""
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus("evals/corpus")))

    result = measure_agreement(matcher, pairs)

    assert result.refused > 0
    assert result.to_json()["refused"] == result.refused
