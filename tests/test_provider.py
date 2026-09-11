"""The seam a provider call crosses: what goes out, what comes back, and what does not.

``tests/test_provider_contract.py`` drives the whole path from bytes to reader
and so proves the seam carries the nine facts. This file asks the narrower
questions that path cannot: what the projection *drops*, whether it survives
being written down, and what the shapes are that a value can legally arrive in.

The credential check is the one to read first. It is the same assertion
``tests/test_transport_conformance.py`` makes about a request body, moved to the
boundary this module invents — a key that rode a field nobody looked at is
exactly how a boundary stops meaning anything.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel

from analysis_service.charges import CHARGE_METADATA_KEY, UPSTREAM_METADATA_KEY
from analysis_service.provider import (
    PER_CALL_SAMPLING,
    ExecutedLlm,
    GenerationRequest,
    GenerationResult,
    InProcessExecutor,
    ProviderCallFailed,
    ProviderExecutor,
)
from analysis_service.retry import (
    FailureKind,
    ProviderFailure,
    RetryBudget,
    RetryPolicy,
    RetryRefusal,
    classify,
)
from analysis_service.system_model import SystemModel

CREDENTIAL = "sk-not-a-real-key-and-it-must-not-travel"


class Nested(BaseModel):
    """A schema with a ``$defs``, because a flat one would prove nothing."""

    system: SystemModel


def a_request(**config_over) -> LlmRequest:
    """One node's ADK request, configured the way a graph node is."""
    config = types.GenerateContentConfig(
        max_output_tokens=4096,
        response_schema=SystemModel,
        response_mime_type="application/json",
        system_instruction="you are a node",
        **config_over,
    )
    return LlmRequest(
        model="openai/gpt-4o",
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
        config=config,
    )


