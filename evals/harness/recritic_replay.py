"""Replay one framework's critic re-ask over a first pass with a Control row fault.

This builds a re-ask on a ``control-row`` problem from a first pass that
holds none. It reads an archived case's critic first pass from its report,
applies one written Control to the drafts, and answers each row the Control
shows with the refusal the fault set states, so the first pass reconciles. It then injects one fault of one shape on one draft:

* ``row-unanswered`` — a shown row is in neither ``control_row`` nor
  ``refused_controls``;
* ``row-not-shown`` — ``refused_controls`` names a row of the Control the draft was
  not shown;
* ``row-on-other-step`` — the shown row sits in ``control_row`` on a ruling that is
  not rejected for ``control``.

The round's own check must name exactly that one fault, or the command refuses
the view before the call. The re-ask request is
:func:`~analysis_service.critic_round.recritic_request` over the round's
:class:`~analysis_service.critic_round.Reask`, and the answer is merged and
checked by the round, as the graph's second look does.
``tests/test_evals_recritic_replay.py`` holds the rebuilt request against the
one a real graph run sent.

**The prompt files are today's.** The command prints both commits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, assert_never, get_args

from google.adk.utils._schema_utils import validate_schema
from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from analysis_service.claims import (
    ControlRowRef,
    FrameworkName,
    RefusedControl,
    Ruling,
    VerdictStatus,
)
from analysis_service.critic_round import (
    Accepted,
    CriticRound,
    Reask,
    recritic_request,
)
from analysis_service.deployment import Deployment
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.graph import RECRITIC_ROLE, FrameworkNodes, claims_of
from analysis_service.job_controls import AppliedControl
from analysis_service.markdown_loader import MarkdownLoader
from evals.harness.artifact import ProvenanceError, load_artifact, repo_commit
from evals.harness.assembly import ArchivedRulingError, rulings_from_report
from evals.harness.bundle import check_out
from evals.harness.critic_replay import (
    Archived,
    load,
    round_of,
    shared_keys,
)
from evals.harness.modes import EvalRunError
from evals.harness.node_call import NodeCall, node_call
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import CorpusError, refuse_holdout
from evals.harness.run_record import loaded, run_record_for

#: Three of the ways a ruling can fail the Control row check, one fault each.
FaultShape = Literal["row-unanswered", "row-not-shown", "row-on-other-step"]


class FaultSet(BaseModel):
    """One written Control, and the first pass's answer to each row it shows.

    ``refused`` holds, by draft ID, the refusal of each row the Control shows
    that draft. With these answers the archived first pass reconciles.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    control: AppliedControl
    refused: dict[str, list[RefusedControl]]


def read_faults(path: Path) -> FaultSet:
    """The fault set at ``path``, or a refusal that names the file."""
    try:
        return FaultSet.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise EvalRunError(f"{path}: not a fault set: {error}") from error


@dataclass(frozen=True)
class FaultView:
    """One first pass with one injected fault, and the re-ask it routes to."""

    draft_id: str
    critic_round: CriticRound
    #: The ruling set with every shown row answered, which reconciles.
    clean: Accepted
    reask: Reask


def answered_first_pass(archived: Archived, faults: FaultSet) -> list[Ruling]:
    """The archived first pass, with each shown row refused as ``faults`` says."""
    if archived.block is None:
        raise EvalRunError("the case wrote no report, so it holds no first pass")
    try:
        rulings = rulings_from_report(archived.block, archived.drafts)
    except ArchivedRulingError as error:
        raise EvalRunError(str(error)) from error
    unknown = set(faults.refused) - {ruling.id for ruling in rulings}
    if unknown:
        raise EvalRunError(f"the fault set refuses rows on no draft: {sorted(unknown)}")
    return [
        _with(ruling, refused_controls=faults.refused[ruling.id])
        if ruling.id in faults.refused
        else ruling
        for ruling in rulings
    ]


def _with(ruling: Ruling, **fields: Any) -> Ruling:
    verdict = ruling.verdict.model_copy(update=fields)
    return ruling.model_copy(update={"verdict": verdict})


