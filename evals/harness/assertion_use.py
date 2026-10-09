"""``run.py assertion-use``: each archived report's use of its assertion catalog.

Reads the figures :func:`~analysis_service.assertion_use.assertion_use` gives a
finished job, over the ``*.report.json`` files of a sweep's ``.reports``
directory. A report that today's model refuses is skipped and counted, and a
report whose job ran no pass is counted apart. It runs no model and reads no
credential.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from analysis_service.assertion_use import AssertionUse, assertion_use
from analysis_service.report import Report

REPORT_GLOB = "*.report.json"


def report_paths(paths: Sequence[Path]) -> list[Path]:
    """Each file named, and each report under each directory named, in order."""
    found: list[Path] = []
    for path in paths:
        found += sorted(path.rglob(REPORT_GLOB)) if path.is_dir() else [path]
    return found


def render(rows: Sequence[tuple[str, AssertionUse]], no_pass: int, refused: int) -> str:
    """One row per report and framework, then the totals."""
    out = [
        (
            "| report | framework | claims | citing | catalog only | catalog leads"
            " | rows | refused rows | open links | assert ms | assert USD |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, use in rows:
        for block in use.blocks:
            leads = "—" if block.catalog_leads is None else block.catalog_leads
            charge = (
                "—" if use.assert_charge_usd is None else f"{use.assert_charge_usd:.4f}"
            )
            out.append(
                f"| {name} | {block.framework} | {block.claims} | {block.citing}"
                f" | {block.catalog_only} | {leads} | {use.rows} | {use.refused}"
                f" | {use.open_links} | {use.assert_ms} | {charge} |"
            )
    blocks = [block for _, use in rows for block in use.blocks]
    claims = sum(block.claims for block in blocks)
    citing = sum(block.citing for block in blocks)
    only = sum(block.catalog_only for block in blocks)
    out += [
        "",
        (
            f"Reports read: {len(rows)}. Reports with no assertion pass: {no_pass}."
            f" Reports today's model refuses: {refused}."
        ),
        (
            f"Claims: {claims}. Claims that cite a catalog row: {citing}."
            f" Claims that rest only on catalog rows: {only}."
        ),
    ]
    return "\n".join(out) + "\n"


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "reports",
        nargs="+",
        type=Path,
        help="report files, or directories to search for *.report.json",
    )


def command_assertion_use(args: argparse.Namespace) -> int:
    """Print each report's use of its catalog. It runs no model."""
    rows: list[tuple[str, AssertionUse]] = []
    no_pass = 0
    refused = 0
    try:
        for path in report_paths(args.reports):
            try:
                report = Report.model_validate_json(path.read_bytes())
            except ValidationError:
                refused += 1
                continue
            use = assertion_use(report)
            if use is None:
                no_pass += 1
            else:
                rows.append((path.name.removesuffix(".report.json"), use))
    except OSError as error:
        print(f"cannot read the reports: {error}", file=sys.stderr)
        return 1
    print(render(rows, no_pass, refused), end="")
    return 0
