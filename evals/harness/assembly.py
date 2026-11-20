"""Assembly, run again on the archive: a critic's rulings rebuilt from a report (#1091).

Assembly turns a framework's drafts and its critic's rulings into the block's
arrays. It is deterministic, so it can run again on any archived case:
the drafts are ``<case>.drafts.json``, and every ruling the critic made is
still in the report, because each ruled claim carries its own verdict and the
fields its package's ruling declares. :func:`rulings_from_report` rebuilds
them, and :func:`replay_case` asks the
:class:`~analysis_service.critic_round.CriticRound` for its outcome over the
pair, as the ``assemble`` node does.

**What it answers.** Two questions, both about the code between the critic
and the report.

* **A report omission** needs no ruling to see. The graph's router refuses a
  critic that leaves a draft unruled, so every draft that reached assembly was
  ruled, and the report carries it in its claims, its rejected claims or its
  returned claims. A draft in ``<case>.drafts.json`` that the report carries
  in none was lost after the critic ruled on it, and no earlier instrument can
  see that.
* **Whether the arrays are what assembly makes of the rulings.** The drafts the
  report rules on are assembled again from the rulings it carries. A
  claim the replay files in another array, or does not rebuild, is a defect
  in assembly or in what follows it.

**What a ruling carries back.** A ruled claim holds the ruling's own fields.
``severity`` is the one draft field a ruling may replace, so it is set only
where the claim's differs from its draft's; ``None`` keeps the draft's, as it
does in a real ruling.

**A returned claim.** Assembly sends a claim the critic kept against every
Control row back to the deferred set (ADR 0126), and the report lists it in
``returned_claims`` with its unit and no ruling. The replay runs that step
again on what it assembles, and checks each returned draft only for being on
the unit its entry names.

**What it cannot see.** The report is the only record of the critic's
rulings, so a claim filed against its own verdict is the report's check to
refuse, and one whose verdict moved with it reads as the critic's ruling. The
report's check also owns each block's summary, so a summary that miscounts its
block fails the load and is named as an unreadable report. The report does
not hold the units a Control row kept from the deferred set, only those it sent back, so a
kept claim that assembly failed to send back reads as the critic's ruling.

**Severity.** No ruling carries a severity, so each reported claim's severity
must be its draft's, and the replay compares the two for a package whose
record grades harm.

A run whose code does not descend from :data:`CRITIC_RATES_NO_SEVERITY`
holds severities its critic set, so the comparison holds only for a run that
descends from it, read by git ancestry from the artifact's commit. An older
run is incompatible. A commit the checkout cannot place, as in a shallow
clone, gives no oracle. Only a package whose record grades harm is counted,
and a refused block has no replay, so neither appears in the severity line.

**What it counts.** Each draft of a replayed block is accounted, omitted,
filed differently or unavailable, where its block was refused or its ID
repeats. A ruled claim with no draft is counted on its own. A case whose
report or drafts file the archive lacks is named, and a sweep whose mode
writes no report gives no row. Every row comes from archived files and
deterministic code, and nothing calls a model. A run of the command takes
about 5 s, most of it start-up.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, NamedTuple, get_args

from pydantic import ValidationError

from analysis_service.claims import (
    AnalysisMarks,
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    Ruling,
    quote_check_ran,
)
from analysis_service.critic_round import CriticRound, Failed
from analysis_service.errors import error_place
from analysis_service.fan_in import drop_duplicate_ids, returned_to_deferred
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.system_model import SystemModel
from evals.harness.artifact import (
    EvalArtifact,
    ProvenanceError,
    RepoCommit,
    load_artifact,
)
from evals.harness.baseline import holds_commit, is_ancestor, is_shallow
from evals.harness.bundle import UnreadableReport, finished_file, read_report
from evals.harness.history import current_commit
from evals.harness.modes import REPORTING_MODES, EvalRunError
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import GoldenCase, development_cases, load_corpus


def ruling_type(framework: FrameworkName) -> type[Ruling]:
    """The package's own ruling record, read off its rulings batch."""
    batch = schemas_for(framework).rulings
    return get_args(batch.model_fields["claims"].annotation)[0]


