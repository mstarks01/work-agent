"""The orders the early-policy comparison reads, and how it scores them."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import question_set
from analysis_service.report import Report
from evals.harness import audit, early_policies
from evals.harness.early_policies import POLICIES, _spend, needs_of, readings
from tests.factories import valid_model

BOTH = {"stride": {}, "asvs": {"level": 2}}


def keys_of(charges):
    """Every question the charges answer, in order."""
    return [key for charge in charges for key in sorted(charge.keys)]


BASELINE = next(
    Path("evals/baselines").glob("6bff717-*/*.reports/01-payments-checkout.report.json")
)


def test_the_shipped_order_starts_with_the_pause_s_first_list():
    first = question_set(
        valid_model(),
        None,
        BOTH,
        [],
        waiting=True,
        answered=AnswerSet(),
        final=False,
        shown=[],
    ).early
    order = keys_of(POLICIES["shipped-list"](valid_model(), BOTH))
    assert order[: len(first)] == [q.key for q in first]


@pytest.mark.parametrize("policy", ["framework-merge", "grouped-merge"])
class TestTheMerge:
    def test_a_shared_question_is_asked_once(self, policy):
        keys = keys_of(POLICIES[policy](valid_model(), BOTH))
        assert len(keys) == len(set(keys))

    def test_each_framework_keeps_its_own_order(self, policy):
        """A question another framework also lists can come earlier, because
        that framework asked it; every other question keeps its place."""
        order = keys_of(POLICIES[policy](valid_model(), BOTH))
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


def test_a_submitter_stops_before_the_choice_that_passes_the_budget():
    order = POLICIES["framework-merge"](valid_model(), BOTH)
    for budget in (1, 5, 10):
        answered, spent = _spend(order, budget)
        assert spent <= budget
        taken, total = set(), 0
        for charge in order:
            if total + charge.choices > budget:
                break
            total += charge.choices
            taken |= charge.keys
        assert (answered, spent) == (taken, total)


def test_a_group_is_one_charge_for_its_rows_and_a_capability_its_own():
    """``group-question`` asks each group of a list once (ADR 0081): one charge,
    at its widest row's parts, answers every row of the run."""
    from itertools import groupby

    from analysis_service.open_facts import CAPABILITY_GROUP

    shown = early_policies._lists(valid_model(), BOTH)[0]
    charges = POLICIES["group-question"](valid_model(), BOTH)
    expected = []
    for group, rows in groupby(shown, key=lambda q: q.group):
        run = list(rows)
        if group == CAPABILITY_GROUP:
            expected += [(q.decisions, frozenset({q.key})) for q in run]
        else:
            expected.append(
                (max(q.decisions for q in run), frozenset(q.key for q in run))
            )
    assert [(c.choices, c.keys) for c in charges[: len(expected)]] == expected
    assert any(len(c.keys) > 1 for c in charges), "the fixture shares a group"


@pytest.mark.parametrize("name", ["none", "third", "all"])
def test_no_to_the_rest_answers_every_capability_left_in_one_choice(name):
    """``no-to-the-rest`` keeps each capability question at its place: the
    "yes" answers come one at a time, and the first question after them is
    one choice answering every capability question of the list left."""
    from analysis_service.fact_answers import key_ref
    from analysis_service.open_facts import CAPABILITY_GROUP

    shown = early_policies._lists(valid_model(), BOTH)[0]
    capabilities = [q for q in shown if q.group == CAPABILITY_GROUP]
    assert len(capabilities) > 1, "the fixture asks several capabilities"
    yes = round(len(capabilities) * early_policies.YES_SHARES[name])
    charges = POLICIES[f"no-to-the-rest-{name}"](valid_model(), BOTH)
    first = {q.key for q in capabilities}
    asked = [c for c in charges if c.keys & first]
    rest = [frozenset(q.key for q in capabilities[yes:])] if capabilities[yes:] else []
    assert [(c.choices, c.keys) for c in asked] == [
        (q.decisions, frozenset({q.key})) for q in capabilities[:yes]
    ] + [(1, keys) for keys in rest]

    def fields(found):
        return [c for c in found if not any(key_ref(k).capability for k in c.keys)]

    assert fields(charges) == fields(POLICIES["group-question"](valid_model(), BOTH))


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
        asked = set(keys_of(build(report.system_model, {"stride": {}})))
        expected = sum(1 for _, keys in needs.findings.values() if keys <= asked)
        assert whole[policy].completed == expected
    by_budget = [one.completed for one in found if one.policy == "shipped-list"]
    assert by_budget == sorted(by_budget)


def test_paired_never_opens_a_holdout_case(tmp_path, monkeypatch):
    """Both instruments pair reports through ``paired``. A holdout case's
    file is skipped unopened, so one that is not JSON raises nothing, and a
    case with one framework's reports pairs with nothing."""
    # An empty ledger exhausts no case, so every flagged case is held out.
    (tmp_path / "ledger").mkdir()
    monkeypatch.setattr(audit, "EXPERIMENTS_DIR", tmp_path / "ledger")
    (tmp_path / BASELINE.name).write_text(
        BASELINE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "14-loyalty-oauth-platform.report.json").write_text("not JSON")
    assert early_policies.paired(tmp_path) == []
    (tmp_path / "01-payments-checkout.report.json").write_text("not JSON")
    with pytest.raises(ValueError):
        early_policies.paired(tmp_path)
