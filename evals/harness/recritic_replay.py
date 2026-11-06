"""Replay one framework's critic re-ask over a first pass with one injected fault.

This builds a re-ask from a first pass that holds no problem. It reads an
archived case's critic first pass from its report, applies one written
Control to the drafts, and answers each row the Control shows with the
refusal the fault set states, so the first pass reconciles. It then injects
one fault of one shape (:data:`FaultShape`) on one draft:

* ``row-unanswered`` — a shown row is in neither ``control_row`` nor
  ``refused_controls``;
* ``row-not-shown`` — ``refused_controls`` names a row of the Control the
  draft was not shown;
* ``row-on-other-step`` — the shown row sits in ``control_row`` on a ruling
  that is not rejected for ``control``;
* ``control-row-missing`` and ``control-row-not-shown`` — the draft's ruling is
  rejected for ``control`` on its shown row, and then names no
  ``control_row``, or also refuses a row the draft was not shown;
* ``dropped`` — the draft has no ruling;
* ``invented`` — a copy of the draft's ruling stands under an ID no lane
  drafted;
* ``duplicate-id`` — the draft's ruling appears twice;
* ``verdict-shape`` — the ruling is rejected for ``reasoning`` and still names
  its open facts;
* ``confirmed-ruled-out`` — the ruling confirms a draft that rules its unit
  out;
* ``unresolved-unknown`` — an open fact names an attribute its element does
  not have.

The round's own check must name exactly the problems :data:`PROBLEMS_BY_SHAPE` gives
the shape, or the command refuses the view before the call. The re-ask request
is :func:`~analysis_service.critic_round.recritic_request` over the round's
:class:`~analysis_service.critic_round.Reask`, and the answer is merged and
checked by the round, as the graph's second look does.
``tests/test_evals_recritic_replay.py`` holds the rebuilt request against the
one a real graph run sent, for each shape a STRIDE case can carry.

**The prompt files are today's.** The command prints both commits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypedDict, assert_never, get_args

from google.adk.utils._schema_utils import validate_schema
from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from analysis_service.claims import (
    Claim,
    ControlRowRef,
    FrameworkName,
    RefusedControl,
    Ruling,
    UnknownRef,
    UnreconciledKind,
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
from analysis_service.system_model import (
    SystemModel,
    all_attribute_names,
    attribute_names,
)
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
from evals.harness.node_call import NodeCall, node_call, spent_line
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import CorpusError, refuse_holdout
from evals.harness.run_record import loaded, run_record_for

#: One fault each. The shapes in :data:`NEEDS_A_REFUSED_ROW` put a Control row fault on
#: a ruling, either rejected for ``control`` or not. The others are the other
#: kinds of problem the round's check names.
FaultShape = Literal[
    "row-unanswered",
    "row-not-shown",
    "row-on-other-step",
    "control-row-missing",
    "control-row-not-shown",
    "dropped",
    "invented",
    "duplicate-id",
    "verdict-shape",
    "confirmed-ruled-out",
    "unresolved-unknown",
]

#: The problems the round's check must name for each shape, in its order, each
#: on the faulted ID. One fault can give two sentences: a ``control`` rejection
#: with no ``control_row`` names no row and leaves its one shown row unanswered.
PROBLEMS_BY_SHAPE: Mapping[FaultShape, tuple[UnreconciledKind, ...]] = MappingProxyType(
    {
        "row-unanswered": ("control-row",),
        "row-not-shown": ("control-row",),
        "row-on-other-step": ("control-row",),
        "control-row-missing": ("control-row", "control-row"),
        "control-row-not-shown": ("control-row",),
        "dropped": ("dropped",),
        "invented": ("invented",),
        "duplicate-id": ("duplicate-id",),
        "verdict-shape": ("verdict-shape",),
        "confirmed-ruled-out": ("verdict-shape",),
        "unresolved-unknown": ("unresolved-unknown",),
    }
)

#: The shapes whose draft's ruling is rejected for ``control`` before the fault.
ON_A_CONTROL_REJECTION: frozenset[FaultShape] = frozenset(
    {"control-row-missing", "control-row-not-shown"}
)

#: The shapes that need a draft shown a row whose ruling refuses it.
NEEDS_A_REFUSED_ROW: frozenset[FaultShape] = ON_A_CONTROL_REJECTION | {
    "row-unanswered",
    "row-not-shown",
    "row-on-other-step",
}


class FaultSet(BaseModel):
    """One written Control, and the first pass's answer to each row it shows.

    ``refused`` holds, by draft ID, the refusal of each row the Control shows
    that draft. With these answers the archived first pass reconciles.
    ``control_reasons`` holds, by draft ID, the reason a ruling rejected for
    ``control`` gives, for the shapes in :data:`ON_A_CONTROL_REJECTION`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    control: AppliedControl
    refused: dict[str, list[RefusedControl]]
    control_reasons: dict[str, str]


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
    #: The ID the fault names: ``draft_id``, or the invented ID.
    faulted_id: str
    critic_round: CriticRound
    #: The first pass before the fault, accepted and assembled.
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
    unknown = (set(faults.refused) | set(faults.control_reasons)) - {
        ruling.id for ruling in rulings
    }
    if unknown:
        raise EvalRunError(f"the fault set names no draft: {sorted(unknown)}")
    return [
        _with(ruling, refused_controls=faults.refused[ruling.id])
        if ruling.id in faults.refused
        else ruling
        for ruling in rulings
    ]


