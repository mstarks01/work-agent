"""The repair pass may change what the issues named, and nothing else (#675 D01)."""

from typing import get_args

import pytest

from analysis_service.validation import (
    RENAMING_CODES,
    IssueCode,
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
    assert repair_scope(issues, {}) == ("elements", ["flow:a", "store:b"])


def test_an_issue_naming_no_element_makes_the_scope_the_whole_model():
    issues = [
        ValidationIssue(code="invalid-reference", message="m", element_id="flow:a"),
        ValidationIssue(code="no-trust-zones", message="m"),
    ]
    assert repair_scope(issues, {}) == ("whole", [])


def test_a_named_element_brings_the_flows_through_it_into_scope():
    """#1040: a rename renames every flow through the element.

    A ``duplicate-id`` names the element and never its flows. The repair
    renames it, which re-derives every flow ID through it, and without this a
    flow is restored pointing at an element ID the rename replaces.
    """
    previous = dump()
    issues = [
        ValidationIssue(code="duplicate-id", message="m", element_id="process:web-app")
    ]

    scope, implicated = repair_scope(issues, previous)

    assert scope == "elements"
    assert implicated == [
        "flow:entity:customer>process:web-app>login",
        "flow:process:web-app>store:orders-db>store-order",
        "process:web-app",
    ]


def test_only_a_renaming_code_brings_the_flows_in():
    """An element named by any other code keeps its ID, so its flows stay pinned."""
    previous = dump()
    issues = [
        ValidationIssue(
            code="illegal-asset-tag", message="m", element_id="process:web-app"
        )
    ]

    assert repair_scope(issues, previous) == ("elements", ["process:web-app"])


def test_every_renaming_code_is_a_real_issue_code():
    assert RENAMING_CODES <= set(get_args(IssueCode))


def test_a_flow_that_does_not_touch_the_named_element_stays_out_of_scope():
    previous = dump()
    issues = [
        ValidationIssue(code="duplicate-id", message="m", element_id="store:orders-db")
    ]

    _, implicated = repair_scope(issues, previous)

    assert "flow:entity:customer>process:web-app>login" not in implicated


@pytest.mark.parametrize(
    "flows",
    [
        None,
        "not a list",
        [None],
        [{"id": 1}],
        [{"no": "id"}],
        # An endpoint of any other shape names no element. These two are apart
        # from the rest: a list and a table raise ``TypeError`` on a set
        # membership test, so the guard has to drop them before the comparison
        # rather than let the comparison answer.
        [{"id": "flow:x", "source": ["process:web-app"], "destination": "store:db"}],
        [{"id": "flow:x", "source": {"a": 1}, "destination": "store:db"}],
    ],
)
def test_a_model_that_failed_the_schema_still_yields_a_scope(flows):
    """The model here is whatever extraction emitted, not a validated one."""
    issues = [
        ValidationIssue(code="duplicate-id", message="m", element_id="process:web-app")
    ]

    assert repair_scope(issues, {"data_flows": flows}) == (
        "elements",
        ["process:web-app"],
    )


def test_a_rename_keeps_its_flows_instead_of_dangling():
    """The round trip #1040 is about, through both halves of the rule.

    Renaming the element re-derives its flow IDs, so the repair returns each
    flow under a new ID. Unless the flows are in scope, the replaced entry
    comes back pointing at an element the model does not hold.
    """
    previous = dump()
    repaired = dump()
    repaired["processes"][0]["name"] = "Seattle web app"
    repaired["processes"][0]["id"] = "process:seattle-web-app"
    for flow in repaired["data_flows"]:
        flow["id"] = flow["id"].replace("process:web-app", "process:seattle-web-app")
        if flow["source"] == "process:web-app":
            flow["source"] = "process:seattle-web-app"
        if flow["destination"] == "process:web-app":
            flow["destination"] = "process:seattle-web-app"
    issues = [
        ValidationIssue(code="duplicate-id", message="m", element_id="process:web-app")
    ]

    _, implicated = repair_scope(issues, previous)
    merged, restored = restore_unimplicated(previous, repaired, implicated)

    endpoints = {
        endpoint
        for flow in merged["data_flows"]
        for endpoint in (flow["source"], flow["destination"])
    }
    assert "process:web-app" not in endpoints
    assert restored == []


def test_two_things_the_text_names_alike_survive_the_repair():
    """The shape #1040 is about, end to end through both halves of the fix.

    Two distinct processes arrive under one name, so both carry one ID and the
    gate reports ``duplicate-id`` against it. ``prompts/extract.md`` rule 3
    now says to put the text's own distinguishing word in front of each, and
    this is what lets the repair that does so through: both elements land, and
    the flow between them points at the names the repair gave.
    """
    previous = dump()
    twin = {**previous["processes"][0], "name": "scheduler"}
    previous["processes"] = [
        {**twin, "id": "process:scheduler", "exposure": "internet-facing"},
        {**twin, "id": "process:scheduler", "exposure": "internal"},
    ]
    previous["data_flows"] = [
        {
            **previous["data_flows"][0],
            "id": "flow:process:scheduler>process:scheduler>hand-job",
            "source": "process:scheduler",
            "destination": "process:scheduler",
            "name": "hand job",
        }
    ]
    repaired = {
        **previous,
        "processes": [
            {
                **twin,
                "id": "process:seattle-scheduler",
                "name": "Seattle scheduler",
                "exposure": "internet-facing",
            },
            {
                **twin,
                "id": "process:dublin-scheduler",
                "name": "Dublin scheduler",
                "exposure": "internal",
            },
        ],
        "data_flows": [
            {
                **previous["data_flows"][0],
                "id": "flow:process:seattle-scheduler>process:dublin-scheduler>hand-job",
                "source": "process:seattle-scheduler",
                "destination": "process:dublin-scheduler",
            }
        ],
    }
    issues = [
        ValidationIssue(
            code="duplicate-id", message="m", element_id="process:scheduler"
        )
    ]

    _, implicated = repair_scope(issues, previous)
    merged, restored = restore_unimplicated(previous, repaired, implicated)

    assert [process["id"] for process in merged["processes"]] == [
        "process:seattle-scheduler",
        "process:dublin-scheduler",
    ]
    assert [flow["source"] for flow in merged["data_flows"]] == [
        "process:seattle-scheduler"
    ]
    assert restored == []


def test_an_uncited_change_is_put_back_and_named():
    previous = dump()
    repaired = dump()
    repaired["processes"][0]["technology"] = "rewritten while I was here"

    merged, restored = restore_unimplicated(
        previous, repaired, ["flow:entity:customer>process:web-app>login"]
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
        previous, repaired, ["flow:entity:customer>process:web-app>login"]
    )

    assert [flow["id"] for flow in merged["data_flows"]] == [
        "flow:process:web-app>store:orders-db>store-order"
    ]
    assert restored == ["flow:process:web-app>store:orders-db>store-order"]


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
