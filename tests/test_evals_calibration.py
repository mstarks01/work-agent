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

import collections
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
    """The admission gate, pinned where it can fail.

    93.7% over the 315 scored pairs the rule can read, with the 9 it refuses
    and the 15 set aside counted beside the bar rather than inside it. A rule
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

    assert result.set_aside == {"unclear": 1}
    assert result.total == len(decided)
    assert result.agreement == 1.0
    assert result.to_json()["set_aside"] == {"unclear": 1}


def test_every_set_aside_pair_says_which_axis_decided_it(pairs):
    """Nothing leaves the score without a disposition that names the reason.

    ``unclear`` ships unused: review sitting 01 returned four and step 5 of
    ``evals/BLESSING.md`` now states the test that decides them, so every
    reader-undecided pair resolved to a binary label.

    ``unsupported`` is the axis split #511 exposed. Those candidates name the
    same place and the same action as their reference and are still not a
    match, because they assert a fact the model does not hold — which no
    comparison of elements and verbs can reach. The count is pinned so it
    cannot drift into a place a rule could quietly hide behind.
    """
    by_label = collections.Counter(pair.label for pair in pairs)

    assert by_label["unclear"] == 0
    assert by_label["unsupported"] == 15
    assert set(by_label) - set(SCORED_LABELS) == {"unsupported"}


def test_the_unsupported_fixtures_are_all_invisible_to_the_rule(pairs):
    """Each one names one place and one action with its reference.

    That is what makes it a groundedness fixture rather than an identity one,
    and it is the property the disposition was assigned on. A pair the rule
    *can* separate does not belong here — it would be a hard negative the score
    should keep — so this fails if one is ever mislabelled that way.
    """
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus("evals/corpus")))

    for pair in pairs:
        if pair.label != "unsupported":
            continue
        ruling = matcher.equivalent(pair.to_claim_pair())
        assert ruling.match, (
            f"{pair.case}: {pair.candidate_claim!r} is labelled unsupported but"
            f" the rule separates it ({ruling.rationale}). Label it no-match:"
            " the identity rule can decide it, so the score should keep it."
        )


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
    merges = measure_merges(matcher, corpus, "stride")

    assert 0 < len(result.false_matches) < len(pairs), (
        "the candidate merge direction is measurable since #511; a zero here"
        " means the negatives lost their element and verb assignments"
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
