"""Replay one lane agent over the material a sweep captured for it (#1091).

A ``place`` loss says a rule led a lane to a reference's elements and the lane
drafted nothing there. Whether the lane read its lead badly or the lead arrived
badly is a question about one lane's request, and a sweep answers it at the
price of every case and every node. This asks it at the price of one call: it
rebuilds the request the lane made from ``<case>.lanes.json`` and sends that
request again, or a changed one.

**The graph's readers, never a second copy.** The instruction is
:func:`~analysis_service.graph.analyze_instruction`, templated by ADK's own
``inject_session_state`` over the captured state keys, so a placeholder ADK
would substitute is substituted the same way here. The user turn is ADK's
``to_user_content`` over ``prepare``'s captured output, which is how ADK hands
a lane its input. The answer parses under the package's own proposal schema.
``tests/test_evals_lane_replay.py`` holds the rebuilt request against the one a
real graph run sent.

**The prompt files are today's.** The captured material is the run's, and the
instruction around it is read from this checkout. The command prints both
commits, and a replay across a prompt change measures that change.

**One extra user part, on request.** ``--append`` sends a file's text as a
second part of the user turn, after the captured input. ADR 0030's measurement
tries its instruction this way, so both of its arms read the same prompt files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.utils.content_utils import to_user_content
from google.adk.utils.instructions_utils import inject_session_state
from google.genai import types
from pydantic import BaseModel

from analysis_service.claims import FrameworkName
from analysis_service.deployment import Deployment
from analysis_service.frameworks import PACKAGES, package_for, schemas_for
from analysis_service.graph import FrameworkNodes, Lane, analyze_instruction
from analysis_service.markdown_loader import MarkdownLoader
from evals.harness.artifact import repo_commit
from evals.harness.bundle import reports_dir
from evals.harness.modes import EvalRunError
from evals.harness.node_call import NodeCall, node_call
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import CorpusError, refuse_holdout


def lane_of(framework: FrameworkName, name: str) -> Lane:
    """The lane ``name`` of ``framework``, or a refusal naming the real ones."""
    lanes = {lane.lane: lane for lane in FrameworkNodes(framework).lanes}
    if name not in lanes:
        raise EvalRunError(
            f"{framework} has no lane {name!r}; it has {', '.join(lanes)}"
        )
    return lanes[name]


def load_material(artifact: Path, case_id: str) -> dict[str, Any]:
    """One case's captured lane material, or a refusal saying why there is none."""
    path = reports_dir(artifact) / f"{case_id}.lanes.json"
    if not path.is_file():
        raise EvalRunError(
            f"{path} is missing; the sweep predates the lane capture (#1091),"
            " so no lane's request can be rebuilt from it"
        )
    material: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if material.get("prepared") is None:
        raise EvalRunError(
            f"{path} carries no user turn; a lane's request without the input"
            " prepare handed it is not the request the lane made"
        )
    return material


async def compose(
    material: dict[str, Any],
    framework: FrameworkName,
    lane_name: str,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
) -> tuple[str, types.Content]:
    """The instruction and the user turn one lane was sent, rebuilt."""
    lane = lane_of(framework, lane_name)
    held = material["lanes"][framework][lane_name]
    state = {
        **material["shared"],
        **{lane.key(name): value for name, value in held.items()},
    }
    # ADK reads only ``_invocation_context.session.state`` for a state name,
    # and the artifact service only for an ``artifact.`` name no lane prompt
    # carries. The test that composes against a real run is what holds this.
    context = SimpleNamespace(
        _invocation_context=SimpleNamespace(
            session=SimpleNamespace(state=state), artifact_service=None
        )
    )
    template = analyze_instruction(
        package_loader, prompt_loader, package_for(framework), lane
    )
    instruction = await inject_session_state(template, cast(ReadonlyContext, context))
    return instruction, to_user_content(material["prepared"])


def parse(raw: str, framework: FrameworkName) -> BaseModel:
    """The lane's answer under its package's own proposal schema, or a refusal."""
    return schemas_for(framework).proposals.model_validate_json(raw)


async def replay(
    material: dict[str, Any],
    framework: FrameworkName,
    lane_name: str,
    call: NodeCall,
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    appended: str | None = None,
) -> BaseModel:
    """One lane's request sent again, with ``appended`` as a last user part."""
    instruction, turn = await compose(
        material, framework, lane_name, package_loader, prompt_loader
    )
    if appended is not None:
        turn.parts = [*(turn.parts or []), types.Part(text=appended)]
    return parse(await call(instruction, turn), framework)


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", type=Path, help="the sweep whose lane to replay")
    parser.add_argument("--case", required=True, help="the case the lane ran on")
    parser.add_argument("--lane", required=True, help="the lane, e.g. spoofing")
    parser.add_argument(
        "--framework", required=True, choices=sorted(PACKAGES), help="the package"
    )
    parser.add_argument(
        "--accept-cost",
        required=True,
        help="required: naming it is your consent to one paid lane call. The"
        " amount, or 'unknown' on a route that prices nothing before the call",
    )
    parser.add_argument(
        "--append",
        type=Path,
        help="a text file sent as one more user part, after the captured input",
    )
    parser.add_argument("--out", type=Path, help="write the lane's proposals here")


def command_lane_replay(args: argparse.Namespace) -> int:
    """Send one captured lane request again, and print what the lane proposed."""
    try:
        refuse_holdout(args.case)
        material = load_material(args.artifact, args.case)
    except (CorpusError, EvalRunError) as error:
        print(error, file=sys.stderr)
        return 1
    ran_at = json.loads(args.artifact.read_text(encoding="utf-8"))["repo_commit"]
    print(
        f"material from {ran_at.get('commit')}; prompt files from"
        f" {repo_commit().commit}"
    )
    lane = lane_of(args.framework, args.lane)
    spent: list[float | None] = []
    call = node_call(
        Deployment.from_env(),
        lane.node_name,
        schemas_for(args.framework).proposals,
        spent,
    )
    batch = asyncio.run(
        replay(
            material,
            args.framework,
            args.lane,
            call,
            MarkdownLoader(REPO_ROOT / "frameworks" / args.framework),
            MarkdownLoader(REPO_ROOT / "prompts"),
            args.append.read_text(encoding="utf-8") if args.append else None,
        )
    )
    dumped = batch.model_dump(mode="json")
    for claim in dumped["claims"]:
        print(f"- {claim.get('title')} | {claim.get('affected_element_ids')}")
    charged = [charge for charge in spent if charge is not None]
    print(
        f"spent: ${sum(charged):.4f} reported"
        + ("" if len(charged) == len(spent) else "; the provider reported no charge")
    )
    if args.out:
        args.out.write_text(json.dumps(dumped, indent=2) + "\n", encoding="utf-8")
    return 0
