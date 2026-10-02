"""A node's output schema, as each vendor's ``schema_rule`` sends it.

Drives the shipped binding down to the call litellm receives, then runs the
pinned litellm's own transform on what it received. So each assertion reads the
request a provider would get, and not only what this service handed over.

The Bedrock half is the defect #1409 records: AWS documents a 400 error for a
structured-output schema that carries ``minimum``, ``maximum``, ``multipleOf``,
``minLength`` or ``maxLength``, and allows ``minItems`` only at 0 or 1
(Bedrock structured-outputs page, read 2026-10-02). The Converse transform sends
every bound unchanged, so the ``bounds_described`` rule rewrites the schema first.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.frameworks import schemas_for
from analysis_service.model_gate import _litellm
from analysis_service.provider import InProcessExecutor
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from analysis_service.vendors import VENDOR_NAMES, VendorName, vendor_for
from tests.factories import PROJECT_ROOT, collected, tiers_for, translator_of

#: Every call here ends at a client the test supplies, never at a network.
pytestmark = pytest.mark.usefixtures("supplied_transport")

CONFIG = PROJECT_ROOT / "config"

#: A real node schema, so the bounds under test are ones the graph sends.
SCHEMA = schemas_for("stride").proposals

#: The keywords AWS documents as refused, and the one it limits.
BEDROCK_REFUSED = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}
BEDROCK_LIMITED_MIN_ITEMS = {0, 1}

FAKE_ENV = {
    var: f"not-a-real-{var.lower()}"
    for name in VENDOR_NAMES
    for mode in vendor_for(name).credential_modes
    for var in vendor_for(name).required_env_vars(mode)
}


class Capturing:
    """A litellm client that records one call and answers with ``message``."""

    def __init__(self, message: dict[str, Any] | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.message = message or {"role": "assistant", "content": "{}"}

    async def acompletion(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return _litellm.ModelResponse(
            choices=[{"index": 0, "message": self.message, "finish_reason": "stop"}],
            model=kwargs["model"],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )


def drive_strong_tier(
    vendor: VendorName,
    client: Capturing,
    *,
    sampling_env: dict[str, str] | None = None,
    rewrite: bool = True,
) -> list[Any]:
    """One ``SCHEMA`` call through the built strong-tier adapter, to ``client``.

    ``rewrite=False`` applies ``as_built`` in place of the vendor's rule.
    """
    adapters = build_tier_adapters(
        tiers_for(vendor),
        load_sampling(CONFIG / "sampling.toml", env=sampling_env or {}),
        load_resilience(CONFIG / "resilience.toml", env={}),
        env=FAKE_ENV,
    )
    adapter = adapters["strong"]
    if not rewrite:
        executor = adapter.executor
        assert isinstance(executor, InProcessExecutor)
        executor._rewrite_schema = dict
    translator_of(adapter).llm_client = client
    request = LlmRequest(
        model=adapter.model,
        contents=[types.Content(role="user", parts=[types.Part(text="x")])],
        config=types.GenerateContentConfig(response_schema=SCHEMA),
    )

    return asyncio.run(collected(adapter.generate_content_async(request, False)))


def _response_format(vendor: VendorName, *, rewrite: bool = True) -> Any:
    """The ``response_format`` the built adapter hands litellm for ``SCHEMA``."""
    client = Capturing()
    drive_strong_tier(vendor, client, rewrite=rewrite)
    return client.kwargs["response_format"]


def _mapped(vendor: VendorName, response_format: Any) -> dict[str, Any]:
    """What the pinned litellm maps ``response_format`` to for this vendor."""
    model = REFERENCE_MODELS[vendor][1]
    return _litellm.utils.get_optional_params(
        model=model,
        custom_llm_provider=vendor_for(vendor).litellm_provider,
        response_format=response_format,
    )


def _nodes(schema: Any) -> Iterator[Mapping[str, Any]]:
    """Every mapping inside a JSON schema."""
    if isinstance(schema, Mapping):
        yield schema
        for value in schema.values():
            yield from _nodes(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _nodes(value)


def _bedrock_schema(response_format: Any) -> dict[str, Any]:
    mapped = _mapped("bedrock", response_format)
    text = mapped["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
    return json.loads(text)


def test_the_node_schema_carries_bounds():
    """Without a bound, the tests below would pass on nothing."""
    found = {key for node in _nodes(SCHEMA.model_json_schema()) for key in node}
    assert found & BEDROCK_REFUSED


def test_bedrock_receives_no_bound_it_refuses():
    schema = _bedrock_schema(_response_format("bedrock"))

    refused = sorted(
        key for node in _nodes(schema) for key in node if key in BEDROCK_REFUSED
    )
    over_limit = sorted(
        node["minItems"]
        for node in _nodes(schema)
        if node.get("minItems", 0) not in BEDROCK_LIMITED_MIN_ITEMS
    )
    assert refused == []
    assert over_limit == []


def test_bedrock_still_states_each_bound_to_the_model():
    """A bound moves into the field's description; it does not disappear."""
    schema = _bedrock_schema(_response_format("bedrock"))

    descriptions = " ".join(
        node["description"]
        for node in _nodes(schema)
        if isinstance(node.get("description"), str)
    )
    assert "maximum length:" in descriptions


def test_bedrock_receives_a_refused_bound_as_built():
    """Under ``as_built``, Bedrock receives a bound it refuses."""
    schema = _bedrock_schema(_response_format("bedrock", rewrite=False))

    assert {key for node in _nodes(schema) for key in node} & BEDROCK_REFUSED


def test_the_anthropic_wire_does_not_change():
    """litellm applies the same rule on this path, so the rule is idempotent here."""
    after = _mapped("anthropic", _response_format("anthropic"))
    before = _mapped("anthropic", _response_format("anthropic", rewrite=False))

    assert after == before


@pytest.mark.parametrize("vendor", VENDOR_NAMES)
def test_every_vendor_names_a_rule_the_executor_can_apply(vendor):
    from analysis_service.model_gate import SCHEMA_RULES

    assert vendor_for(vendor).schema_rule in SCHEMA_RULES
