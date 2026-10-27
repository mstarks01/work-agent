"""The instruction and the user turn one LLM node sends.

:func:`node_instruction` joins a node's skill text and prompt text, and the
graph builds the instruction of each LLM node with it. :func:`node_request`
renders an instruction and a user turn over session state as ADK renders
them, so a replay sends the bytes a graph run sends.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.utils.content_utils import to_user_content
from google.adk.utils.instructions_utils import inject_session_state
from google.genai import types


def node_instruction(skills: str, prompt: str) -> str:
    """Skill text then prompt text: what to know, then what to do with it.

    **The prefix this shares is across jobs for one node, and never across the
    nodes of one job.** The skill text is a node's own, so the first bytes of
    two lanes' instructions already differ; what one node repeats from case to
    case is its skill and its prompt down to the first job-varying placeholder.

    The lanes of one job read one **System Model** and one set of sources
    between them, and ``analyze.md`` lays those out after ``{scope}``, which
    the graph's ``LANE_ARTIFACTS`` makes per lane. So the material every lane
    shares sits behind the first thing that separates them.

    **Moving the shared material first would cost more than it saves** (#1400).
    The lanes of one job start together from one ``prepare`` route, so none
    finds another's cache entry written. And the order here is what lets each
    lane's own prefix, 4,576 to 6,520 tokens of skill and prompt, cache across
    jobs (:mod:`analysis_service.prompt_cache`); with the job data first, that
    prefix would shrink to the few tokens of ``analyze.md`` before its title
    names the lane.
    """
    return f"{skills.strip()}\n\n{prompt.strip()}\n"


async def node_request(
    template: str,
    state: Mapping[str, Any],
    output: Any,
    closing: str | None,
) -> tuple[str, types.Content]:
    """The request one LLM node sends, rebuilt off the graph.

    The instruction is ``template`` with ADK's own ``inject_session_state``
    run over ``state``, so a placeholder ADK would substitute is substituted
    the same way here. The user turn is ADK's ``to_user_content`` over
    ``output``, the way ADK hands a node the node before it returned, and
    ``closing`` is appended as the turn's last part, as the graph's callback
    appends it. ``tests/test_evals_lane_replay.py`` and
    ``tests/test_evals_critic_replay.py`` hold a rebuilt request against the
    one a real graph run sent, byte for byte.
    """
    # ADK reads only ``_invocation_context.session.state`` for a state name,
    # and the artifact service only for an ``artifact.`` name no prompt here
    # carries.
    context = SimpleNamespace(
        _invocation_context=SimpleNamespace(
            session=SimpleNamespace(state=dict(state)), artifact_service=None
        )
    )
    instruction = await inject_session_state(template, cast(ReadonlyContext, context))
    turn = to_user_content(output)
    if closing:
        turn.parts = [*(turn.parts or []), types.Part(text=closing)]
    return instruction, turn
