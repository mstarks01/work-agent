"""The retry loop, moved up to where it can be bounded.

LiteLLM retries beneath the adapter, and that is the problem. Its
``num_retries`` sets the provider SDK's own ``max_retries`` on the way to the
client, so the worst case is ``2 * attempts - 1`` requests per node, which is
five times the fan-out in the seconds the lane agents go out together. None of
it is visible above ``generate_content_async``. Nothing above can pace it, count
it, or decide it has gone on long enough, because the requests are already made
by the time the call returns. ``tests/test_model_gate.py`` probes that coupling.
It is a fact about the installed library rather than something to argue with.

The library's retry layer is therefore switched off, at ``num_retries=0``, which
that same probe shows is exactly one request per call. This module re-implements
it, where two bounds become expressible that could not exist below:

* A shared budget. :class:`RetryBudget` is one token bucket for the whole
  process. A retry costs a token, and a success credits a fraction of one. The
  service therefore caps retries at a ratio of successful traffic rather than at
  a count per node, which is the difference between a retry policy and a storm.
  When a provider is genuinely down, every node fails at once, the bucket
  empties, and the service stops retrying instead of multiplying one outage by
  five. When a single call fails in isolation, the bucket is full and nothing
  changes.

* Decorrelated timing. Lane agents that start together fail together, and on any
  fixed backoff curve they retry together, reconverging on the quota they just
  tripped. Full jitter spreads them out: :func:`_backoff_seconds` draws
  uniformly over the whole interval, rather than adding noise to a fixed delay.
  This is the half of the storm that survives even a perfectly sized budget.

Where the provider says when to come back, in a ``Retry-After`` on a 429, that
wins over any curve computed here. It is the one authoritative number in the
exchange, and the service does not cap it. A deliberately long ``Retry-After``
is the provider asking for room, and the job deadline is what bounds the wait.

This reverses version 2's removal of the backoff knobs, and only because the
premise changed. They went because they connected to nothing: LiteLLM picked its
own curve internally, and the config surface was decoration. With the loop here,
a curve exists to describe. This module pins it rather than re-opening it as
configuration, because it does not vary by deployment. The one number that does
vary is how much retrying a deployment will tolerate, and that is
``retry_budget_ratio`` in ``config/resilience.toml``.

Retrying is no longer quite all this module does, and the exception is
deliberate. :class:`RetryingLlm` is the one object this service owns that sees
every raw response from every provider for every node. That makes it the only
place a length-stopped completion can be caught uniformly; see
:func:`_reject_truncated`. Nothing here can change an answer. It can refuse one
the provider has already said is incomplete.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from functools import cache
from typing import ClassVar

logger = logging.getLogger(__name__)

#: The ``custom_metadata`` key under which a response says which attempt
#: produced it. The executor reads it off the event by this name.
ATTEMPTS_METADATA_KEY = "attempts"

# Full-jitter bounds, pinned rather than configured (see the module docstring).
# Conventional values from AWS's "Exponential Backoff and Jitter" — the one pair
# in this module not derived from something measured. They are the fallback
# curve only: a provider that sends Retry-After overrides them outright, and a
# 429 usually does.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0

# ADK maps LiteLLM's ``finish_reason="length"`` onto this member of
# ``google.genai.types.FinishReason`` and hangs it on every non-streaming
# response. Compared as a bare string rather than imported, because
# ``FinishReason`` subclasses ``str`` and this module stays free of the provider
# libraries at import time — the same reason ``_retryable_types`` is deferred.
_MAX_TOKENS_FINISH_REASON = "MAX_TOKENS"

# The remedy half of every truncation message, shared with
# ``graph._TRUNCATION_HINT`` because a run can reach truncation from either
# direction and both want the operator to turn the same knob. It lives in this
# module for a mechanical reason: ``retry`` imports nothing from the package, so
# ``graph`` can import *it* without the cycle the other direction would make.
TRUNCATION_REMEDY = (
    "Raise max_output_tokens for this node's tier in config/sampling.toml, or"
    " reduce what the node is asked to produce."
)


class TruncatedCompletionError(RuntimeError):
    """A completion stopped at ``max_output_tokens`` with its output half-written.

    The vendor-visible twin of :class:`~analysis_service.graph.SilentNodeError`,
    and the reason both exist: *providers do not agree on what truncation looks
    like*. Anthropic and Vertex return no text at all, so the node writes no
    ``output_key`` and the hole is found downstream by a node that can name it.
    OpenAI returns the **partial** text, which ADK hands to ``validate_schema``
    unaware that it is a fragment — the run then dies inside pydantic on a
    ``ValidationError`` naming a column offset, with the drafts already paid
    for and nothing in the message pointing at the cap.

    So this is checked where the difference is still visible. ``finish_reason``
    says truncation outright on both paths, which is strictly better evidence
    than :class:`~analysis_service.graph.SilentNodeError`'s inference from an
    absent key — it names the node's model and fires before the partial text
    reaches a validator that will misdescribe it.

    **Deliberately not retryable.** It is not in :func:`_retryable_types` and
    must not be: the same request against the same cap truncates again, and the
    second ask is a paid-for identical answer. That is the rule the whole module
    already applies to a malformed request or a rejected credential.
    """


class RetryBudgetExhausted(RuntimeError):
    """A retry was refused because the process had spent its retry budget.

    Raised in place of the transient error that would have been retried, so the
    job fails naming the budget rather than the 429 — a run that dies here died
    because the whole service was failing, not because this one call was
    unlucky, and those want different operator responses.
    """


@dataclass
class RetryBudget:
    """One token bucket bounding retries as a share of successful traffic.

    The anti-storm mechanism, and deliberately *not* a per-node counter: a count
    per node is what produces ``2 * attempts - 1`` in the first place, because
    every node gets its full allowance no matter what the others are seeing.
    A budget is shared, so correlated failure — the only kind that becomes a
    storm — exhausts it once for everyone.

    ``ratio`` is the sustained ceiling: at 0.1, retries can never exceed ~10% of
    successful requests over the long run, whatever the failure rate does. The
    bucket starts full so an isolated early failure is still retried; ``capacity``
    is what one job may spend from cold, so a single unlucky run retries freely
    and a service-wide outage does not.

    Not thread-safe, and does not need to be: one process, one event loop, and
    every mutation here is a whole statement between awaits.
    """

    capacity: float
    ratio: float
    tokens: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def credit(self) -> None:
        """Record a successful request, refilling the bucket toward capacity."""
        self.tokens = min(self.capacity, self.tokens + self.ratio)

    def withdraw(self) -> bool:
        """Spend a token on one retry, or report that there is none to spend."""
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


@cache
def _retryable_types() -> tuple[type[BaseException], ...]:
    """The transient failures worth asking again about.

    Imported lazily and memoized: this module is on the import path of callers
    that never make a request, and ``model_gate`` has to pin the local cost map
    before anything pulls litellm in.

    Everything absent from this tuple fails on the first attempt, which is the
    point rather than an omission — a malformed request, a rejected credential
    or an over-long context is not transient, and retrying it spends quota to
    reach the identical answer. That is the same reasoning the graph applies to
    a rejected job: a second identical ask is not a recovery strategy.
    """
    from litellm import (
        APIConnectionError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )

    return (
        APIConnectionError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )


def _retry_after_seconds(exc: BaseException) -> float | None:
    """What the provider asked us to wait, if it said anything.

    The only authoritative number in the exchange: a computed curve is a guess
    about when capacity returns, and this is the answer. Both spellings are
    accepted because providers disagree on which they send, and header lookup is
    case-insensitive because HTTP is.
    """
    headers = getattr(exc, "headers", None)
    if not isinstance(headers, dict):
        return None
    lowered = {str(key).lower(): value for key, value in headers.items()}
    for name, scale in (("retry-after", 1.0), ("retry-after-ms", 0.001)):
        raw = lowered.get(name)
        if raw is None:
            continue
        try:
            seconds = float(raw) * scale
        except (TypeError, ValueError):
            # A date-formatted Retry-After, which this does not parse. Falling
            # through to the jittered curve is right: an unreadable hint is no
            # hint, and guessing at a date is worse than backing off.
            continue
        if seconds >= 0:
            return seconds
    return None


def _backoff_seconds(attempt: int) -> float:
    """Full jitter: a uniform draw over the whole interval, not a delay plus noise.

    ``attempt`` is 1-based, so the first retry draws from [0, base]. Drawing
    across the entire window is what actually decorrelates the lane agents that
    failed at the same instant; an exponential delay with a jitter *added* keeps
    them clustered around the same point on the curve, which is the shape that
    reconverges on the quota it just tripped.
    """
    ceiling = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
    return random.uniform(0, ceiling)


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try again, and how much the process may try in total.

    ``attempts`` keeps the meaning it has in ``config/resilience.toml`` — a
    *total* count, not retries-after-the-first — so the number an operator turns
    down mid-incident still means what the file says. It is now honest as well:
    with the library's own layer off, ``attempts`` is the request count per node
    rather than half of a product with it.
    """

    attempts: int
    budget: RetryBudget

    async def sleep_before_retry(self, attempt: int, exc: BaseException) -> None:
        """Wait out one backoff interval, preferring the provider's own answer."""
        delay = _retry_after_seconds(exc)
        if delay is None:
            delay = _backoff_seconds(attempt)
        await asyncio.sleep(delay)

    def should_retry(self, attempt: int, exc: BaseException) -> bool:
        """Whether ``exc`` on ``attempt`` earns another try, budget included.

        Order matters: the transient check comes first so a non-transient
        failure never spends a token it was never going to benefit from.
        """
        if attempt >= self.attempts:
            return False
        if not isinstance(exc, _retryable_types()):
            return False
        return self.budget.withdraw()


