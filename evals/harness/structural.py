"""Tier 1: the structural gates, and the only ones that block in phase 1.

Ticket 009 decision 16 splits thresholds by nature. These are the absolute,
per-case, zero-tolerance ones — a report parses as a
:class:`~stride_service.report.StrideReport`, its references resolve, its
threat IDs are unique and carry the right category letters, its severity bands
match :func:`~stride_service.report.derive_severity_level`, and its summary
counts match its own contents. They gate from day one because they are
deterministic, free, and already enforced by shipped validators; must-find
recall computes and reports but does not block until baselines exist
(decision 19).

The checks are re-asserted here rather than delegated wholesale to the
model validator, for two reasons: a raw payload has to be gradeable before it
is known to parse, and a gate that would silently weaken if someone relaxed
:class:`StrideReport` is not a gate. Every failure in a report is listed at
once — a run artifact naming one problem per iteration wastes a live sweep.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from stride_service.report import (
    CATEGORY_LETTERS,
    StrideReport,
    build_summary,
    derive_severity_level,
)


def structural_issues(payload: dict[str, Any]) -> list[str]:
    """Every Tier 1 failure in one report payload, empty if it is sound.

    A payload that will not parse fails here with the validator's own messages
    rather than an error count: the run artifact is what a human reads to fix
    the run, and "3 errors" sends them back to the model to find out which.
    Most Tier 1 properties are enforced by :class:`StrideReport` itself, so a
    broken payload usually fails at this step; :func:`report_issues` is the
    same gates applied to an object that already parsed.
    """
    try:
        report = StrideReport.model_validate(payload)
    except ValidationError as exc:
        return [
            "report does not parse as StrideReport:"
            f" {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
    return report_issues(report)


def report_issues(report: StrideReport) -> list[str]:
    """The same gates, over an already-parsed report."""
    issues: list[str] = []
    known_ids = {element.id for element in report.system_model.elements()}
    all_threats = [*report.threats, *report.rejected_threats]

    for threat in all_threats:
        letter = CATEGORY_LETTERS[threat.category]
        if not threat.id.startswith(f"{letter}-"):
            issues.append(
                f"threat {threat.id!r} does not carry the {threat.category}"
                f" category letter {letter!r}"
            )
        issues += [
            f"threat {threat.id!r} references element {ref!r}, absent from the"
            " embedded system model"
            for ref in threat.affected_element_ids
            if ref not in known_ids
        ]
        issues += [
            f"threat {threat.id!r} hangs its needs-info verdict on element"
            f" {ref.element_id!r}, absent from the embedded system model"
            for ref in threat.verdict.related_unknowns
            if ref.element_id not in known_ids
        ]
        derived = derive_severity_level(
            threat.severity.likelihood, threat.severity.impact
        )
        if threat.severity.level != derived:
            issues.append(
                f"threat {threat.id!r} carries severity band"
                f" {threat.severity.level!r}, but the matrix derives {derived!r}"
            )

    issues += [
        f"threat ID {threat_id!r} appears {count} times"
        for threat_id, count in Counter(t.id for t in all_threats).items()
        if count > 1
    ]

    if report.boundary_crossings != report.system_model.boundary_crossings():
        issues.append(
            "boundary_crossings are not the crossings derived from the embedded"
            " system model"
        )
    if report.summary != build_summary(
        report.threats, report.rejected_threats, report.system_model
    ):
        issues.append("summary does not match the report's own contents")
    return issues