def policy(attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(attempts=attempts, budget=RetryBudget(capacity=10, ratio=0.1))


class TestWhatGoesOut:
    def test_the_credential_is_not_in_the_projection(self):
        """The rule the whole boundary rests on, asserted against the bytes.

        A credential reaches the translator on its constructor and stays there;
        this is what says the request carries no copy of it. A key that rode an
        unexamined field would make every other claim here decoration.
        """
        request = a_request()
        request.config.http_options = types.HttpOptions(
            headers={"authorization": f"Bearer {CREDENTIAL}"}
        )

        payload = json.dumps(
            GenerationRequest.project("openai/gpt-4o", request).payload()
        )

        assert CREDENTIAL not in payload

    def test_the_projection_serialises(self):
        """A boundary can only carry what can be written down.

        This is not a feature, it is the check: an implementation elsewhere
        gets the payload and nothing else, so a field that cannot survive JSON
        is a field that was never really part of the contract.
        """
        payload = GenerationRequest.project("openai/gpt-4o", a_request()).payload()

        assert json.loads(json.dumps(payload)) == payload

    def test_the_output_schema_crosses_as_data_and_not_as_a_class(self):
        """Nothing executable crosses. A node binds a pydantic class; what
        travels is the JSON schema that class describes."""
        projected = GenerationRequest.project("openai/gpt-4o", a_request())

        assert isinstance(projected.output_schema, dict)
        assert projected.output_schema["title"] == SystemModel.__name__

    def test_a_nested_schema_keeps_its_defs(self):
        """The shape the whole native-structured-output gate is about: a model
        with a nested one arrives as ``$defs`` plus references to them."""
        request = a_request()
        request.config.response_schema = Nested

        projected = GenerationRequest.project("openai/gpt-4o", request)

        assert "$defs" in projected.output_schema

    @pytest.mark.parametrize(
        "schema",
        [
            SystemModel,
            types.Schema(type=types.Type.OBJECT),
            {"type": "object", "title": "Already"},
        ],
    )
    def test_every_shape_adk_admits_reduces_to_json(self, schema):
        """A class, a pydantic instance and a mapping — the producer's shapes,
        not the ones this file finds convenient."""
        request = a_request()
        request.config.response_schema = schema

        projected = GenerationRequest.project("openai/gpt-4o", request)

        assert isinstance(projected.output_schema, dict)

    def test_a_node_with_no_schema_projects_none(self):
        request = a_request()
        request.config.response_schema = None

        assert GenerationRequest.project("x/y", request).output_schema is None

    def test_the_sampling_that_crosses_is_a_closed_list(self):
        """A boundary that forwarded whatever the config held would be none.

        ``GenerateContentConfig`` carries tools, safety settings and cache
        handles this service never sets, and the seam names what it takes.
        """
        projected = GenerationRequest.project("openai/gpt-4o", a_request())

        assert set(projected.sampling) <= set(PER_CALL_SAMPLING)
        assert projected.sampling["max_output_tokens"] == 4096

    def test_an_unset_param_is_absent_rather_than_none(self):
        """The same rule the wire keeps: a ``None`` is a value sent, and the
        vendors the omission exists for reject it."""
        projected = GenerationRequest.project("openai/gpt-4o", a_request())

        assert "temperature" not in projected.sampling

    def test_a_system_instruction_given_as_content_still_crosses_as_text(self):
        """ADK's own flow may replace the string a node set with a ``Content``.
        What the producer can emit is the question."""
        request = a_request()
        request.config.system_instruction = types.Content(
            parts=[types.Part(text="you are"), types.Part(text=" a node")]
        )

        projected = GenerationRequest.project("openai/gpt-4o", request)

        assert projected.system_instruction == "you are a node"

    def test_the_round_trip_keeps_what_a_node_configured(self):
        """Rebuilt on the far side, the request still says what it said.

        The wire-level proof is ``tests/test_transport_conformance.py``, which
        reads the bytes that come out of exactly this rebuild. This is the same
        claim one level up, where a failure names the field.
        """
        rebuilt = GenerationRequest.project(
            "openai/gpt-4o", a_request()
        ).into_llm_request()

        assert rebuilt.model == "openai/gpt-4o"
        assert rebuilt.config.max_output_tokens == 4096
        assert rebuilt.config.response_mime_type == "application/json"
        assert rebuilt.config.system_instruction == "you are a node"
        assert rebuilt.contents[0].parts[0].text == "hi"


class TestWhatComesBack:
    def test_every_fact_the_service_reads_survives_the_result(self):
        response = LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="{}")]),
            model_version="a-build-nobody-requested",
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=11, total_token_count=14
            ),
            finish_reason=types.FinishReason.STOP,
            custom_metadata={
                CHARGE_METADATA_KEY: 2.54e-06,
                UPSTREAM_METADATA_KEY: "DeepInfra",
            },
        )

        back = GenerationResult.of(response).into_response()

        assert back.model_version == "a-build-nobody-requested"
        assert back.usage_metadata.prompt_token_count == 11
        assert back.finish_reason == "STOP"
        assert back.custom_metadata == {
            CHARGE_METADATA_KEY: 2.54e-06,
            UPSTREAM_METADATA_KEY: "DeepInfra",
        }

    def test_the_finish_reason_crosses_as_the_bare_word(self):
        """``str()`` on ADK's enum gives ``'FinishReason.STOP'``, which is a
        value the type cannot be rebuilt from — and which the truncation rule,
        comparing the bare word, would never match again."""
        result = GenerationResult.of(
            LlmResponse(finish_reason=types.FinishReason.MAX_TOKENS)
        )

        assert result.finish_reason == "MAX_TOKENS"

    def test_a_direct_route_carries_neither_metadata_key(self):
        """Absent rather than ``None``: every reader downstream distinguishes
        the two, and a key written as ``None`` reads as a fact stated."""
        back = GenerationResult.of(LlmResponse(model_version="m")).into_response()

        assert back.custom_metadata is None


