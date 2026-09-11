"""Measure whether a reported charge reaches this service's record (#822).

`analysis_service.charges` reads a provider's own statement of what it charged
out of litellm's ``_hidden_params``, carries it across the ADK boundary and
stamps it on the response the executor reads. Every step of that is offline and
tested. What no test can settle is the first step: whether OpenRouter states a
charge at all, and whether litellm files it where this code looks — two claims
about a third party and a pinned dependency.

So this drives the service's own adapter against the live API. Run it from the
repository root with a key in ``ANALYSIS_OPENROUTER_API_KEY``::

    uv run python docs/research/probe_openrouter_reported_charge.py

One billed completion of a few output tokens, on a slug whose input rate is
about a ten-millionth of a dollar per token. The whole run costs a small
fraction of a cent.

Three things are printed, and each one can fail independently:

1. **What OpenRouter stated.** The ``usage`` block of the raw response, which is
   where ``cost`` and ``cost_details`` arrive. A deployment paying OpenRouter
   directly should see a ``cost`` that is the whole charge.
2. **Where litellm filed it.** The ``_hidden_params`` entry this code reads. A
   litellm that stopped asking for the figure, or filed it elsewhere, shows up
   here as an absence while leg 1 still succeeds.
3. **What the record carries.** The ``custom_metadata`` stamp on the response
   ADK builds, which is exactly what ``NodeRun.reported_charge_usd`` is read
   from.
4. **Which arrangement the provider says served the call.** ``is_byok`` in the
   completion body's ``usage`` block, read through the same function the
   deployment reads it with. A flag that disagrees with the declared
   arrangement stops a run, so what this leg confirms is that the reader sees
   what the provider actually sends.
5. **What OpenRouter's own record says.** ``GET /generation`` on the returned
   id, which is unbilled and states ``is_byok`` and the upstream cost beside
   the total. That is what decides whether the reported figure is the whole
   charge under this account's arrangement, rather than a part of it.

This is a probe, not a gate. It asserts nothing and fails no build.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import httpx
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.charges import (
    CHARGE_METADATA_KEY,
    UPSTREAM_METADATA_KEY,
    reported_charge_of,
    served_upstream_of,
    stated_arrangement_of,
)
from analysis_service.model_tiers import load_model_tiers
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling

#: Many providers front this one, and it is among the cheapest OpenRouter
#: carries. The figure under test is whether a charge is *reported*, not what it
#: is, so the cheapest legal slug is the right one.
SLUG = "meta-llama/llama-3.3-70b-instruct"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(REPO_ROOT, "config")


def _deployment():
    """The service's own adapters, selecting OpenRouter under a direct account.

    Built through ``build_tier_adapters`` rather than by hand, because what is
    under test includes the wiring: which client a tier binds is decided there
    from the registry and the declared arrangement.
    """
    env = {
        "ANALYSIS_MODEL_BASE_VENDOR": "openrouter",
        "ANALYSIS_MODEL_BASE_MODEL": SLUG,
        "ANALYSIS_MODEL_STRONG_VENDOR": "openrouter",
        "ANALYSIS_MODEL_STRONG_MODEL": SLUG,
        "ANALYSIS_MODEL_CHARGES_OPENROUTER": "direct",
    }
    tiers = load_model_tiers(os.path.join(CONFIG, "model_tiers.toml"), env=env)
    return build_tier_adapters(
        tiers,
        load_sampling(os.path.join(CONFIG, "sampling.toml"), env={}),
        load_resilience(os.path.join(CONFIG, "resilience.toml"), env={}),
        env={"ANALYSIS_OPENROUTER_API_KEY": os.environ["ANALYSIS_OPENROUTER_API_KEY"]},
    )


def _request() -> LlmRequest:
    """The smallest completion this service will accept.

    Not one token: ``analysis_service.retry`` refuses a completion that stopped
    at ``max_output_tokens``, because a fragment is not an answer. So the cap
    has to leave room for the model to stop on its own, and the prompt asks for
    a reply that fits.
    """
    return LlmRequest(
        # No ``model``: the adapter carries the route its tier resolved, which
        # is the whole point of driving the service's own binding.
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text="Reply with the word OK and nothing else.")],
            )
        ],
        config=types.GenerateContentConfig(max_output_tokens=16),
    )


async def _drive(adapter) -> list[Any]:
    return [
        response async for response in adapter.generate_content_async(_request(), False)
    ]


class _Recording:
    """Keeps the raw responses the service's own client sees, and nothing else.

    Wrapped **outside** the capturing client rather than replacing it, so the
    capture under test still runs: legs 2 and 3 are about what that client and
    the adapter above it do with the response this class merely keeps a
    reference to.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.seen: list[Any] = []

    async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
        response = await self.inner.acompletion(*args, **kwargs)
        self.seen.append(response)
        return response


