"""The shapes of a raw body that :func:`refusal_category_of` reads.

``tests/test_provider_contract.py`` drives the category through the real
Anthropic transform. These cover the body shapes that the transform never
passes on to a test there.
"""

from __future__ import annotations

import json

import pytest

from analysis_service.refusals import refusal_category_of


def test_a_stated_category_is_kept():
    body = json.dumps({"stop_details": {"type": "refusal", "category": "bio"}})

    assert refusal_category_of(body) == "bio"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "",
        json.dumps(["stop_details"]),
        json.dumps("cyber"),
        json.dumps({}),
        json.dumps({"stop_details": None}),
        json.dumps({"stop_details": "cyber"}),
        json.dumps({"stop_details": ["cyber"]}),
        json.dumps({"stop_details": {"type": "refusal"}}),
        json.dumps({"stop_details": {"category": "cyber\n"}}),
    ],
)
def test_any_other_shape_states_no_category(body):
    assert refusal_category_of(body) is None
