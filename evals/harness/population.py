"""The review population #926's support measurement is read over, frozen first.

Semantic support is measured by people assessing rows. **Which rows** they
assess decides what the measurement can say, and a population chosen after the
assessments can drop exactly the unsupported rows it had to count. So the rows
are recorded here, from the job's own readers, before anybody looks — and a
freeze over a catalog that already carries an assessment is refused.

A row's **route** is what the job did with it, read off the same functions the
job calls: ``projected`` is a settled row the graph carries,
``offered`` is a settled row the Evidence Catalog cites as itself, ``open`` is
an unknown, ``set-aside`` is a row a conflict or an assessment kept from
settling, and ``refused`` is a row the gate refused, named by the code that
refused it. The first two are the rows that reach a consumer, which is the
population the owner's decision of 2026-09-23 fixes; the rest are recorded
beside them so a reviewer sees what the job did not use as well.

Nothing here assesses anything, and it runs no model.

**After the review**, ``run.py reassess`` applies a reviewer's verdicts to one
report through :func:`~analysis_service.reassess.reassess` and writes what the
review changed — rows rejected, findings withdrawn, leads reopened — beside the
report. The verdicts name the catalog digest they were written against, and a
verdict file for another catalog is refused rather than applied to rows its
reviewer never saw.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from analysis_service.assertions import (
    UNKNOWN,
    AssertionRecord,
    apply_projection,
    assertion_id,
    offered,
    settled,
)
from analysis_service.reassess import Verdict, reassess
from analysis_service.report import Report
from analysis_service.system_model import SystemModel

Route = Literal["projected", "offered", "open", "set-aside", "refused"]

#: The routes that put a row in front of a lane. The population the support
#: measurement is read over is these rows, frozen.
REACHES: frozenset[str] = frozenset({"projected", "offered"})

#: The format of a frozen population file. Bumped when a field changes
#: meaning, so a population frozen under one rule is never read under another.
POPULATION_VERSION = 1


class PopulationError(ValueError):
    """A population that cannot be frozen honestly."""


@dataclass(frozen=True)
class PopulationRow:
    """One row the job built or refused, and what the job did with it."""

    case: str
    route: Route
    #: The row's identity, or ``""`` for a proposed row the resolver refused
    #: before it had one.
    identity: str
    #: What refused it, for a ``refused`` row.
    codes: tuple[str, ...] = ()
    #: The proposed row's index, for a row the resolver refused.
    proposed_row: int | None = None


def catalog_digest(record: AssertionRecord) -> str:
    """The sha256 of the record's catalog, serialized with sorted keys.

    What a frozen population pins: an assessment written against a catalog
    with another digest was written against rows this population never held.
    """
    body = json.dumps(record.catalog.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def population(
    case: str, record: AssertionRecord, model: SystemModel
) -> tuple[PopulationRow, ...]:
    """Every row of one job, routed by the job's own readers.

    ``model`` is the model the job read. It is projected again here, which is
    idempotent on a model the job already projected, so a report's embedded
    model and an unprojected one route the same.
    """
    catalog = record.catalog
    projected, _ = apply_projection(model, catalog)
    cited = {assertion_id(entry) for entry in offered(catalog, projected)}
    held = {assertion_id(entry) for entry in settled(catalog)}
    rows = []
    for entry in catalog.entries:
        identity = assertion_id(entry)
        if identity in held:
            route: Route = "offered" if identity in cited else "projected"
        elif entry.value == UNKNOWN:
            route = "open"
        else:
            route = "set-aside"
        rows.append(PopulationRow(case, route, identity))

    refused: dict[tuple[int | None, str], set[str]] = {}
    for issue in record.issues:
        if issue.code == "graph-contradiction" or issue.code == "support-truncated":
            continue
        key = (issue.row, "" if issue.row is not None else issue.assertion or "")
        refused.setdefault(key, set()).add(issue.code)
    for (index, identity), codes in sorted(
        refused.items(), key=lambda item: (item[0][0] is None, item[0])
    ):
        rows.append(
            PopulationRow(
                case, "refused", identity, tuple(sorted(codes)), proposed_row=index
            )
        )
    return tuple(rows)


def freeze(
    reports: Mapping[str, Mapping[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    """The frozen population over ``reports``, keyed by case.

    **Refused if any row already carries an assessment.** Freezing after a
    review has begun is the retrospective selection this exists to prevent,
    so it is an error rather than a warning.
    """
    cases: dict[str, Any] = {}
    for case, report in sorted(reports.items()):
        held = report.get("assertions")
        if not held:
            raise PopulationError(f"{case}: the report ran no assertion pass")
        record = AssertionRecord.model_validate(held)
        assessed = [
            assertion_id(entry)
            for entry in record.catalog.entries
            if entry.assessment != "unchecked"
        ]
        if assessed:
            raise PopulationError(
                f"{case}: {len(assessed)} row(s) are already assessed, so a"
                " population frozen now could select around them"
            )
        model = SystemModel.model_validate(report["system_model"])
        cases[case] = {
            "catalog_digest": catalog_digest(record),
            "rows": [asdict(row) for row in population(case, record, model)],
        }
    return {
        "population_version": POPULATION_VERSION,
        "frozen_at": (now or datetime.now(UTC)).isoformat(),
        "cases": cases,
    }


def reached(frozen: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """The frozen rows a lane read, which the support measurement is over."""
    return [
        row
        for case in frozen["cases"].values()
        for row in case["rows"]
        if row["route"] in REACHES
    ]


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "reports",
        type=Path,
        nargs="+",
        help="report files (*.report.json); the case is the file's stem",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the population"
    )


def command_freeze_population(args: argparse.Namespace) -> int:
    """Freeze the review population. It runs no model and never overwrites."""
    if args.out.exists():
        print(
            f"{args.out} exists; a frozen population is never rewritten",
            file=sys.stderr,
        )
        return 1
    try:
        reports = {
            path.name.removesuffix(".report.json"): json.loads(
                path.read_text(encoding="utf-8")
            )
            for path in args.reports
        }
        frozen = freeze(reports)
    except (OSError, ValueError) as error:
        print(f"cannot freeze: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    print(
        f"froze {len(reached(frozen))} row(s) a lane read, across"
        f" {len(frozen['cases'])} case(s), to {args.out}"
    )
    return 0


def reassess_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("report", type=Path, help="the reviewed report (*.report.json)")
    parser.add_argument(
        "verdicts",
        type=Path,
        help="JSON: catalog_digest, and verdicts keyed by assertion identity,"
        " each an assessment and an assessor",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the reassessment"
    )


def command_reassess(args: argparse.Namespace) -> int:
    """Recompute a report under a review. It runs no model and writes no report."""
    try:
        report = Report.model_validate_json(args.report.read_text(encoding="utf-8"))
        review = json.loads(args.verdicts.read_text(encoding="utf-8"))
        if report.assertions is None:
            raise PopulationError("the report ran no assertion pass")
        digest = catalog_digest(report.assertions)
        if review.get("catalog_digest") != digest:
            raise PopulationError(
                "the verdicts were written against another catalog"
                f" ({review.get('catalog_digest')!r}, this report's is {digest!r})"
            )
        verdicts = {
            identity: Verdict(held["assessment"], held["assessor"])
            for identity, held in review["verdicts"].items()
        }
        result = reassess(report, verdicts)
    except (OSError, KeyError, ValueError) as error:
        print(f"cannot reassess: {error}", file=sys.stderr)
        return 1
    written = {
        "catalog_digest": digest,
        "rejected": list(result.rejected),
        "withdrawn": [asdict(row) for row in result.withdrawn],
        "reopened": list(result.reopened),
        "projections": [asdict(row) for row in result.projections],
        "record": result.record.model_dump(mode="json"),
        "system_model": result.model.model_dump(mode="json"),
    }
    args.out.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    print(
        f"{len(result.rejected)} row(s) rejected, {len(result.withdrawn)}"
        f" finding(s) withdrawn, {len(result.reopened)} lead(s) reopened for"
        f" re-analysis; written to {args.out}"
    )
    return 0