def main() -> int:
    if not os.environ.get("ANALYSIS_OPENROUTER_API_KEY", "").strip():
        print("set ANALYSIS_OPENROUTER_API_KEY to run this probe", file=sys.stderr)
        return 2

    adapters = _deployment()
    adapter = adapters["base"]
    captured = adapter.llm_client
    print(f"client class: {type(captured).__module__}.{type(captured).__name__}")

    recorder = _Recording(captured)
    adapter.llm_client = recorder
    responses = asyncio.run(_drive(adapter))

    for raw in recorder.seen:
        usage = getattr(raw, "usage", None)
        print("\n--- what OpenRouter stated (usage) ---")
        print(json.dumps(_plain(usage), indent=2, sort_keys=True, default=str))
        print("\n--- where litellm filed it (_hidden_params) ---")
        hidden = getattr(raw, "_hidden_params", None)
        print(json.dumps(_plain(hidden), indent=2, sort_keys=True, default=str))
        print(f"\nreported_charge_of(...) -> {reported_charge_of(raw)!r}")
        stated = stated_arrangement_of(raw)
        print(f"stated_arrangement_of(...) -> {stated.value if stated else None!r}")
        print(f"served_upstream_of(...) -> {served_upstream_of(raw)!r}")

    print("\n--- what the record carries ---")
    for response in responses:
        stamped = response.custom_metadata or {}
        for key in (CHARGE_METADATA_KEY, UPSTREAM_METADATA_KEY):
            print(f"custom_metadata[{key!r}] -> {stamped.get(key)!r}")

    for raw in recorder.seen:
        generation_id = _generation_id(raw)
        print(f"\n--- what OpenRouter's record says ({generation_id}) ---")
        if generation_id is None:
            print("no generation id in the response headers")
            continue
        print(json.dumps(_generation(generation_id), indent=2, sort_keys=True))
    return 0


#: The base litellm's pinned ``main.py`` falls back to for this provider, and
#: the host the generation record is read from.
API_BASE = "https://openrouter.ai/api/v1"


def _generation(generation_id: str) -> Any:
    """OpenRouter's own record of one call, which costs nothing to read.

    It lands a few seconds after the completion, so this waits and retries
    rather than reporting an absence that is only a delay.
    """
    key = os.environ["ANALYSIS_OPENROUTER_API_KEY"]
    for _ in range(6):
        response = httpx.get(
            f"{API_BASE}/generation",
            params={"id": generation_id},
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()
        time.sleep(2)
    return {"unavailable": response.status_code, "body": response.text[:200]}


def _generation_id(raw: Any) -> str | None:
    """The id OpenRouter returned, which litellm keeps among the headers."""
    hidden = getattr(raw, "_hidden_params", None) or {}
    headers = hidden.get("additional_headers") or {}
    return headers.get("llm_provider-x-generation-id")


def _plain(value: Any) -> Any:
    """A pydantic model, a mapping or a scalar, as something json can print."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict") and callable(value.dict):
        return value.dict()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
