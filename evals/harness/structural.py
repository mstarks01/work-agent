"""Tier 1: the structural gates, and the only ones that block.

Thresholds are split by nature; these are the absolute, per-case,
zero-tolerance ones — a payload parses as a
:class:`~analysis_service.report.Report`, its references resolve, its claim IDs
are unique within each block, its severity bands match
:func:`~analysis_service.report.derive_severity_level`, and each block's summary
counts match its own contents. They gate from day one because they are
deterministic, free, and already enforced by shipped validators; must-find
recall computes and reports but does not block until baselines exist.

The checks are re-asserted here rather than delegated wholesale to the
model validator, for two reasons: a raw payload has to be gradeable before it
is known to parse, and a gate that would silently weaken if someone relaxed
:class:`Report` is not a gate. Every failure in a report is listed at
once — a run artifact naming one problem per iteration wastes a live sweep.

**Per block, and the framework named in every message.** A report carries one
:class:`~analysis_service.report.FrameworkAnalysis` per framework the job
selected, so a claim ID is unique only within its own block and a failure that
did not say whose block it was in would send a reader through N of them. The
neutral half runs over every block; the severity check is STRIDE's, and runs
only where the package's record grades harm.

One check is deliberately gone. ``^[STRIDE]-\\d{2}$`` and the category-letter
assertion were deleted with ``schema_version`` 3.0: the ID is composed by the
service from the package's own ``IdRule`` and the lane is stamped from the same
call, so the letter and the lane cannot disagree unless the composition itself
is wrong — which a re-validation of the string would hide rather than catch.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from analysis_service.frameworks import package_for
from analysis_service.report import (
    FrameworkAnalysis,
    Report,
    derive_severity_level,
)


def structural_issues(payload: dict[str, Any]) -> list[str]:
    """Every Tier 1 failure in one report payload, empty if it is sound.

    A payload that will not parse fails here with the validator's own messages
    rather than an error count: the run artifact is what a human reads to fix
    the run, and "3 errors" sends them back to the model to find out which.
    Most Tier 1 properties are enforced by :class:`Report` itself, so a
    broken payload usually fails at this step; :func:`report_issues` is the
    same gates applied to an object that already parsed.
    """
    try:
        report = Report.model_validate(payload)
    except ValidationError as exc:
        return [
            "report does not parse as Report:"
            f" {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
    return report_issues(report)


def report_issues(report: Report) -> list[str]:
    """The same gates, over an already-parsed report."""
    known_ids = {element.id for element in report.system_model.elements()}
    issues = [
        issue for block in report.analyses for issue in _block_issues(block, known_ids)
    ]
    if report.boundary_crossings != report.system_model.boundary_crossings():
        issues.append(
            "boundary_crossings are not the crossings derived from the embedded"
            " system model"
        )
    element_count = len(report.system_model.elements())
    if report.elements_analyzed != element_count:
        issues.append(
            f"elements_analyzed is {report.elements_analyzed}, but the embedded"
            f" system model carries {element_count} elements"
        )
    return issues


def _block_issues(block: FrameworkAnalysis, known_ids: set[str]) -> list[str]:
    """One framework's block, on the neutral gates plus its package's own."""
    where = block.framework
    issues: list[str] = []
    claims = block.all_claims()

    for claim in claims:
        issues += [
            f"{where}: claim {claim.id!r} references element {ref!r}, absent from"
            " the embedded system model"
            for ref in claim.affected_element_ids
            if ref not in known_ids
        ]
        # Only the model-reference spelling names an element. A ``subject``
        # states a question about a fact the System Model has no slot for, so it
        # carries no ``element_id`` and there is nothing here to resolve.
        #
        # **This check is the service's own, one seam later, and it drifted.**
        # `Report._reference_issues` learned the second spelling when it
        # arrived; this did not, so the first ASVS corpus sweep hard-failed on
        # eleven claims that were correctly shaped. A gate that re-derives a
        # service rule has to move when the rule does, and nothing offline
        # could catch that: the scripted critic never emits a subject.
        issues += [
            f"{where}: claim {claim.id!r} hangs its needs-info verdict on element"
            f" {ref.element_id!r}, absent from the embedded system model"
            for ref in claim.verdict.related_unknowns
            if ref.names_an_element and ref.element_id not in known_ids
        ]

    issues += [
        f"{where}: claim ID {claim_id!r} appears {count} times"
        for claim_id, count in Counter(claim.id for claim in claims).items()
        if count > 1
    ]

    # The band arithmetic is the one thing the corpus and production share, and
    # it exists only where the package's record grades harm. Read off the
    # package rather than off a flag here, so the rubric the gate demands and
    # the field this check reads cannot disagree.
    if package_for(block.framework).carries_severity():
        for claim in claims:
            # The guard above is what puts the field here: a block's claims
            # validate as its own package's record, and that record declares
            # `severity` exactly when carries_severity() is true. The read is
            # dynamic because the annotation is the neutral base, which by
            # design carries no field a framework judges with.
            severity = getattr(claim, "severity", None)
            if severity is None:
                issues.append(
                    f"{where}: claim {claim.id!r} carries no severity, but this"
                    " package's record grades harm"
                )
                continue
            derived = derive_severity_level(severity.likelihood, severity.impact)
            if severity.level != derived:
                issues.append(
                    f"{where}: claim {claim.id!r} carries severity band"
                    f" {severity.level!r}, but the matrix derives {derived!r}"
                )

    if block.summary != type(block).summarize(block.claims, block.rejected_claims):
        issues.append(f"{where}: summary does not match the block's own contents")
    return issues
