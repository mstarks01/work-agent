"""What an aggregator does differently, probed against the installed litellm.

A vendor row makes claims about a **third party**, and an aggregator makes two
of them: one about the gateway, and one about whichever upstream provider the
gateway reached. Neither is decidable from this repository's own tables, so
everything here drives the installed library against a recorded response rather
than asserting a remembered fact. A ``litellm`` bump that moves any of it shows
up here rather than on node one of a paid-for job.

The four questions are the ones named on
[#174](https://github.com/mstarks01/work-agent/issues/174): streaming, tool-call
deltas, non-200 error bodies, and whether the wrapped shapes reach the retry
ladder as the ladder expects. Two of them turn out to be about a path this
service does not take, and that is recorded rather than hidden — a test that
asserts about unreached machinery reads as coverage and is not.
"""

from __future__ import annotations

from typing import Any

import pytest

# Imported through ``model_gate`` so the model-cost map is pinned before
# anything here reaches litellm, exactly as every other module in this suite
# does it.
from analysis_service.model_gate import (
    emulates_structured_output,
    supports_structured_output,
)
from analysis_service.retry import _retryable_types
from analysis_service.vendors import VENDOR_NAMES, vendor_for

VENDOR = "openrouter"

#: The slug this suite drives. The gateway's own vendor segment is the half
#: that makes this row different from every other one.
MODEL = "anthropic/claude-opus-4.7"


def _mapped_exception(provider: str, model: str, status_code: int) -> Exception:
    """What litellm turns a provider failure at ``status_code`` into.

    The shape ``convert_to_model_response_object`` raises is a bare
    ``Exception`` carrying ``status_code`` and ``message``, so this is the real
    input the mapper sees on the error paths below rather than a stand-in for
    it.
    """
    from litellm.litellm_core_utils.exception_mapping_utils import exception_type

    raw = Exception()
    raw.status_code = status_code  # type: ignore[attr-defined]
    raw.message = "the upstream declined"  # type: ignore[attr-defined]
    with pytest.raises(Exception) as excinfo:
        exception_type(
            model=model,
            custom_llm_provider=provider,
            original_exception=raw,
            completion_kwargs={},
            extra_kwargs={},
        )
    return excinfo.value


def _error_body(code: int) -> dict[str, Any]:
    """OpenRouter's own error envelope, as it arrives inside a 200 response."""
    return {
        "error": {"code": code, "message": "upstream declined"},
        "user_id": "someone",
    }


def _transform(status_code: int, body: dict[str, Any]) -> Any:
    """Drive the installed OpenRouter transformation over one canned response."""
    import httpx
    from litellm.litellm_core_utils.litellm_logging import Logging
    from litellm.types.utils import LlmProviders, ModelResponse
    from litellm.utils import ProviderConfigManager

    config = ProviderConfigManager.get_provider_chat_config(
        model=MODEL, provider=LlmProviders(vendor_for(VENDOR).litellm_provider)
    )
    assert config is not None
    raw = httpx.Response(
        status_code,
        json=body,
        request=httpx.Request("POST", "https://provider.invalid/v1"),
    )
    logging_obj = Logging(
        model=MODEL,
        messages=[],
        stream=False,
        call_type="completion",
        start_time=0,
        litellm_call_id="offline",
        function_id="offline",
    )
    logging_obj.optional_params = {}
    return config.transform_response(
        model=MODEL,
        raw_response=raw,
        model_response=ModelResponse(),
        logging_obj=logging_obj,
        request_data={},
        messages=[{"role": "user", "content": "hi"}],
        optional_params={},
        litellm_params={},
        encoding=None,
    )


def _transform_error(code: int) -> BaseException:
    """The exception one wrapped upstream failure produces."""
    with pytest.raises(Exception) as excinfo:
        _transform(200, _error_body(code))
    return excinfo.value


class TestAnErrorInsideATwoHundred:
    """The shape only an aggregator sends, and the one most likely to be missed.

    A direct provider signals a failure with a status code. OpenRouter answers
    ``200`` and puts the upstream's failure in the body, so every check that
    reads a status code sees success. What decides whether the retry ladder
    behaves is therefore not the HTTP status but what litellm does with the
    envelope.
    """

    def test_the_response_raises_rather_than_parsing_as_an_empty_completion(self):
        """The failure mode this rules out is the expensive one.

        A body with an ``error`` object and no ``choices`` could parse to a
        completion with nothing in it. That reaches the node's own output
        validation, which reports a schema failure, and the provider's actual
        message is gone by then.
        """
        with pytest.raises(Exception) as excinfo:
            _transform(200, _error_body(429))
        assert getattr(excinfo.value, "status_code", None) == 429

    @pytest.mark.parametrize("code", [429, 503])
    def test_a_wrapped_transient_code_reaches_the_ladder_as_retryable(self, code):
        """The upstream's own code survives the wrapper, so retry still works.

        This is the half of the #174 comment's worry that turns out to be
        unfounded on the pinned library: a wrapped 429 does not read as a 500.
        """
        raised = _transform_error(code)
        mapped = _mapped_exception(
            vendor_for(VENDOR).litellm_provider,
            MODEL,
            getattr(raised, "status_code", 0),
        )
        assert isinstance(mapped, _retryable_types())

    @pytest.mark.parametrize("code", [400, 401])
    def test_a_wrapped_permanent_code_is_not_retried(self, code):
        """A rejected credential spends no quota reaching the identical answer."""
        raised = _transform_error(code)
        mapped = _mapped_exception(
            vendor_for(VENDOR).litellm_provider,
            MODEL,
            getattr(raised, "status_code", 0),
        )
        assert not isinstance(mapped, _retryable_types())


