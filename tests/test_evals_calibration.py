"""Rule-vs-label agreement: the machinery, and the fixtures behind it.

The scoreboard prices the shipped identity rule against the recorded labels,
offline and free. These tests cover the machinery around the number: that the
fixtures are loadable and balanced, that agreement is computed the way the
decision states, that a refusal and an ``unclear`` label are each counted
rather than scored, and that a matcher below the bar fails rather than passes
with a note.

They also cover the merge direction, which the labels cannot answer: the
``no-match`` half carries no candidate elements, so ``measure_merges`` reads
the corpus's own distinct claims instead.
"""

from __future__ import annotations

import json

import pytest

from evals.harness.calibration import (
    AGREEMENT_BAR,
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

    assert result.unclear == 1
    assert result.total == len(decided)
    assert result.agreement == 1.0
    assert result.to_json()["unclear"] == 1


def test_no_shipped_pair_is_undecided_yet(pairs):
    """The disposition ships unused, and this records that rather than hiding it.

    Review sitting 01 returned four ``unclear`` answers and step 5 of
    ``evals/BLESSING.md`` now states the test that decides them, so every
    shipped label is binary. When a sitting records the first genuinely
    undecidable pair, this fails and the counts quoted in the guides move with
    it.
    """
    assert [pair for pair in pairs if not pair.is_scored] == []
    assert set(SCORED_LABELS) == {pair.label for pair in pairs}


def test_the_merge_direction_is_measured_over_distinct_reference_claims(pairs):
    """The direction the labels cannot answer, and why it reads the corpus.

    Every ``no-match`` fixture carries no candidate elements, so the rule
    refuses all of them and ``measure_agreement`` reports a structurally zero
    false-merge count. Reference claims carry the fields, so the merge question
    is asked of them instead — and every within-lane pair of them is a pair the
    corpus already calls two findings, so every merge is an error.
    """
    corpus = load_corpus("evals/corpus")
    matcher = SubsetVerbIdentity(_flows_by_case(corpus))

    result = measure_agreement(matcher, pairs)
    merges = measure_merges(matcher, corpus, "stride")

    assert result.false_matches == (), "candidate merges are unmeasurable (#511)"
    assert all(
        pair.candidate_element_ids is None for pair in pairs if not pair.label_match
    )
    assert 0 < len(merges.merges) < merges.within_lane_pairs
    assert merges.to_json()["merges"] == len(merges.merges)


def test_the_merge_direction_refuses_a_package_with_no_lane():
    """It raises for ASVS rather than inventing a lane, and names the issue.

    A package that keys on a catalog requirement reaches no within-lane
    question in this shape. Answering zero would read as "no collisions" when
    it means "nothing was asked".
    """
    corpus = load_corpus("evals/corpus")
    matcher = SubsetVerbIdentity(_flows_by_case(corpus))

    with pytest.raises(CalibrationError, match="#512"):
        measure_merges(matcher, corpus, "asvs")
