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
from analysis_service.model_gate import (
    _litellm,
    emulates_structured_output,
    schema_support,
)
from analysis_service.provider import InProcessExecutor
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from analysis_service.vendors import (
    _CLAUDE_FAMILY,
    VENDOR_NAMES,
    VendorName,
    vendor_for,
)
from tests.factories import (
    PROJECT_ROOT,
    collected,
    declared_env,
    tiers_for,
    translator_of,
)

#: Every call here ends at a client the test supplies, never at a network.
pytestmark = pytest.mark.usefixtures("supplied_transport")

CONFIG = PROJECT_ROOT / "config"

#: A real node schema, so the bounds under test are ones the graph sends.
SCHEMA = schemas_for("stride").proposals

#: The keywords AWS documents as refused, and the one it limits.
BEDROCK_REFUSED = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}
BEDROCK_LIMITED_MIN_ITEMS = {0, 1}

#: The keywords Claude's constrained decoding refuses with a 400 error
#: (Anthropic's structured-outputs page, read 2026-10-02).
CLAUDE_REFUSED = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}

#: A Claude in each spelling of a vendor that serves Claude. ``openai`` and
#: ``gemini`` serve one provider's own models, and neither is Claude.
CLAUDE_ON: dict[VendorName, str] = {
    "anthropic": "claude-sonnet-5",
    "bedrock": "global.anthropic.claude-sonnet-4-6",
    "openrouter": "anthropic/claude-sonnet-4.6",
    "vertex": "claude-sonnet-4-6",
}

#: A Bedrock Claude that the pinned litellm sends on the native path and that
#: takes a forced tool. The pinned litellm sends Claude 5 on the tool path there,
#: and Opus 5.5 refuses a forced tool, so a test of either path names this one.
BEDROCK_SONNET_4_6 = CLAUDE_ON["bedrock"]

