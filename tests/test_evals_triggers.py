"""Candidate-trigger recall over the real corpus.

Credential-free and deterministic, so it gates on every PR: the rules and the
blessed models are all it reads.
"""

from pathlib import Path

import pytest

from evals.harness.reference import load_corpus
from evals.harness.triggers import case_trigger_recall, corpus_recall, summarize
from stride_service.report import STRIDE_CATEGORIES

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"

# The floor, not the target. Measured at 0.81 must-find / 0.78 overall across
# 224 references in 12 cases when the rule table landed; the gap to 1.0 is
# threats that turn on what a submitter *said* rather than on the model's
# shape, which have no structural trigger by construction and should not
# acquire one. The floor sits ~10 points below the measurement so a rule
# rewrite has room to trade one lead for a better one, and a collapse — a rule
# silently matching nothing after a schema change — still fails.
MUST_FIND_TRIGGER_FLOOR = 0.70
OVERALL_TRIGGER_FLOOR = 0.65


@pytest.fixture(scope="module")
def results():
    return corpus_recall(load_corpus(CORPUS))


def test_trigger_recall_holds_its_floor(results):
    totals = summarize(results)
    assert totals["must_find_recall"] >= MUST_FIND_TRIGGER_FLOOR
    assert totals["recall"] >= OVERALL_TRIGGER_FLOOR


def test_every_case_gets_some_structural_lead(results):
    """A case where nothing fires would mean the rules cannot read that shape."""
    for result in results:
        assert result.triggered > 0, result.case_id


def test_every_category_is_triggered_somewhere_in_the_corpus(results):
    """A lane no rule ever fires in is a lane the candidate layer skipped."""
    covered = {
        hit.category for result in results for hit in result.hits if hit.triggered
    }
    assert covered == set(STRIDE_CATEGORIES)


def test_a_miss_is_recorded_rather_than_hidden(results):
    """The metric names what it did not see; a silent drop would flatter it."""
    misses = [hit for result in results for hit in result.hits if not hit.triggered]
    assert misses
    assert all(hit.rule_ids == () for hit in misses)
    assert sum(result.total for result in results) == sum(
        len(result.hits) for result in results
    )


def test_scoring_is_stable_across_calls():
    case = load_corpus(CORPUS)[0]
    first = case_trigger_recall(case)
    second = case_trigger_recall(case)
    assert first == second
