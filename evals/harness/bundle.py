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
    GRAPH_BOUND,
    REGISTRY,
    AssertionCatalog,
    AssertionRecord,
    CatalogIssue,
    CatalogProposal,
    Quarantined,
    project,
    subject_id,
)
from analysis_service.claims import FrameworkAnalysis, FrameworkName
from analysis_service.compact import FULL_FORMAT, parse_extraction
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.stride.record import Threat
from analysis_service.report import Report
from analysis_service.system_model import FLOW_ID_RULES
from evals.harness import flow_ids, modes, replay
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
        # What each lane was asked, beside what it answered: without it a lane
        # that wrote nothing at a place cannot be read against its leads.
        lanes = directory / f"{case_id}.lanes.json"
        lanes.write_text(archive_bytes("lanes", dict(run.lane_material)), "utf-8")
        total_bytes += path.stat().st_size + drafts.stat().st_size
    print(f"{len(runs)} report(s) written to {directory} ({total_bytes / 1024:.0f} KB)")


def write_assertions(
    out: str, mode: str, resolved: Mapping[str, modes.AssertionResult]
) -> None:
    """Persist every assertion run beside the artifact, so it can be re-read.

    :func:`write_extractions`'s counterpart for every mode that ends in a
    catalog — the assertion mode and #1003's head-only mode — and it keeps the
    same three things for the same reason: what the model emitted, what code
    built from it, and why each dropped row dropped.

    * ``proposal`` — what ``assert`` emitted. The only one that cannot be
      recomputed: resolving has already located spans and dropped rows, and a
      resolver change needs the rows that arrived.
    * ``catalog`` — the rows code built, which every count was taken over.
    * ``issues`` — why each dropped row dropped, structured as a repair pass
      would receive it.
    * ``proposed`` and ``quarantined`` — how many rows were proposed, and the
      built rows the gate removed. With ``issues`` they are what
      :meth:`~analysis_service.assertions.AssertionRecord.refused_rows` reads,
      so a replay counts a refused catalog as the loss it was.
    * ``projection`` — what each graph attribute the rows reach would hold, the
      reason it reads that way, and the rows behind it. It is here rather than
      recomputed by a reader because a degraded value is only explainable with
      the rows that would not fit, and the counts in the artifact carry the
      totals without them.
    * ``stages`` — what every earlier node of the run wrote, by
      :data:`~evals.harness.modes.ARCHIVED_STATE`. A head that reads the sources
      facts-first composes its proposal out of a bundle code resolved, so a
      replay that held the proposal alone could not re-run the resolver over
      what the model actually emitted, or say where between the two a fact was
      lost. It is empty for a run whose head wrote none of those keys.

    **These files are publishable** on the same reading the reports are: they
    carry quotes of corpus source text, which is in this repository. The same
    path carries a submitter's own words the moment it runs outside the corpus.
    """
    # Every mode that keeps a catalog, read off the table the replay reads:
    # the head-only mode keeps one too, and a writer with its own list of modes
    # would have archived nothing for it — which is what happened, and is
    # invisible until a replay finds an empty directory.
    if replay.KEEPS.get(mode) != "catalog":
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
                    "proposed": result.record.proposed,
                    "quarantined": [
                        row.model_dump(mode="json") for row in result.record.quarantined
                    ],
                    "projection": [
                        asdict(projection) for projection in project(result.catalog)
                    ],
                    "stages": dict(result.stages),
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

    * ``repair`` — what ``repair`` emitted, where the gate sent the first
      pass to it, else ``null``. The extraction mode stops at the gate, so
      there it is always ``null``; an end-to-end sweep ran the repair, and its
      report carries only the model after the overlay, under ``system_model``
      beside ``model_repair``, which does not reconstruct what the node
      returned (#961).

    Every mode that ran ``extract`` writes it, so an end-to-end sweep's first
    pass replays under the same instrument as an extraction sweep's. The
    analysis mode seeds the blessed model and has no emission to keep.

    **These files are publishable** on the same reading the reports are: they
    carry a model of corpus source text, which is in this repository. The same
    path carries a submitter's own system the moment it runs outside the corpus.
    """
    if mode not in modes.EXTRACTING_MODES:
        print(f"no extractions written: {mode} mode runs no extraction")
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
                    "repair": None if result.repair is None else dict(result.repair),
                },
            ),
            "utf-8",
        )
        total_bytes += path.stat().st_size
    print(
        f"{len(extracted)} extraction(s) written to {directory}"
        f" ({total_bytes / 1024:.0f} KB)"
    )


def write_failures(out: str, failed: Mapping[str, Mapping[str, Any]]) -> None:
    """Persist what each failed case's session held when its graph node raised.

    A case that fails inside the graph leaves no report, so without this file
    the gate's message is the only record, and every look at the failure costs
    a paid run (#1097). The file holds the fault's ``repr`` and every key the
    graph's nodes wrote: the drafts, the critic's rulings and the marks.

    The suffix is one no reader loads: ``score`` and ``replay`` read named
    files only, so a captured failure never reads as a finished case.
    """
    if not failed:
        return
    directory = reports_dir(out)
    directory.mkdir(parents=True, exist_ok=True)
    for case_id, captured in sorted(failed.items()):
        path = directory / f"{case_id}.failure.json"
        path.write_text(archive_bytes("failure", dict(captured)), "utf-8")
    print(f"{len(failed)} failed case(s) captured in {directory}")


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
        # The lanes' proposals where the sweep archived them: `losses` reads
        # them for the `fan-in` cause, and a sweep from before they were kept
        # leaves that cause undecided rather than refused.
        proposals_path = directory / f"{case.id}.proposals.json"
        proposals = (
            json.loads(proposals_path.read_text(encoding="utf-8"))
            if proposals_path.exists()
            else {}
        )
        runs[case.id] = modes.AnalysisRun(
            report=report, drafts=drafts, proposals=proposals
        )
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
            # ``.get``: an emission archived before the key existed came from
            # a sweep that ran no repair, which is what an absent key means.
            repair=written.get("repair"),
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

    A proposal's subjects are lifted to the flow identity version the case's
    graph is at before it is resolved (#989, ADR 0037 rule 4). A row naming an
    interaction spells the flow ID the way the version the sweep ran under
    spelled it, and resolving that against a migrated graph binds nothing — the
    row would read as a subject the graph refuses rather than as a reading taken
    under another rule. The lift is derived from the graph's own endpoints and is
    a no-op on a proposal already at this version.
    """
    results = {}
    for case, written in _case_files(artifact, cases, ".assertions.json"):
        lifted = _lift_proposal(written["proposal"], case)
        record = AssertionRecord.of(
            CatalogProposal.model_validate(lifted),
            case.model,
            _source_texts(case),
        )
        results[case.id] = modes.AssertionResult(
            case_id=case.id,
            proposal=lifted,
            record=record,
            stages=written.get("stages", {}),
        )
    return results


def redrawn(catalog: AssertionCatalog) -> AssertionCatalog:
    """One archived catalog with each layer-own subject ID re-derived from its label.

    **A slug rule that moves re-keys the reference and not the archive (#1044).**
    A principal, a credential and an artifact carry an ID that is a pure
    function of the words a source used, through
    :func:`~analysis_service.assertions.subject_id`. The signed reference
    derives it today; an archived catalog holds the ID the run wrote on the day.
    So a change to :func:`~analysis_service.system_model.normalize_name` moved
    one side and not the other, and an archived row lost the reference row that
    took it.

    The label is what the archive carries, and it is what the rule reads, so
    re-deriving from it is the same repair the alignment makes for an element:
    a name is authoritative and an ID follows. :func:`subject_id` is called
    rather than respelled, so this moves with the rule instead of becoming a
    second reading of it.

    **A graph-bound subject is left exactly as archived.** Its ID is an
    **Element ID** the run's own extracted model decided, and no rule here can
    re-derive one — which is why :func:`heads_from_reports` reads the catalog
    the run gated rather than re-resolving it.

    **A move onto an ID something else already holds is refused.** Two labels
    that slugged apart under the older rule can slug together under the newer
    one, and rewriting both would merge two subjects the run kept apart. Those
    stay as archived, which is the same answer the resolver gives a duplicate.
    """
    moved: dict[str, str] = {}
    taken = {subject.id for subject in catalog.subjects}
    for subject in catalog.subjects:
        if subject.type in GRAPH_BOUND:
            continue
        try:
            derived = subject_id(subject.type, subject.label)
        except ValueError:
            continue
        if derived == subject.id or derived in taken:
            continue
        moved[subject.id] = derived
        taken.add(derived)
    if not moved:
        return catalog
    return AssertionCatalog(
        registry_version=catalog.registry_version,
        subjects=[
            subject.model_copy(update={"id": moved[subject.id]})
            if subject.id in moved
            else subject
            for subject in catalog.subjects
        ],
        entries=[_redrawn_entry(entry, moved) for entry in catalog.entries],
    )


def _redrawn_entry(entry, moved: Mapping[str, str]):
    """One row under the moved subject IDs, in its subject and in its value.

    A ``reference`` value names a subject, so a row pointing at a moved one
    follows it. Every other value is prose or a term and is left alone.
    """
    update: dict[str, str] = {}
    if entry.subject in moved:
        update["subject"] = moved[entry.subject]
    predicate = REGISTRY.get(entry.predicate)
    names_a_subject = predicate is not None and predicate.value == "reference"
    if names_a_subject and entry.value in moved:
        update["value"] = moved[entry.value]
    return entry.model_copy(update=update) if update else entry


def heads_from_reports(
    artifact: Path, cases: Sequence[GoldenCase]
) -> dict[str, modes.AssertionResult]:
    """Read a finished head-only sweep's saved catalogs back, as they were built.

    **The archived catalog, not the proposal re-resolved.**
    :func:`assertions_from_reports` re-resolves against ``case.model`` because
    an assertion sweep was *shown* that model; a head-only sweep extracted its
    own, and its rows name that model's element IDs. Resolving them against the
    blessed model would bind almost nothing and read as an arm that recovered
    almost nothing — a silent zero in the one number #1003 exists to compare.

    So the catalog the run's own terminal node gated is what a replay grades,
    and the rows are compared to the reference through the signed subject
    aliases, which is where a reviewer has already ruled whether two names are
    one subject.

    :func:`redrawn` runs over it first, and only over the subjects no graph
    decides. :func:`assertions_from_reports` needs no such step because it
    re-resolves the whole proposal, which re-derives every layer-own ID on the
    way; this reader cannot, and those subjects are exactly the ones it can
    still bring forward.
    """
    results = {}
    for case, written in _case_files(artifact, cases, ".assertions.json"):
        results[case.id] = modes.AssertionResult(
            case_id=case.id,
            proposal=written.get("proposal", {}),
            record=AssertionRecord(
                proposed=written.get(
                    "proposed", len(written.get("proposal", {}).get("assertions", ()))
                ),
                catalog=redrawn(AssertionCatalog.model_validate(written["catalog"])),
                issues=[
                    CatalogIssue.model_validate(issue)
                    for issue in written.get("issues", ())
                ],
                quarantined=[
                    Quarantined.model_validate(row)
                    for row in written.get("quarantined", ())
                ],
            ),
            # What every earlier node wrote, which for a facts-first head is
            # the bundle and its dispositions. A charge that has to say whether
            # a row was read and dropped or never read reads them, and reading
            # the file a second time beside this one would be a second reader
            # of one archive.
            stages=written.get("stages", {}),
        )
    return results


def _lift_proposal(proposal: Any, case: GoldenCase) -> Any:
    """One archived proposal with every flow ID moved to the graph's version.

    Every version but the graph's own, so the lift is total rather than a guess
    about which one an archive holds: each table is keyed by the ID that version
    derives, and an archive at the current version contributes an empty one.
    """
    raw = case.model.model_dump(mode="json")
    current = flow_ids.flow_id_version_of_graph(raw)
    table: dict[str, str] = {}
    for version in FLOW_ID_RULES:
        if version != current:
            table.update(flow_ids.table_between(raw, version, current))
    return flow_ids.lift_value(proposal, table)
