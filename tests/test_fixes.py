"""The missing controls a report's confirmed findings rest on, grouped for a reader.

Held against real archived reports, as the open facts are, because the shapes
that matter — one finding resting on two controls, a control four findings rest
on — are the ones the lanes actually write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    Subject,
    assertion_id,
)
from analysis_service.claims import Ground
from analysis_service.fixes import (
    absent_rows,
    fixes,
    fixes_by_framework,
    missing_controls,
)
from analysis_service.report import Report
from tests.factories import sample_threat

REPORTS = sorted(
    Path(__file__)
    .resolve()
    .parents[1]
    .glob("evals/baselines/6bff717-*/*.reports/*.report.json")
)


def _archived(path):
    """One archived report, read as the service reads a stored one."""
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=REPORTS, ids=lambda path: path.name[:2])
def report(request):
    return _archived(request.param)


def _fixes(report):
    return [
        fix for block in report.analyses for fix in fixes(block, report.system_model)
    ]


def test_the_archive_is_there_to_read():
    assert len(REPORTS) >= 10


def test_each_confirmed_finding_is_placed_at_most_once(report):
    for block in report.analyses:
        placed = [i for fix in fixes(block, report.system_model) for i in fix.placed]
        confirmed = {c.id for c in block.claims if c.verdict.status == "confirmed"}
        assert len(placed) == len(set(placed))
        assert set(placed) <= confirmed


def test_a_group_is_a_control_two_findings_rest_on(report):
    for fix in _fixes(report):
        assert len(fix.rests) > 1
        assert set(fix.placed) <= set(fix.rests)
        assert fix.placed


def test_the_order_puts_the_control_most_findings_rest_on_first(report):
    for block in report.analyses:
        counts = [len(fix.rests) for fix in fixes(block, report.system_model)]
        assert counts == sorted(counts, reverse=True)


def test_one_missing_control_groups_four_findings():
    """Case 01 states no authentication on the order submission, and four
    confirmed findings rest on that one fact."""
    (path,) = [p for p in REPORTS if p.name.startswith("01-")]
    (fix,) = _fixes(_archived(path))
    assert fix.label == (
        "Add authentication to the connection from storefront API to order service"
    )
    assert sorted(fix.placed) == ["E-01", "R-01", "S-03", "T-02"]


def test_a_row_stating_a_control_absent_is_a_missing_control():
    """An absent row counts as the control it projects into, so it groups with
    an absent attribute of the same name on the same subject."""
    flow = "flow:entity:customer>process:web-app>login"
    row = Assertion(
        subject=flow,
        predicate="credential-presented",
        value=ABSENT,
        basis="inferred",
        origin="assertion-pass",
        explanation="the description names no credential",
    )
    catalog = AssertionCatalog(
        subjects=[Subject(id=flow, type="interaction", label="login")],
        entries=[row],
    )
    rows = absent_rows(catalog)
    assert rows == {assertion_id(row): (flow, "authentication")}
    by_row = sample_threat(
        grounds=[Ground(kind="assertion", assertion=assertion_id(row))]
    )
    by_attribute = sample_threat(
        grounds=[
            Ground(kind="absent-attribute", element_id=flow, attribute="authentication")
        ]
    )
    assert missing_controls(by_row, rows) == missing_controls(by_attribute, rows)


def test_an_unknown_control_is_not_a_missing_one():
    """An unstated control is a question, not a fix."""
    unstated = sample_threat(
        grounds=[
            Ground(
                kind="unknown-attribute",
                element_id="flow:entity:customer>process:web-app>login",
                attribute="authentication",
            )
        ]
    )
    assert missing_controls(unstated, {}) == frozenset()


def test_the_page_payload_is_json_per_framework(report):
    payload = fixes_by_framework(report.analyses, report.system_model)
    assert set(payload) == {block.framework for block in report.analyses}
    for entries in payload.values():
        for entry in entries:
            assert set(entry) == {"key", "label", "rests", "placed"}
