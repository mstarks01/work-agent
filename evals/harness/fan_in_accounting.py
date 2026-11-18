"""Fan-in, run again on the archive: every proposal a lane emitted, accounted (#2273).

The fan-in merges a framework's lane batches into the drafts its critic reads.
It is deterministic, so it can run again on an archived case: the batches are
``<case>.proposals.json``, and :func:`replay_fan_in` hands them to the shipped
:func:`~analysis_service.fan_in.fan_in` with the case's own model, its sources
as :func:`~analysis_service.execution.seed_state` renders them, and the
assertion catalog its report holds. ``descendants`` replays through the same
function.

**What it answers.** Where each **Proposal** a lane emitted went, valid or
not. :func:`account` reads the fan-in's own output and gives each proposal, by
the ID the fan-in gives it, one fate: kept as a draft, dropped with the reason
the fan-in recorded, dropped as an identity its framework does not have, or
deferred because the job carries no evidence of the kind it needs. A proposal
with none of these is unexplained. An output that names an ID no proposal
carries, or names one ID more often than proposals carry it, is extra.

**Quotes.** The drafts' quote grounds, counted three ways: verified as
written, repaired to the source's own span, and refused. Only the drafts that
survive carry their grounds, so a draft dropped because no ground verifies
counts among the drops, not among the refused quotes.

**What it cannot read.** The report does not hold the units the package's
rules ruled out or in, or the units an applied Control row covers, so the
replay passes none. A case whose replayed drafts differ from its archived
drafts is therefore incompatible, and so is one whose sources the artifact
records under another digest, or whose batches today's fan-in refuses. An
incompatible case is named with its reason and never counted as accounted.

**Cost.** Archived files and deterministic code only; nothing calls a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from pydantic import ValidationError

from analysis_service.claims import FrameworkName, ProposalBatch
from analysis_service.evidence import invalid_proposal_marks, known_proposals
from analysis_service.execution import seed_state
from analysis_service.fan_in import DraftJoinError, FanIn, fan_in
from analysis_service.frameworks import PACKAGES, FrameworkPackage, schemas_for
from analysis_service.frameworks.asvs.record import RequirementProposals
from analysis_service.frameworks.stride.record import ArchivedThreatProposals
from analysis_service.graph import STATE_SOURCE_TEXTS
from analysis_service.report import Report
from analysis_service.sources import CARRIED_EVIDENCE_KINDS
from evals.harness.artifact import (
    EvalArtifact,
    ProvenanceError,
    load_artifact,
    recorded_sources,
    rendered_sources,
)
from evals.harness.bundle import UnreadableReport, finished_file, read_report
from evals.harness.modes import REPORTING_MODES, EvalRunError
from evals.harness.reference import GoldenCase, development_cases, load_corpus

#: Each package's batch type for a stored lane batch: the agent's own schema,
#: or an archived reading of it that tolerates a field the run did not store.
ARCHIVED_BATCHES: Mapping[FrameworkName, type[ProposalBatch]] = MappingProxyType(
    {"asvs": RequirementProposals, "stride": ArchivedThreatProposals}
)


def replay_fan_in(
    case: GoldenCase,
    report: Report,
    proposals: Mapping[str, Any],
    framework: FrameworkName,
) -> tuple[dict[str, ProposalBatch], FanIn]:
    """The shipped fan-in over one framework's stored lane batches.

    ``proposals`` is each lane against its stored batch. Raises
    :class:`~analysis_service.fan_in.DraftJoinError` where the fan-in does.
    """
    batch_type = ARCHIVED_BATCHES[framework]
    batches = {lane: batch_type.model_validate(raw) for lane, raw in proposals.items()}
    merged = fan_in(
        batches,
        PACKAGES[framework],
        report.system_model,
        seed_state(case.sources)[STATE_SOURCE_TEXTS],
        assertions=report.assertions.catalog if report.assertions else None,
    )
    return batches, merged


@dataclass(frozen=True)
class FanInAccount:
    """Where each proposal of one framework's lane batches went in one case."""

    case: str
    framework: FrameworkName
    #: Each proposal the lanes emitted, valid or not, by the ID the fan-in gives it.
    proposals: tuple[str, ...]
    kept: tuple[str, ...]
    #: Each dropped proposal's ID against the reason the fan-in recorded.
    dropped: tuple[tuple[str, str], ...]
    #: Proposals naming an identity their framework does not have.
    unknown: tuple[str, ...]
    #: Proposals the package deferred for evidence the job does not carry.
    deferred: tuple[str, ...]
    #: Proposals no fate above accounts for, once per proposal.
    unexplained: tuple[str, ...]
    #: IDs the output names once per name beyond the proposals that carry them.
    extra: tuple[str, ...]
    quotes_verified: int
    quotes_repaired: int
    quotes_refused: int
    #: Why the replay cannot stand for the run, or ``""``.
    incompatible: str = ""

    @property
    def accounted(self) -> int:
        """Proposals the fan-in's output accounts for; none for an incompatible case."""
        if self.incompatible:
            return 0
        return len(self.proposals) - len(self.unexplained)


