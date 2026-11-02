"""Tier 1 structural gates — the only ones that block.

Each check is exercised against a report broken in one way after it parsed.
The gates are re-asserted in :mod:`evals.harness.structural` rather than
delegated to :class:`~analysis_service.report.Report`'s own validator, so a
report edited in place still fails them. ``tests/test_structural_readers.py``
breaks one sound report each way either reader names and holds the two readers
against each other.

**Per block since schema 3.0.** A claim lives in its framework's own analysis
block, so a report is broken by reaching into ``analyses[0]`` rather than into
a top-level threat array, and every message the gate returns names the
framework whose block it came from.
"""

from __future__ import annotations

from analysis_service.claims import UnknownRef, Verdict
from evals.harness.structural import report_issues
from tests.factories import sample_report, sample_threat


def test_a_sound_report_has_no_issues():
    assert report_issues(sample_report()) == []


def test_a_subject_only_unknown_is_not_a_dangling_reference():
    """The second `related_unknowns` spelling names no element to resolve.

    The gate re-derives a service rule one seam later, so it must accept each
    spelling the report accepts. Otherwise an ASVS corpus sweep fails on
    correctly shaped claims. Other offline tests cannot see this, because the
    scripted critic never emits a subject.
    """
    report = sample_report()
    unknown = UnknownRef(
        element_id="", attribute="", subject="are queries parameterized"
    )
    report.analyses[0].claims[0].verdict = Verdict(
        status="needs-info",
        reason="the input does not say whether queries are parameterized",
        related_unknowns=[unknown],
    )

    assert not unknown.names_an_element
    assert not [issue for issue in report_issues(report) if "system model" in issue]


def test_summary_inconsistent_with_contents_fails_the_gate():
    """Mutated after construction, because the block itself refuses the payload.

    A block recounts its own contents on validation, so a mismatched summary
    never parses — which is exactly why the gate re-asserts the check rather
    than delegating it: the object in front of it may not be the object that
    was validated.
    """
    report = sample_report()
    report.analyses[0].summary.claim_count = 99

    issues = report_issues(report)

    assert any("summary does not match" in issue for issue in issues)


def test_every_failure_is_reported_at_once():
    # One run of the gate must name everything wrong: a report that surfaces
    # one problem per iteration wastes a live sweep.
    report = sample_report(threats=[sample_threat("S-01"), sample_threat("S-02")])
    block = report.analyses[0]
    block.claims[1].affected_element_ids = ["process:ghost"]
    block.claims[0].severity.level = "low"
    report.boundary_crossings = []

    issues = report_issues(report)

    assert len(issues) >= 3
    assert any("matrix" in issue for issue in issues)
    assert any("process:ghost" in issue for issue in issues)
    assert any("boundary_crossings" in issue for issue in issues)


def test_every_block_issue_names_its_framework():
    """A failure that did not say whose block it was in sends a reader through N."""
    report = sample_report()
    report.analyses[0].claims[0].affected_element_ids = ["process:ghost"]

    issues = report_issues(report)

    assert issues and all(issue.startswith("stride: ") for issue in issues)


def test_gate_fires_on_a_report_mutated_after_construction():
    # The point of re-asserting rather than delegating to the model validator:
    # the gate checks the object in front of it, not the object the validator
    # saw at construction. A report edited in place still fails.
    report = sample_report()
    report.boundary_crossings = []

    assert any("boundary_crossings" in issue for issue in report_issues(report))
