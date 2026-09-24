"""One graph node's model call, made outside the graph on the route the graph would use.

A replay runs one node over archived input and pays for one call. The call has
to reach the provider in the shape the graph gives it, or the replay measures
a different request: the node's own tier adapter, the tier's sampling, the
node's output schema as ``response_schema``, and the instruction as the system
instruction. ADK builds all four from the ``LlmAgent``; this builds the same
four from the deployment, through :meth:`~analysis_service.deployment.Deployment.tier_of`,
which is the one reader of the walk from a graph node to its tier.

The user turn is the caller's, because it differs by node: a lane receives
``prepare``'s output and a critic receives its fan-in's, each through ADK's
``to_user_content``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.charges import CHARGE_METADATA_KEY
from analysis_service.deployment import Deployment

#: One call: the instruction and the user turn in, the model's raw text out.
NodeCall = Callable[[str, types.Content], Awaitable[str]]


def node_call(
    deployment: Deployment,
    graph_node: str,
    schema: Any,
    spent: list[float | None] | None = None,
) -> NodeCall:
    """The call ``graph_node`` makes, on this deployment's route for it.

    ``spent`` receives the charge the provider reported for each call, or
    ``None`` where it reported none, read off the stamp
    :mod:`analysis_service.charges` puts on the response. A replay is a paid
    call, and its caller states what it spent rather than an estimate.
    """
    tier = deployment.tier_of(graph_node)
    adapter = build_tier_adapters(
        deployment.tiers,
        deployment.sampling,
        deployment.resilience,
        env=deployment.env,
    )[tier]
    sampling = deployment.sampling.for_tier(tier)

    async def call(instruction: str, turn: types.Content) -> str:
        config = sampling.to_generate_content_config()
        config.system_instruction = instruction
        config.response_schema = schema
        request = LlmRequest(model=adapter.model, contents=[turn], config=config)
        chunks = []
        charge = None
        async for response in adapter.generate_content_async(request, False):
            charge = (response.custom_metadata or {}).get(CHARGE_METADATA_KEY, charge)
            for part in (response.content.parts if response.content else []) or []:
                if part.text:
                    chunks.append(part.text)
        if spent is not None:
            spent.append(charge)
        return "".join(chunks)

    return call