def account(
    case_id: str,
    batches: Mapping[str, ProposalBatch],
    package: FrameworkPackage,
    merged: FanIn,
) -> FanInAccount:
    """Each proposal of ``batches``, given its fate from the fan-in's ``merged``.

    The fan-in keys a deferral by unit, and a unit can hold more than one
    proposal, so each lane's known proposals go through the package's own split
    again, with the empty set of covered units the replay passes the fan-in.
    The units that split defers must be the fan-in's own, or the account is
    incompatible.
    """
    key_field = schemas_for(package.name).key_field
    proposals: list[str] = []
    deferred: list[str] = []
    units: dict[str, str] = {}
    for lane, batch in batches.items():
        ids = {
            id(proposal): package.compose_id(lane, getattr(proposal, key_field))
            for proposal in batch.claims
        }
        proposals += ids.values()
        proposals += [
            mark.claim_id
            for mark in invalid_proposal_marks(
                batch.invalid, package, lane
            ).dropped_claims
        ]
        known, _ = known_proposals(batch.claims, package, lane)
        kept, reasons = package.record.partition_proposals(
            known, lane, CARRIED_EVIDENCE_KINDS, ()
        )
        units.update(reasons)
        held = {id(proposal) for proposal in kept}
        deferred += [ids[id(p)] for p in known if id(p) not in held]
    marks = merged.marks
    dropped = [(mark.claim_id, mark.reason) for mark in marks.dropped_claims]
    unknown = [mark.claim_id for mark in marks.unknown_claim_identities]
    left = Counter(proposals)
    for fate in (
        [draft.id for draft in merged.drafts],
        [claim_id for claim_id, _ in dropped],
        unknown,
        deferred,
    ):
        left.subtract(fate)
    unexplained = [i for i, count in sorted(left.items()) for _ in range(count)]
    extra = [i for i, count in sorted(left.items()) for _ in range(-count)]
    quotes = sum(
        ground.kind == "quote" for draft in merged.drafts for ground in draft.grounds
    )
    repaired = len(marks.repaired_quotes)
    refused = len(marks.unverified_grounds)
    return FanInAccount(
        case=case_id,
        framework=package.name,
        proposals=tuple(proposals),
        kept=tuple(draft.id for draft in merged.drafts),
        dropped=tuple(dropped),
        unknown=tuple(unknown),
        deferred=tuple(deferred),
        unexplained=tuple(unexplained),
        extra=tuple(extra),
        quotes_verified=quotes - repaired - refused,
        quotes_repaired=repaired,
        quotes_refused=refused,
        incompatible=(
            ""
            if units == merged.deferred
            else "the package's split defers other units than the fan-in did"
        ),
    )


def _drift_reason(
    replayed: FanIn, archived: Sequence[Mapping[str, Any]], package: FrameworkPackage
) -> str:
    """How the replayed drafts differ from the archived ones, or ``""``.

    Each archived draft is read through today's record first, so a field the
    record added since the run, at its default, is no difference. A draft
    today's record refuses is a difference too.
    """
    ours = [draft.model_dump(mode="json") for draft in replayed.drafts]
    try:
        theirs = [
            package.record.model_validate(draft).model_dump(mode="json")
            for draft in archived
        ]
    except ValidationError as error:
        return f"today's record refuses an archived draft: {error}"
    if ours == theirs:
        return ""
    by_id = {draft["id"]: draft for draft in theirs}
    changed = sum(
        1 for draft in ours if draft["id"] in by_id and draft != by_id[draft["id"]]
    )
    only_ours = len({draft["id"] for draft in ours} - by_id.keys())
    only_theirs = len(by_id.keys() - {draft["id"] for draft in ours})
    return (
        "the replayed drafts differ from the archived drafts:"
        f" {only_ours} only in the replay, {only_theirs} only in the archive,"
        f" {changed} changed"
    )


def account_case(
    case: GoldenCase,
    report: Report,
    stored: Mapping[str, Any],
    drafts: Mapping[str, Any],
    sources_moved: str,
) -> list[FanInAccount]:
    """One account per framework block of ``report``.

    ``sources_moved`` is why the case's sources are not the run's, or ``""``.
    """
    rows = []
    for block in report.analyses:
        framework = block.framework
        package = PACKAGES[framework]
        empty = FanInAccount(
            case=case.id,
            framework=framework,
            proposals=(),
            kept=(),
            dropped=(),
            unknown=(),
            deferred=(),
            unexplained=(),
            extra=(),
            quotes_verified=0,
            quotes_repaired=0,
            quotes_refused=0,
        )
        lanes = stored.get(framework)
        if lanes is None:
            rows.append(replace(empty, incompatible="the archive holds no proposals"))
            continue
        try:
            batches, merged = replay_fan_in(case, report, lanes, framework)
        except (DraftJoinError, ValidationError) as error:
            rows.append(replace(empty, incompatible=str(error)))
            continue
        row = account(case.id, batches, package, merged)
        reason = (
            row.incompatible
            or sources_moved
            or _drift_reason(merged, drafts.get(framework, []), package)
        )
        rows.append(replace(row, incompatible=reason))
    return rows