def inject(
    rulings: list[Ruling],
    control: AppliedControl,
    shown: frozenset[ControlRowRef],
    draft_id: str,
    shape: FaultShape,
) -> list[Ruling]:
    """``rulings`` with one fault of ``shape`` on the ruling of ``draft_id``.

    ``shown`` is the rows the draft was shown, and the draft's ruling refuses
    each of them.
    """
    ruling = next(ruling for ruling in rulings if ruling.id == draft_id)
    first, *rest = ruling.verdict.refused_controls
    if shape == "row-unanswered":
        faulty = _with(ruling, refused_controls=rest)
    elif shape == "row-not-shown":
        unshown = [
            ref
            for row in control.rows
            if (ref := ControlRowRef(control=control.id, unit=row.unit)) not in shown
        ]
        if not unshown:
            raise EvalRunError(f"{control.id} has no row that {draft_id} was not shown")
        extra = RefusedControl(**unshown[0].model_dump(), reason=first.reason)
        faulty = _with(ruling, refused_controls=[first, *rest, extra])
    elif shape == "row-on-other-step":
        row = ControlRowRef(control=first.control, unit=first.unit)
        faulty = _with(ruling, control_row=row, refused_controls=rest)
    else:
        assert_never(shape)
    return [faulty if each.id == draft_id else each for each in rulings]


def fault_view(
    archived: Archived, faults: FaultSet, draft_id: str, shape: FaultShape
) -> FaultView:
    """The re-ask one injected fault routes to, or a refusal.

    Refused where the answered first pass does not reconcile, where the draft
    was shown no row, or where the check names anything but one
    ``control-row`` problem on the draft.
    """
    critic_round = round_of(archived, [faults.control])
    rulings = answered_first_pass(archived, faults)
    clean = critic_round.first(_emitted(rulings, archived.framework))
    if not isinstance(clean, Accepted):
        raise EvalRunError(
            "the first pass with the fault set's answers does not reconcile: "
            + "; ".join(clean.messages)
        )
    shown = critic_round.shown().get(draft_id, frozenset())
    if not shown:
        raise EvalRunError(f"{draft_id} was shown no row of {faults.control.id}")
    if not faults.refused.get(draft_id):
        raise EvalRunError(f"{draft_id} refuses no shown row")
    outcome = critic_round.first(
        _emitted(
            inject(rulings, faults.control, shown, draft_id, shape),
            archived.framework,
        )
    )
    if isinstance(outcome, Accepted):
        raise EvalRunError(f"the {shape} fault on {draft_id} gives no problem")
    if [(problem.kind, problem.claim_id) for problem in outcome.problems] != [
        ("control-row", draft_id)
    ]:
        raise EvalRunError(
            f"the {shape} fault on {draft_id} does not give one control-row"
            f" problem: {outcome.messages}"
        )
    return FaultView(draft_id, critic_round, clean, outcome)


def stored(raw: str, framework: FrameworkName) -> list[Any]:
    """The rulings in ``raw`` as the graph stores a critic's answer.

    ADK's own reader writes the node's output key, so ``previous_review`` holds
    the bytes the graph would hold.
    """
    return claims_of(validate_schema(schemas_for(framework).rulings, raw))


def _emitted(rulings: list[Ruling], framework: FrameworkName) -> list[Any]:
    answer = {"claims": [ruling.model_dump(mode="json") for ruling in rulings]}
    return stored(json.dumps(answer), framework)


async def compose(
    archived: Archived,
    reask: Reask,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
) -> tuple[str, types.Content]:
    """The instruction and the user turn the recritic node sends for ``reask``."""
    return await recritic_request(
        package_loader,
        prompt_loader,
        FrameworkNodes(archived.framework),
        shared_keys(archived),
        reask,
    )


class StatusChange(TypedDict):
    """The draft's verdict with every row answered, and after the repair."""

    answered: VerdictStatus
    #: ``None`` where the merged set still does not reconcile.
    repaired: VerdictStatus | None


class Repair(TypedDict):
    """What one re-ask answer did to one fault view."""

    problems: list[str]
    answer: str
    schema_error: str | None
    remaining: list[dict[str, Any]]
    unbriefed: list[dict[str, Any]]
    status: StatusChange


