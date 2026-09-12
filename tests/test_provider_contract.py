"""Every fact this service reads off a provider, driven from the wire.

## The gap this closes

The executor reads five things off an ADK event and the retry driver reads four
off a response or an exception. Nine facts, and every other offline test
supplies them **itself**: ``tests/factories.ScriptedLlm`` constructs an
``LlmResponse`` with a ``model_version`` and a ``usage_metadata`` written by
hand, and ``tests/test_retry.py`` builds a ``RateLimitError`` with the headers
it wants to read back. Each reader was therefore held against a shape the test
had written, which is the two-readers-agreeing failure ``CLAUDE.md`` names —
except that here the second reader is a stand-in for a third party, and the
third party disagrees.

It disagreed. ``_retry_after_seconds`` read ``exc.headers``, which litellm
deliberately leaves empty on a vendor failure, so the provider's own
``Retry-After`` reached nothing on any real 429 and the ceiling #830 added
guarded a value that never arrived.

## What these tests drive

The shipped binding, down to an HTTP transport that answers without a network
and without a credential, and — for the event half — up through ADK's own flow
to the :class:`~google.adk.events.event.Event` the executor stamps from. Nothing
is mocked above the dependency boundary: the adapter is the one
``build_tier_adapters`` builds, the retry loop is the shipped one, and the
readers are the shipped ones, imported rather than restated.

``tests/test_transport_conformance.py`` is the outbound half of the same idea —
what this service *sends*. This file is what it **reads**.

## Two routes, because the facts split that way

A direct vendor names no upstream and reports no charge; a gateway vendor
carries both. So ``openai`` stands for the direct route and ``openrouter`` for
the gateway one, and each drives the facts it can actually carry. The injection
seam differs between them because litellm reaches the two through different
clients — the OpenAI SDK for one, its own HTTP handler for the other — which is
a fact about the translator rather than about the vendors, and
:func:`tests.factories.inject_transport` raises rather than defaults when it
meets a third.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import pytest
from google.adk.agents import LlmAgent
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import InMemoryRunner
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.charges import CHARGE_METADATA_KEY, UPSTREAM_METADATA_KEY
from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.execution import (
    _attempts_of,
    _reported_charge_of,
    _served_route,
    _served_upstream_of,
    _usage_of,
)
from analysis_service.report import TokenUsage
from analysis_service.resilience import load_resilience
from analysis_service.retry import (
    ATTEMPTS_METADATA_KEY,
    TruncatedCompletionError,
    _clears_with_time,
    _is_transient,
    _retry_after_seconds,
)
from analysis_service.sampling import load_sampling
from analysis_service.system_model import SystemModel
from analysis_service.vendors import VendorName, vendor_for
from tests.factories import PROJECT_ROOT, inject_transport, tiers_for

CONFIG = PROJECT_ROOT / "config"

#: Key-shaped strings that are visibly not keys. Nothing here authenticates: the
#: transport answers before anything reads one.
FAKE_KEYS: Mapping[VendorName, dict[str, str]] = {
    "openai": {"ANALYSIS_OPENAI_API_KEY": "not-a-real-openai-key"},
    "openrouter": {"ANALYSIS_OPENROUTER_API_KEY": "not-a-real-openrouter-key"},
}

#: The build the provider says answered, deliberately unlike anything requested.
#: A served build equal to the route would pass a reader that recorded what was
#: *asked for*, which is the distinction ``_served_route`` exists to keep.
SERVED_BUILD = "a-build-nobody-requested"

#: Five distinct counters, none a sum of the others, in the provider's own
#: spelling. Equal numbers would pass a mapping that transposed two of them.
PROVIDER_USAGE: Mapping[str, Any] = {
    "prompt_tokens": 1100,
    "completion_tokens": 300,
    "total_tokens": 1400,
    "prompt_tokens_details": {"cached_tokens": 700},
    "completion_tokens_details": {"reasoning_tokens": 9000},
}

#: What those five must become, in this service's own vocabulary.
EXPECTED_USAGE = TokenUsage(
    prompt_tokens=1100,
    cached_prompt_tokens=700,
    completion_tokens=300,
    reasoning_tokens=9000,
    total_tokens=1400,
)

#: What a gateway says beyond the tokens: the organisation that served the call,
#: and what it charged for it.
UPSTREAM = "DeepInfra"
CHARGE = 2.54e-06


def completion_body(vendor: VendorName, **over: Any) -> dict[str, Any]:
    """One provider's answer to a completion, in the shape it really sends.

    The gateway body carries the two fields only a gateway has — the upstream
    it named at the top level, and the charge and arrangement it states inside
    the usage block. Both are where the live measurements in
    ``docs/research/`` found them.
    """
    usage = dict(PROVIDER_USAGE)
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": SERVED_BUILD,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }
    if not vendor_for(vendor).routes_to_one_provider:
        body["provider"] = UPSTREAM
        usage["cost"] = CHARGE
        usage["is_byok"] = False
    body.update(over)
    return body


class _Provider:
    """One mock provider: a fixed answer, and a count of what was asked of it."""

    def __init__(self, response: Callable[[], httpx.Response]) -> None:
        self._response = response
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response()


def answering(vendor: VendorName, **over: Any) -> _Provider:
    """A provider that completes normally."""
    return _Provider(lambda: httpx.Response(200, json=completion_body(vendor, **over)))


def declining(status: int, headers: Mapping[str, str] | None = None) -> _Provider:
    """A provider that refuses, with whatever headers it refuses under."""
    return _Provider(
        lambda: httpx.Response(
            status,
            headers=dict(headers or {}),
            json={"error": {"message": "declined", "type": "x", "code": "x"}},
        )
    )


def bound(vendor: VendorName, provider: _Provider):
    """The shipped ``base``-tier adapter for one vendor, wired to ``provider``."""
    sampling = load_sampling(CONFIG / "sampling.toml", env={})
    adapters = build_tier_adapters(
        tiers_for(vendor),
        sampling,
        load_resilience(CONFIG / "resilience.toml", env={}),
        env=dict(FAKE_KEYS[vendor]),
    )
    adapter = adapters["base"]
    inject_transport(adapter, vendor, provider.handle)
    return adapter, sampling.for_tier("base")


def responses_from(vendor: VendorName, provider: _Provider) -> list:
    """Drive one call through the shipped adapter and collect what it yielded."""
    adapter, sampling = bound(vendor, provider)
    config = sampling.to_generate_content_config()
    config.response_schema = SystemModel
    config.response_mime_type = "application/json"
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
        config=config,
    )

    async def drive():
        return [r async for r in adapter.generate_content_async(request, False)]

    return asyncio.run(drive())


def events_from(vendor: VendorName, provider: _Provider) -> list:
    """Drive one call through **ADK's own flow** and collect the events.

    The half a response-level test cannot reach: ADK merges an ``LlmResponse``
    into the ``Event`` the executor reads, and every fact below has to survive
    that merge. The agent is a bare ``LlmAgent`` rather than this repository's
    graph because the graph is what ``tests/test_execution.py`` drives — what is
    under test here is one node's response becoming one node's event.
    """
    adapter, _ = bound(vendor, provider)
    runner = InMemoryRunner(
        agent=LlmAgent(name="contract", model=adapter, instruction="answer"),
        app_name="contract",
    )

    async def drive():
        session = await runner.session_service.create_session(
            app_name="contract", user_id="u"
        )
        return [
            event
            async for event in runner.run_async(
                user_id="u",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
            )
        ]

    return asyncio.run(drive())


def raised_by(vendor: VendorName, provider: _Provider) -> BaseException:
    """The exception one refused call reaches this service as."""
    with pytest.raises(Exception) as excinfo:
        responses_from(vendor, provider)
    return excinfo.value


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """The retry loop is exercised at full speed, never waited out."""

    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


class TestWhatTheExecutorReadsOffAnEvent:
    """The five facts ``analysis_service.execution`` stamps a node run from.

    Each is read by the shipped reader, imported rather than restated, off an
    event ADK built from a response litellm parsed from bytes a provider sent.
    """

    def test_the_served_build_is_what_the_provider_named(self):
        """Not what was requested — the two are different strings here for that
        reason, and a reader recording the route would pass on equal ones."""
        (event,) = events_from("openai", answering("openai"))

        assert event.model_version == SERVED_BUILD

    def test_the_served_route_carries_the_vendor_and_the_served_build(self):
        """What reaches ``NodeRun.model`` and the execution fingerprint: the
        requested route's vendor joined to the build that answered."""
        (event,) = events_from("openai", answering("openai"))
        requested = f"openai/{REFERENCE_MODELS['openai'][0]}"

        assert _served_route(requested, event.model_version) == (
            f"openai/{SERVED_BUILD}"
        )

    def test_every_token_counter_lands_in_its_own_field(self):
        """Five counters, five fields, no two of them equal — so a mapping that
        transposed a pair fails here rather than mis-pricing a sweep."""
        (event,) = events_from("openai", answering("openai"))

        assert _usage_of(event) == EXPECTED_USAGE

    def test_a_provider_that_meters_nothing_is_recorded_as_a_free_call(self):
        """A limit of the translator, pinned rather than wished away.

        ``_usage_of`` returns ``None`` where every counter is absent, so that an
        unmeasured call and a free call stay different facts. **Through LiteLLM
        they do not:** a response body carrying no ``usage`` block at all is
        parsed into ``Usage(prompt_tokens=0, completion_tokens=0,
        total_tokens=0)``, and the zeros reach the record as a call that cost
        nothing.

        Nothing in the response survives to tell the two apart, so there is no
        reader to fix — only a boundary to state. The ``None`` branch stays
        reachable for a stand-in that reports no usage, which is what
        ``tests/factories.UnmeteredLlm`` is, and unreachable for every vendor
        this service binds. A translator that stopped synthesising the block
        fails here, which is the notice worth having.
        """
        (event,) = events_from("openai", answering("openai", usage=None))

        assert _usage_of(event) == TokenUsage(
            prompt_tokens=0,
            cached_prompt_tokens=0,
            completion_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
        )

    def test_the_attempt_count_reaches_the_event(self):
        """``stamp_attempt`` writes it on the response; this is the claim its
        docstring makes about ADK carrying it onto the event."""
        (event,) = events_from("openai", answering("openai"))

        assert _attempts_of(event) == 1

    def test_a_direct_vendor_names_no_upstream_and_reports_no_charge(self):
        """Never invented: a direct route records ``None`` rather than its own
        vendor copied out of the route it was asked for."""
        (event,) = events_from("openai", answering("openai"))

        assert _served_upstream_of(event) is None
        assert _reported_charge_of(event) is None

    def test_a_gateway_names_its_upstream_and_its_charge_on_the_event(self):
        """The two facts only a gateway has, from the fields the live
        measurements found them in, through the shipped charge client."""
        (event,) = events_from("openrouter", answering("openrouter"))

        assert _served_upstream_of(event) == UPSTREAM
        assert _reported_charge_of(event) == CHARGE


