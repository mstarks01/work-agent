"""A sweep's report bundle: the per-case files beside an artifact, and their readers.

A sweep writes one report, one drafts file and one proposals file per case
into the directory :func:`reports_dir` derives from the artifact path. The
artifact holds the measurements somebody thought of in advance; the bundle
holds what the agents said, so every later question about a finished sweep
reads here rather than paying for a second sweep. An extraction sweep writes
one emission file per case instead, and an assertion sweep one proposal
file; :func:`extractions_from_reports` and :func:`assertions_from_reports`
read those back through the parser and the resolver that stand today, which
is what a replay wants.

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
from dataclasses import asdict
from pathlib import Path
from typing import Any

from analysis_service.assertions import (
    AssertionRecord,
    CatalogProposal,
    project,
)
from analysis_service.claims import FrameworkAnalysis, FrameworkName
from analysis_service.compact import FULL_FORMAT, parse_extraction
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.stride.record import Threat
from analysis_service.report import Report
from evals.harness import modes
from evals.harness.archive import archive_bytes
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
            archive_bytes(
                "drafts",
                {
                    framework: [claim.model_dump(mode="json") for claim in claims]
                    for framework, claims in run.drafts.items()
                },
            ),
            "utf-8",
        )
        # The lanes' own emissions, before the fan-in routed anything away.
        # ``score`` does not read them: they exist so a reader can ask what a
        # lane answered, which the drafts no longer say.
        proposals = directory / f"{case_id}.proposals.json"
        proposals.write_text(archive_bytes("proposals", dict(run.proposals)), "utf-8")
        total_bytes += path.stat().st_size + drafts.stat().st_size
    print(f"{len(runs)} report(s) written to {directory} ({total_bytes / 1024:.0f} KB)")


def write_assertions(
    out: str, mode: str, resolved: Mapping[str, modes.AssertionResult]
) -> None:
    """Persist every assertion run beside the artifact, so it can be re-read.

    :func:`write_extractions`'s counterpart for the assertion mode, and it
    keeps the same three things for the same reason: what the model emitted,
    what code built from it, and why each dropped row dropped.

    * ``proposal`` — what ``assert`` emitted. The only one that cannot be
      recomputed: resolving has already located spans and dropped rows, and a
      resolver change needs the rows that arrived.
    * ``catalog`` — the rows code built, which every count was taken over.
    * ``issues`` — why each dropped row dropped, structured as a repair pass
      would receive it.
    * ``projection`` — what each graph attribute the rows reach would hold, the
      reason it reads that way, and the rows behind it. It is here rather than
      recomputed by a reader because a degraded value is only explainable with
      the rows that would not fit, and the counts in the artifact carry the
      totals without them.

    **These files are publishable** on the same reading the reports are: they
    carry quotes of corpus source text, which is in this repository. The same
    path carries a submitter's own words the moment it runs outside the corpus.
    """
    if mode != "assertions":
        return
    directory = reports_dir(out)
    directory.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for case_id, result in sorted(resolved.items()):
        path = directory / f"{case_id}.assertions.json"
        path.write_text(
            archive_bytes(
                "assertions",
                {
                    "proposal": dict(result.proposal),
                    "catalog": result.catalog.model_dump(mode="json"),
                    "issues": [
                        issue.model_dump(mode="json") for issue in result.issues
                    ],
                    "projection": [
                        asdict(projection) for projection in project(result.catalog)
                    ],
                },
            ),
            "utf-8",
        )
        total_bytes += path.stat().st_size
    if resolved:
        print(
            f"{len(resolved)} assertion runs written to {directory}"
            f" ({total_bytes / 1024:.0f} KiB)"
        )


def write_extractions(
    out: str, mode: str, extracted: Mapping[str, modes.ExtractionResult]
) -> None:
    """Persist every extraction beside the artifact, so it can be re-scored.

    The extraction mode's counterpart to :func:`write_reports`, and it exists
    for the same reason (#180): the artifact holds the measurements somebody
    thought of in advance, and every other question about a finished sweep
    costs a second sweep. A scorer change is exactly that kind of question —
    the figures are recomputed offline from two models, so a sweep that kept
    its models can answer a figure invented after it ran, and one that kept
    only its scores cannot (#925).

    Three things per case, because a re-score needs all three:

    * ``raw`` — what ``extract`` emitted. The only one that cannot be
      recomputed: normalizing has already made a slug decision, and a rule that
      derives IDs differently needs the names that arrived.
    * ``normalized`` — the model the scores were taken over, or ``null`` where
      the output would not parse.
    * ``issues`` — the gate's verdict on it, structured as the repair pass
      would have received it.

    **No post-repair model, because this mode does not produce one.** It stops
    at the gate by design, so what is written here is the first pass. An
    analysis or end-to-end sweep carries its repaired model inside the report
    :func:`write_reports` writes, under ``system_model`` beside ``model_repair``.

    **These files are publishable** on the same reading the reports are: they
    carry a model of corpus source text, which is in this repository. The same
    path carries a submitter's own system the moment it runs outside the corpus.
    """
    if mode != "extraction":
        print(f"no extractions written: {mode} mode keeps its models in its reports")
        return
    directory = reports_dir(out)
    directory.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for case_id, result in sorted(extracted.items()):
        path = directory / f"{case_id}.extraction.json"
        path.write_text(
            archive_bytes(
                "extraction",
                {
                    "raw": dict(result.raw),
                    "normalized": (
                        result.extracted.model_dump(mode="json")
                        if result.extracted
                        else None
                    ),
                    "issues": [
                        issue.model_dump(mode="json") for issue in result.issues
                    ],
                },
            ),
            "utf-8",
        )
        total_bytes += path.stat().st_size
    print(
        f"{len(extracted)} extraction(s) written to {directory}"
        f" ({total_bytes / 1024:.0f} KB)"
    )


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


def _source_texts(case: GoldenCase) -> dict[str, str]:
    """Label to text, which is what both resolvers read."""
    return {source.label: source.text for source in case.sources}


def _case_files(
    artifact: Path, cases: Sequence[GoldenCase], suffix: str
) -> list[tuple[GoldenCase, dict[str, Any]]]:
    """Each case's saved file under ``suffix``, parsed, for the cases that have one."""
    directory = reports_dir(artifact)
    if not directory.is_dir():
        raise modes.EvalRunError(
            f"{directory} does not exist; a replay reads the files a sweep"
            " writes beside its artifact, not the artifact alone"
        )
    found = []
    for case in cases:
        path = directory / f"{case.id}{suffix}"
        if path.exists():
            found.append((case, json.loads(path.read_text(encoding="utf-8"))))
    if not found:
        raise modes.EvalRunError(
            f"{directory} carries no {suffix} file for any case in the artifact"
        )
    return found


def extractions_from_reports(
    artifact: Path, cases: Sequence[GoldenCase]
) -> dict[str, modes.ExtractionResult]:
    """Read a finished extraction sweep's saved emissions back, re-parsed.

    :func:`runs_from_reports`'s counterpart for the extraction mode. It reads
    ``raw`` — what ``extract`` emitted — and derives the model again through
    the :func:`~analysis_service.compact.parse_extraction` the ``validate``
    node calls **today**, with the case's own sources so the citation half
    of the gate runs. The ``normalized`` model and ``issues`` beside it are
    what the sweep scored on the day; a replay wants the emission under the
    current normalizer and gate, which is the whole point of keeping ``raw``
    (#925, #961). A reader that wants the recorded model opens the file.

    Every saved emission is in the full transport: the compact route has
    never persisted one, and :data:`~analysis_service.compact.COMPACT_FORMAT`
    says so. A payload in another shape parses to no model with the gate's
    own issues, which the replay reports rather than skips.
    """
    results = {}
    for case, written in _case_files(artifact, cases, ".extraction.json"):
        model, issues = parse_extraction(
            written["raw"], FULL_FORMAT, sources=_source_texts(case)
        )
        results[case.id] = modes.ExtractionResult(
            case_id=case.id,
            extracted=model,
            issues=tuple(issues),
            raw=written["raw"],
        )
    return results


def assertions_from_reports(
    artifact: Path, cases: Sequence[GoldenCase]
) -> dict[str, modes.AssertionResult]:
    """Read a finished assertion sweep's saved proposals back, re-resolved.

    Reads ``proposal`` — what ``assert`` emitted — and builds the catalog
    again through :meth:`~analysis_service.assertions.AssertionRecord.of`,
    the resolver and the gate as one reader, exactly as
    :func:`~evals.harness.modes.run_assertions` and the production ``prepare``
    node do over a live emission. The ``catalog`` beside it is what the sweep
    counted on the day; the resolver has moved since (#940, #964), and a
    replay grades the proposal under the resolver that stands.
    """
    results = {}
    for case, written in _case_files(artifact, cases, ".assertions.json"):
        record = AssertionRecord.of(
            CatalogProposal.model_validate(written["proposal"]),
            case.model,
            _source_texts(case),
        )
        results[case.id] = modes.AssertionResult(
            case_id=case.id,
            proposal=written["proposal"],
            catalog=record.catalog,
            issues=tuple(record.issues),
        )
    return results