class TestWhatAFailureIs:
    @pytest.mark.parametrize(
        ("status", "kind"),
        [
            (401, FailureKind.AUTHENTICATION),
            (403, FailureKind.AUTHENTICATION),
            (400, FailureKind.INVALID_REQUEST),
            (404, FailureKind.INVALID_REQUEST),
            (408, FailureKind.TIMEOUT),
            (500, FailureKind.UNAVAILABLE),
            (503, FailureKind.UNAVAILABLE),
            (504, FailureKind.TIMEOUT),
        ],
    )
    def test_a_status_names_a_kind(self, status, kind):
        exc = ValueError("declined")
        exc.status_code = status

        assert classify(exc).kind is kind

    def test_a_throttle_and_a_spent_quota_share_a_status_and_not_a_kind(self):
        """The one code whose meaning the number does not settle. Both are 429;
        only the dimension says whether time will fix it, and the record has to
        keep the difference."""
        throttled = ValueError("slow down")
        throttled.status_code = 429
        throttled.rate_limit_type = "requests"
        spent = ValueError("no credit")
        spent.status_code = 429
        spent.rate_limit_type = "budget"

        assert classify(throttled).kind is FailureKind.THROTTLED
        assert classify(spent).kind is FailureKind.QUOTA_EXHAUSTED

    def test_a_kind_never_decides_retrying_for_itself(self):
        """The rule that keeps this a label rather than a second reader.

        A spent quota and a throttle are both 429 and the kinds differ; what
        decides the retry is ``retryable``, which came from the same one rule
        it always did.
        """
        spent = ValueError("no credit")
        spent.status_code = 429
        spent.rate_limit_type = "budget"

        failure = classify(spent)

        assert failure.kind is FailureKind.QUOTA_EXHAUSTED
        assert failure.retryable is False

    def test_something_from_outside_the_provider_library_is_unknown(self):
        assert classify(ValueError("nothing")).kind is FailureKind.UNKNOWN

    def test_the_detail_is_a_type_name_and_never_a_message(self):
        """A provider's message can quote the prompt back, and this value goes
        to logs and into records (OWASP LLM02)."""
        failure = classify(ValueError("the user's secret plan"))

        assert failure.detail == "ValueError"
        assert "secret" not in failure.detail

    def test_in_process_the_original_exception_is_kept(self):
        """So nothing loses a traceback, or an exception type a caller was
        already catching, to the seam."""
        original = ValueError("declined")

        assert classify(original).cause is original


class _Scripted:
    """An executor with a fixed answer, for the wiring rather than the rules."""

    def __init__(self, *, results=None, raises=None) -> None:
        self.results = results
        self.raises = raises
        self.seen: list[GenerationRequest] = []

    async def generate(self, request):
        self.seen.append(request)
        if self.raises is not None:
            raise ProviderCallFailed(classify(self.raises))
        return self.results


class TestTheAdapterOverTheSeam:
    def test_the_in_process_executor_satisfies_the_interface(self):
        """Structural, which is all this seam asks of an implementation — and
        what a second one will be measured against."""
        translator = ExecutedLlm(
            model="x/y", executor=_Scripted(results=[]), retry_policy=policy()
        )

        assert isinstance(InProcessExecutor(translator), ProviderExecutor)

    def test_the_executor_is_handed_the_projection_and_nothing_else(self):
        executor = _Scripted(results=[GenerationResult.of(LlmResponse())])
        adapter = ExecutedLlm(
            model="openai/gpt-4o", executor=executor, retry_policy=policy()
        )

        async def drive():
            return [r async for r in adapter.generate_content_async(a_request())]

        asyncio.run(drive())

        assert isinstance(executor.seen[0], GenerationRequest)
        assert executor.seen[0].route == "openai/gpt-4o"

    def test_a_failure_that_crossed_a_boundary_still_raises_something(self):
        """The case the in-process implementation never produces: no exception
        to re-raise, so the kind and the detail are what names the failure."""
        crossed = ProviderFailure(
            kind=FailureKind.AUTHENTICATION,
            retryable=False,
            retry_after_seconds=None,
            detail="AuthenticationError",
            cause=None,
        )

        raised = policy(attempts=1).give_up(
            RetryRefusal.NOT_RETRYABLE, 1, crossed, "openai/gpt-4o"
        )

        assert isinstance(raised, RuntimeError)
        assert "authentication" in str(raised)
        assert "AuthenticationError" in str(raised)
