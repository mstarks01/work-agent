"""The early-answerers walk ends, reads prefixes, reverses an answer through the
service, and tells the two policies apart."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import FactAnswer, key_ref
from analysis_service.report import Report
from evals.harness import early_answerers
from evals.harness.early_answerers import (
    ANSWERERS,
    BUDGETS,
    POLICIES,
    _reversal,
    readings,
    walk,
)
from evals.harness.early_policies import Selection
from tests.factories import valid_model

LEVEL_2: Selection = {"asvs": {"level": 2}}
BOTH = {"stride": {}, "asvs": {"level": 2}}

BASELINE = next(
    Path("evals/baselines").glob("6bff717-*/*.reports/01-payments-checkout.report.json")
)


@pytest.mark.parametrize("who", sorted(ANSWERERS))
@pytest.mark.parametrize("effects", [True, False], ids=["effects", "no-effects"])
def test_a_walk_spends_at_most_its_budget_one_question_a_save(who, effects):
    walked = walk(valid_model(), LEVEL_2, ANSWERERS[who], effects=effects, budget=10)
    assert walked.steps, "a control: the list asks something"
    spent = [step.spent for step in walked.steps]
    assert spent == sorted(set(spent)), "each save spends at least one choice"
    assert spent[-1] >= 10 or walked.ended
    assert spent[-2] < 10 or len(spent) == 1, "the walk stops once it spends 10"
    assert len(walked.within(5)) <= len(walked.within(10)) <= len(walked.steps)
    assert len({step.key for step in walked.steps}) == len(walked.steps), (
        "one save each"
    )


def test_a_skip_spends_one_choice_and_answers_nothing():
    walked = walk(valid_model(), LEVEL_2, ANSWERERS["skip"], effects=True, budget=5)
    assert [step.spent for step in walked.steps] == [1, 2, 3, 4, 5]
    assert all(step.answer is None for step in walked.steps)


def test_the_mixed_answerer_reads_a_signed_fixture():
    """The fixture answerer says "yes" and "no" as the signed truth states."""
    (name,) = (who for who in ANSWERERS if who.startswith("fixture "))
    walked = walk(valid_model(), LEVEL_2, ANSWERERS[name], effects=True, budget=10)
    values = {step.answer.value for step in walked.steps if step.answer is not None}
    assert {"yes", "no"} <= values


def _capability(name: str, value: str) -> FactAnswer:
    return FactAnswer(key=UnknownRef(capability=name).key, value=value)


def test_a_reversal_turns_the_first_answer_and_the_parts_it_contradicts():
    answers = [
        _capability("authentication", "yes"),
        _capability("browser-frontend", "yes"),
        _capability("password-authentication", "yes"),
    ]
    turned = [(key_ref(one.key).capability, one.value) for one in _reversal(answers)]
    assert turned == [("authentication", "no"), ("password-authentication", "no")]
    up = _reversal([_capability("password-authentication", "no")])
    assert [one.value for one in up] == ["yes"]
    assert _reversal([_capability("authentication", "unknown")]) == ()


def test_a_reversal_reopens_what_a_no_removed_and_closes_what_a_yes_listed():
    """Turning the first "no" to "yes" reopens the parts the "no" made
    unnecessary, and turning the first "yes" to "no" makes them unnecessary,
    through the service's own save. A "yes" with an answered part under it is
    refused alone, and admitted with the part turned too."""
    report = Report.model_validate_json(BASELINE.read_text(encoding="utf-8"))
    at = {
        (one.answerer, one.budget): one
        for one in readings("01", str(BASELINE), report, {"all": BOTH})
        if one.policy == "effects"
    }
    assert at[("no", 5)].reopened > 0 and at[("no", 5)].closed == 0
    assert at[("yes", 5)].closed > 0 and at[("yes", 5)].reopened == 0
    assert at[("yes", 20)].reversal_refused and at[("yes", 20)].reversal_answers > 1
    assert not at[("unknown", 20)].reversed and not at[("skip", 20)].reversed


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


def test_a_no_decides_more_units_at_five_choices_than_a_yes():
    """A "no" decides every unit its capability's rule reads, a "yes" only
    the units no other capability decides, so on the shared test model at
    ASVS level 2 the all-no submitter decides more units at five choices than
    the all-yes submitter, under both policies."""
    model = valid_model()

    def decided(who: str, effects: bool) -> int:
        walked = walk(model, LEVEL_2, ANSWERERS[who], effects=effects, budget=5)
        answers = [step.answer for step in walked.steps if step.answer is not None]
        return early_answerers._decided(model, LEVEL_2, "asvs", answers)[1]

    for effects in POLICIES.values():
        assert decided("no", effects) > decided("yes", effects)
        assert decided("yes", effects) > decided("skip", effects)


def test_readings_cover_every_policy_answerer_selection_and_budget():
    report = Report.model_validate_json(BASELINE.read_text(encoding="utf-8"))
    found = readings("01", str(BASELINE), report, {"alone": {"stride": {}}})
    assert len(found) == len(POLICIES) * len(ANSWERERS) * len(BUDGETS)
    for one in found:
        assert one.completed <= one.findings and one.decided <= one.units
        assert one.choices <= one.budget
        assert sum(one.finding_bands.values()) == one.findings
        # A submitter who knows nothing answers "I don't know" to every question.
        if one.answerer == "unknown":
            assert one.unknown_answers == one.asked
        else:
            assert one.unknown_answers <= one.asked
    table = early_answerers.summary([one.__dict__ for one in found])
    (group,) = table
    assert group == "stride / alone"
    assert "effects / unknown @ 10" in table[group]