class ArchivedRulingError(ValueError):
    """A ruled claim whose ruling today's ruling record refuses.

    A report reads the shapes its archive holds, and a ruling record holds
    only what a critic may write today: a claim rejected as ``duplicate``
    (ADR 0059) rebuilds a ruling that :class:`~analysis_service.claims.ProposedVerdict`
    refuses.
    """


def rulings_from_report(
    block: FrameworkAnalysis, drafts: Sequence[Claim]
) -> list[Ruling]:
    """Every ruling the critic made on this block, rebuilt from its ruled claims.

    Raises :class:`ArchivedRulingError`, naming the claim and the field, where
    a rebuilt ruling is one today's critic may not write.
    """
    record = ruling_type(block.framework)
    verdict_fields = get_args(record.model_fields["verdict"].annotation) or (
        record.model_fields["verdict"].annotation,
    )
    verdict_type = verdict_fields[0]
    by_id = {draft.id: draft for draft in drafts}
    rulings = []
    for claim in block.all_claims():
        values: dict[str, Any] = {"id": claim.id}
        values["verdict"] = {
            name: getattr(claim.verdict, name) for name in verdict_type.model_fields
        }
        for name in record.model_fields:
            if name in Ruling.model_fields or not hasattr(claim, name):
                continue
            value = getattr(claim, name)
            draft = by_id.get(claim.id)
            if draft is not None and getattr(draft, name, None) == value:
                value = None
            values[name] = value
        try:
            rulings.append(record.model_validate(values))
        except ValidationError as error:
            problems = "; ".join(
                f"{error_place(problem)} holds {problem['input']!r}: {problem['msg']}"
                for problem in error.errors()
            )
            raise ArchivedRulingError(
                f"{claim.id}: today's {record.__name__} refuses {problems}"
            ) from error
    return rulings


@dataclass(frozen=True)
class AssemblyReplay:
    """One case's block, as the report holds it and as assembly rebuilds it."""

    case: str
    framework: FrameworkName
    #: Each draft ID ``<case>.drafts.json`` holds, once, in its order.
    draft_ids: tuple[str, ...]
    reported: tuple[str, ...]
    replayed: tuple[str, ...]
    rejected_reported: tuple[str, ...]
    rejected_replayed: tuple[str, ...]
    returned_reported: tuple[str, ...]
    returned_replayed: tuple[str, ...]
    #: Drafts the critic ruled on that the report carries in no array.
    omitted: tuple[str, ...]
    #: Draft IDs the drafts file holds more than once. The fan-in keeps one
    #: draft under each ID, so the capture is broken; the first copy replays,
    #: and the counts leave the ID out.
    repeated: tuple[str, ...]
    #: Whether the run's code makes each draft's severity the report's.
    severity_oracle: SeverityOracle
    #: Claims whose reported severity is not the one assembly gives them.
    severity_differs: tuple[str, ...] = ()
    #: How many graded claims had their severity compared, and how many were
    #: not: the run's critic set it, the clone cannot place the run, or the
    #: report does not hold the claim.
    severity_compared: int = 0
    severity_unchecked: int = 0
    #: Why today's ruling record or review check refused the archived rulings,
    #: where one did. Assembly fails closed on a ruling either refuses, so an
    #: older archive can hold rulings today's code would never assemble.
    refused: str = ""

    @property
    def unexplained(self) -> tuple[str, ...]:
        """Claims whose array differs from what assembly makes of the rulings.

        A refused block has no replay, so it names none.
        """
        if self.refused:
            return ()
        return tuple(
            sorted(
                (set(self.reported) ^ set(self.replayed))
                | (set(self.rejected_reported) ^ set(self.rejected_replayed))
                | (set(self.returned_reported) ^ set(self.returned_replayed))
            )
        )

    @property
    def filed_differently(self) -> tuple[str, ...]:
        """Drafts whose array differs from what assembly makes of the rulings."""
        return tuple(
            draft
            for draft in self.draft_ids
            if draft in self.unexplained and draft not in self.repeated
        )

    @property
    def undrafted(self) -> tuple[str, ...]:
        """Claims the report rules on or returns that no draft names."""
        return tuple(sorted(set(self.unexplained) - set(self.draft_ids)))

    @property
    def accounted(self) -> tuple[str, ...]:
        """Drafts the replay files where the report files them."""
        if self.refused:
            return ()
        lost = {*self.omitted, *self.unexplained, *self.repeated}
        return tuple(draft for draft in self.draft_ids if draft not in lost)

    @property
    def unavailable(self) -> tuple[str, ...]:
        """Drafts left out of the counts: a refused block's, or a repeated ID.

        An omitted draft is counted as omitted only.
        """
        cannot_file = self.draft_ids if self.refused else self.repeated
        return tuple(draft for draft in cannot_file if draft not in self.omitted)