def _stamped(responses: Sequence, attempt: int) -> Sequence:
    """``responses`` carrying the number of the attempt that produced them.

    A failed attempt yields no response and so meters nothing, and the one
    that answered meters only itself. The count rides on the response's own
    metadata, which ADK copies onto the event the executor reads, so a
    settlement can charge the prompt bytes the failed attempts sent (OWASP
    LLM10). Stamped on every attempt rather than only a retried one, so a
    response without the stamp is one that never passed through this driver.
    """
    for response in responses:
        response.custom_metadata = {
            **(response.custom_metadata or {}),
            ATTEMPTS_METADATA_KEY: attempt,
        }
    return responses


def _reject_truncated(responses: Sequence, model: str) -> None:
    """Raise if the provider stopped any of ``responses`` at the token cap.

    Every response is checked rather than just the last: a non-streaming call
    yields one today, but the collection is a sequence and a truncated part
    anywhere in it is a truncated answer.

    ``finish_reason`` is read instead of ``error_code``, which ADK sets to the
    same value. The former is the provider's own word for what happened; the
    latter is ADK's overloading of an error channel for a response that carries
    no error, and it stops being set the day that overloading is reconsidered.
    """
    truncated = any(
        response.finish_reason == _MAX_TOKENS_FINISH_REASON for response in responses
    )
    if not truncated:
        return
    logger.error(
        "%s: completion stopped at max_output_tokens with partial output;"
        " failing the node rather than validating a fragment",
        model,
    )
    raise TruncatedCompletionError(
        f"{model} stopped at max_output_tokens with its output incomplete, so"
        f" what it emitted is a fragment rather than an answer."
        f" {TRUNCATION_REMEDY}"
    )


