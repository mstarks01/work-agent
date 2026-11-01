"""Assembly, run again on the archive: a critic's rulings rebuilt from a report (#1091).

Assembly turns a framework's drafts and its critic's rulings into the block's
two arrays. It is deterministic, so it can run again on any archived case:
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
  ruled, and the report carries it in one of its two arrays. A draft in
  ``<case>.drafts.json`` that the report carries in neither was lost after the
  critic ruled on it, and no earlier instrument can see that.
* **Whether the arrays are what assembly makes of the rulings.** The drafts the
  report does account for are assembled again from the rulings it carries. A
  claim the replay files in the other array, or does not rebuild, is a defect
  in assembly or in what follows it.

**What a ruling carries back.** A ruled claim holds the ruling's own fields.
``severity`` is the one draft field a ruling may replace, so it is set only
where the claim's differs from its draft's; ``None`` keeps the draft's, as it
does in a real ruling.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from analysis_service.claims import (
    AnalysisMarks,
    Claim,
    FrameworkAnalysis,
    FrameworkName,
    Ruling,
)
from analysis_service.critic_round import CriticRound, Failed
from analysis_service.errors import error_place
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.artifact import EvalArtifact, ProvenanceError, load_artifact
from evals.harness.bundle import finished_file
from evals.harness.modes import EvalRunError
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
    reported: tuple[str, ...]
    replayed: tuple[str, ...]
    rejected_reported: tuple[str, ...]
    rejected_replayed: tuple[str, ...]
    #: Why today's ruling record or review check refused the archived rulings,
    #: where one did. Assembly fails closed on a ruling either refuses, so an
    #: older archive can hold rulings today's code would never assemble.
    refused: str = ""

    #: Drafts the critic ruled on that the report carries in neither array.
    omitted: tuple[str, ...] = ()

    @property
    def unexplained(self) -> tuple[str, ...]:
        """Claims whose array differs from what assembly makes of the rulings."""
        return tuple(
            sorted(
                (set(self.reported) ^ set(self.replayed))
                | (set(self.rejected_reported) ^ set(self.rejected_replayed))
            )
        )


def replay_case(
    case: GoldenCase,
    block: FrameworkAnalysis,
    drafts: Sequence[Claim],
    model: SystemModel,
) -> AssemblyReplay:
    """Assembly run again over ``drafts`` and the rulings ``block`` carries."""
    framework = block.framework
    ruled = {claim.id for claim in block.all_claims()}
    omitted = tuple(sorted(draft.id for draft in drafts if draft.id not in ruled))
    accounted = [draft for draft in drafts if draft.id in ruled]
    held = AssemblyReplay(
        case=case.id,
        framework=framework,
        reported=tuple(claim.id for claim in block.claims),
        replayed=(),
        rejected_reported=tuple(claim.id for claim in block.rejected_claims),
        rejected_replayed=(),
        omitted=omitted,
    )
    try:
        rulings = rulings_from_report(block, accounted)
    except ArchivedRulingError as error:
        return replace(held, refused=str(error))
    # The block carries no fan-in marks and no catalog a replay of assembly
    # reads: the view is not built here, only the rulings are checked and merged.
    critic_round = CriticRound.of(framework, model, accounted, AnalysisMarks(), None)
    outcome = critic_round.outcome(
        [ruling.model_dump(mode="json") for ruling in rulings]
    )
    if isinstance(outcome, Failed):
        return replace(held, refused=str(outcome.error))
    return replace(
        held,
        replayed=tuple(claim.id for claim in outcome.claims),
        rejected_replayed=tuple(claim.id for claim in outcome.rejected),
    )


def replay_artifact(
    artifact: EvalArtifact, cases: Sequence[GoldenCase]
) -> list[AssemblyReplay]:
    """Every framework block of every finished case, assembled again."""
    rows = []
    for case in cases:
        report_path = finished_file(artifact, case.id, "report")
        if report_path is None:
            continue
        drafts_path = finished_file(artifact, case.id, "drafts")
        # A finished case: finished_file returns the path or raises.
        assert drafts_path is not None
        report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        held = json.loads(drafts_path.read_text(encoding="utf-8"))
        for block in report.analyses:
            record = PACKAGES[block.framework].record
            drafts = [record.model_validate(d) for d in held.get(block.framework, [])]
            rows.append(replay_case(case, block, drafts, report.system_model))
    return rows


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
        rows = replay_artifact(artifact, cases)
    except (EvalRunError, ProvenanceError) as error:
        print(error, file=sys.stderr)
        return 1
    moved = 0
    for row in rows:
        for claim_id in row.omitted:
            print(f"  {row.case}: ruled on and omitted from the report: {claim_id}")
        moved += bool(row.omitted)
        if row.refused:
            print(f"{row.case:34} {row.framework:6} refused by today's check:")
            print(f"  {row.refused[:200]}")
            continue
        status = "same" if not (row.omitted or row.unexplained) else "DIFFERS"
        print(
            f"{row.case:34} {row.framework:6} claims {len(row.reported):>3}"
            f" rejected {len(row.rejected_reported):>3}  {status}"
        )
        for claim_id in row.unexplained:
            print(f"  filed differently from its ruling: {claim_id}")
        moved += bool(row.unexplained)
    refused = sum(bool(row.refused) for row in rows)
    print(f"{len(rows)} block(s), {moved} differ, {refused} refused by today's check")
    return 1 if moved else 0
