"""Tier 1 structural gates — the only ones that block in phase 1.

Each check is exercised against a report deliberately broken in one way. The
gates are re-asserted in :mod:`evals.harness.structural` rather than delegated
to :class:`StrideReport`'s own validator, so these tests build the broken
payloads as raw JSON where the model would refuse to construct them.
"""

from __future__ import annotations

from evals.harness.structural import report_issues, structural_issues
from tests.factories import sample_report, sample_threat


def test_a_sound_report_has_no_issues():
    assert report_issues(sample_report()) == []
    assert structural_issues(sample_report().model_dump(mode="json")) == []


def test_unparseable_payload_fails_the_gate():
    issues = structural_issues({"job": {"id": "x"}})

    assert issues and all("does not parse as StrideReport" in i for i in issues)


def test_dangling_element_reference_fails_the_gate():
    payload = sample_report().model_dump(mode="json")
    payload["threats"][0]["affected_element_ids"] = ["process:ghost"]

    assert any("system model" in issue for issue in structural_issues(payload))


def test_wrong_category_letter_fails_the_gate():
    payload = sample_report().model_dump(mode="json")
    payload["threats"][0]["id"] = "T-01"

    assert any("category letter" in issue for issue in structural_issues(payload))


def test_duplicate_threat_ids_fail_the_gate():
    payload = sample_report(
        threats=[sample_threat("S-01"), sample_threat("S-02")]
    ).model_dump(mode="json")
    payload["threats"][1]["id"] = "S-01"

    assert any("appears" in issue for issue in structural_issues(payload))


def test_severity_band_contradicting_the_matrix_fails_the_gate():
    payload = sample_report().model_dump(mode="json")
    payload["threats"][0]["severity"]["level"] = "low"

    assert any("matrix" in issue for issue in structural_issues(payload))


def test_summary_inconsistent_with_contents_fails_the_gate():
    payload = sample_report().model_dump(mode="json")
    payload["summary"]["threat_count"] = 99

    issues = structural_issues(payload)

    assert any("summary does not match" in issue for issue in issues)


def test_boundary_crossings_must_be_the_derived_ones():
    payload = sample_report().model_dump(mode="json")
    payload["boundary_crossings"] = []

    assert any("boundary_crossings" in issue for issue in structural_issues(payload))


def test_every_failure_is_reported_at_once():
    # One run of the gate must name everything wrong: a report that surfaces
    # one problem per iteration wastes a live sweep.
    report = sample_report(threats=[sample_threat("S-01"), sample_threat("S-02")])
    report.threats[0].id = "T-01"
    report.threats[1].affected_element_ids = ["process:ghost"]
    report.boundary_crossings = []

    issues = report_issues(report)

    assert len(issues) >= 3
    assert any("category letter" in issue for issue in issues)
    assert any("process:ghost" in issue for issue in issues)
    assert any("boundary_crossings" in issue for issue in issues)


def test_gate_fires_on_a_report_mutated_after_construction():
    # The point of re-asserting rather than delegating to the model validator:
    # the gate checks the object in front of it, not the object that was once
    # validated. A report edited in place still fails.
    report = sample_report()
    report.boundary_crossings = []

    assert any("boundary_crossings" in issue for issue in report_issues(report))
