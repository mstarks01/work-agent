"""A report of the web app as one Markdown file (#2157).

The file holds each framework's findings, the findings it ruled out, and the
report's Controls section. Each value a submitter, an owner or a model wrote
is escaped, so it reads as text in any Markdown viewer and never forms
markup or a link.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from analysis_service import Report
from analysis_service.job_controls import SectionEntry

#: The characters that start Markdown markup or an HTML tag inline.
_MARKUP = re.compile(r"([\\`*_\[\]<>#|!~&])")


def _text(value: str) -> str:
    """``value`` on one line, with each character that starts markup escaped."""
    return _MARKUP.sub(r"\\\1", " ".join(value.split()))


def _findings(report: Report) -> list[str]:
    lines: list[str] = []
    for block in report.analyses:
        lines += [
            "",
            f"## {_text(block.framework.upper())} {_text(block.framework_version)}",
        ]
        lines += ["", "### Findings", ""]
        lines += [
            f"- **{_text(claim.id)}** {_text(claim.title)} ({claim.verdict.status})."
            f" {_text(claim.description)}"
            for claim in block.claims
        ] or ["No finding."]
        if block.rejected_claims:
            lines += ["", "### Ruled out", ""]
            lines += [
                f"- **{_text(claim.id)}** {_text(claim.title)}: rejected for"
                f" {claim.verdict.rejected_because or 'no stated step'}."
                f" {_text(claim.verdict.reason)}"
                for claim in block.rejected_claims
            ]
    return lines


def _scope(entry: SectionEntry) -> str:
    scope = entry.scope
    if scope.type == "application":
        return "the whole application"
    return f"the {scope.type} “{_text(scope.name)}”"


def controls_markdown(section: Sequence[SectionEntry]) -> list[str]:
    """The Controls section as Markdown lines."""
    lines = ["", "## Controls", ""]
    if not section:
        return [*lines, "This analysis applied no Control."]
    for entry in section:
        lines.append(f"### {_text(entry.name)}")
        lines.append("")
        if entry.state == "applied":
            reached = ", ".join(_text(name) for name in entry.elements)
            lines.append(
                f"Protects {_scope(entry)}: {entry.outcome}"
                + (f", applied to {reached}." if reached else ".")
            )
        else:
            lines.append(f"Protects {_scope(entry)}: left out, {entry.reason}.")
        for fact in entry.facts:
            stated = (
                f" (the description states “{_text(fact.stated)}”)"
                if fact.stated
                else ""
            )
            lines.append(
                f"- Fact: {_text(fact.label)}: {_text(fact.value)} — {fact.state}{stated}"
            )
        for row in entry.rows:
            label = f" · {_text(row.label)}" if row.label else ""
            lines.append(
                f"- Row: {_text(row.framework.upper())} {_text(row.unit)}{label} —"
                f" {row.outcome}. {row.meaning}"
            )
            for ruling in row.rulings:
                lines.append(
                    f"  - {_text(ruling.claim)}: {ruling.ruling}. {_text(ruling.reason)}"
                )
            for line in row.quoted:
                lines.append(
                    f"  - Evidence: {_text(line.file)} ({line.evidence_class}),"
                    f" line {line.line}: {_text(line.text)}"
                )
        lines.append("")
    return lines


def report_markdown(name: str, report: Report, section: Sequence[SectionEntry]) -> str:
    """The report named ``name`` as one Markdown file."""
    lines = [
        f"# {_text(name)}",
        "",
        f"Generated {report.job.completed_at.isoformat()}. {_text(report.disclaimer)}",
        *_findings(report),
        *controls_markdown(section),
    ]
    return "\n".join(lines).rstrip() + "\n"
