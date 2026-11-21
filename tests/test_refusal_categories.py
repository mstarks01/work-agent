"""The shapes of a raw body that :func:`refusal_category_of` reads.

``tests/test_provider_contract.py`` drives the category through the real
Anthropic transform. These cover the body shapes that the transform never
passes on to a test there.
"""

from __future__ import annotations

import json

import pytest

from katra.refusal_categories import refusal_category_of


@pytest.mark.parametrize("category", ["bio", "x" * 40])
def test_a_stated_category_is_kept(category):
    body = json.dumps({"stop_details": {"type": "refusal", "category": category}})

    assert refusal_category_of(body) == category


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
        json.dumps({"stop_details": {"category": "cyber"}}),
        json.dumps({"stop_details": {"type": "other", "category": "cyber"}}),
        json.dumps({"stop_details": {"type": "refusal", "category": "cyber\n"}}),
        json.dumps({"stop_details": {"type": "refusal", "category": "x" * 41}}),
    ],
)
def test_any_other_shape_gives_no_category(body):
    assert refusal_category_of(body) is None


def test_a_category_of_an_unknown_shape_is_logged_without_its_value(caplog):
    body = json.dumps({"stop_details": {"type": "refusal", "category": "Cyber-9"}})

    assert refusal_category_of(body) is None
    assert "unknown shape" in caplog.text
    assert "Cyber-9" not in caplog.text
