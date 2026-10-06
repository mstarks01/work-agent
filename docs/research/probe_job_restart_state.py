"""Does a job record survive JSON, and how large is each part? (wayfinder #1526)

Run from the repository root, with the run archive beside it::

    ANALYSIS_OFFLINE=1 uv run python docs/research/probe_job_restart_state.py \
        /home/me/work-agent/evals/runs

Reads every archived ``*.report.json`` that the current ``Report`` schema
accepts. For each one it builds two job records from the report and its corpus
case: a completed job, and a resumed job whose resumption and checkpoint carry
the report's model and catalog. It runs each record through
``model_dump_json`` and ``model_validate_json``, and compares the records and
the bytes. It prints the UTF-8 size of the sources, the report, the checkpoint
and the events.

It calls no model and rules nothing.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from analysis_service.certification import CertifyResult, UncertifiedNode
from analysis_service.jobs import Checkpoint, JobRecord, Resumption
from analysis_service.report import Report
from analysis_service.sources import Source

CORPUS = Path("evals/corpus")


def _sources(case_id: str) -> list[Source]:
    case_dir = CORPUS / case_id
    case = json.loads((case_dir / "case.json").read_text())
    return [
        Source(
            kind=entry["kind"],
            label=entry["label"],
            text=(case_dir / entry["file"]).read_text(),
        )
        for entry in case["sources"]
    ]


def _completed(report: Report, sources: list[Source]) -> JobRecord:
    record = JobRecord.create(
        owner_subject="probe",
        sources=sources,
        frameworks=report.job.frameworks,
        reserved_tokens=1,
    )
    record.transition("running")
    for node in report.nodes:
        record.record_node(node.node)
    record.report = report
    record.certification = CertifyResult(
        certified=False,
        uncertified=(UncertifiedNode(node="extract", fingerprint="0" * 64),),
        unexercised=("recritic",),
    )
    record.transition("completed")
    return record


def _resumed(report: Report, sources: list[Source]) -> JobRecord:
    checkpoint = Checkpoint(
        system_model=report.system_model, assertions=report.assertions
    )
    record = JobRecord.create(
        owner_subject="probe",
        sources=sources,
        frameworks=report.job.frameworks,
        shown_early=[("a", "b", "c", "d", "e", "f")],
        resumption=Resumption(
            parent_id="job-parent",
            checkpoint=checkpoint,
            follow_up=False,
            certification=CertifyResult(certified=True),
        ),
    )
    record.checkpoint = checkpoint
    record.skipped_early = [("a", "b", "c", "d", "e", "f"), "link-key"]
    return record


def _round_trips(record: JobRecord) -> bool:
    dumped = record.model_dump_json()
    back = JobRecord.model_validate_json(dumped)
    return back == record and back.model_dump_json() == dumped


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _summary(name: str, values: list[int]) -> str:
    return (
        f"{name:<12} median {statistics.median(values):>9,.0f}"
        f"  min {min(values):>9,}  max {max(values):>9,}"
    )


def main(archive: Path) -> None:
    outcomes: Counter[str] = Counter()
    sizes: dict[str, list[int]] = {
        "sources": [],
        "report": [],
        "checkpoint": [],
        "events": [],
        "record": [],
    }
    event_counts: list[int] = []
    for path in sorted(archive.rglob("*.report.json")):
        try:
            report = Report.model_validate_json(path.read_text())
        except ValidationError:
            outcomes["refused by today's schema"] += 1
            continue
        case_id = path.name.removesuffix(".report.json")
        if not (CORPUS / case_id).is_dir():
            outcomes["no corpus case"] += 1
            continue
        sources = _sources(case_id)
        completed = _completed(report, sources)
        resumed = _resumed(report, sources)
        for label, record in (("completed", completed), ("resumed", resumed)):
            key = "round trip ok" if _round_trips(record) else "round trip LOST"
            outcomes[f"{label}: {key}"] += 1
        checkpoint = Checkpoint(
            system_model=report.system_model, assertions=report.assertions
        )
        sizes["sources"].append(
            sum(_size(source.model_dump_json()) for source in sources)
        )
        sizes["report"].append(_size(report.model_dump_json()))
        sizes["checkpoint"].append(_size(checkpoint.model_dump_json()))
        sizes["events"].append(
            sum(_size(event.model_dump_json()) for event in completed.events)
        )
        sizes["record"].append(_size(completed.model_dump_json()))
        event_counts.append(len(completed.events))
    print(f"probed at {datetime.now(UTC):%Y-%m-%dT%H:%MZ}")
    for outcome, count in sorted(outcomes.items()):
        print(f"{outcome:<32} {count}")
    print("UTF-8 bytes of each part, over the reports today's schema accepts:")
    for name, values in sizes.items():
        print(_summary(name, values))
    print(_summary("event count", event_counts))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
