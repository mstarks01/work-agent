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
    CatalogProposal,
    apply_projection,
    assertion_id,
    offered,
    settled,
)
from analysis_service.reassess import Verdict, reassess
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.artifact import load_artifact
from evals.harness.bundle import assertions_from_reports
from evals.harness.reference import GoldenCase, load_corpus

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

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
    #: before it had one, or a quarantined row with an unregistered predicate.
    identity: str
    #: What refused it, for a ``refused`` row.
    codes: tuple[str, ...] = ()
    #: The proposed row's index, for a row lost before the gate built it.
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

    rows.extend(
        PopulationRow(
            case,
            "refused",
            refusal.identity,
            refusal.codes,
            proposed_row=refusal.proposed_row,
        )
        for refusal in record.refusals()
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
        nargs="*",
        help="report files (*.report.json)",
    )
    parser.add_argument(
        "--assertions",
        type=Path,
        nargs="+",
        default=[],
        help="assertion-mode sweep artifacts, each with its .reports/ dir; each"
        " case's catalog is resolved on the case's reviewed model",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS,
        help="corpus root, for the models and sources an assertion sweep read",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the population"
    )


def label(path: Path) -> str:
    """The key a report is frozen under: its path, without the suffix.

    The run as well as the case, because two runs of one case are two
    populations, and a key by case alone let the second overwrite the first.
    """
    return str(path.with_name(path.name.removesuffix(".report.json")))


def assertion_reports(
    path: Path, cases: Sequence[GoldenCase]
) -> dict[str, dict[str, Any]]:
    """One assertion-mode sweep's catalogs, in the shape :func:`freeze` reads.

    The sweep keeps each case's proposal, not the gate's record, so the
    record is rebuilt here by :meth:`~AssertionRecord.of` on the case's
    reviewed model and sources, the inputs the ``assert`` node read. That
    runs the same resolver and quarantine as a job, so a refused row is
    counted by the same reader either way.
    """
    held = [case for case in cases if case.id in load_artifact(path).cases]
    by_id = {case.id: case for case in held}
    reports = {}
    for case_id, result in assertions_from_reports(path, held).items():
        case = by_id[case_id]
        record = AssertionRecord.of(
            CatalogProposal.model_validate(result.proposal),
            case.model,
            {source.label: source.text for source in case.sources},
        )
        reports[f"{path.with_suffix('')}/{case_id}"] = {
            "assertions": record.model_dump(mode="json"),
            "system_model": case.model.model_dump(mode="json"),
        }
    return reports


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
            label(path): json.loads(path.read_text(encoding="utf-8"))
            for path in args.reports
        }
        if args.assertions:
            cases = load_corpus(args.corpus)
            for path in args.assertions:
                reports.update(assertion_reports(path, cases))
        if not reports:
            raise PopulationError("name at least one report or assertion sweep")
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


def parsed_verdicts(held: object) -> dict[str, Verdict]:
    """A verdict file's ``verdicts`` object, each entry checked for its shape.

    Each entry is an object holding a string ``assessment`` and a string
    ``assessor``. Whether the assessment is one a review may write is
    :func:`~analysis_service.reassess.reviewed`'s question, not this one.
    """
    if not isinstance(held, dict):
        raise PopulationError("`verdicts` is not an object keyed by assertion identity")
    verdicts = {}
    for identity, entry in held.items():
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(field), str) for field in ("assessment", "assessor")
        ):
            raise PopulationError(
                f"{identity}: a verdict is an object with a string `assessment`"
                " and a string `assessor`"
            )
        verdicts[identity] = Verdict(entry["assessment"], entry["assessor"])
    return verdicts


def command_reassess(args: argparse.Namespace) -> int:
    """Recompute a report under a review. It runs no model and writes no report."""
    try:
        report = Report.model_validate_json(args.report.read_text(encoding="utf-8"))
        review = json.loads(args.verdicts.read_text(encoding="utf-8"))
        if report.assertions is None:
            raise PopulationError("the report ran no assertion pass")
        if not isinstance(review, dict):
            raise PopulationError("the verdict file is not a JSON object")
        digest = catalog_digest(report.assertions)
        if review.get("catalog_digest") != digest:
            raise PopulationError(
                "the verdicts were written against another catalog"
                f" ({review.get('catalog_digest')!r}, this report's is {digest!r})"
            )
        result = reassess(report, parsed_verdicts(review.get("verdicts")))
    except (OSError, ValueError) as error:
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
