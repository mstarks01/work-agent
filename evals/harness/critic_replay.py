"""Replay one framework's critic over an archived case's drafts (#1091).

``evals/critic_review`` replays a critic over a signed fixture set. This
replays the critic a real case had: the drafts its fan-in handed on, in the
view the ``merge`` node rendered, against the model and crossings its lanes
read. One call answers what the critic makes of those drafts again, or of
drafts ``run.py descendants`` changed or injected.

**The graph's readers, never a second copy.** The instruction is
:func:`~analysis_service.graph.critic_instruction` templated by ADK's
``inject_session_state``. ``{system_model}`` and ``{boundary_crossings}`` are
the job-wide keys ``<case>.lanes.json`` captured. The draft view is
:func:`~analysis_service.critic.critic_view` rendered by
:func:`~analysis_service.graph.render_fenced`, over ``<case>.drafts.json``, the
repaired quotes and unverified grounds the report's block carries, and the
report's assertion catalog. The user turn is ADK's ``to_user_content`` over
:func:`~analysis_service.graph.merge_summary`. The answer is parsed by
:func:`~analysis_service.graph.rulings_of`. ``tests/test_evals_critic_replay.py``
holds the rebuilt request against the one a real graph run sent.

**The prompt files are today's.** The command prints both commits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.utils.content_utils import to_user_content
from google.adk.utils.instructions_utils import inject_session_state
from google.genai import types

from analysis_service.claims import AnalysisMarks, Claim, FrameworkName, Ruling
from analysis_service.critic import critic_view
from analysis_service.deployment import Deployment
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.graph import (
    CRITIC_ROLE,
    STATE_BOUNDARY_CROSSINGS,
    STATE_SYSTEM_MODEL,
    FrameworkNodes,
    critic_instruction,
    merge_summary,
    render_fenced,
    rulings_of,
)
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.report import Report
from evals.harness.artifact import repo_commit
from evals.harness.bundle import reports_dir
from evals.harness.lane_replay import load_material
from evals.harness.modes import EvalRunError
from evals.harness.node_call import NodeCall, node_call
from evals.harness.provenance import REPO_ROOT


@dataclass(frozen=True)
class Archived:
    """What one framework's critic was given, read back from a sweep."""

    framework: FrameworkName
    drafts: list[Claim]
    marks: AnalysisMarks
    report: Report
    shared: dict[str, Any]


def load(artifact: Path, case_id: str, framework: FrameworkName) -> Archived:
    """The drafts, marks, report and job-wide keys one critic call read."""
    directory = reports_dir(artifact)
    material = load_material(artifact, case_id)
    report = Report.model_validate_json(
        (directory / f"{case_id}.report.json").read_text(encoding="utf-8")
    )
    block = next((b for b in report.analyses if b.framework == framework), None)
    if block is None:
        raise EvalRunError(f"{case_id} carries no {framework} block")
    held = json.loads(
        (directory / f"{case_id}.drafts.json").read_text(encoding="utf-8")
    )
    record = PACKAGES[framework].record
    marks = AnalysisMarks(
        repaired_quotes=list(block.repaired_quotes),
        unverified_grounds=list(block.unverified_grounds),
        unresolved_mentions=list(block.unresolved_mentions),
    )
    return Archived(
        framework=framework,
        drafts=[record.model_validate(draft) for draft in held.get(framework, [])],
        marks=marks,
        report=report,
        shared=material["shared"],
    )