class TestWhatTheRetryDriverReadsOffAResponse:
    """``finish_reason``, the one fact read off a response rather than an event."""

    def test_a_completion_stopped_at_the_cap_is_refused(self):
        """ADK maps litellm's ``finish_reason="length"`` onto the bare string
        ``retry`` compares against. Nothing else holds that mapping, and a
        library that renamed the member would otherwise let a fragment through
        to a validator that misdescribes it."""
        body = completion_body("openai")
        body["choices"][0]["finish_reason"] = "length"

        with pytest.raises(TruncatedCompletionError):
            responses_from("openai", _Provider(lambda: httpx.Response(200, json=body)))

    def test_a_completion_that_ended_normally_is_not_refused(self):
        """The other direction, so the check above cannot pass by refusing
        everything."""
        responses = responses_from("openai", answering("openai"))

        assert responses[0].finish_reason == "STOP"


class TestWhatTheRetryDriverReadsOffAnException:
    """``status_code``, ``rate_limit_type`` and the ``Retry-After`` header.

    Driven from an HTTP status and real response headers, rather than from an
    exception this file constructed. ``tests/test_retry.py`` holds the rules
    themselves over every shape; what is new here is that the shapes are the
    provider's.
    """

    def test_a_refused_request_carries_the_status_the_provider_sent(self):
        exc = raised_by("openai", declining(429))

        assert getattr(exc, "status_code", None) == 429
        assert _is_transient(exc)

    def test_a_malformed_request_is_not_retried(self):
        exc = raised_by("openai", declining(400))

        assert getattr(exc, "status_code", None) == 400
        assert not _is_transient(exc)

    def test_a_vendor_429_states_no_dimension_and_is_retried(self):
        """The ordinary case and the safe one: litellm names a dimension only
        when its own limiter fired, so an upstream throttle arrives unstated
        and must survive."""
        exc = raised_by("openai", declining(429))

        assert getattr(exc, "rate_limit_type", None) is None
        assert _clears_with_time(exc)

    def test_the_provider_s_retry_after_reaches_the_reader(self):
        """**The one that was broken.** litellm files a vendor's response
        headers under ``litellm_response_headers`` and leaves ``exc.headers``
        empty on purpose, so a reader of ``headers`` alone found nothing on
        every real failure — while the unit test that built the exception with
        ``headers={...}`` agreed with it."""
        exc = raised_by("openai", declining(429, {"retry-after": "7"}))

        assert _retry_after_seconds(exc) == 7.0

    def test_the_millisecond_spelling_reaches_the_reader_too(self):
        exc = raised_by("openai", declining(429, {"retry-after-ms": "1500"}))

        assert _retry_after_seconds(exc) == pytest.approx(1.5)

    def test_a_refusal_with_no_hint_is_no_hint(self):
        """So the two above cannot pass by reading something that is always
        there."""
        exc = raised_by("openai", declining(429))

        assert _retry_after_seconds(exc) is None

    def test_a_hint_past_the_ceiling_stops_the_node_rather_than_parking_it(self):
        """#830's rule, on a header that now actually arrives. A provider
        asking for a day has answered the question a retry exists to ask."""
        provider = declining(429, {"retry-after": "86400"})
        raised_by("openai", provider)

        assert len(provider.requests) == 1, (
            "a stated wait past the ceiling must end the node, not be retried"
        )

    def test_a_hint_inside_the_ceiling_is_still_retried(self):
        """The rule refuses a long wait, not every stated one — and the
        adapter's own attempt count is what says so.

        45 seconds rather than a token number: it is a whole tokens-per-minute
        window, which is the case the ceiling is sized for and the one a
        30-second ceiling would refuse."""
        provider = declining(429, {"retry-after": "45"})
        raised_by("openai", provider)

        attempts = load_resilience(CONFIG / "resilience.toml", env={}).attempts
        assert len(provider.requests) == attempts


