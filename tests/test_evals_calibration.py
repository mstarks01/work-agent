"""Judge-vs-human agreement: the machinery, and the fixtures behind it.

The bar itself (>= 90%) can only be measured against the real judge, which
needs provider credentials and therefore belongs to the live CI job. What is
testable offline is everything around it: that the fixtures are loadable and
balanced, that agreement is computed the way the decision states, and that a
judge below the bar fails rather than passes with a note.
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
from tests.eval_factories import LabelReplayJudge


@pytest.fixture(scope="module")
def pairs():
    return load_pairs()


@pytest.fixture(scope="module")
def labels(pairs):
    return {
        (pair.reference_claim, pair.candidate_claim): pair.human_match
        for pair in pairs
    }


def test_fixture_set_is_large_and_balanced(pairs):
    # Agreement measured on a lopsided fixture set is not informative, and the
    # bar was sized against ~100 pairs.
    assert len(pairs) >= 100
    matches = sum(1 for pair in pairs if pair.human_match)
    assert 0.3 <= matches / len(pairs) <= 0.7


def test_a_judge_that_replays_the_labels_agrees_completely(pairs, labels):
    result = measure_agreement(LabelReplayJudge(labels), pairs)

    assert result.agreement == 1.0
    assert result.meets_bar
    assert result.false_matches == ()
    assert result.false_non_matches == ()


def test_agreement_below_the_bar_does_not_pass(pairs, labels):
    # Flip every fifth pair: 80% agreement, comfortably under the bar.
    flipped = {pair.candidate_claim for pair in pairs[::5]}
    judge = LabelReplayJudge(labels, flip=lambda pair: pair.candidate_claim in flipped)

    result = measure_agreement(judge, pairs)

    assert result.agreement < AGREEMENT_BAR
    assert not result.meets_bar


def test_disagreements_are_split_by_direction(pairs, labels):
    # A bare percentage says nothing about which distinction the judge misses:
    # false matches inflate recall, false non-matches deflate it.
    first = pairs[0]
    judge = LabelReplayJudge(
        labels, flip=lambda pair: pair.candidate_claim == first.candidate_claim
    )

    result = measure_agreement(judge, pairs)

    if first.human_match:
        assert len(result.false_non_matches) == 1
        assert result.false_matches == ()
    else:
        assert len(result.false_matches) == 1
        assert result.false_non_matches == ()


def test_result_serializes_every_disagreement(pairs, labels):
    judge = LabelReplayJudge(
        labels, flip=lambda pair: pair.candidate_claim == pairs[0].candidate_claim
    )

    payload = measure_agreement(judge, pairs).to_json()

    assert payload["bar"] == AGREEMENT_BAR
    disagreements = payload["false_matches"] + payload["false_non_matches"]
    assert len(disagreements) == 1
    assert disagreements[0]["human_note"]
    assert disagreements[0]["judge_rationale"]


def test_reference_claims_stay_attached_to_the_corpus(pairs):
    # build_pairs.py pulls reference claims from threats.json by index, so a
    # reworded reference cannot silently detach a fixture from what it labels.
    from pathlib import Path

    from evals.harness.reference import load_corpus

    corpus_dir = Path(__file__).resolve().parents[1] / "evals" / "corpus"
    claims_by_case = {
        case.id: {reference.claim for reference in case.references}
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
        measure_agreement(LabelReplayJudge({}), [])