class FanInSweep(NamedTuple):
    """Every block of a sweep, its fan-in accounted, and each case it could not read."""

    rows: list[FanInAccount]
    #: One line for each finished case whose report today's Report refuses.
    unreadable: list[str]
    #: One line for each case whose report, drafts or proposals file the
    #: archive lacks, or holds for a case it records as failed.
    capture_faults: list[str]
    #: False for a sweep whose mode writes no report, and so no proposals.
    writes_reports: bool


def replay_artifact(artifact: EvalArtifact, cases: Sequence[GoldenCase]) -> FanInSweep:
    """Every framework block of every finished case, its fan-in accounted."""
    sweep = FanInSweep(
        rows=[],
        unreadable=[],
        capture_faults=[],
        writes_reports=artifact.mode in REPORTING_MODES,
    )
    if not sweep.writes_reports:
        return sweep
    recorded = recorded_sources(artifact) or {}
    for case in cases:
        try:
            paths = [
                finished_file(artifact, case.id, kind)
                for kind in ("report", "drafts", "proposals")
            ]
        except EvalRunError as error:
            sweep.capture_faults.append(str(error))
            continue
        report_path, drafts_path, proposals_path = paths
        if report_path is None or drafts_path is None or proposals_path is None:
            continue
        try:
            report = read_report(report_path)
            stored = json.loads(proposals_path.read_text(encoding="utf-8"))
            drafts = json.loads(drafts_path.read_text(encoding="utf-8"))
        except UnreadableReport as error:
            sweep.unreadable.append(str(error))
            continue
        except json.JSONDecodeError as error:
            sweep.capture_faults.append(f"{case.id}: a capture is not JSON: {error}")
            continue
        blocks = {block.framework for block in report.analyses}
        if stray := sorted((set(stored) | set(drafts)) - blocks):
            sweep.capture_faults.append(
                f"{case.id}: the captures hold {stray}, which the report has no"
                " block for"
            )
        digest = recorded.get(case.id)
        moved = (
            "the artifact records the case's sources under another digest"
            if digest is not None and digest != rendered_sources([case])[case.id]
            else ""
        )
        sweep.rows.extend(account_case(case, report, stored, drafts, moved))
    return sweep


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", type=Path, help="a sweep with archived proposals")
    parser.add_argument("--case", action="append", default=[], help="limit to a case")
    parser.add_argument("--corpus", type=Path, default=Path("evals/corpus"))


def command_fan_in(args: argparse.Namespace) -> int:
    """Run each archived fan-in again and account for every proposal."""
    try:
        artifact = load_artifact(args.artifact)
        cases = [
            case
            for case in development_cases(load_corpus(args.corpus))
            if case.id in artifact.cases and (not args.case or case.id in args.case)
        ]
        sweep = replay_artifact(artifact, cases)
    except (EvalRunError, ProvenanceError) as error:
        print(error, file=sys.stderr)
        return 1
    if not sweep.writes_reports:
        print(f"{artifact.path}: the {artifact.mode} mode writes no report")
        return 0
    for line in (*sweep.capture_faults, *sweep.unreadable):
        print(line)
    for row in sweep.rows:
        if row.incompatible:
            print(f"{row.case:34} {row.framework:6} incompatible: {row.incompatible}")
            continue
        print(
            f"{row.case:34} {row.framework:6} proposals {len(row.proposals):>3}"
            f" kept {len(row.kept):>3} dropped {len(row.dropped):>3}"
            f" unknown {len(row.unknown):>3} deferred {len(row.deferred):>3}"
            f" unexplained {len(row.unexplained):>3} extra {len(row.extra):>3}"
        )
        for claim_id, reason in row.dropped:
            print(f"  dropped {claim_id}: {reason[:160]}")
        for claim_id in row.unexplained:
            print(f"  unexplained: {claim_id}")
        for claim_id in row.extra:
            print(f"  extra in the output: {claim_id}")
    compatible = [row for row in sweep.rows if not row.incompatible]
    print(
        f"{len(sweep.rows)} block(s), {len(sweep.rows) - len(compatible)} incompatible;"
        f" {len(sweep.unreadable)} report(s) refused by today's Report;"
        f" {len(sweep.capture_faults)} case(s) with a missing or stray capture"
    )
    print(
        f"proposals: {sum(len(row.proposals) for row in sweep.rows)} inspected,"
        f" {sum(row.accounted for row in compatible)} accounted,"
        f" {sum(len(row.unexplained) for row in compatible)} unexplained,"
        f" {sum(len(row.proposals) for row in sweep.rows if row.incompatible)}"
        f" unavailable; {sum(len(row.extra) for row in compatible)} extra"
    )
    print(
        f"quotes on kept drafts: {sum(row.quotes_verified for row in compatible)}"
        f" verified, {sum(row.quotes_repaired for row in compatible)} repaired,"
        f" {sum(row.quotes_refused for row in compatible)} refused"
    )
    unaccounted = any(row.unexplained or row.extra for row in compatible)
    return 1 if unaccounted or sweep.capture_faults else 0