class TestTheAttemptCountMeansRequests:
    """``attempts`` is requests, which is only true while ``num_retries=0`` holds.

    ``tests/test_transport_conformance.py`` counts one request for one call.
    This is the other half: when the call is retried, the number the event
    carries is the number of requests the provider saw.
    """

    def test_a_retried_call_reports_the_attempt_that_answered(self):
        answers = [
            httpx.Response(429, json={"error": {"message": "slow", "code": "x"}}),
            httpx.Response(200, json=completion_body("openai")),
        ]
        provider = _Provider(lambda: answers.pop(0))

        responses = responses_from("openai", provider)

        assert len(provider.requests) == 2
        assert responses[0].custom_metadata[ATTEMPTS_METADATA_KEY] == 2


class TestTheMetadataKeysAreTheOnesTheReadersUse:
    """The stamp and the read are two readers of one key, so hold them together.

    Cheap, and it is the shape that went wrong twice in this area: a key spelled
    in two modules agrees with itself until one of them is edited.
    """

    def test_the_gateway_stamps_exactly_the_keys_the_executor_reads(self):
        (event,) = events_from("openrouter", answering("openrouter"))

        assert set(event.custom_metadata) == {
            ATTEMPTS_METADATA_KEY,
            CHARGE_METADATA_KEY,
            UPSTREAM_METADATA_KEY,
        }
