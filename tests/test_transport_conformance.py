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

Two of those sentences are about the credential rather than about the request.
``tests/test_vendors.py`` holds the registry's half — what
``Vendor.credential_kwargs`` refuses — and :class:`TestWhichKeyAuthenticates`
below holds what actually authenticates. It runs on the gateway route, because
the direct route's header comes from the injected SDK client rather than from
the adapter. The other half of recommendation 4's transport pair, TLS
verification, sits in ``tests/test_translator_seam.py`` with the kwarg lints it
belongs beside.
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
from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.resilience import load_resilience
from analysis_service.sampling import TierSampling, load_sampling
from analysis_service.system_model import SystemModel
from analysis_service.vendors import ProviderAuthError, VendorName, vendor_for
from tests.factories import (
    AMBIENT_KEY_VARS,
    PROJECT_ROOT,
    SDK_CLIENT_KEY,
    inject_transport,
    tiers_for,
    translator_of,
)

CONFIG = PROJECT_ROOT / "config"

#: A key-shaped string that is visibly not a key. Nothing here authenticates:
#: the transport answers before anything reads it, which is the point — a
#: transport test that needed a credential would run nowhere.
FAKE_KEY = "not-a-real-openai-key"

#: The gateway route, used by one class below and nowhere else. It is the only
#: one of the two seams whose ``Authorization`` header is evidence about the
#: adapter: litellm composes an OpenRouter request itself, from the key it
#: resolved, while the OpenAI SDK composes one from the key its own client
#: holds. See :func:`tests.factories.inject_transport`.
GATEWAY: VendorName = "openrouter"

#: A key the deployment declares, and a key it does not. Different strings,
#: because the whole question here is which of the two arrives.
DECLARED_KEY = "not-a-real-declared-openrouter-key"
UNDECLARED_KEY = "not-a-real-undeclared-openrouter-key"

#: The direct route, which every check but the credential class runs on: this
#: file is about the bytes rather than about coverage, and
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

    The injection is :func:`tests.factories.inject_transport`, one reader for
    a chain ``tests/test_provider_contract.py`` drives too.

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
    adapter = adapters["base"]
    inject_transport(adapter, VENDOR, wire.handle)
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

    Only half of the credential question can be asked on this route. The
    injected OpenAI SDK client composes the ``Authorization`` header from a key
    of its own, so what is in that header says nothing about which key the
    adapter resolved. :class:`TestWhichKeyAuthenticates` asks that on the
    gateway route, where litellm composes the request itself.
    """
    _send(wire)
    body = wire.requests[0].content.decode("utf-8")

    assert FAKE_KEY not in body
    assert SDK_CLIENT_KEY not in body
    assert wire.requests[0].headers.get("authorization") == f"Bearer {SDK_CLIENT_KEY}"


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


class TestWhichKeyAuthenticates:
    """Which credential reaches the provider: the declared one, or an ambient one.

    ``tests/test_vendors.py`` holds the registry half — ``credential_kwargs``
    refuses a vendor-scoped variable the deployment did not declare. This is the
    half about the bytes, and it needs the gateway route, because the direct
    route's header comes from the injected SDK client rather than from the
    adapter.

    The three checks are one statement in three parts: litellm reads an
    undeclared key out of the process environment on its own, the declared key
    beats it when one is passed, and a deployment that declared none never gets
    an adapter at all.
    """

    @staticmethod
    def _send(wire: _Wire, env: dict[str, str], *, drop_credential: bool = False):
        """Drive one gateway call built under ``env``, and return its request.

        ``drop_credential`` takes the resolved key back off the built translator,
        which is the shape of a regression that stopped passing one — the point
        being what litellm then does, rather than how the kwarg went missing.
        """
        sampling = load_sampling(CONFIG / "sampling.toml", env={})
        adapters = build_tier_adapters(
            tiers_for(GATEWAY),
            sampling,
            load_resilience(CONFIG / "resilience.toml", env={}),
            env=dict(env),
        )
        adapter = adapters["base"]
        if drop_credential:
            translator_of(adapter)._additional_args.pop("api_key")
        inject_transport(adapter, GATEWAY, wire.handle)

        async def drive():
            request = _request(sampling.for_tier("base"))
            return [r async for r in adapter.generate_content_async(request, False)]

        asyncio.run(drive())
        return wire.requests[0]

    @pytest.mark.parametrize("ambient", AMBIENT_KEY_VARS[GATEWAY])
    def test_the_declared_key_is_the_one_on_the_wire(self, wire, monkeypatch, ambient):
        """The declared credential wins over one sitting in the environment.

        Both are present, which is the realistic shape: a machine that has ever
        run another tool against this provider carries its variable. Every name
        litellm reads for this vendor is checked, from the same table the
        registry's own refusal is checked against.
        """
        monkeypatch.setenv(ambient, UNDECLARED_KEY)
        request = self._send(wire, {vendor_for(GATEWAY).api_key_var: DECLARED_KEY})
        sent = request.content.decode("utf-8") + str(dict(request.headers))

        assert request.headers.get("authorization") == f"Bearer {DECLARED_KEY}"
        assert UNDECLARED_KEY not in sent

    @pytest.mark.parametrize("ambient", AMBIENT_KEY_VARS[GATEWAY])
    def test_an_undeclared_key_authenticates_on_its_own_when_none_is_passed(
        self, wire, monkeypatch, ambient
    ):
        """Why the registry's refusal is load-bearing rather than belt and braces.

        A measurement of the dependency, not a preference of ours: with no
        ``api_key`` among the kwargs, litellm reads the process environment and
        authenticates with what it finds. That is the ASI03 inherited-credential
        path, and it is the reason the registry refuses an undeclared variable
        instead of leaving the question to the adapter — by the time a request
        is composed there is nothing left to refuse.

        So this asserts the outcome the service exists to prevent. It fails if
        litellm stops falling back, which is worth knowing: the refusal above
        would then be closing a door that is already shut.
        """
        monkeypatch.setenv(ambient, UNDECLARED_KEY)
        request = self._send(
            wire,
            {vendor_for(GATEWAY).api_key_var: DECLARED_KEY},
            drop_credential=True,
        )

        assert request.headers.get("authorization") == f"Bearer {UNDECLARED_KEY}"

    @pytest.mark.parametrize("ambient", AMBIENT_KEY_VARS[GATEWAY])
    def test_an_undeclared_key_alone_builds_no_adapter(self, wire, ambient):
        """The build fails closed, so the fallback above never gets a request.

        The environment is passed whole in a deployment, so an ambient variable
        really is in the mapping ``build_tier_adapters`` reads. It is not the
        one the registry asks for, and the failure names the variable a
        deployment has to declare rather than the value it found.
        """
        with pytest.raises(ProviderAuthError, match=vendor_for(GATEWAY).api_key_var):
            self._send(wire, {ambient: UNDECLARED_KEY})

        assert not wire.requests
