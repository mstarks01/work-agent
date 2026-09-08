"""A control that opens with a negation is refused and repaired (#675 D02)."""

import pytest

from analysis_service.analysis import control_state, leading_word
from analysis_service.validation import AMBIGUOUS_CONTROL_LEADS, validate
from tests.factories import valid_model


def codes(model):
    return [issue.code for issue in validate(model)]


@pytest.mark.parametrize(
    "value", ["no MFA on the password login", "Not stated", "without TLS", "N/A"]
)
def test_a_control_opening_with_a_negation_is_refused_and_named(value):
    """The leading-token rule reads every one of these as ``stated``; the gate
    refuses the shape so the repair pass says which of the three it meant."""
    model = valid_model()
    model.data_flows[0].authentication = value
    assert control_state(value) == "stated"
    (issue,) = [issue for issue in validate(model) if issue.code == "ambiguous-control"]
    assert issue.element_id == model.data_flows[0].id
    assert issue.field == "authentication"
    assert "'none'" in issue.message


@pytest.mark.parametrize(
    "value",
    ["none; accepted by network position", "unknown; possibly shared", "mTLS, no MFA"],
)
def test_the_three_legal_shapes_pass(value):
    model = valid_model()
    model.data_flows[0].authentication = value
    assert "ambiguous-control" not in codes(model)


def test_the_leads_are_negations_and_not_the_absence_sentinel():
    assert "none" not in AMBIGUOUS_CONTROL_LEADS
    assert {"no", "not", "without"} <= AMBIGUOUS_CONTROL_LEADS


@pytest.mark.parametrize(
    "value", ["no-mfa on the login", "no/unknown", "No, only an ACL"]
)
def test_a_negation_joined_by_punctuation_still_opens_the_value(value):
    """The gate reads the leading word through ``leading_word``, the reader
    ``control_state`` uses for its two sentinels, so a word ends at a word
    boundary and not at whitespace. Read by whitespace, ``no-mfa`` passed."""
    model = valid_model()
    model.data_flows[0].authentication = value
    assert control_state(value) == "stated"
    assert "ambiguous-control" in codes(model)


def test_the_gate_and_control_state_share_one_reader_of_the_leading_word():
    assert leading_word("none; by network position", ("none", "unknown")) == "none"
    assert leading_word("nonexistent control", ("none", "unknown")) is None
    assert leading_word("  Not stated", AMBIGUOUS_CONTROL_LEADS) == "not"
    assert leading_word("nobody said", AMBIGUOUS_CONTROL_LEADS) is None
    assert leading_word("N/A", AMBIGUOUS_CONTROL_LEADS) == "n/a"
