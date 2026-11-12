"""The shared-answers instrument reads the page's boxes and the blessed truth."""

from __future__ import annotations

import json
from itertools import product

from evals.harness.early_rounds import paused_state
from evals.harness.shared_answers import blanked, boxes, shareable
from tests import test_webapp
from tests.factories import valid_model


def _served():
    model, _ = blanked(valid_model())
    listed = paused_state(model, {"stride": {}}, (), ()).questions.early
    return [question.to_json() for question in listed]


def test_the_instrument_and_the_page_share_one_rule():
    """``shareable`` has two readers, the page and the instrument, so each
    pair of served questions is put to both."""
    served = _served()
    pairs = list(product(served, repeat=2))
    page = test_webapp.run_list_rules(
        f"const pairs = {json.dumps(pairs)};\n"
        "console.log(JSON.stringify(pairs.map(([a, b]) => shareable(a, b))));"
    )
    assert page == [shareable(a, b) for a, b in pairs]
    assert True in page and False in page, "a control: both answers occur"


def test_a_stated_attribute_is_made_unknown_and_kept_as_the_truth():
    blessed = valid_model()
    flow = blessed.data_flows[0]
    flow.authentication = "mutual TLS"
    model, truth = blanked(blessed)
    assert truth[(flow.id, "authentication")] == "mutual TLS"
    assert model.get(flow.id).authentication == "unknown"
    assert blessed.data_flows[0].authentication == "mutual TLS"


def test_a_box_of_two_true_values_counts_one_over_claim():
    blessed = valid_model()
    for flow in blessed.data_flows:
        flow.authentication = "mutual TLS"
    blessed.data_flows[0].authentication = "none; accepted by network position"
    found = [
        box
        for box in boxes("case", blessed)
        if box.selected == "stride"
        and box.known
        and box.group.endswith("authentication")
    ]
    assert found, "a control: the flows' authentication is asked once"
    [box] = found
    assert box.distinct == 2
    assert box.unlike == 1
    assert box.choices_shared == 1
    assert box.choices_picked == 1 + (box.known - 1) + 1