def retrying_llm_class(litellm_cls: type, policy: RetryPolicy) -> type:
    """A ``LiteLlm`` subclass that owns its retries, built against one policy.

    Takes the class rather than importing it, so this module stays free of the
    provider library at import time — ``binding`` already has it in hand, and
    already had to defer that import for the cost-map ordering.

    One class per policy rather than a pydantic field: the policy holds a
    mutable budget, and a shared mutable object is not what a frozen model field
    is for. It is readable back off the class as ``retry_policy``, which is what
    makes the wiring assertable — every other test in this area can pass while
    the adapters a deployment actually builds carry no retry loop at all.
    """

    class RetryingLlm(litellm_cls):
        """One tier's adapter, retrying under the process-wide budget.

        The responses of a non-streaming call are collected before any is
        yielded. That is what makes a retry safe: a failure part-way through the
        inner generator has produced nothing the caller has already seen, so
        asking again cannot duplicate output. A streaming call cannot offer that
        and is passed straight through — this service binds an ``output_schema``
        on every node and so never streams, and a retry policy that silently
        replayed half a stream would be worse than none.

        The truncation check rides on that same split, and for the same reason:
        a streamed answer is yielded before any chunk carries a
        ``finish_reason``, so there is nothing left to refuse by the time the
        caller has seen the text. Unreached today, and it stays that way for as
        long as every node carries a schema.
        """

        # ClassVar, not a field: shared by every instance built against this
        # policy, and pydantic leaves it alone on the LiteLlm path.
        retry_policy: ClassVar[RetryPolicy] = policy

        async def generate_content_async(
            self, llm_request, stream: bool = False
        ) -> AsyncGenerator:
            if stream:
                async for response in super().generate_content_async(
                    llm_request, stream
                ):
                    yield response
                return

            # Checked here rather than inside ``_attempt_with_retries`` so that
            # method keeps its one job — surviving transport failures — and a
            # truncation is never mistaken for one of them.
            responses = await self._attempt_with_retries(llm_request)
            _reject_truncated(responses, self.model)
            for response in responses:
                yield response

        async def _attempt_with_retries(self, llm_request) -> Sequence:
            last_exc: BaseException | None = None
            for attempt in range(1, self.retry_policy.attempts + 1):
                try:
                    responses = [
                        response
                        async for response in super().generate_content_async(
                            llm_request, False
                        )
                    ]
                except Exception as exc:
                    last_exc = exc
                    if not self.retry_policy.should_retry(attempt, exc):
                        raise self._give_up(attempt, exc) from exc
                    logger.warning(
                        "%s: attempt %d/%d failed (%s); retrying",
                        self.model,
                        attempt,
                        self.retry_policy.attempts,
                        type(exc).__name__,
                    )
                    await self.retry_policy.sleep_before_retry(attempt, exc)
                else:
                    self.retry_policy.budget.credit()
                    return _stamped(responses, attempt)
            raise AssertionError(f"retry loop fell through: {last_exc!r}")

        def _give_up(self, attempt: int, exc: BaseException) -> BaseException:
            """The exception to raise, naming the budget when that is the cause.

            A run killed by an empty budget and a run killed by an unretryable
            400 both stop on their first exception, but they mean opposite
            things to whoever is paged: one says the whole service is failing,
            the other says this one request was wrong. Only the first is worth
            renaming.
            """
            spent_budget = (
                attempt < self.retry_policy.attempts
                and isinstance(exc, _retryable_types())
                and self.retry_policy.budget.tokens < 1.0
            )
            if not spent_budget:
                return exc
            logger.error(
                "%s: retry budget exhausted at attempt %d/%d after %s;"
                " failing fast rather than adding to the storm",
                self.model,
                attempt,
                self.retry_policy.attempts,
                type(exc).__name__,
            )
            return RetryBudgetExhausted(
                f"retry budget exhausted while retrying {type(exc).__name__}"
            )

    return RetryingLlm