class TestTheLadderReadsThisVendorDifferently:
    """A measured difference, pinned because nothing else would show it.

    ``_retryable_types`` names litellm's exception classes, and which class a
    status code becomes is decided per provider. On ``openrouter`` a 500
    becomes ``APIError``; on every other vendor this service supports it
    becomes ``InternalServerError``. Only the second is retryable here.

    **The consequence is a cost, not a wrong answer**: a transient upstream 500
    fails its node on the first attempt instead of the third. Widening
    ``_retryable_types`` to ``APIError`` is not the fix — every litellm status
    exception descends from it, so a rejected credential would retry too. The
    design question is #807.
    """

    TRANSIENT = 500

    @pytest.mark.parametrize(
        "name", sorted(name for name in VENDOR_NAMES if name != VENDOR)
    )
    def test_every_other_vendor_retries_a_five_hundred(self, name):
        mapped = _mapped_exception(
            vendor_for(name).litellm_provider, "some-model", self.TRANSIENT
        )
        assert isinstance(mapped, _retryable_types())

    def test_this_vendor_does_not(self):
        """Pinned as the measurement it is. A litellm bump that repairs this
        fails here, which is the signal to close #807 and delete this test."""
        mapped = _mapped_exception(
            vendor_for(VENDOR).litellm_provider, MODEL, self.TRANSIENT
        )
        assert not isinstance(mapped, _retryable_types())


class TestTheSchemaPathIsNativeHere:
    """Why there is no tool-call-delta test, stated as an assertion.

    Tool-call deltas are where an aggregating proxy most often differs, and
    this service never sends a tool: the schema reaches the provider as
    ``response_format``, because litellm's mapped params for this vendor carry
    no synthesised response-format tool. ``binding`` refuses a tier that would
    get the emulated path, so a bump that moved this vendor onto it fails the
    build rather than the response.
    """

    def test_no_synthesised_tool_is_sent(self):
        assert not emulates_structured_output(vendor_for(VENDOR), MODEL)

    def test_the_capability_lookup_and_the_mapped_params_disagree(self):
        """Recorded because the disagreement decided the row's design.

        The lookup answers for a model OpenRouter serves and Anthropic serves
        natively, and it is the mapped params that say what the request will
        really carry. The build gate reads the params, which is why a slug the
        lookup has not caught up with still binds.
        """
        vendor = vendor_for(VENDOR)
        unlisted = "anthropic/claude-sonnet-4.6"
        assert not supports_structured_output(vendor, unlisted)
        assert not emulates_structured_output(vendor, unlisted)


class TestStreamingIsNotAPathThisServiceTakes:
    """The fourth question, answered rather than tested into the void.

    ``retry.retrying_llm_class`` passes a streaming call straight through to
    the adapter — no retry, no truncation check — and nothing in the graph asks
    for one. So chunk assembly is unreached machinery here, and an assertion
    about it would read as coverage this service does not have.

    What is worth pinning is the one thing the streaming handler decides that
    the non-streaming path also depends on: an error inside a chunk keeps the
    upstream's own status code rather than flattening to one of litellm's.
    """

    def test_an_error_chunk_keeps_the_upstream_status_code(self):
        from litellm.llms.openrouter.chat.transformation import (
            OpenRouterChatCompletionStreamingHandler,
        )

        handler = OpenRouterChatCompletionStreamingHandler(
            streaming_response=iter(()), sync_stream=True
        )
        with pytest.raises(Exception) as excinfo:
            handler.chunk_parser(
                {"error": {"code": 429, "message": "upstream declined"}}
            )
        assert excinfo.value.status_code == 429

    def test_a_well_formed_chunk_assembles(self):
        from litellm.llms.openrouter.chat.transformation import (
            OpenRouterChatCompletionStreamingHandler,
        )

        handler = OpenRouterChatCompletionStreamingHandler(
            streaming_response=iter(()), sync_stream=True
        )
        parsed = handler.chunk_parser(
            {
                "id": "gen-offline",
                "created": 1,
                "model": MODEL,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": "hi"}}
                ],
            }
        )
        assert parsed.model == MODEL
        assert parsed.choices[0].delta.content == "hi"


def test_the_gate_probe_the_spec_asked_for():
    """#174 step 4, run as a check rather than left as a command to paste.

    The spec expected ``supports False`` / ``emulates False`` on the pinned
    library and said not to resolve the disagreement by adding a model to a
    list. Both halves are asserted above; this records the probe itself so the
    numbers a later reader compares against are produced by the tree rather
    than by a shell history.
    """
    vendor = vendor_for(VENDOR)
    profile = {
        model: (
            supports_structured_output(vendor, model),
            emulates_structured_output(vendor, model),
        )
        for model in ("anthropic/claude-sonnet-4.6", MODEL)
    }
    assert profile == {
        "anthropic/claude-sonnet-4.6": (False, False),
        MODEL: (True, False),
    }