def _with(ruling: Ruling, **fields: Any) -> Ruling:
    verdict = ruling.verdict.model_copy(update=fields)
    return ruling.model_copy(update={"verdict": verdict})


def _rejected_for_control(ruling: Ruling, faults: FaultSet) -> Ruling:
    """``ruling`` rejected for ``control`` on the first row it refuses."""
    reason = faults.control_reasons.get(ruling.id)
    if reason is None:
        raise EvalRunError(
            f"the fault set's control_reasons give no reason for {ruling.id}"
        )
    first, *rest = faults.refused[ruling.id]
    return _with(
        ruling,
        rejected_because="control",
        reason=reason,
        related_unknowns=[],
        control_row=ControlRowRef(control=first.control, unit=first.unit),
        refused_controls=rest,
    )


def _unshown_row(
    faults: FaultSet, shown: frozenset[ControlRowRef], draft_id: str
) -> RefusedControl:
    """A row of the Control the draft was not shown, refused for its first reason."""
    control = faults.control
    unshown = [
        ref
        for row in control.rows
        if (ref := ControlRowRef(control=control.id, unit=row.unit)) not in shown
    ]
    if not unshown:
        raise EvalRunError(f"{control.id} has no row that {draft_id} was not shown")
    return RefusedControl(
        **unshown[0].model_dump(), reason=faults.refused[draft_id][0].reason
    )


def _unknowns_with_a_foreign_attribute(
    ruling: Ruling, model: SystemModel
) -> list[UnknownRef]:
    """The ruling's open facts, the first on an element given an attribute it lacks.

    The attribute is the first of every element type's attributes that this
    element does not have, so the provider schema still admits it.
    """
    by_id = {element.id: element for element in model.elements()}
    unknowns = list(ruling.verdict.related_unknowns)
    for index, ref in enumerate(unknowns):
        element = by_id.get(ref.element_id)
        if element is not None and ref.attribute:
            own = attribute_names(element)
            foreign = next(
                (name for name in all_attribute_names() if name not in own), None
            )
            if foreign is None:
                raise EvalRunError(f"element {element.id} has every attribute")
            unknowns[index] = ref.model_copy(update={"attribute": foreign})
            return unknowns
    raise EvalRunError(f"{ruling.id} names no attribute of an element")


