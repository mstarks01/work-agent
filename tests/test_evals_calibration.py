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
    compare_judges,
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
        (pair.reference_claim, pair.candidate_claim): pair.human_match for pair in pairs
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


class TestJudgeComparison:
    """Several candidate judges over the same pairs.

    The selection exercise #116 asks for: a production judge chosen on measured
    agreement rather than on which platform the project started on, plus the
    robustness check that asks whether a conclusion survives changing the
    judge's vendor.

    Everything here runs offline against scripted judges replaying the recorded
    labels. What cannot be tested offline is the same thing that has always been
    untestable offline — the agreement of a *real* judge, which needs
    credentials. The machinery around it is what these cover.
    """

    def test_a_single_candidate_is_measured_exactly_as_before(self, pairs, labels):
        # A comparison of one must not be a different measurement from the
        # single-judge path, or the two would disagree about the same judge.
        comparison = compare_judges({"replay": LabelReplayJudge(labels)}, pairs)
        alone = measure_agreement(LabelReplayJudge(labels), pairs)

        assert comparison.candidates[0].result.agreement == alone.agreement
        assert comparison.best.label == "replay"

    def test_candidates_are_ranked_by_measured_agreement(self, pairs, labels):
        """The whole point: the judge is selected on a number, not on history."""
        flipped = {pair.candidate_claim for pair in pairs[::5]}
        comparison = compare_judges(
            {
                "weak": LabelReplayJudge(
                    labels, flip=lambda pair: pair.candidate_claim in flipped
                ),
                "strong": LabelReplayJudge(labels),
            },
            pairs,
        )

        assert comparison.best.label == "strong"
        assert comparison.meets_bar

    def test_the_comparison_survives_one_candidate_failing_the_bar(self, pairs, labels):
        # A candidate below the bar is that candidate's problem. The exercise
        # only fails when there is no judge left to select.
        flipped = {pair.candidate_claim for pair in pairs[::5]}
        comparison = compare_judges(
            {
                "weak": LabelReplayJudge(
                    labels, flip=lambda pair: pair.candidate_claim in flipped
                ),
                "strong": LabelReplayJudge(labels),
            },
            pairs,
        )

        assert not comparison.candidates[0].result.meets_bar
        assert comparison.meets_bar

    def test_every_candidate_below_the_bar_fails_the_comparison(self, pairs, labels):
        flipped = {pair.candidate_claim for pair in pairs[::3]}
        judge = LabelReplayJudge(
            labels, flip=lambda pair: pair.candidate_claim in flipped
        )
        comparison = compare_judges({"a": judge, "b": judge}, pairs)

        assert not comparison.meets_bar

    def test_judge_agreement_is_not_implied_by_human_agreement(self, pairs, labels):
        """The measurement a per-judge accuracy cannot produce.

        Two judges flipping *disjoint* pairs score identically against the
        human and disagree with each other on every pair either got wrong. That
        is precisely the case where "model A beats model B" can turn over on the
        judge's vendor, and a report of two agreement percentages would show
        nothing at all.
        """
        odd = {pair.candidate_claim for pair in pairs[1::10]}
        even = {pair.candidate_claim for pair in pairs[0::10]}
        comparison = compare_judges(
            {
                "judge-a": LabelReplayJudge(
                    labels, flip=lambda pair: pair.candidate_claim in odd
                ),
                "judge-b": LabelReplayJudge(
                    labels, flip=lambda pair: pair.candidate_claim in even
                ),
            },
            pairs,
        )

        a, b = comparison.candidates
        assert a.result.agreement == pytest.approx(b.result.agreement)
        assert comparison.agreement_between("judge-a", "judge-b") < 1.0

    def test_identical_judges_agree_completely_and_diverge_nowhere(self, pairs, labels):
        comparison = compare_judges(
            {"a": LabelReplayJudge(labels), "b": LabelReplayJudge(labels)}, pairs
        )

        assert comparison.agreement_between("a", "b") == 1.0
        assert comparison.divergences() == ()

    def test_divergences_name_the_pair_and_every_judges_ruling(self, pairs, labels):
        # Kept whole rather than counted: which claims the judges read
        # differently is the actionable half.
        first = pairs[0]
        comparison = compare_judges(
            {
                "agrees": LabelReplayJudge(labels),
                "differs": LabelReplayJudge(
                    labels,
                    flip=lambda pair: pair.candidate_claim == first.candidate_claim,
                ),
            },
            pairs,
        )

        divergences = comparison.divergences()
        assert len(divergences) == 1
        entry = divergences[0]
        assert entry["reference_claim"] == first.reference_claim
        assert entry["human"] == first.label
        assert set(entry["judges"]) == {"agrees", "differs"}
        assert entry["judges"]["agrees"] != entry["judges"]["differs"]

    def test_the_report_serialises_pairwise_agreement_for_every_combination(
        self, pairs, labels
    ):
        comparison = compare_judges(
            {name: LabelReplayJudge(labels) for name in ("a", "b", "c")}, pairs
        )

        payload = json.loads(json.dumps(comparison.to_json()))
        assert set(payload["pairwise_agreement"]) == {
            "a vs b",
            "a vs c",
            "b vs c",
        }
        assert payload["best"] in {"a", "b", "c"}
        assert payload["divergences"] == []

    def test_comparing_no_judges_is_an_error(self, pairs):
        with pytest.raises(CalibrationError, match="no judges"):
            compare_judges({}, pairs)

    def test_an_unknown_label_is_an_error(self, pairs, labels):
        comparison = compare_judges({"a": LabelReplayJudge(labels)}, pairs)
        with pytest.raises(CalibrationError, match="no candidate"):
            comparison.agreement_between("a", "nope")
