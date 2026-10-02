"""The explicit prompt-cache breakpoint, from a node's template to the wire (#1400).

The wire checks drive the shipped binding to an HTTP transport that answers
without a network, as ``tests/test_transport_conformance.py`` does, and read
the body litellm composed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.model_tiers import TierName
from analysis_service.prompt_cache import (
    BREAKPOINT_FIELD,
    EXPLICIT,
    OPTIONS_FIELD,
    cache_marking_client_class,
    marked,
    remember_stable_prefix,
    stable_prefix,
)
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from analysis_service.vendors import VendorName
from tests.factories import PROJECT_ROOT, inject_transport, tiers_for

pytestmark = pytest.mark.usefixtures("supplied_transport")

CONFIG = PROJECT_ROOT / "config"
PREFIX = "# Role\n\nYou rule on drafts.\n\n"
REST = "The drafts:\n\n[]\n"
MARKED = [
    {"type": "text", "text": PREFIX, BREAKPOINT_FIELD: EXPLICIT},
    {"type": "text", "text": REST},
]


class TestStablePrefix:
    def test_it_ends_at_the_first_brace(self):
        assert stable_prefix("Read this.\n\n{system_model}\n\n{drafts}") == (
            "Read this.\n\n"
        )

    def test_a_template_with_no_placeholder_is_all_prefix(self):
        assert stable_prefix("No job data here.") == "No job data here."


class TestMarked:
    def system(self, content: Any) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": content},
            {"role": "user", "content": "hi"},
        ]

    def test_the_system_text_splits_at_the_prefix(self):
        result = marked(self.system(PREFIX + REST), PREFIX)

        assert result == [
            {"role": "system", "content": MARKED},
            {"role": "user", "content": "hi"},
        ]

    def test_a_system_text_that_is_all_prefix_is_one_marked_block(self):
        result = marked(self.system(PREFIX), PREFIX)

        assert result[0]["content"] == [MARKED[0]]

    @pytest.mark.parametrize(
        ("messages", "prefix"),
        [
            pytest.param([], PREFIX, id="no messages"),
            pytest.param([{"role": "user", "content": PREFIX}], PREFIX, id="no system"),
            pytest.param(
                [{"role": "system", "content": [{"type": "text", "text": PREFIX}]}],
                PREFIX,
                id="system is not plain text",
            ),
            pytest.param(
                [{"role": "system", "content": "Other text."}], PREFIX, id="mismatch"
            ),
            pytest.param([{"role": "system", "content": PREFIX}], None, id="no prefix"),
            pytest.param(
                [{"role": "system", "content": PREFIX}], "", id="empty prefix"
            ),
        ],
    )
    def test_nothing_is_marked_where_the_prefix_does_not_lead(self, messages, prefix):
        assert marked(messages, prefix) is None


class _Recording:
    """A base client that records what the cache layer hands it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _complete(client: Any, messages: list[dict[str, Any]], prefix: str, **kwargs):
    async def drive():
        remember_stable_prefix(prefix)(None, None)
        await client.acompletion(model="m", messages=messages, tools=None, **kwargs)

    asyncio.run(drive())


def test_the_options_join_an_upstream_pin_already_in_the_body():
    client = cache_marking_client_class(_Recording)()
    pin = {"provider": {"only": ["openai/flex"], "allow_fallbacks": False}}

    _complete(
        client, [{"role": "system", "content": PREFIX + REST}], PREFIX, extra_body=pin
    )

    assert client.calls[0]["extra_body"] == {**pin, OPTIONS_FIELD: EXPLICIT}


def test_an_unmarked_call_keeps_its_body_as_it_was():
    client = cache_marking_client_class(_Recording)()
    pin = {"provider": {"only": ["openai/flex"], "allow_fallbacks": False}}

    _complete(
        client, [{"role": "system", "content": "Other text."}], PREFIX, extra_body=pin
    )

    assert client.calls[0]["extra_body"] == pin


class _Wire:
    def __init__(self, model: str) -> None:
        self.model = model
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": self.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


def _sent(
    vendor: VendorName, models: tuple[str, str], tier: TierName
) -> dict[str, Any]:
    """The body one call on ``tier`` puts on the wire, after its node's callback."""
    wire = _Wire(models[0] if tier == "base" else models[1])
    sampling = load_sampling(CONFIG / "sampling.toml", env={})
    adapters = build_tier_adapters(
        tiers_for(vendor, models=models),
        sampling,
        load_resilience(CONFIG / "resilience.toml", env={}),
        env={f"ANALYSIS_{vendor.upper()}_API_KEY": "not-a-real-key"},
    )
    adapter = adapters[tier]
    inject_transport(adapter, vendor, wire.handle)
    config = sampling.for_tier(tier).to_generate_content_config()
    config.system_instruction = PREFIX + REST

    async def drive():
        remember_stable_prefix(PREFIX)(None, None)
        request = LlmRequest(
            contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
            config=config,
        )
        return [r async for r in adapter.generate_content_async(request, False)]

    asyncio.run(drive())
    assert len(wire.requests) == 1
    return json.loads(wire.requests[0].content)


@pytest.mark.parametrize(
    ("vendor", "models", "tier"),
    [
        pytest.param(
            "openai", ("gpt-4o-2024-08-06", "gpt-5.6-sol"), "strong", id="openai"
        ),
        pytest.param(
            "openrouter",
            ("openai/gpt-5.6-luna", "openai/gpt-5.6-terra"),
            "strong",
            id="openrouter",
        ),
    ],
)
def test_a_model_that_takes_the_breakpoint_gets_it_on_the_wire(vendor, models, tier):
    body = _sent(vendor, models, tier)

    assert body["messages"][0] == {"role": "system", "content": MARKED}
    assert body[OPTIONS_FIELD] == EXPLICIT


@pytest.mark.parametrize(
    ("vendor", "models", "tier"),
    [
        pytest.param(
            "openai", ("gpt-4o-2024-08-06", "gpt-5.6-sol"), "base", id="gpt-4o"
        ),
        pytest.param(
            "openrouter",
            ("anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.7"),
            "strong",
            id="openrouter-claude",
        ),
    ],
)
def test_a_model_that_does_not_take_it_sends_the_system_text_unchanged(
    vendor, models, tier
):
    body = _sent(vendor, models, tier)

    assert body["messages"][0] == {"role": "system", "content": PREFIX + REST}
    assert OPTIONS_FIELD not in body
