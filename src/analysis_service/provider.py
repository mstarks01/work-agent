"""The seam a provider call crosses, and the only shape allowed to cross it.

## What this is for, stated plainly

**It hardens nothing today.** Every provider call still runs in this process,
against the same translator, under the same credential. No arrangement of
checks inside a process protects that process from the code it has loaded, and
this module does not pretend otherwise.

What it does is make the boundary *expressible*. Before it, a provider call was
an ADK ``LlmRequest`` handed to a LiteLLM adapter, and moving that call
elsewhere meant deciding, for every field of every dependency type, whether it
was part of the contract. Now there is one answer to that question, in code:
:class:`GenerationRequest` is what goes out, :class:`GenerationResult` is what
comes back, and
:class:`~analysis_service.retry.ProviderFailure` is what a failure is. That is
recommendation 6 of #824, built as the enabler for recommendation 1 rather than
as a claim to have delivered it.

## The projection, and the rule behind it

**What varies per call crosses; what is constant for a tier is configuration
the provider side already holds.**

| direction | carries |
| --- | --- |
| out | the route, the turn's contents, the system instruction, the output schema as JSON, the per-call decoding params, the deadline |
| never out | the credential, the seed, the reasoning effort, the request timeout |
| back | content parts, the served build, usage, the finish reason, the reported charge, the named upstream |
| back, as data | what a failure was — see :func:`~analysis_service.retry.classify` |

The credential is the extreme case of that rule and the reason for it: it is
tier configuration, it never appears in a request, and an implementation that
runs elsewhere resolves its own rather than being sent one. The seed, the
reasoning effort and the request timeout ride ADK's constructor today for
reasons ``binding`` sets out, and they are tier configuration on the same
terms. A worker would be configured per tier, exactly as this process is.

**Nothing executable crosses.** The output schema travels as a JSON schema
mapping and never as the pydantic class it was derived from, which is why
:meth:`GenerationRequest.payload` can exist at all: a request that serialises
is a request a boundary could carry, and the alternative is a promise nobody
can check.

## What it does not do

There is one implementation, :class:`InProcessExecutor`, and it wraps the
adapter ``binding`` already built. The seam is proven rather than hypothetical
because every call the service makes now crosses it — the transport tests read
the same bytes they read before, and ``tests/test_provider_contract.py`` reads
the same nine facts off the other side.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, Self, runtime_checkable

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel

from analysis_service.charges import CHARGE_METADATA_KEY, UPSTREAM_METADATA_KEY
from analysis_service.retry import (
    ProviderFailure,
    RetryPolicy,
    classify,
    reject_truncated,
    stamp_attempt,
)

#: The decoding params that travel with each call, named as
#: ``types.GenerateContentConfig`` names them. A closed list rather than a dump
#: of the config: that object carries tools, safety settings and cache handles
#: this service never sets, and a boundary that forwarded whatever it was
#: handed would be no boundary. A param this service starts setting has to be
#: added here, and the transport tests fail until it is.
PER_CALL_SAMPLING: tuple[str, ...] = (
    "temperature",
    "top_p",
    "top_k",
    "candidate_count",
    "max_output_tokens",
    "stop_sequences",
    "presence_penalty",
    "frequency_penalty",
)


@dataclass(frozen=True)
class GenerationRequest:
    """One provider call, as the facts the provider side needs and no more.

    Built by :meth:`project` from the ADK request a node composed, and turned
    back into one by :meth:`into_llm_request` on the other side. The round trip
    is what makes the projection checkable rather than asserted: the shipped
    transport tests read the bytes that come out of the rebuilt request, so a
    field this drops stops reaching the wire and something fails.
    """

    route: str
    contents: tuple[types.Content, ...]
    system_instruction: str | None
    output_schema: Mapping[str, Any] | None
    sampling: Mapping[str, Any]
    response_mime_type: str | None

    @classmethod
    def project(cls, route: str, llm_request: LlmRequest) -> Self:
        """The bounded request one ADK request reduces to.

        The output schema is taken as **JSON** whatever it arrived as. ADK
        accepts a pydantic class, a ``types.Schema`` or a mapping; a class is
        the one this service's nodes use and the one a boundary must not carry,
        so it is resolved here, once, on the near side.
        """
        config = llm_request.config
        return cls(
            route=route,
            contents=tuple(llm_request.contents or ()),
            system_instruction=_instruction_text(config),
            output_schema=_schema_json(getattr(config, "response_schema", None)),
            sampling={
                name: value
                for name in PER_CALL_SAMPLING
                if (value := getattr(config, name, None)) is not None
            },
            response_mime_type=getattr(config, "response_mime_type", None),
        )

    def into_llm_request(self) -> LlmRequest:
        """This request as the ADK object an in-process adapter expects.

        Composed from the projection alone, so nothing an implementation was
        handed out of band can reach the provider. An implementation elsewhere
        would build its own provider payload from the same fields.
        """
        config = types.GenerateContentConfig(**dict(self.sampling))
        if self.system_instruction is not None:
            config.system_instruction = self.system_instruction
        if self.output_schema is not None:
            config.response_schema = dict(self.output_schema)
        if self.response_mime_type is not None:
            config.response_mime_type = self.response_mime_type
        return LlmRequest(model=self.route, contents=list(self.contents), config=config)

    def payload(self) -> dict[str, Any]:
        """This request as plain JSON-compatible data.

        **The check, not a feature.** A boundary can only carry what
        serialises, and the way a credential or an executable class reaches the
        far side is by riding a field nobody looked at. This is what a test
        looks at: ``tests/test_provider.py`` asserts the payload holds no
        credential and nothing but data.
        """
        return {
            "route": self.route,
            "contents": [content.model_dump(mode="json") for content in self.contents],
            "system_instruction": self.system_instruction,
            "output_schema": dict(self.output_schema)
            if self.output_schema is not None
            else None,
            "sampling": dict(self.sampling),
            "response_mime_type": self.response_mime_type,
        }


@dataclass(frozen=True)
class GenerationResult:
    """What one provider call answered, as the facts this service reads.

    Exactly the nine ``tests/test_provider_contract.py`` drives, minus the
    attempt count — that one is the retry loop's own, written above this seam
    on the way back out, because an implementation cannot know how many times
    its caller asked.
    """

    content: types.Content | None
    served_model: str | None
    usage: types.GenerateContentResponseUsageMetadata | None
    finish_reason: str | None
    reported_charge_usd: float | None
    served_upstream: str | None

    @classmethod
    def of(cls, response: LlmResponse) -> Self:
        """One ADK response reduced to the facts that cross.

        The charge and the upstream come off ``custom_metadata`` under the keys
        :mod:`analysis_service.charges` writes them with, rather than being
        re-read from a provider object — that module owns both the capture and
        the rule, and this is a carrier.
        """
        stamped = response.custom_metadata or {}
        return cls(
            content=response.content,
            served_model=response.model_version,
            usage=response.usage_metadata,
            finish_reason=_finish_reason_text(response.finish_reason),
            reported_charge_usd=stamped.get(CHARGE_METADATA_KEY),
            served_upstream=stamped.get(UPSTREAM_METADATA_KEY),
        )

    def into_response(self) -> LlmResponse:
        """This result as the ADK response the graph's flow consumes.

        The two metadata keys are written only where there is something to
        write, so an event from a direct vendor carries neither key rather than
        carrying them as ``None`` — which is what every reader downstream
        already distinguishes.
        """
        stamped = {
            CHARGE_METADATA_KEY: self.reported_charge_usd,
            UPSTREAM_METADATA_KEY: self.served_upstream,
        }
        present = {key: value for key, value in stamped.items() if value is not None}
        return LlmResponse(
            content=self.content,
            model_version=self.served_model,
            # ADK types this as its own enum and pydantic coerces the bare word
            # back into a member. The word is what crosses, because the enum is
            # a dependency's type and the rule that reads it compares text.
            finish_reason=self.finish_reason,  # type: ignore[arg-type]
            usage_metadata=self.usage,
            custom_metadata=present or None,
        )


class ProviderCallFailed(Exception):
    """One provider call failed, carrying what the failure was as a value.

    The seam's error channel. In process ``failure.cause`` is the exception the
    translator raised and the retry loop re-raises exactly that, so nothing
    loses a traceback or an exception type a caller was already catching. An
    implementation elsewhere fills the value and leaves ``cause`` empty, which
    is the difference this type exists to make visible.
    """

    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(f"{failure.kind.value}: {failure.detail}")
        self.failure = failure


@runtime_checkable
class ProviderExecutor(Protocol):
    """Whatever runs a generation, wherever it runs it.

    Runtime-checkable because :class:`ExecutedLlm` is a pydantic model and the
    field has to validate; the check is structural, which is the whole of what
    this seam asks of an implementation.

    One method, because one is what the service asks for. Cancellation is the
    caller's ``asyncio`` task and the deadline is the job's, both of which an
    in-process implementation inherits for free and an implementation elsewhere
    has to carry itself — which is a thing to build, not a thing to declare
    here before there is one.
    """

    async def generate(
        self, request: GenerationRequest
    ) -> Sequence[GenerationResult]: ...


class InProcessExecutor:
    """The one implementation: ADK's LiteLLM adapter, in this process.

    Holds the adapter ``binding`` built, with its credential, its seed, its
    reasoning effort and its request timeout — the tier configuration the
    projection deliberately does not carry.

    A sequence comes back rather than one result. A non-streaming call yields
    exactly one response today and this service never streams, but the
    translator's own contract is a generator and the truncation check has
    always scanned all of it. Narrowing that here would be this module
    deciding something that is not its to decide.
    """

    def __init__(self, adapter: BaseLlm) -> None:
        self._adapter = adapter

    @property
    def translator(self) -> BaseLlm:
        """The configured adapter this runs calls through.

        Exposed for the same reason ADK exposes ``LiteLlm.llm_client``: a test
        that wants to drive the real code down to a transport it controls needs
        a seam to reach, and the alternative is for ``src`` to learn that a test
        is running. Read-only, and nothing in the service reads it.
        """
        return self._adapter

    async def generate(self, request: GenerationRequest) -> Sequence[GenerationResult]:
        try:
            responses = [
                response
                async for response in self._adapter.generate_content_async(
                    request.into_llm_request(), False
                )
            ]
        except Exception as exc:
            raise ProviderCallFailed(classify(exc)) from exc
        return [GenerationResult.of(response) for response in responses]


class ExecutedLlm(BaseLlm):
    """A node's model, answering through the seam under the process's retries.

    The object the graph holds. It is a ``BaseLlm`` because that is what ADK's
    flow takes, and everything below it is this service's own vocabulary.

    **The retry loop is here, above the seam, and that is the point of putting
    it here.** Its budget is one bucket for the whole process and its jitter
    decorrelates lanes that failed together; neither is a thing an
    implementation running one call can do. So the loop asks, the executor
    answers, and a failure arrives as a value the loop decides from — the same
    decision whether the call ran here or elsewhere.

    A streaming call is refused rather than passed through. Every node in this
    graph binds an output schema and so never streams; the previous
    pass-through was an untravelled branch that would have skipped the retry
    loop, the truncation check and this seam all at once.
    """

    executor: ProviderExecutor
    retry_policy: RetryPolicy

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            raise NotImplementedError(
                "this service binds an output schema on every node and so never"
                " streams; a streamed call would cross no seam, count no"
                " attempt and check no finish reason"
            )
        request = GenerationRequest.project(self.model, llm_request)
        results, attempt = await self._attempt_with_retries(request)
        responses = [result.into_response() for result in results]
        # Refused here rather than inside the loop, so that method keeps its one
        # job — surviving transport failures — and a truncation is never
        # mistaken for one of them.
        reject_truncated(responses, self.model)
        for response in stamp_attempt(responses, attempt):
            yield response

    async def _attempt_with_retries(
        self, request: GenerationRequest
    ) -> tuple[Sequence[GenerationResult], int]:
        """Ask until an answer arrives, the policy refuses, or the budget is out.

        Results are collected before any is yielded, which is what makes a
        retry safe: a failure part-way through has produced nothing the caller
        has seen, so asking again cannot duplicate output.
        """
        last: ProviderFailure | None = None
        for attempt in range(1, self.retry_policy.attempts + 1):
            try:
                results = await self.executor.generate(request)
            except ProviderCallFailed as failed:
                last = failed.failure
                # Asked once, and the answer says which rule refused. The
                # conditions overlap, so a second reader that re-derived it
                # could name a rule that did not fire.
                refusal = self.retry_policy.refuse_retry(attempt, last)
                if refusal is not None:
                    raise self.retry_policy.give_up(
                        refusal, attempt, last, self.model
                    ) from failed
                self.retry_policy.log_retry(attempt, last, self.model)
                await self.retry_policy.sleep_before_retry(attempt, last)
            else:
                self.retry_policy.budget.credit()
                return results, attempt
        raise AssertionError(f"retry loop fell through: {last!r}")


def _finish_reason_text(reason: Any) -> str | None:
    """How a provider says one completion ended, as the bare word.

    ADK hangs a ``types.FinishReason`` member here, and that enum's ``str()``
    is ``'FinishReason.STOP'`` rather than ``'STOP'`` — so stringifying it
    carried a value the type cannot be rebuilt from, and the truncation rule,
    which compares the bare word, would never have matched again. Taking
    ``value`` where there is one is the same read
    :func:`analysis_service.retry._clears_with_time` makes of litellm's enum,
    and for the same reason: a member and its string both arrive.
    """
    if reason is None:
        return None
    return str(getattr(reason, "value", reason))


def _instruction_text(config: Any) -> str | None:
    """The system instruction as text, whatever shape ADK put it in.

    ``GenerateContentConfig.system_instruction`` is typed as a union: this
    service's nodes set a string, and ADK's own flow may have replaced it with
    a ``Content``. Both are handled, because what the producer can emit is the
    question rather than what it usually emits.
    """
    stated = getattr(config, "system_instruction", None)
    if stated is None or isinstance(stated, str):
        return stated
    parts = getattr(stated, "parts", None) or []
    texts = [part.text for part in parts if getattr(part, "text", None)]
    return "".join(texts) or None


def _schema_json(schema: Any) -> Mapping[str, Any] | None:
    """One output schema as a plain JSON schema, or ``None`` where none is set.

    Every shape ADK admits is handled here rather than forwarded: a pydantic
    **class**, which is the one this service's nodes bind and the one that must
    not cross a boundary; a pydantic *instance* such as ``types.Schema``; and a
    mapping, which is already what a boundary carries.
    """
    if schema is None:
        return None
    if isinstance(schema, Mapping):
        return dict(schema)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    dumped = getattr(schema, "model_dump", None)
    if dumped is not None:
        return dumped(exclude_none=True, mode="json")
    raise TypeError(f"an output schema this seam cannot reduce to JSON: {schema!r}")