async def compose(
    archived: Archived,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
) -> tuple[str, types.Content]:
    """The instruction and the user turn the critic was sent, rebuilt."""
    nodes = FrameworkNodes(archived.framework)
    record = archived.report.assertions
    view = critic_view(
        archived.drafts,
        archived.report.system_model,
        repaired=archived.marks.repaired_quotes,
        unverified=archived.marks.unverified_grounds,
        assertions=None if record is None else record.catalog,
    )
    state = {
        STATE_SYSTEM_MODEL: archived.shared[STATE_SYSTEM_MODEL],
        STATE_BOUNDARY_CROSSINGS: archived.shared[STATE_BOUNDARY_CROSSINGS],
        nodes.key("draft_view"): render_fenced(view),
    }
    # ADK reads only ``_invocation_context.session.state`` for a state name.
    context = SimpleNamespace(
        _invocation_context=SimpleNamespace(
            session=SimpleNamespace(state=state), artifact_service=None
        )
    )
    template = critic_instruction(
        package_loader, prompt_loader, PACKAGES[archived.framework], nodes
    )
    instruction = await inject_session_state(template, cast(ReadonlyContext, context))
    turn = to_user_content(
        merge_summary(archived.framework, archived.drafts, archived.marks)
    )
    return instruction, turn


def parse(raw: str, framework: FrameworkName) -> list[Ruling]:
    """The critic's answer, read by the graph's own reader, or a refusal."""
    payload = json.loads(raw)
    if not isinstance(payload, dict) or "claims" not in payload:
        raise ValueError("critic output carries no 'claims' array")
    return rulings_of(payload["claims"], schemas_for(framework))


@dataclass(frozen=True)
class Agreement:
    """One draft's verdict, as the archived critic ruled and as the replay did."""

    draft_id: str
    archived: str
    replayed: str


def compare(archived: Archived, rulings: Sequence[Ruling]) -> list[Agreement]:
    """Each draft's verdict status, archived against replayed."""
    block = next(
        b for b in archived.report.analyses if b.framework == archived.framework
    )
    before = {claim.id: claim.verdict.status for claim in block.all_claims()}
    after = {ruling.id: ruling.verdict.status for ruling in rulings}
    return [
        Agreement(
            draft.id, before.get(draft.id, "unruled"), after.get(draft.id, "unruled")
        )
        for draft in archived.drafts
    ]


async def replay(
    archived: Archived,
    call: NodeCall,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
) -> list[Ruling]:
    """The critic's request sent again, and its rulings parsed."""
    instruction, turn = await compose(archived, package_loader, prompt_loader)
    return parse(await call(instruction, turn), archived.framework)


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", type=Path, help="the sweep whose critic to replay")
    parser.add_argument("--case", required=True, help="the case the critic ran on")
    parser.add_argument(
        "--framework", required=True, choices=sorted(PACKAGES), help="the package"
    )
    parser.add_argument(
        "--accept-cost",
        required=True,
        help="required: naming it is your consent to one paid critic call. The"
        " amount, or 'unknown' on a route that prices nothing before the call",
    )


def command_critic_replay(args: argparse.Namespace) -> int:
    """Send one archived critic request again and compare the verdicts."""
    try:
        archived = load(args.artifact, args.case, args.framework)
    except (EvalRunError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    ran_at = json.loads(args.artifact.read_text(encoding="utf-8"))["repo_commit"]
    print(
        f"material from {ran_at.get('commit')}; prompt files from"
        f" {repo_commit().commit}"
    )
    spent: list[float | None] = []
    call = node_call(
        Deployment.from_env(),
        FrameworkNodes(args.framework).node(CRITIC_ROLE),
        schemas_for(args.framework).rulings,
        spent,
    )
    rulings = asyncio.run(
        replay(
            archived,
            call,
            MarkdownLoader(REPO_ROOT / "frameworks" / args.framework),
            MarkdownLoader(REPO_ROOT / "prompts"),
        )
    )
    rows = compare(archived, rulings)
    same = sum(row.archived == row.replayed for row in rows)
    for row in rows:
        mark = "=" if row.archived == row.replayed else "≠"
        print(
            f"  {mark} {row.draft_id:10} archived {row.archived:12} replayed {row.replayed}"
        )
    print(f"{same} of {len(rows)} verdicts agree")
    charged = [charge for charge in spent if charge is not None]
    print(
        f"spent: ${sum(charged):.4f} reported"
        + ("" if len(charged) == len(spent) else "; the provider reported no charge")
    )
    return 0
