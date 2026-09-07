"""The repair pass may change what the issues named, and nothing else (#675 D01)."""

import pytest

from analysis_service.validation import (
    ValidationIssue,
    repair_scope,
    restore_unimplicated,
)
from tests.factories import valid_model


def dump():
    return valid_model().model_dump(mode="json")


def test_scope_is_the_elements_the_issues_name():
    issues = [
        ValidationIssue(code="invalid-reference", message="m", element_id="flow:a"),
        ValidationIssue(code="illegal-asset-tag", message="m", element_id="store:b"),
    ]
    assert repair_scope(issues) == ("elements", ["flow:a", "store:b"])


def test_an_issue_naming_no_element_makes_the_scope_the_whole_model():
    issues = [
        ValidationIssue(code="invalid-reference", message="m", element_id="flow:a"),
        ValidationIssue(code="no-trust-zones", message="m"),
    ]
    assert repair_scope(issues) == ("whole", [])


def test_an_uncited_change_is_put_back_and_named():
    previous = dump()
    repaired = dump()
    repaired["processes"][0]["technology"] = "rewritten while I was here"

    merged, restored = restore_unimplicated(
        previous, repaired, ["flow:customer-to-web-app:login"]
    )

    assert (
        merged["processes"][0]["technology"] == previous["processes"][0]["technology"]
    )
    assert restored == ["process:web-app"]


def test_a_cited_change_is_kept():
    previous = dump()
    repaired = dump()
    repaired["processes"][0]["technology"] = "corrected"

    merged, restored = restore_unimplicated(previous, repaired, ["process:web-app"])

    assert merged["processes"][0]["technology"] == "corrected"
    assert restored == []


def test_an_uncited_deletion_is_put_back_and_a_cited_one_stands():
    previous = dump()
    repaired = dump()
    repaired["data_flows"] = []

    merged, restored = restore_unimplicated(
        previous, repaired, ["flow:customer-to-web-app:login"]
    )

    assert [flow["id"] for flow in merged["data_flows"]] == [
        "flow:web-app-to-orders-db:store-order"
    ]
    assert restored == ["flow:web-app-to-orders-db:store-order"]


def test_an_added_element_is_kept():
    """A dangling reference is sometimes answered by the element it named."""
    previous = dump()
    repaired = dump()
    repaired["processes"].append(
        {**previous["processes"][0], "id": "process:new", "name": "New"}
    )

    merged, restored = restore_unimplicated(previous, repaired, [])

    assert [process["id"] for process in merged["processes"]] == [
        "process:web-app",
        "process:new",
    ]
    assert restored == []


def test_assumptions_are_the_repairs_own():
    previous = dump()
    repaired = dump()
    repaired["assumptions"] = []

    merged, _ = restore_unimplicated(previous, repaired, [])

    assert merged["assumptions"] == []


@pytest.mark.parametrize("group", ["external_entities", "processes", "data_stores"])
def test_every_group_is_walked(group):
    previous = dump()
    repaired = dump()
    repaired[group][0]["name"] = "Renamed"

    _, restored = restore_unimplicated(previous, repaired, [])

    assert restored == [previous[group][0]["id"]]