FAKE_ENV = {
    var: value
    for name in VENDOR_NAMES
    for mode in vendor_for(name).credential_modes
    for var, value in declared_env(name, mode).items()
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
    model: str | None = None,
) -> list[Any]:
    """One ``SCHEMA`` call through the built strong-tier adapter, to ``client``.

    ``rewrite=False`` applies ``as_built`` in place of the vendor's rule.
    ``model`` replaces the vendor's reference pair on both tiers.
    """
    adapters = build_tier_adapters(
        tiers_for(vendor, models=None if model is None else (model, model)),
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


def _response_format(
    vendor: VendorName, *, rewrite: bool = True, model: str | None = None
) -> Any:
    """The ``response_format`` the built adapter hands litellm for ``SCHEMA``."""
    client = Capturing()
    drive_strong_tier(vendor, client, rewrite=rewrite, model=model)
    return client.kwargs["response_format"]


def _mapped(
    vendor: VendorName, response_format: Any, model: str | None = None
) -> dict[str, Any]:
    """What the pinned litellm maps ``response_format`` to for this vendor."""
    model = model or REFERENCE_MODELS[vendor][1]
    return _litellm.utils.get_optional_params(
        model=model,
        custom_llm_provider=vendor_for(vendor).litellm_provider,
        response_format=response_format,
    )


def _nodes(schema: Any) -> Iterator[Mapping[str, Any]]:
    """Every mapping inside a JSON schema, a schema sent as JSON text included."""
    if isinstance(schema, str) and schema.startswith("{"):
        yield from _nodes(json.loads(schema))
    elif isinstance(schema, Mapping):
        yield schema
        for value in schema.values():
            yield from _nodes(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _nodes(value)


def _bedrock_schema(response_format: Any) -> dict[str, Any]:
    mapped = _mapped("bedrock", response_format, BEDROCK_SONNET_4_6)
    text = mapped["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
    return json.loads(text)


def test_the_bedrock_test_model_takes_both_paths_under_the_pin():
    """Without this, a test of one path could pass on another."""
    from analysis_service.ladder import _PROBE_TOOL, RUNG_KWARGS, _accepts

    forced_tool = {"tools": [_PROBE_TOOL], **RUNG_KWARGS["forced_tool"]}
    bedrock = vendor_for("bedrock")

    assert not emulates_structured_output(bedrock, BEDROCK_SONNET_4_6)
    assert _accepts(bedrock, BEDROCK_SONNET_4_6, forced_tool)


def test_the_node_schema_carries_bounds():
    """Without a bound, the tests below would pass on nothing."""
    found = {key for node in _nodes(SCHEMA.model_json_schema()) for key in node}
    assert found & BEDROCK_REFUSED


def test_bedrock_receives_no_bound_it_refuses():
    schema = _bedrock_schema(_response_format("bedrock", model=BEDROCK_SONNET_4_6))

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
    schema = _bedrock_schema(_response_format("bedrock", model=BEDROCK_SONNET_4_6))

    descriptions = " ".join(
        node["description"]
        for node in _nodes(schema)
        if isinstance(node.get("description"), str)
    )
    assert "maximum length:" in descriptions


def test_bedrock_receives_a_refused_bound_as_built():
    """Under ``as_built``, Bedrock receives a bound it refuses."""
    schema = _bedrock_schema(
        _response_format("bedrock", rewrite=False, model=BEDROCK_SONNET_4_6)
    )

    assert {key for node in _nodes(schema) for key in node} & BEDROCK_REFUSED


def test_the_anthropic_wire_does_not_change():
    """litellm applies the same rule on this path, so the rule is idempotent here."""
    after = _mapped("anthropic", _response_format("anthropic"))
    before = _mapped("anthropic", _response_format("anthropic", rewrite=False))

    assert after == before


@pytest.mark.parametrize("vendor", VENDOR_NAMES)
def test_every_vendor_names_a_rule_the_executor_can_apply(vendor):
    from analysis_service.model_gate import SCHEMA_RULES

    entries = vendor_for(vendor).schema_rules
    assert {entry.rule for entry in entries} <= SCHEMA_RULES.keys()
    assert entries[-1].family.pattern == "", "the last entry answers every model"


@pytest.mark.parametrize("vendor", VENDOR_NAMES)
def test_every_row_that_serves_claude_describes_its_bounds(vendor):
    """``schema_rules`` answers for Claude wherever ``form_rules`` serves it.

    Checked against the other table rather than a list of vendors, so a row
    added tomorrow with a Claude form rule cannot send Claude its bounds.
    """
    row = vendor_for(vendor)
    serves_claude = any(rule.family is _CLAUDE_FAMILY for rule in row.form_rules)

    if not serves_claude:
        pytest.skip(f"{vendor} serves no Claude")
    assert row.schema_rule(CLAUDE_ON.get(vendor, "claude-sonnet-4-6")) == (
        "bounds_described"
    )


@pytest.mark.parametrize(("vendor", "model"), sorted(CLAUDE_ON.items()))
def test_a_claude_sent_a_native_schema_gets_no_bound_it_refuses(vendor, model):
    """Claude refuses these bounds on every vendor that sends it a native schema.

    A vendor that sends Claude no native schema reaches it through a tool,
    which takes the bounds, so this asks only the native ones.
    """
    if schema_support(vendor_for(vendor), model) != "native":
        pytest.skip(f"{vendor} sends {model} no native schema")

    mapped = _mapped(vendor, _response_format(vendor, model=model), model)

    assert {key for node in _nodes(mapped) for key in node} & CLAUDE_REFUSED == set()


def test_openrouter_gpt_wire_does_not_change():
    """The archive's OpenRouter models keep the schema they were measured on."""
    model = "openai/gpt-5.6-terra"

    sent = _response_format("openrouter", model=model)

    assert sent == _response_format("openrouter", model=model, rewrite=False)
    assert {key for node in _nodes(sent) for key in node} & CLAUDE_REFUSED
