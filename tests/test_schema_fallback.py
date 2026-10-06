"""The runtime fallback to the tool path (ADR 0058, #1412).

Under ``structured_output = "auto"``, a provider 400 that refuses the native
schema moves the tier to the forced tool call, and the same request is sent
again. These tests drive the built Bedrock adapter on a model that the pinned
map marks native, and the provider double refuses it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.ladder import OUTPUT_TOOL_NAME, REFUSAL_SCOPE, SCHEMA_REFUSALS
from analysis_service.model_gate import _litellm
from analysis_service.provider import (
    PROMPT_SCHEMA_INSTRUCTION,
    SCHEMA_FALLBACK_METADATA_KEY,
    SCHEMA_PATH_METADATA_KEY,
    InProcessExecutor,
)
from analysis_service.resilience import load_resilience
from analysis_service.retry import classify
from analysis_service.sampling import load_sampling
from analysis_service.system_model import SystemModel
from tests.factories import collected, rungs_of, tiers_for
from tests.test_schema_rules import BEDROCK_SONNET_4_6, CONFIG, FAKE_ENV, SCHEMA
from tests.test_tool_path import tool_call_message

pytestmark = pytest.mark.usefixtures("supplied_transport")

#: Message text in the shape litellm raises for a Bedrock 400. The exact words
#: Bedrock uses are not documented, so the rule matches the field name.
BEDROCK_REFUSAL = (
    'BedrockException - {"message":"The model does not support outputConfig'
    ' for this request."}'
)


def _bad_request(message: str) -> Exception:
    return _litellm.exceptions.BadRequestError(
        message=message, model="m", llm_provider="bedrock"
    )


class _Provider:
    """A litellm client that answers each call from a list of outcomes."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes[len(self.calls) - 1]
        # Yield once, so concurrent calls are in flight together, as six lanes
        # on one tier are.
        await asyncio.sleep(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _litellm.ModelResponse(
            choices=[{"index": 0, "message": outcome, "finish_reason": "stop"}],
            model=kwargs["model"],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )


def _strong_adapter(sampling_env: dict[str, str] | None = None):
    adapters = build_tier_adapters(
        tiers_for("bedrock", models=(BEDROCK_SONNET_4_6, BEDROCK_SONNET_4_6)),
        load_sampling(CONFIG / "sampling.toml", env=sampling_env or {}),
        load_resilience(CONFIG / "resilience.toml", env={}),
        env=FAKE_ENV,
    )
    return adapters["strong"]


def _wire(adapter, provider: _Provider) -> InProcessExecutor:
    """Point every rung's translator at ``provider``."""
    executor = adapter.executor
    assert isinstance(executor, InProcessExecutor)
    for translator in executor.translators.values():
        translator_any: Any = translator
        translator_any.llm_client = provider
    return executor


def _request(adapter) -> LlmRequest:
    return LlmRequest(
        model=adapter.model,
        contents=[types.Content(role="user", parts=[types.Part(text="x")])],
        config=types.GenerateContentConfig(response_schema=SCHEMA),
    )


def _ask(adapter):
    return asyncio.run(
        collected(adapter.generate_content_async(_request(adapter), False))
    )


class TestTheMatcher:
    def test_a_400_naming_the_schema_field_matches(self):
        failure = classify(_bad_request(BEDROCK_REFUSAL))

        assert failure.schema_refusal == "schema_field_refused"

    def test_the_recorded_anthropic_message_matches(self):
        message = "AnthropicException - the compiled grammar is too large"

        assert classify(_bad_request(message)).schema_refusal == "grammar_too_large"

    def test_another_400_does_not_match(self):
        assert classify(_bad_request("prompt is too long")).schema_refusal is None

    def test_a_server_error_naming_the_field_does_not_match(self):
        error = _litellm.exceptions.InternalServerError(
            message="outputConfig", model="m", llm_provider="bedrock"
        )

        assert classify(error).schema_refusal is None

    def test_the_failure_keeps_no_message_text(self):
        """The record carries a rule name; the message can quote the prompt."""
        failure = classify(_bad_request(BEDROCK_REFUSAL))

        assert failure.detail == "BadRequestError"


def test_an_auto_tier_has_every_rung_its_pair_supports():
    assert rungs_of(_strong_adapter()) == [
        "native",
        "forced_tool",
        "offered_tool",
        "prompt",
    ]


@pytest.mark.parametrize(
    ("setting", "rungs"),
    [("native", ["native"]), ("tool", ["forced_tool", "offered_tool"])],
)
def test_an_explicit_setting_limits_the_ladder(setting, rungs):
    adapter = _strong_adapter({"ANALYSIS_SAMPLING_STRONG_STRUCTURED_OUTPUT": setting})

    assert rungs_of(adapter) == rungs


def test_a_refused_native_schema_is_sent_again_as_a_tool_call():
    adapter = _strong_adapter()
    provider = _Provider(_bad_request(BEDROCK_REFUSAL), tool_call_message())
    _wire(adapter, provider)

    (response,) = _ask(adapter)

    native, tool = provider.calls
    assert native["response_format"] is not None
    assert tool["response_format"] is None
    assert tool["tools"][0]["function"]["name"] == OUTPUT_TOOL_NAME
    assert SCHEMA.model_validate_json(response.content.parts[0].text).claims == []
    assert response.custom_metadata[SCHEMA_PATH_METADATA_KEY] == "tool"
    assert response.custom_metadata[SCHEMA_FALLBACK_METADATA_KEY] == (
        "schema_field_refused"
    )


def test_the_tier_stays_on_the_tool_path():
    adapter = _strong_adapter()
    provider = _Provider(
        _bad_request(BEDROCK_REFUSAL), tool_call_message(), tool_call_message()
    )
    _wire(adapter, provider)

    _ask(adapter)
    (later,) = _ask(adapter)

    assert len(provider.calls) == 3
    assert provider.calls[2]["response_format"] is None
    assert SCHEMA_FALLBACK_METADATA_KEY not in later.custom_metadata


GRAMMAR_REFUSAL = "AnthropicException - the compiled grammar is too large"


def _request_for(adapter, schema) -> LlmRequest:
    return LlmRequest(
        model=adapter.model,
        contents=[types.Content(role="user", parts=[types.Part(text="x")])],
        config=types.GenerateContentConfig(response_schema=schema),
    )


def test_a_grammar_refusal_moves_only_the_schema_it_refused():
    """``extract``'s schema is refused alone; another node on the tier stays native."""
    adapter = _strong_adapter()
    empty_model = tool_call_message()
    empty_model["tool_calls"][0]["function"]["arguments"] = "{}"
    provider = _Provider(
        _bad_request(GRAMMAR_REFUSAL),
        empty_model,
        {"role": "assistant", "content": json.dumps({"claims": []})},
        empty_model,
    )
    executor = _wire(adapter, provider)

    def ask(schema):
        request = _request_for(adapter, schema)
        return asyncio.run(collected(adapter.generate_content_async(request, False)))

    (refused,) = ask(SystemModel)
    ask(SCHEMA)
    ask(SystemModel)

    first, retried, other, again = provider.calls
    assert first["response_format"] is not None
    assert retried["response_format"] is None
    assert other["response_format"] is not None
    assert again["response_format"] is None
    assert executor.ladder.rung == "native"
    assert refused.custom_metadata[SCHEMA_FALLBACK_METADATA_KEY] == "grammar_too_large"


def test_every_refusal_rule_states_its_scope():
    assert REFUSAL_SCOPE.keys() == SCHEMA_REFUSALS.keys()


def test_another_400_still_fails_the_call():
    adapter = _strong_adapter()
    provider = _Provider(_bad_request("prompt is too long"))
    _wire(adapter, provider)

    with pytest.raises(_litellm.exceptions.BadRequestError):
        _ask(adapter)

    assert len(provider.calls) == 1
    assert adapter.executor.ladder.rung == "native"


def test_two_lanes_refused_at_once_both_fall_back():
    """Six lanes share one tier, so two refusals can arrive together."""
    adapter = _strong_adapter()
    refusal = _bad_request(BEDROCK_REFUSAL)
    provider = _Provider(refusal, refusal, tool_call_message(), tool_call_message())
    _wire(adapter, provider)

    async def both():
        return await asyncio.gather(
            collected(adapter.generate_content_async(_request(adapter), False)),
            collected(adapter.generate_content_async(_request(adapter), False)),
        )

    first, second = asyncio.run(both())

    assert len(provider.calls) == 4
    assert first[0].custom_metadata[SCHEMA_PATH_METADATA_KEY] == "tool"
    assert second[0].custom_metadata[SCHEMA_PATH_METADATA_KEY] == "tool"


def test_a_refused_forced_tool_is_offered_instead():
    """A model with thinking always on refuses a forced tool."""
    adapter = _strong_adapter()
    forced_refusal = _bad_request(
        "AnthropicError - Thinking may not be enabled when tool_choice forces tool use."
    )
    provider = _Provider(
        _bad_request(BEDROCK_REFUSAL), forced_refusal, tool_call_message()
    )
    _wire(adapter, provider)

    (response,) = _ask(adapter)

    offered = provider.calls[2]
    assert offered["tools"][0]["function"]["name"] == OUTPUT_TOOL_NAME
    assert "tool_choice" not in offered
    assert response.custom_metadata[SCHEMA_FALLBACK_METADATA_KEY] == (
        "forced_tool_refused"
    )
    assert adapter.executor.ladder.rung == "offered_tool"


def test_a_model_that_refuses_tools_gets_the_schema_in_its_prompt():
    adapter = _strong_adapter()
    fenced = "```json\n" + json.dumps({"claims": []}) + "\n```"
    provider = _Provider(
        _bad_request(BEDROCK_REFUSAL),
        _bad_request("This model doesn't support tool use in streaming mode."),
        _bad_request("This model doesn't support tool use."),
        {"role": "assistant", "content": fenced},
    )
    _wire(adapter, provider)

    (response,) = _ask(adapter)

    prompted = provider.calls[3]
    assert prompted["tools"] is None
    assert prompted["response_format"] is None
    assert PROMPT_SCHEMA_INSTRUCTION in json.dumps(prompted["messages"])
    assert SCHEMA.model_validate_json(response.content.parts[0].text).claims == []
    assert response.custom_metadata[SCHEMA_PATH_METADATA_KEY] == "prompt"
    assert adapter.executor.ladder.rung == "prompt"


def test_a_refusal_on_the_last_rung_fails_the_call():
    adapter = _strong_adapter({"ANALYSIS_SAMPLING_STRONG_STRUCTURED_OUTPUT": "native"})
    provider = _Provider(_bad_request(BEDROCK_REFUSAL))
    _wire(adapter, provider)

    with pytest.raises(_litellm.exceptions.BadRequestError):
        _ask(adapter)


@pytest.mark.parametrize(
    ("status", "message", "rule"),
    [
        (
            400,
            'Invalid JSON payload. Unknown name "response_schema"',
            "schema_field_refused",
        ),
        (400, "Invalid schema for response_format 'x'", "schema_field_refused"),
        (400, "The model does not support the toolConfig field", "tools_refused"),
        (
            404,
            "No endpoints found that can handle the requested parameters.",
            "no_endpoint",
        ),
    ],
)
def test_every_vendor_s_refusal_matches(status, message, rule):
    error = _bad_request(message)
    error.status_code = status

    assert classify(error).schema_refusal == rule


def test_a_404_that_is_not_a_refusal_does_not_match():
    error = _bad_request("model not found")
    error.status_code = 404

    assert classify(error).schema_refusal is None