def repair(view: FaultView, raw: str) -> Repair:
    """What the re-ask's answer did, merged and checked as the second look does.

    ``schema_error`` is why an answer outside the rulings schema was not read.
    ``remaining`` is every problem the merged set still has, and ``unbriefed``
    is each ruling the re-ask changed that no problem named. The statuses are the
    draft's verdict with every row answered, and after the repair.
    """
    result = Repair(
        problems=view.reask.messages,
        answer=raw,
        schema_error=None,
        remaining=[],
        unbriefed=[],
        status=StatusChange(answered=_status(view.clean, view.draft_id), repaired=None),
    )
    try:
        merged, unbriefed = view.critic_round.merge(
            view.reask.first, stored(raw, view.critic_round.framework)
        )
    except ValidationError as error:
        result["schema_error"] = str(error)
        return result
    outcome = view.critic_round.outcome(merged)
    result["unbriefed"] = [problem.model_dump(mode="json") for problem in unbriefed]
    if isinstance(outcome, Accepted):
        result["status"]["repaired"] = _status(outcome, view.draft_id)
    else:
        result["remaining"] = [
            problem.model_dump(mode="json") for problem in outcome.problems
        ]
    return result


def _status(accepted: Accepted, draft_id: str) -> VerdictStatus:
    return next(
        claim.verdict.status
        for claim in (*accepted.claims, *accepted.rejected)
        if claim.id == draft_id
    )


async def replay(
    archived: Archived,
    view: FaultView,
    call: NodeCall,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
) -> Repair:
    """The re-ask sent once, and what its answer repaired."""
    instruction, turn = await compose(
        archived, view.reask, package_loader, prompt_loader
    )
    return repair(view, await call(instruction, turn))


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "artifact", type=Path, help="the sweep whose first pass to read"
    )
    parser.add_argument("--case", required=True, help="the case the critic ran on")
    parser.add_argument(
        "--framework", required=True, choices=sorted(PACKAGES), help="the package"
    )
    parser.add_argument(
        "--faults",
        type=Path,
        required=True,
        help="the fault set: one Control and the refusal of each row it shows",
    )
    parser.add_argument("--draft", required=True, help="the draft to put the fault on")
    parser.add_argument(
        "--fault", required=True, choices=get_args(FaultShape), help="the fault"
    )
    parser.add_argument(
        "--accept-cost",
        required=True,
        help="required: naming it is your consent to one paid re-ask call. The"
        " amount, or 'unknown' on a route that prices nothing before the call",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="also write the problems sent, the answer and what it repaired here",
    )


def command_recritic_replay(args: argparse.Namespace) -> int:
    """Send one re-ask over an injected Control row fault and check the repair."""
    package_loader = MarkdownLoader(REPO_ROOT / "frameworks" / args.framework)
    prompt_loader = MarkdownLoader(REPO_ROOT / "prompts")
    try:
        refuse_holdout(args.case)
        check_out(args.out)
        artifact = load_artifact(args.artifact)
        archived = load(artifact, args.case, args.framework)
        view = fault_view(archived, read_faults(args.faults), args.draft, args.fault)
        record = run_record_for(
            [args.out],
            "recritic-replay",
            {
                "case": args.case,
                "framework": args.framework,
                "draft": args.draft,
                "fault": args.fault,
            },
            {
                **{path: {} for path in (artifact.path, args.faults, *archived.files)},
                **loaded((package_loader, prompt_loader)),
            },
        )
    except (CorpusError, EvalRunError, ProvenanceError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    print(
        f"material from {artifact.commit.commit}; prompt files from"
        f" {repo_commit().commit}"
    )
    spent: list[float | None] = []
    call = node_call(
        Deployment.from_env(),
        FrameworkNodes(args.framework).node(RECRITIC_ROLE),
        schemas_for(args.framework).rulings,
        spent,
    )
    result = asyncio.run(replay(archived, view, call, package_loader, prompt_loader))
    if args.out is not None:
        payload = {"record": record, "draft": args.draft, "fault": args.fault, **result}
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if result["schema_error"] is not None:
        print(f"the answer was not read: {result['schema_error']}")
    print(
        f"{args.draft} {args.fault}: {len(result['remaining'])} problems remain,"
        f" {len(result['unbriefed'])} unbriefed changes; status"
        f" {result['status']['answered']} -> {result['status']['repaired']}"
    )
    charged = [charge for charge in spent if charge is not None]
    print(
        f"spent: ${sum(charged):.4f} reported"
        + ("" if len(charged) == len(spent) else "; the provider reported no charge")
    )
    return 0
