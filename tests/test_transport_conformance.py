"""What this service actually puts on the wire, asserted against the bytes.

Every check here drives the **shipped binding** down to an HTTP transport that
answers without a network or a credential, and reads the request body litellm
composed. Nothing is mocked above the dependency boundary: the adapter is the
one ``build_tier_adapters`` builds, the sampling is the one
``config/sampling.toml`` holds, and the schema is a node's own.

**Why the wire and not the kwargs.** ``tests/test_translator_seam.py`` holds
what crosses into the translator, and ``analysis_service.model_gate`` holds what
LiteLLM says it would map. Neither says what the provider receives, and the
distance between those three is where #259 lives: the capability lookup and the
mapped params disagreed for one model, and only the request settles which was
right. A claim about the wire that nothing drives is prose.

Each test here pins a sentence this repository states somewhere else, so the
sentence fails when it stops being true.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from google.adk.models.lite_llm import LiteLLMClient
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from openai import AsyncOpenAI

from analysis_service.binding import build_tier_adapters
from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.resilience import load_resilience
from analysis_service.sampling import TierSampling, load_sampling
from analysis_service.system_model import SystemModel
from analysis_service.vendors import VendorName
from tests.factories import PROJECT_ROOT, tiers_for, translator_of

CONFIG = PROJECT_ROOT / "config"

#: A key-shaped string that is visibly not a key. Nothing here authenticates:
#: the transport answers before anything reads it, which is the point — a
#: transport test that needed a credential would run nowhere.
FAKE_KEY = "not-a-real-openai-key"

#: One vendor, because this file is about the bytes rather than about coverage.
#: ``tests/test_conformance.py`` is what walks the matrix. The pair comes from
#: the reference matrix rather than from two strings here, so the build-time
#: gate that refuses an over-ceiling tier has nothing to refuse — it already
#: caught a hand-picked pair while this file was being written.
VENDOR: VendorName = "openai"
MODEL = REFERENCE_MODELS[VENDOR][0]


class _Wire:
    """One captured exchange: what was sent, and how many times."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
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

    @property
    def body(self) -> dict[str, Any]:
        """The one request's JSON body, or a failure naming what happened."""
        assert len(self.requests) == 1, (
            f"expected one request, saw {len(self.requests)}"
        )
        return json.loads(self.requests[0].content)


def _adapter(wire: _Wire, base: TierSampling | None = None):
    """The shipped adapter for one tier, wired to ``wire`` instead of a network.

    The injection is in the client, which is the seam ADK already offers for
    testability, so nothing in ``src/`` learns that a test is running. The
    kwargs the adapter was built with reach litellm unchanged.

    ``base`` substitutes the base tier's sampling **before the adapter is
    built**, because some params ride the constructor rather than the request —
    an override applied only to the request would leave the adapter carrying
    the shipped value and the test asserting its own scaffolding. That is how
    the ``constrain_output`` check below first passed for the wrong reason.
    """
    sampling = load_sampling(CONFIG / "sampling.toml", env={})
    if base is not None:
        sampling = sampling.model_copy(
            update={"tiers": {**sampling.tiers, "base": base}}
        )
    adapters = build_tier_adapters(
        tiers_for(VENDOR),
        sampling,
        load_resilience(CONFIG / "resilience.toml", env={}),
        env={"ANALYSIS_OPENAI_API_KEY": FAKE_KEY},
    )
    client = AsyncOpenAI(
        api_key=FAKE_KEY,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wire.handle)),
    )

    class _Injecting(LiteLLMClient):
        async def acompletion(self, model, messages, tools, **kwargs):
            return await super().acompletion(
                model, messages, tools, client=client, **kwargs
            )

    # One layer in: the adapter is an ``ExecutedLlm`` over the seam, and the
    # translator that holds the tier's credential is on the provider side
    # of it. That is where a transport goes.
    adapter = adapters["base"]
    translator_of(adapter).llm_client = _Injecting()
    return adapter, sampling.for_tier("base")


