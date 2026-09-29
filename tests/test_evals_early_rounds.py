"""The early-rounds replay ends, and its figures agree with each other."""

from __future__ import annotations

import pytest

from evals.harness.early_rounds import ANSWERERS, replay
from tests.factories import valid_model

STRIDE: dict = {"stride": {}}
ASVS: dict = {"asvs": {"level": 1}}


def open_controls():
    model = valid_model()
    for flow in model.data_flows:
        flow.authentication = "unknown"
        flow.encryption_in_transit = "unknown"
    return model


@pytest.mark.parametrize("name", sorted(ANSWERERS))
@pytest.mark.parametrize("frameworks", [STRIDE, ASVS], ids=["stride", "asvs"])
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
