"""The early-rounds replay ends, and its figures agree with each other."""

from __future__ import annotations

import pytest

from evals.harness.early_rounds import ANSWERERS, replay
from tests.factories import valid_model

STRIDE: dict = {"stride": {}}
ASVS: dict = {"asvs": {"level": 1}}
BOTH: dict = STRIDE | ASVS


def open_controls():
    model = valid_model()
    for flow in model.data_flows:
        flow.authentication = "unknown"
        flow.encryption_in_transit = "unknown"
    return model


@pytest.mark.parametrize("name", sorted(ANSWERERS))
@pytest.mark.parametrize(
    "frameworks", [STRIDE, ASVS, BOTH], ids=["stride", "asvs", "both"]
)
def test_every_answerer_reaches_the_end(name, frameworks):
    found = replay("case", open_controls(), valid_model(), frameworks, name)

    assert found.ended
    assert found.above_floor, "a control: the model has questions at the floor"
    assert found.asked <= found.above_floor + found.added


def test_a_stated_control_closes_leads_and_takes_questions_away():
    """Naming a mechanism for every control lowers later scores (E20)."""
    none = replay("case", open_controls(), valid_model(), STRIDE, "controls-none")
    stated = replay("case", open_controls(), valid_model(), STRIDE, "controls-stated")
    assert stated.asked <= none.asked


def test_an_owner_who_skips_everything_is_asked_each_question_once():
    found = replay("case", open_controls(), valid_model(), STRIDE, "skip-all")
    assert found.repeats == 0
    assert found.skipped == found.asked
    # The skipped questions leave the ones under the floor (ADR 0068).
    assert found.stop == "below-floor"


def test_an_owner_who_answers_in_part_and_skips_the_rest_is_not_asked_again():
    """Answering one facet and saving asked the same question fifty rounds
    over, before "Skip the rest" could set it aside (#1289)."""
    found = replay("case", open_controls(), valid_model(), STRIDE, "facets-partial")
    assert found.ended
    assert found.repeats == 0


def test_a_replay_records_what_its_stop_leaves_open():
    """The workload replay reports the stop's summary (#1542 B, ADR 0068):
    the questions under a floor and held back, and each framework's units by
    band and state."""
    found = replay(
        "case", valid_model(), valid_model(), {"asvs": {"level": 2}}, "capability-yes"
    )
    assert found.stop in {"budget-exhausted", "below-floor", "nothing-left"}
    bands = found.units["asvs"]
    assert set(bands) == {"level 1", "level 2"}
    assert all("unaskable" in states for states in bands.values())
    assert found.held_back + found.below_floor > 0, "a control: something stays out"
    stride = replay("case", valid_model(), valid_model(), STRIDE, "capability-yes")
    assert stride.units == {}
