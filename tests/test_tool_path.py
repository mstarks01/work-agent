"""The forced tool path (ADR 0058, #1411).

A ``tool`` tier sends its node schema as the parameters of one tool, forces the
call, and reads the call's arguments back as the node's JSON answer. These tests
drive the built Bedrock adapter, then run the pinned litellm's own transform on
what it received, so they read the request a provider would get.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.model_gate import _litellm
from analysis_service.provider import (
    OUTPUT_TOOL_NAME,
    PROMPT_SCHEMA_INSTRUCTION,
    SCHEMA_PATH_METADATA_KEY,
    answer_from_tool_call,
    answer_unfenced,
    schema_as_tool,
    schema_in_prompt,
)
from analysis_service.vendors import vendor_for
from tests.test_schema_rules import SCHEMA, Capturing, drive_strong_tier

pytestmark = pytest.mark.usefixtures("supplied_transport")

#: The strong tier on the tool path, as a deployment would set it.
TOOL_TIER = {"ANALYSIS_SAMPLING_STRONG_STRUCTURED_OUTPUT": "tool"}

ANSWER: dict[str, Any] = {"claims": []}


def tool_call_message() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": OUTPUT_TOOL_NAME,
                    "arguments": json.dumps(ANSWER),
                },
            }
        ],
    }


def _bedrock_mapped(kwargs: dict[str, Any]) -> dict[str, Any]:
    """What the pinned litellm maps the captured tool arguments to on Bedrock."""
    return _litellm.utils.get_optional_params(
        model=REFERENCE_MODELS["bedrock"][1],
        custom_llm_provider=vendor_for("bedrock").litellm_provider,
        tools=kwargs["tools"],
        tool_choice=kwargs["tool_choice"],
    )


def test_a_tool_tier_sends_the_schema_as_a_forced_tool():
    client = Capturing(tool_call_message())

    drive_strong_tier("bedrock", client, sampling_env=TOOL_TIER)

    kwargs = client.kwargs
    assert kwargs["response_format"] is None
    (tool,) = kwargs["tools"]
    assert tool["function"]["name"] == OUTPUT_TOOL_NAME
    assert "claims" in tool["function"]["parameters"]["properties"]
    assert kwargs["tool_choice"]["function"]["name"] == OUTPUT_TOOL_NAME


def test_bedrock_receives_a_tool_config_and_no_output_config():
    client = Capturing(tool_call_message())

    drive_strong_tier("bedrock", client, sampling_env=TOOL_TIER)

    mapped = _bedrock_mapped(client.kwargs)
    assert "outputConfig" not in mapped
    assert mapped["tools"][0]["function"]["name"] == OUTPUT_TOOL_NAME
    assert mapped["tool_choice"] == {"tool": {"name": OUTPUT_TOOL_NAME}}


def test_the_tool_s_parameters_carry_no_bound_bedrock_refuses():
    """The vendor's schema rule applies on the tool path too: one schema text."""
    client = Capturing(tool_call_message())

    drive_strong_tier("bedrock", client, sampling_env=TOOL_TIER)

    parameters = json.dumps(client.kwargs["tools"][0]["function"]["parameters"])
    assert '"maxLength"' not in parameters
    assert "maximum length:" in parameters


def test_the_tool_call_reaches_the_node_as_json_text():
    client = Capturing(tool_call_message())

    (response,) = drive_strong_tier("bedrock", client, sampling_env=TOOL_TIER)

    (part,) = response.content.parts
    assert part.function_call is None
    assert SCHEMA.model_validate_json(part.text).claims == []


def test_the_native_tier_sends_no_tool():
    client = Capturing()

    drive_strong_tier("bedrock", client)

    assert client.kwargs["tools"] is None
    assert client.kwargs["response_format"] is not None


def _response(*parts: types.Part) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=list(parts)))


def test_a_preamble_is_dropped_and_a_thought_is_kept():
    thought = types.Part(text="thinking", thought=True)
    preamble = types.Part(text="Here is my answer:")
    call = types.Part.from_function_call(name=OUTPUT_TOOL_NAME, args=ANSWER)

    converted = answer_from_tool_call(_response(thought, preamble, call))

    texts = [(part.text, bool(part.thought)) for part in converted.content.parts]
    assert texts == [("thinking", True), (json.dumps(ANSWER), False)]


def test_a_response_without_the_tool_call_passes_unchanged():
    """Its text goes to the schema check, which may re-ask."""
    text = _response(types.Part(text='{"claims": []}'))

    assert answer_from_tool_call(text) is text


def test_a_request_without_a_schema_gets_no_tool():
    request = LlmRequest(config=types.GenerateContentConfig())

    assert schema_as_tool(request) is request


def test_the_node_records_which_path_its_schema_took():
    tool = drive_strong_tier(
        "bedrock", Capturing(tool_call_message()), sampling_env=TOOL_TIER
    )
    native = drive_strong_tier("bedrock", Capturing())

    assert tool[0].custom_metadata[SCHEMA_PATH_METADATA_KEY] == "tool"
    assert native[-1].custom_metadata[SCHEMA_PATH_METADATA_KEY] == "native"


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"claims": []}\n```',
        '```\n{"claims": []}\n```',
        '  ```json\n{"claims": []}\n```  \n',
    ],
)
def test_one_fence_around_the_whole_answer_is_removed(text):
    (part,) = answer_unfenced(_response(types.Part(text=text))).content.parts

    assert part.text == '{"claims": []}'


@pytest.mark.parametrize(
    "text",
    ['{"claims": []}', 'Here it is:\n```json\n{"claims": []}\n```'],
)
def test_any_other_answer_passes_unchanged(text):
    response = _response(types.Part(text=text))

    assert answer_unfenced(response) is response


def test_the_schema_joins_the_last_user_turn():
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="go")])],
        config=types.GenerateContentConfig(response_schema={"type": "object"}),
    )

    prompted = schema_in_prompt(request)

    (turn,) = prompted.contents
    assert [part.text.split("\n")[0] for part in turn.parts] == [
        "go",
        PROMPT_SCHEMA_INSTRUCTION,
    ]
    assert prompted.config.response_schema is None


def test_every_rung_has_a_path_and_translator_arguments():
    """A rung added to the vocabulary must answer in both tables."""
    from typing import get_args

    from analysis_service.binding import _RUNG_KWARGS
    from analysis_service.provider import PATH_OF_RUNG, Rung

    assert set(PATH_OF_RUNG) == set(get_args(Rung)) == set(_RUNG_KWARGS)