def replay_case(
    case: GoldenCase,
    block: FrameworkAnalysis,
    drafts: Sequence[Claim],
    model: SystemModel,
    oracle: SeverityOracle,
) -> AssemblyReplay:
    """Assembly run again over ``drafts`` and the rulings ``block`` carries.

    ``oracle`` says whether the run's severities can be compared.
    """
    framework = block.framework
    record = PACKAGES[framework].record
    # The fan-in's own rule picks the copy of a repeated ID that replays.
    kept, dropped = drop_duplicate_ids(drafts)
    by_id = {draft.id: draft for draft in kept}
    returned = {entry.claim_id: entry.unit for entry in block.returned_claims}
    ruled = {claim.id for claim in block.all_claims()} | returned.keys()
    ruled_drafts = [
        draft
        for draft in by_id.values()
        if draft.id in ruled and draft.id not in returned
    ]
    row = AssemblyReplay(
        case=case.id,
        framework=framework,
        draft_ids=tuple(by_id),
        severity_oracle=oracle,
        reported=tuple(claim.id for claim in block.claims),
        replayed=(),
        rejected_reported=tuple(claim.id for claim in block.rejected_claims),
        rejected_replayed=(),
        returned_reported=tuple(entry.claim_id for entry in block.returned_claims),
        returned_replayed=(),
        omitted=tuple(sorted(by_id.keys() - ruled)),
        repeated=tuple(sorted({mark.claim_id for mark in dropped})),
    )
    # A ruled claim with no draft stays reported and is never rebuilt, so it
    # is named while its neighbours replay, whatever its ruling holds.
    drafted = {draft.id for draft in ruled_drafts}
    with_drafts = block.model_copy(
        update={
            "claims": [claim for claim in block.claims if claim.id in drafted],
            "rejected_claims": [
                claim for claim in block.rejected_claims if claim.id in drafted
            ],
        }
    )
    try:
        rulings = rulings_from_report(with_drafts, ruled_drafts)
    except ArchivedRulingError as error:
        return replace(row, refused=str(error))
    # The block carries no fan-in marks and no catalog a replay of assembly
    # reads: the view is not built here, only the rulings are checked and merged.
    critic_round = CriticRound.of(
        framework,
        model,
        ruled_drafts,
        AnalysisMarks(),
        quote_check_ran(block.quotes_checked),
        None,
        (),
    )
    outcome = critic_round.outcome(
        [ruling.model_dump(mode="json") for ruling in rulings]
    )
    if isinstance(outcome, Failed):
        return replace(row, refused=str(outcome.error))
    # A returned claim leaves the block with no ruling, so its unit and what
    # it needs are all the replay reads back. Assembly sends back every kept
    # claim on such a unit, which is what this replays; a returned draft is
    # checked only for being on the unit its entry names.
    assembled = returned_to_deferred(
        outcome.claims,
        outcome.rejected,
        {entry.unit: entry.needs for entry in block.returned_claims},
        record,
    )
    on_unit = [
        draft.id
        for draft in by_id.values()
        if returned.get(draft.id) == record.unit_of(draft)
    ]
    filed = [*assembled.claims, *outcome.rejected]
    # Only a package whose record grades harm has a severity to compare.
    graded = filed if PACKAGES[framework].carries_severity() else []
    compared = _severities(block, graded) if oracle == "compared" else {}
    return replace(
        row,
        severity_differs=tuple(sorted(i for i, same in compared.items() if not same)),
        severity_compared=len(compared),
        severity_unchecked=len(graded) - len(compared),
        replayed=tuple(claim.id for claim in assembled.claims),
        rejected_replayed=tuple(claim.id for claim in outcome.rejected),
        returned_replayed=(
            *on_unit,
            *(entry.claim_id for entry in assembled.returned),
        ),
    )


