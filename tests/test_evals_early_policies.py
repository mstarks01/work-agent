"""The orders the early-policy comparison reads, and how it scores them."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.answer_round import question_set
from analysis_service.early_questions import early_questions
from analysis_service.report import Report
from evals.harness import early_policies
from evals.harness.early_policies import POLICIES, _prefix, needs_of, readings
from tests.factories import valid_model

BOTH = {"stride": {}, "asvs": {"level": 2}}

BASELINE = next(
    Path("evals/baselines").glob("6bff717-*/*.reports/01-payments-checkout.report.json")
)


def test_the_shipped_order_starts_with_the_pause_s_first_round():
    first = question_set(
        valid_model(),
        None,
        BOTH,
        [],
        waiting=True,
        answered=[],
        answered_links=[],
        final=False,
        shown=[],
    ).early
    order = POLICIES["shipped-rounds"](valid_model(), BOTH)
    assert order[: len(first)] == list(first)


@pytest.mark.parametrize("policy", ["framework-merge", "grouped-merge"])
class TestTheMerge:
    def test_a_shared_question_is_asked_once(self, policy):
        keys = [q.key for q in POLICIES[policy](valid_model(), BOTH)]
        assert len(keys) == len(set(keys))

    def test_each_framework_keeps_its_own_order(self, policy):
        """A question another framework also lists can come earlier, because
        that framework asked it; every other question keeps its place."""
        order = [q.key for q in POLICIES[policy](valid_model(), BOTH)]
        lists = early_policies._own_lists(valid_model(), BOTH)
        for name, listed in lists.items():
            others = {
                q.key for other, rest in lists.items() if other != name for q in rest
            }
            own = [q.key for q in listed if q.key not in others]
            assert [key for key in order if key in own] == own, name

    def test_the_selection_order_changes_nothing(self, policy):
        backward = dict(reversed(BOTH.items()))
        assert POLICIES[policy](valid_model(), BOTH) == POLICIES[policy](
            valid_model(), backward
        )


def test_an_owner_stops_before_the_choice_that_passes_the_budget():
    order = POLICIES["framework-merge"](valid_model(), BOTH)
    for budget in (1, 5, 10):
        answered = _prefix(order, budget)
        spent = sum(q.decisions for q in order if q.key in answered)
        assert spent <= budget
        assert answered == {q.key for q in order[: len(answered)]}


def test_a_finding_completes_only_when_every_fact_it_waits_on_is_answered():
    report = Report.model_validate_json(BASELINE.read_text(encoding="utf-8"))
    needs = needs_of(report)
    assert needs.findings
    found = readings("01", str(BASELINE), report, {"alone": {"stride": {}}})
    whole = {
        one.policy: one
        for one in found
        if one.budget is None and one.selected == "alone"
    }
    for policy, build in POLICIES.items():
        asked = {q.key for q in build(report.system_model, {"stride": {}})}
        expected = sum(1 for _, keys in needs.findings.values() if keys <= asked)
        assert whole[policy].completed == expected
    by_budget = [one.completed for one in found if one.policy == "shipped-rounds"]
    assert by_budget == sorted(by_budget)


@pytest.mark.parametrize("frameworks", [{"stride": {}}, BOTH])
def test_the_round_builder_given_the_shipped_list_gives_the_shipped_order(frameworks):
    """The per-choice order differs from the shipped one only in its list."""
    listed = early_questions(valid_model(), frameworks, None)
    assert early_policies._rounds_from(listed) == POLICIES["shipped-rounds"](
        valid_model(), frameworks
    )


def test_a_field_question_ranks_by_its_score_per_choice():
    order = POLICIES["per-choice-rounds"](valid_model(), {"stride": {}})
    first = next(q for q in order if q.kind != "capability")
    best = max(
        (q for q in order if q.kind != "capability"),
        key=lambda q: q.score / q.decisions,
    )
    assert first.score / first.decisions == best.score / best.decisions
