"""A report of the web app as one Markdown file (#2157), and its findings as
one CSV file.

The Markdown file holds each framework's findings, the findings it ruled out,
and the report's Controls section. Each value a submitter, an owner or a model
wrote is escaped, so it forms no Markdown markup, no HTML, no Markdown link and
no GFM autolink. A viewer that links any bare domain name can still link one.

The CSV file holds one row for each finding of each framework, the findings it
ruled out too. A cell that a spreadsheet can read as a formula starts with an
apostrophe, so the spreadsheet shows it as text.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal

from analysis_service import Report
from analysis_service.claims import (
    REJECTION_MEANINGS,
    VERDICT_LABELS,
    Claim,
    Mitigation,
    Severity,
    Verdict,
)
from analysis_service.frameworks import lane_of
from analysis_service.job_controls import SectionEntry
from analysis_service.open_facts import element_names

#: The characters that start Markdown markup or an HTML tag inline.
_MARKUP = re.compile(r"([\\`*_\[\]<>#|!~&])")

#: The character that lets a viewer make an autolink of a URL, a ``www.``
#: name or an email address: the colon before ``//``, the dot after ``www``
#: and the ``@``.
_AUTOLINK = re.compile(r"(?i):(?=//)|(?<=www)\.|@")


def _text(value: str) -> str:
    """``value`` on one line, with each character that starts markup or an
    autolink escaped."""
    escaped = _MARKUP.sub(r"\\\1", " ".join(value.split()))
    return _AUTOLINK.sub(lambda found: "\\" + found.group(0), escaped)


def _rejection_clause(verdict: Verdict) -> str:
    """Why the critic rejected the draft, or nothing where a report read back
    names no step."""
    step = verdict.rejected_because
    return "" if step is None else f", because {REJECTION_MEANINGS[step]}"


def _findings(report: Report) -> list[str]:
    lines: list[str] = []
    for block in report.analyses:
        lines += [
            "",
            f"## {_text(block.framework)} {_text(block.framework_version)}",
        ]
        lines += ["", "### Findings", ""]
        lines += [
            f"- **{_text(claim.id)}** {_text(claim.title)}"
            f" ({VERDICT_LABELS[claim.verdict.status]})."
            f" {_text(claim.description)}"
            for claim in block.claims
        ] or ["No finding."]
        if block.rejected_claims:
            lines += ["", "### Ruled out", ""]
            lines += [
                f"- **{_text(claim.id)}** {_text(claim.title)}: rejected"
                f"{_rejection_clause(claim.verdict)}. {_text(claim.verdict.reason)}"
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
            reached = ", ".join(_text(element.name) for element in entry.elements)
            lines.append(
                f"Protects {_scope(entry)}: {entry.meaning}"
                + (f", applied to {reached}." if reached else ".")
            )
        else:
            lines.append(
                f"Protects {_scope(entry)}: left out, because {entry.meaning}."
            )
        for fact in entry.facts:
            stated = (
                f" (the description states “{_text(fact.stated)}”)"
                if fact.stated
                else ""
            )
            lines.append(
                f"- Fact: {_text(fact.label)}: {_text(fact.value)} — {fact.meaning}{stated}"
            )
        for row in entry.rows:
            label = f" · {_text(row.label)}" if row.label else ""
            lines.append(
                f"- Row: {_text(row.framework)} {_text(row.unit)}{label} —"
                f" {row.meaning}"
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


#: The CSV file's columns, in order.
CSV_COLUMNS = (
    "framework",
    "id",
    "title",
    "status",
    "severity",
    "category",
    "affected elements",
    "description",
    "mitigation",
    "controls",
)

#: The first characters that make a spreadsheet read a cell as a formula
#: (OWASP, CSV injection).
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _level(severity: Severity) -> str:
    # The type allows None, but the validator of a Severity always sets the
    # level. The page reads the same field with no fallback.
    return severity.level or ""


def _summaries(mitigations: Sequence[Mitigation]) -> str:
    return " | ".join(mitigation.summary for mitigation in mitigations)


#: A judgement field of a record that the CSV export reads into a cell.
JudgementField = Literal["severity", "mitigations"]

#: The CSV columns that read a judgement field a record may declare, keyed by
#: the field and never by a framework. A record that does not declare the
#: field leaves its cell empty.
#: ``test_the_judgement_cells_name_each_judgement_field_of_each_record`` holds
#: the keys to the judgement fields of each package's record, and
#: ``test_the_report_page_reads_the_judgement_cells_the_csv_writes`` holds the
#: report page's readers to these cells.
JUDGEMENT_CELLS: Mapping[JudgementField, Callable[[Any], str]] = MappingProxyType(
    {"severity": _level, "mitigations": _summaries}
)


def judgement_cells(claim: Claim) -> dict[JudgementField, str]:
    """The cell of each judgement field for ``claim``, empty where its record
    does not declare the field."""
    declared = type(claim).model_fields
    return {
        field: read(getattr(claim, field)) if field in declared else ""
        for field, read in JUDGEMENT_CELLS.items()
    }


def _cell(value: str) -> str:
    """``value`` as a spreadsheet shows it as text, never as a formula."""
    return f"'{value}" if value.startswith(_FORMULA_LEAD) else value


def report_csv(report: Report, section: Sequence[SectionEntry]) -> str:
    """The findings of ``report`` as one CSV file, with a row for each.

    The category is the claim's lane, as :func:`lane_of` reads it. The
    severity and the mitigation come from :func:`judgement_cells`. The
    controls cell names each Control row that ruled on the finding, with its
    ruling.
    """
    names = element_names(report.system_model)
    rulings: dict[tuple[str, str], list[str]] = {}
    for entry in section:
        for row in entry.rows:
            for ruling in row.rulings:
                rulings.setdefault((row.framework, ruling.claim), []).append(
                    f"{entry.name}: {ruling.ruling}"
                )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for block in report.analyses:
        for claim in [*block.claims, *block.rejected_claims]:
            judged = judgement_cells(claim)
            cells = (
                block.framework,
                claim.id,
                claim.title,
                claim.verdict.status,
                judged["severity"],
                str(lane_of(claim) or ""),
                " | ".join(
                    names.get(element, element)
                    for element in claim.affected_element_ids
                ),
                claim.description,
                judged["mitigations"],
                " | ".join(rulings.get((block.framework, claim.id), [])),
            )
            writer.writerow([_cell(value) for value in cells])
    return out.getvalue()