def _severities(block: FrameworkAnalysis, replayed: Sequence[Claim]) -> dict[str, bool]:
    """Each claim with an oracle for its severity, against whether the report's
    severity is the one assembly gives it.

    The caller hands in claims of a package whose record grades harm.
    """
    reported = {claim.id: claim for claim in block.all_claims()}
    # The neutral record has no severity field; the package's own record has
    # one wherever carries_severity says so.
    return {
        claim.id: getattr(reported[claim.id], "severity")  # noqa: B009
        == getattr(claim, "severity")  # noqa: B009
        for claim in replayed
        if claim.id in reported
    }


#: The commit of #1208, after which the critic rates no severity and the lane
#: agent's rating stands on every claim.
CRITIC_RATES_NO_SEVERITY = "8bbc10689a93e0caa2432143dbcaef9e7372170c"

#: Whether a run's severities can be compared with its drafts': its code
#: descends from :data:`CRITIC_RATES_NO_SEVERITY`, it does not, or the
#: checkout cannot tell.
SeverityOracle = Literal["compared", "critic-set", "unknown"]


def severity_oracle(commit: RepoCommit) -> SeverityOracle:
    """Whether the run on ``commit`` left each draft's severity in place.

    :class:`~evals.harness.artifact.RepoCommit` holds only a hex commit ID, so
    the ID reaches ``git`` as an object, never as an option. A history rewrite
    renames a commit, so the check reads it at the ID it has now. A shallow
    clone, or one that does not hold both commits, cannot place the run, so
    the answer is ``unknown``.
    """
    now = current_commit(commit.commit)
    placed = not is_shallow(REPO_ROOT) and all(
        holds_commit(REPO_ROOT, held) for held in (now, CRITIC_RATES_NO_SEVERITY)
    )
    if not placed:
        return "unknown"
    if is_ancestor(REPO_ROOT, CRITIC_RATES_NO_SEVERITY, now):
        return "compared"
    return "critic-set"


class AssemblySweep(NamedTuple):
    """Every block of a sweep, assembled again, and each case it could not read."""

    rows: list[AssemblyReplay]
    #: One line for each finished case whose report today's
    #: :class:`~analysis_service.report.Report` refuses. That case has no
    #: block to replay, and the other cases replay.
    unreadable: list[str]
    #: One line for each case whose report or drafts file the archive lacks,
    #: or holds for a case it records as failed. The other cases replay.
    capture_faults: list[str]
    #: False for a sweep whose mode writes no report: it has no block to
    #: replay, and gives no row.
    writes_reports: bool


