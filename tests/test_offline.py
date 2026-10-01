"""Offline mode refuses a live provider call before the transport sees it.

Each test drives a shipped path to a transport it supplies and counts what
reached it. The count with offline mode off is the positive control: without
it, a guard that refused nothing and a transport the path never reached would
read the same.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service import model_gate
from analysis_service.binding import build_tier_adapters
from analysis_service.deployment import Deployment
from analysis_service.frameworks import schemas_for
from analysis_service.graph import CRITIC_ROLE, FrameworkNodes
from analysis_service.offline import OFFLINE_ENV, LiveInferenceRefused
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from evals.harness.node_call import node_call
from tests.factories import PROJECT_ROOT, inject_transport, tiers_for

CONFIG = PROJECT_ROOT / "config"


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """A retry, where one happens, runs at full speed."""

    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


def _drive_base_tier() -> tuple[list[httpx.Request], BaseException]:
    """One call through the shipped ``base`` adapter, and what it reached.

    Returns the requests the transport saw and the exception the call ended
    in. The transport refuses every request, so every call ends in one.
    """
    seen: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": {"message": "no", "type": "x"}})

    adapter = build_tier_adapters(
        tiers_for("openai"),
        load_sampling(CONFIG / "sampling.toml", env={}),
        load_resilience(CONFIG / "resilience.toml", env={}),
        env={"ANALYSIS_OPENAI_API_KEY": "not-a-real-openai-key"},
    )["base"]
    inject_transport(adapter, "openai", refuse)
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
        config=types.GenerateContentConfig(),
    )

    async def drive():
        return [r async for r in adapter.generate_content_async(request, False)]

    with pytest.raises(Exception) as raised:
        asyncio.run(drive())
    return seen, raised.value


def test_without_offline_mode_the_call_reaches_the_transport(supplied_transport):
    seen, error = _drive_base_tier()

    assert seen, "the positive control: the shipped path reaches this transport"
    assert not isinstance(error, LiveInferenceRefused)


def test_a_negative_value_leaves_offline_mode_off(monkeypatch, supplied_transport):
    """The guard reads the flag as every other boolean flag here does."""
    monkeypatch.setenv(OFFLINE_ENV, "false")

    seen, error = _drive_base_tier()

    assert seen
    assert not isinstance(error, LiveInferenceRefused)


def test_offline_mode_refuses_before_the_transport_on_every_attempt():
    seen, error = _drive_base_tier()

    assert seen == []
    # The refusal itself, not a retries-exhausted error over it: the retry
    # loop never classified it, so it never asked again.
    assert isinstance(error, LiveInferenceRefused)
    assert "openai/" in str(error)
    assert OFFLINE_ENV in str(error)


def test_offline_mode_refuses_a_single_node_replay():
    """The path ``critic-replay`` and ``lane-replay`` pay for one call on."""
    call = node_call(
        Deployment.from_env(),
        FrameworkNodes("stride").node(CRITIC_ROLE),
        schemas_for("stride").rulings,
    )
    turn = types.Content(role="user", parts=[types.Part(text="drafts")])

    with pytest.raises(LiveInferenceRefused):
        asyncio.run(call("rule on these", turn))


def test_offline_mode_refuses_the_direct_completion():
    with pytest.raises(LiveInferenceRefused, match="openai/gpt-4o"):
        model_gate.completion(model="openai/gpt-4o", messages=[])