def inject(
    drafts: Sequence[Claim],
    model: SystemModel,
    rulings: list[Ruling],
    faults: FaultSet,
    shown: frozenset[ControlRowRef],
    draft_id: str,
    shape: FaultShape,
) -> tuple[list[Ruling], str]:
    """``rulings`` with one fault of ``shape`` on ``draft_id``, and the faulted ID.

    The faulted ID is ``draft_id``, except for ``invented``: that fault copies
    the draft's ruling, with no Control row, under an ID no lane drafted, the
    draft's ID with ``a`` added. ``shown`` is the rows the draft was shown.
    """
    index, ruling = next(
        (index, ruling) for index, ruling in enumerate(rulings) if ruling.id == draft_id
    )
    before, after = rulings[:index], rulings[index + 1 :]
    faulted_id = draft_id
    refused = list(ruling.verdict.refused_controls)
    if shape == "row-unanswered":
        changed = [_with(ruling, refused_controls=refused[1:])]
    elif shape == "row-not-shown" or shape == "control-row-not-shown":
        extra = _unshown_row(faults, shown, draft_id)
        changed = [_with(ruling, refused_controls=[*refused, extra])]
    elif shape == "row-on-other-step":
        row = ControlRowRef(control=refused[0].control, unit=refused[0].unit)
        changed = [_with(ruling, control_row=row, refused_controls=refused[1:])]
    elif shape == "control-row-missing":
        changed = [_with(ruling, control_row=None)]
    elif shape == "dropped":
        changed = []
    elif shape == "invented":
        faulted_id = f"{draft_id}a"
        if faulted_id in {draft.id for draft in drafts}:
            raise EvalRunError(f"a lane drafted {faulted_id}, so it is not invented")
        invented_ruling = _with(ruling, control_row=None, refused_controls=[])
        changed = [ruling, invented_ruling.model_copy(update={"id": faulted_id})]
    elif shape == "duplicate-id":
        changed = [ruling, ruling]
    elif shape == "verdict-shape":
        if not ruling.verdict.related_unknowns:
            raise EvalRunError(f"{draft_id} names no open fact")
        changed = [_with(ruling, rejected_because="reasoning")]
    elif shape == "confirmed-ruled-out":
        draft = next(draft for draft in drafts if draft.id == draft_id)
        if not type(draft).excludes(draft):
            raise EvalRunError(f"{draft_id} does not rule its unit out")
        changed = [_with(ruling, rejected_because=None, related_unknowns=[])]
    elif shape == "unresolved-unknown":
        unknowns = _unknowns_with_a_foreign_attribute(ruling, model)
        changed = [_with(ruling, related_unknowns=unknowns)]
    else:
        assert_never(shape)
    return [*before, *changed, *after], faulted_id


def fault_view(
    archived: Archived, faults: FaultSet, draft_id: str, shape: FaultShape
) -> FaultView:
    """The re-ask one injected fault routes to, or a refusal.

    Refused where the first pass before the fault does not reconcile, where a
    shape in :data:`NEEDS_A_REFUSED_ROW` meets a draft shown no row or refusing none,
    or where the check names anything but the problems :data:`PROBLEMS_BY_SHAPE` gives
    the shape, on the faulted ID.
    """
    if draft_id not in {draft.id for draft in archived.drafts}:
        raise EvalRunError(f"no draft {draft_id}")
    critic_round = round_of(archived, [faults.control])
    rulings = answered_first_pass(archived, faults)
    shown = critic_round.shown().get(draft_id, frozenset())
    if shape in NEEDS_A_REFUSED_ROW:
        if not shown:
            raise EvalRunError(f"{draft_id} was shown no row of {faults.control.id}")
        if not faults.refused.get(draft_id):
            raise EvalRunError(f"{draft_id} refuses no shown row")
    if shape in ON_A_CONTROL_REJECTION:
        rulings = [
            _rejected_for_control(ruling, faults) if ruling.id == draft_id else ruling
            for ruling in rulings
        ]
    clean = critic_round.first(_emitted(rulings, archived.framework))
    if not isinstance(clean, Accepted):
        raise EvalRunError(
            "the first pass before the fault does not reconcile: "
            + "; ".join(clean.messages)
        )
    faulty, faulted_id = inject(
        archived.drafts, archived.model, rulings, faults, shown, draft_id, shape
    )
    outcome = critic_round.first(_emitted(faulty, archived.framework))
    if isinstance(outcome, Accepted):
        raise EvalRunError(f"the {shape} fault on {draft_id} gives no problem")
    named = [(problem.kind, problem.claim_id) for problem in outcome.problems]
    if named != [(kind, faulted_id) for kind in PROBLEMS_BY_SHAPE[shape]]:
        raise EvalRunError(
            f"the {shape} fault on {draft_id} does not give the"
            f" problems {list(PROBLEMS_BY_SHAPE[shape])} on {faulted_id}:"
            f" {outcome.messages}"
        )
    return FaultView(draft_id, faulted_id, critic_round, clean, outcome)


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
        help="the fault set: one Control, the refusal of each row it shows, and"
        " the reason each draft rejected for control gives",
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
    """Send one re-ask over one injected fault and check the repair."""
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
        payload = {
            "record": record,
            "draft": args.draft,
            "fault": args.fault,
            "faulted_id": view.faulted_id,
            **result,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if result["schema_error"] is not None:
        print(f"the answer was not read: {result['schema_error']}")
    print(
        f"{args.draft} {args.fault} on {view.faulted_id}:"
        f" {len(result['remaining'])} problems remain,"
        f" {len(result['unbriefed'])} unbriefed changes; status"
        f" {result['status']['answered']} -> {result['status']['repaired']}"
    )
    print(spent_line(spent))
    return 0
