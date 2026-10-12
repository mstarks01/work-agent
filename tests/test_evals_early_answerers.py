"""The early-answerers walk ends, reads prefixes, and tells the two policies apart."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.report import Report
from evals.harness import early_answerers
from evals.harness.early_answerers import ANSWERERS, BUDGETS, POLICIES, readings, walk
from tests.factories import valid_model

LEVEL_2 = {"asvs": {"level": 2}}
BOTH = {"stride": {}, "asvs": {"level": 2}}

BASELINE = next(
    Path("evals/baselines").glob("6bff717-*/*.reports/01-payments-checkout.report.json")
)


@pytest.mark.parametrize("who", sorted(ANSWERERS))
@pytest.mark.parametrize("effects", [True, False], ids=["effects", "no-effects"])
def test_a_walk_spends_at_most_its_budget_one_question_a_save(who, effects):
    walked = walk(valid_model(), LEVEL_2, ANSWERERS[who], effects=effects, budget=10)
    assert walked.steps, "a control: the list asks something"
    assert walked.saves == len(walked.steps) or walked.ended
    spent = [step.spent for step in walked.steps]
    assert spent == sorted(spent) and spent[-1] >= 10 or walked.ended
    assert (
        len(walked.answers(5)) <= len(walked.answers(10)) <= len(walked.answers(None))
    )
    assert len({step.key for step in walked.steps}) == len(walked.steps), (
        "one save each"
    )


def test_the_two_policies_agree_on_the_order_up_to_ties():
    """The shipped order is the band-and-units order; the effects break
    ties only. So both policies answer the same questions in their first
    six, and may differ only within a tie (ADR 0078)."""
    orders = {
        name: [
            step.key
            for step in walk(
                valid_model(), LEVEL_2, ANSWERERS["unknown"], effects=on, budget=6
            ).steps
        ]
        for name, on in POLICIES.items()
    }
    assert orders["effects"][0][-1] == "authentication"
    assert set(orders["effects"]) == set(orders["no-effects"])


def test_a_no_decides_more_units_per_choice_than_a_yes_under_the_effects():
    """The effects rank the parent whose "no" removes its parts first, so the
    all-no owner decides more units at five choices than the all-yes owner."""
    report = Report.model_validate_json(BASELINE.read_text(encoding="utf-8"))
    found = readings("01", str(BASELINE), report, {"alone": {"stride": {}}})
    assert found, "a control"
    at = {
        (one.policy, one.answerer, one.budget): one
        for one in readings("01", str(BASELINE), report, {"all": BOTH})
    }
    assert at[("effects", "no", 5)].decided >= at[("effects", "yes", 5)].decided


def test_readings_cover_every_policy_answerer_selection_and_budget():
    report = Report.model_validate_json(BASELINE.read_text(encoding="utf-8"))
    found = readings("01", str(BASELINE), report, {"alone": {"stride": {}}})
    assert len(found) == len(POLICIES) * len(ANSWERERS) * (len(BUDGETS) + 1)
    for one in found:
        assert one.completed <= one.findings and one.decided <= one.units
        assert one.budget is None or one.choices <= one.budget
        # An owner who knows nothing answers "I don't know" to every question.
        if one.answerer == "unknown":
            assert one.unknown_answers == one.asked
        else:
            assert one.unknown_answers <= one.asked
    table = early_answerers.summary([one.__dict__ for one in found])
    (group,) = table
    assert group == "stride / alone"
    assert "effects / unknown @ 10" in table[group]
