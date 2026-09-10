"""A sweep's report bundle: the per-case files beside an artifact, and their readers.

A sweep writes one report, one drafts file and one proposals file per case
into the directory :func:`reports_dir` derives from the artifact path. The
artifact holds the measurements somebody thought of in advance; the bundle
holds what the agents said, so every later question about a finished sweep
reads here rather than paying for a second sweep.

The block readers sit beside the bundle because every reader of a saved
report asks the same first question: which framework's block, and at which
record type. ``stride_block`` and ``stride_threats`` are named accessors over
the neutral :func:`framework_block` for the scorer that grades STRIDE's open
claim set; every neutral reader goes through :func:`framework_block` or
:func:`optional_block` with the framework it was handed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from analysis_service.claims import FrameworkAnalysis, FrameworkName
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.stride.record import Threat
from analysis_service.report import Report
from evals.harness import modes
from evals.harness.reference import GoldenCase


def framework_block(report: Report, framework: FrameworkName) -> FrameworkAnalysis:
    """One framework's block off a report that carries one per selection.

    The grading contract is per framework (#167), so every scorer names the
    block it grades rather than assuming the report holds one. A report missing
    a block the job selected is a driver defect rather than a sweep result: the
    envelope's own check requires the blocks to answer the job's frameworks with
    none dropped.
    """
    for block in report.analyses:
        if block.framework == framework:
            return block
    raise modes.EvalRunError(f"the report carries no {framework} analysis block")


def optional_block(
    report: Report, framework: FrameworkName
) -> FrameworkAnalysis | None:
    """The same, for a framework a case may not have declared."""
    return next(
        (block for block in report.analyses if block.framework == framework), None
    )


def stride_block(report: Report) -> FrameworkAnalysis:
    """STRIDE's block, which every case declares and every mode builds for."""
    return framework_block(report, "stride")


def stride_threats(report: Report) -> list[Threat]:
    """This report's STRIDE claims, at the record type they validate as.

    ``claims`` is annotated at the neutral :class:`~analysis_service.claims.RuledClaim` because a block
    holds whatever its own package produced; the scorers grade ``category`` and
    ``severity``, which only STRIDE's record carries. The envelope already
    validated this block as its package's own shape, so this re-states that
    where a caller needs it and fails loudly if it ever stops being true.
    """
    claims = stride_block(report).claims
    narrowed = [claim for claim in claims if isinstance(claim, Threat)]
    if len(narrowed) != len(claims):
        raise modes.EvalRunError(
            "the stride block's claims did not load as Threat records"
        )
    return narrowed


#: The extension a sweep's report directory takes, replacing the artifact's.
REPORTS_SUFFIX = ".reports"


def reports_dir(out: str | Path) -> Path:
    """Where a sweep's per-case reports land, given its artifact path.

    Derived from ``--out`` rather than selected by a second flag: the reports
    and the artifact describe one sweep, and two independent paths let an
    operator point them at two different ones.
    """
    return Path(out).with_suffix(REPORTS_SUFFIX)


def write_reports(out: str, mode: str, runs: Mapping[str, modes.AnalysisRun]) -> None:
    """Persist every finished case's whole report beside the artifact.

    A sweep is paid work and the report is the only record of what the agents
    said: what each threat cited, what the critic rejected and on what
    reasoning, and what the ``scope`` list carried. The artifact holds the
    measurements somebody thought of in advance, so without this file every
    other question about a finished sweep costs a second sweep
    ([#180](https://github.com/mstarks01/work-agent/issues/180)).

    Beside the artifact rather than inside it. A report embeds the whole
    **Valid System Model** and every claim's grounds, so folding a corpus of
    them into the artifact would bury the aggregates a reader opens it for.

    **These reports are publishable.** They carry corpus source text, which is
    in this repository, so writing them raises no disclosure question. That is
    stated rather than assumed, because the same code path carries a
    submitter's own text the moment it runs outside the corpus.
    """
    if mode not in modes.REPORTING_MODES:
        print(f"no reports written: {mode} mode produces none")
        return
    directory = reports_dir(out)
    directory.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for case_id, run in sorted(runs.items()):
        path = directory / f"{case_id}.report.json"
        path.write_text(run.report.model_dump_json(indent=2) + "\n", "utf-8")
        # The drafts beside the report, because the report is what survived the
        # critic and half of what a score reads is what did not. Without them
        # ``score`` could recompute recall and not critic yield, and a command
        # that re-scores some of a sweep is worse than one that refuses.
        drafts = directory / f"{case_id}.drafts.json"
        drafts.write_text(
            json.dumps(
                {
                    framework: [claim.model_dump(mode="json") for claim in claims]
                    for framework, claims in run.drafts.items()
                },
                indent=2,
            )
            + "\n",
            "utf-8",
        )
        # The lanes' own emissions, before the fan-in routed anything away.
        # ``score`` does not read them: they exist so a reader can ask what a
        # lane answered, which the drafts no longer say.
        proposals = directory / f"{case_id}.proposals.json"
        proposals.write_text(json.dumps(dict(run.proposals), indent=2) + "\n", "utf-8")
        total_bytes += path.stat().st_size + drafts.stat().st_size
    print(f"{len(runs)} report(s) written to {directory} ({total_bytes / 1024:.0f} KB)")


def runs_from_reports(artifact: Path, cases: Sequence[GoldenCase]) -> dict[str, Any]:
    """Read a finished sweep's saved reports and drafts back into runs.

    The pair is what a score needs: the report holds what survived the critic
    and the drafts hold what it was handed, and critic yield is the difference.
    A sweep whose directory carries a report and no drafts refuses here rather
    than scoring the half it can — re-run that sweep.
    """
    directory = reports_dir(artifact)
    if not directory.is_dir():
        raise modes.EvalRunError(
            f"{directory} does not exist; a score reads the reports a sweep"
            " writes beside its artifact, not the artifact alone"
        )

    runs: dict[str, Any] = {}
    for case in cases:
        report_path = directory / f"{case.id}.report.json"
        drafts_path = directory / f"{case.id}.drafts.json"
        if not report_path.exists():
            continue
        if not drafts_path.exists():
            raise modes.EvalRunError(
                f"{drafts_path} is missing; this sweep predates the drafts"
                " being written beside its report, so its critic yield cannot"
                " be recomputed. Re-run the sweep rather than scoring half of it"
            )
        report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        raw = json.loads(drafts_path.read_text(encoding="utf-8"))
        drafts = {
            framework: tuple(
                PACKAGES[framework].record.model_validate(claim) for claim in claims
            )
            for framework, claims in raw.items()
        }
        runs[case.id] = modes.AnalysisRun(report=report, drafts=drafts)
    if not runs:
        raise modes.EvalRunError(
            f"{directory} carries no report for any case in the artifact"
        )
    return runs