def _request(
    sampling: TierSampling, *, schema: type | None = SystemModel
) -> LlmRequest:
    """One node's request, configured the way the graph configures a node."""
    config = sampling.to_generate_content_config()
    if schema is not None:
        config.response_schema = schema
        config.response_mime_type = "application/json"
    return LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
        config=config,
    )


def _send(wire: _Wire, base: TierSampling | None = None, **kwargs) -> None:
    adapter, sampling = _adapter(wire, base)

    async def drive():
        request = _request(sampling, **kwargs)
        return [r async for r in adapter.generate_content_async(request, False)]

    asyncio.run(drive())


@pytest.fixture
def wire() -> _Wire:
    return _Wire()


def test_one_call_makes_exactly_one_request(wire):
    """``num_retries=0`` holds below ADK, at the SDK that would retry for us.

    ``binding`` says so in a comment — the kwarg is what keeps the library's
    own layer down to one request per call, so ``attempts`` means requests
    rather than half of a product with them. Counted here rather than trusted.
    """
    _send(wire)

    assert len(wire.requests) == 1


def test_the_node_s_schema_reaches_the_provider(wire):
    """The native structured-output path, as the build-time gate admits it.

    ``_check_native_structured_output`` refuses a tier whose model would get
    LiteLLM's synthesised tool instead. This is the other half of that claim:
    on a model the gate accepts, the schema really does travel as
    ``response_format``.
    """
    _send(wire)
    response_format = wire.body["response_format"]

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"]["title"] == SystemModel.__name__


def test_the_schema_travels_with_its_defs_unresolved(wire):
    """A measurement rather than a preference, and the reason the gate exists.

    A nested model arrives as ``$defs`` plus references to them. The provider
    resolves it on the native path, which is why that path is the one the gate
    admits — and why the emulated path, which forwards the same shape to a
    provider that will not resolve it, is refused at build time.
    """
    _send(wire)
    schema = wire.body["response_format"]["json_schema"]["schema"]

    assert "$defs" in schema, "a nested model with no $defs would prove nothing"


def test_an_unset_param_is_absent_rather_than_null(wire):
    """What lets one shipped file bind every vendor.

    ``binding`` states it: temperature is left unset because Claude 4.7 and
    later reject the param and OpenAI's reasoning families take only their own
    default. A ``null`` on the wire is a value sent, and would be rejected by
    the vendors the omission exists for.
    """
    _send(wire)

    assert "temperature" not in wire.body


def test_a_set_ceiling_reaches_the_wire(wire):
    """The pinned output cap, which the gate checks against the model's own.

    Read under either spelling: which of ``max_tokens`` and
    ``max_completion_tokens`` litellm sends is its business and varies by model
    family. That the tier's number arrives is ours.
    """
    _send(wire)
    body = wire.body
    ceiling = body.get("max_completion_tokens", body.get("max_tokens"))

    assert (
        ceiling
        == load_sampling(CONFIG / "sampling.toml", env={})
        .for_tier("base")
        .max_output_tokens
    )


def test_the_credential_never_rides_in_the_body(wire):
    """A key belongs in a header, and a body is what logs and proxies keep.

    Nothing in this repository puts it there, and this is the assertion that
    says so about the bytes rather than about the code that composed them.
    """
    _send(wire)

    assert FAKE_KEY not in wire.requests[0].content.decode("utf-8")
    assert wire.requests[0].headers.get("authorization") == f"Bearer {FAKE_KEY}"


def test_turning_off_constraint_suppresses_the_schema_on_the_wire(wire):
    """The claim ``constructor_kwargs`` makes about ADK's ordering.

    ADK derives ``response_format`` from the node's ``output_schema`` and then
    applies the constructor's own kwargs *over* it, so an explicit ``None``
    there is what suppresses the schema. That is a statement about a pinned
    library's internals, and this is what holds it: the node still carries its
    schema, and the request carries none.
    """
    shipped = load_sampling(CONFIG / "sampling.toml", env={}).for_tier("base")
    unconstrained = shipped.model_copy(update={"constrain_output": False})

    _send(wire, unconstrained)

    assert wire.body.get("response_format") is None