def replay_artifact(
    artifact: EvalArtifact, cases: Sequence[GoldenCase]
) -> AssemblySweep:
    """Every framework block of every finished case, assembled again."""
    sweep = AssemblySweep(
        rows=[],
        unreadable=[],
        capture_faults=[],
        writes_reports=artifact.mode in REPORTING_MODES,
    )
    if not sweep.writes_reports:
        return sweep
    oracle = severity_oracle(artifact.commit)
    for case in cases:
        try:
            report_path = finished_file(artifact, case.id, "report")
            drafts_path = finished_file(artifact, case.id, "drafts")
        except EvalRunError as error:
            sweep.capture_faults.append(str(error))
            continue
        if report_path is None or drafts_path is None:
            continue
        try:
            report = read_report(report_path)
        except UnreadableReport as error:
            sweep.unreadable.append(str(error))
            continue
        held = json.loads(drafts_path.read_text(encoding="utf-8"))
        for block in report.analyses:
            record = PACKAGES[block.framework].record
            drafts = [record.model_validate(d) for d in held.get(block.framework, [])]
            sweep.rows.append(
                replay_case(case, block, drafts, report.system_model, oracle)
            )
    return sweep


def _unchecked(rows: Sequence[AssemblyReplay], oracle: SeverityOracle) -> int:
    """Claims not compared, in rows whose run reads ``oracle``."""
    return sum(row.severity_unchecked for row in rows if row.severity_oracle == oracle)


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", type=Path, help="a sweep with archived drafts")
    parser.add_argument("--case", action="append", default=[], help="limit to a case")
    parser.add_argument("--corpus", type=Path, default=Path("evals/corpus"))


def command_assembly(args: argparse.Namespace) -> int:
    """Assemble every archived block again and name any claim it moves."""
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
    moved = 0
    for row in sweep.rows:
        for claim_id in row.omitted:
            print(f"  {row.case}: ruled on and omitted from the report: {claim_id}")
        for claim_id in row.repeated:
            print(f"  {row.case}: held twice in the drafts: {claim_id}")
        differs = bool(
            row.omitted or row.repeated or row.unexplained or row.severity_differs
        )
        moved += differs
        if row.refused:
            print(f"{row.case:34} {row.framework:6} refused by today's check:")
            print(f"  {row.refused[:200]}")
            continue
        status = "DIFFERS" if differs else "same"
        print(
            f"{row.case:34} {row.framework:6} claims {len(row.reported):>3}"
            f" rejected {len(row.rejected_reported):>3}"
            f" returned {len(row.returned_reported):>3}  {status}"
        )
        for claim_id in row.filed_differently:
            print(f"  filed differently from its ruling: {claim_id}")
        for claim_id in row.undrafted:
            print(f"  ruled on with no draft: {claim_id}")
        for claim_id in row.severity_differs:
            print(f"  reported with a severity its draft does not hold: {claim_id}")
    refused = sum(bool(row.refused) for row in sweep.rows)
    print(
        f"{len(sweep.rows)} block(s), {moved} differ, {refused} refused by today's check;"
        f" {len(sweep.unreadable)} report(s) refused by today's Report;"
        f" {len(sweep.capture_faults)} case(s) with a missing or stray capture"
    )

    def total(part: Callable[[AssemblyReplay], tuple[str, ...]]) -> int:
        return sum(len(part(row)) for row in sweep.rows)

    print(
        f"drafts: {total(lambda row: row.draft_ids)} inspected,"
        f" {total(lambda row: row.accounted)} accounted,"
        f" {total(lambda row: row.omitted)} omitted,"
        f" {total(lambda row: row.filed_differently)} filed differently,"
        f" {total(lambda row: row.unavailable)} unavailable;"
        f" {total(lambda row: row.undrafted)} ruled claim(s) with no draft"
    )
    print(
        f"ruled severity: {sum(row.severity_compared for row in sweep.rows)}"
        f" compared, {sum(len(row.severity_differs) for row in sweep.rows)} differ;"
        f" {_unchecked(sweep.rows, 'critic-set')} claim(s) incompatible, because"
        f" the run's critic set severity;"
        f" {_unchecked(sweep.rows, 'unknown')} claim(s) with no oracle, because"
        " the clone cannot place the run"
    )
    return 1 if moved or sweep.capture_faults else 0
