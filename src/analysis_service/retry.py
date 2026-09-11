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
wins over any curve computed here, up to
:data:`_RETRY_AFTER_CEILING_SECONDS`. It is the one authoritative number in the
exchange and it is still a third party's, so it cannot decide how long this
process blocks: a hint past the ceiling ends the node rather than parking it,
because a provider asking for an hour has already answered the question a retry
exists to ask. It arrives on the exception where litellm files it, which is not
where an exception's ``headers`` attribute is — see :data:`_HEADER_ATTRIBUTES`.

This reverses version 2's removal of the backoff knobs, and only because the
premise changed. They went because they connected to nothing: LiteLLM picked its
own curve internally, and the config surface was decoration. With the loop here,
a curve exists to describe. This module pins it rather than re-opening it as
configuration, because it does not vary by deployment. The one number that does
vary is how much retrying a deployment will tolerate, and that is
``retry_budget_ratio`` in ``config/resilience.toml``.

Retrying is no longer quite all this module does, and the exception is
deliberate. :func:`retrying_llm_class` is the one object this service owns that sees
every raw response from every provider for every node. That makes it the only
place a length-stopped completion can be caught uniformly; see
:func:`_reject_truncated`. Nothing here can change an answer. It can refuse one
the provider has already said is incomplete.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
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

# The longest wait this module will take on a provider's word. A hint beyond it
# is not a pause, it is the provider saying capacity will not return inside the
# window this job is willing to wait, and the honest answer to that is to stop
# rather than to sleep and ask again into the same limit.
#
# **A third party must not control how long this process blocks.** Without a
# ceiling, one ``Retry-After`` header decides that: a header of 86400 parks a
# paid job for a day, and ``float("inf")`` — which ``float()`` accepts — parks
# it forever, on a call that had a deadline. Pinned rather than configured, for
# the reason the two constants above are.
#
# **60 seconds, sized against the limit that actually sends the header.** The
# ceiling was the backoff cap — the longest wait this module would choose for
# itself — for as long as no ``Retry-After`` reached it. PR #833 found that
# none ever had, because the header arrives on an attribute this module was not
# reading, so the number was chosen against a value that never came.
#
# A tokens-per-minute window is 60 seconds wide, so a provider naming 45 is
# naming a limit that really does reopen, and refusing it fails a node that one
# sleep would have carried. Beyond a minute the hint describes something other
# than the current window and the refusal is right again. It sits inside both
# bounds a job already has: one request's timeout is 300 s and the job deadline
# is 900 s, so a full set of attempts sleeping the ceiling stays under either.
_RETRY_AFTER_CEILING_SECONDS = 60.0

# ADK maps LiteLLM's ``finish_reason="length"`` onto this member of
# ``google.genai.types.FinishReason`` and hangs it on every non-streaming
# response. Compared as a bare string rather than imported, because
# ``FinishReason`` subclasses ``str`` and this module stays free of the provider
# libraries at import time, which ``_is_transient`` keeps true by reading a
# status code off the exception rather than naming a litellm class.
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

    **Deliberately not retryable.** It carries no status code, so
    :func:`_is_transient` answers no, and it must stay that way: the same
    request against the same cap truncates again, and the second ask is a
    paid-for identical answer. That is the rule the whole module already
    applies to a malformed request or a rejected credential.
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


#: Status codes that mean "ask again" rather than "this request is wrong".
#: ``408`` and ``429`` are the two client-side codes that describe the moment
#: rather than the request. Everything from ``500`` up is a server saying it
#: failed, including the gateway range (``502``, ``504``), Anthropic's overload
#: code (``529``) and the ``52x`` codes an edge network in front of an
#: aggregator can emit. ``501`` and ``505`` are permanent in HTTP's own terms
#: and are still admitted here, because litellm flattens them to ``500`` on four
#: of the six vendors and a rule that can only be obeyed on two is worse than a
#: rule that retries a code no model endpoint sends.
_TRANSIENT_CLIENT_STATUS_CODES = frozenset({408, 429})
_LOWEST_SERVER_STATUS_CODE = 500

#: Whether a stated rate-limit dimension clears on the timescale a retry waits.
#: **Not every 429 is a moment.** A request-per-minute ceiling is one, and a
#: spend cap is not: asking again in eight seconds reaches the same refusal,
#: having spent an attempt, a budget token and the wall-clock of a paid job.
#:
#: The pinned translator states the dimension on the exception. Measured on
#: litellm 1.97.0: every ``RateLimitError`` carries ``category`` (defaulting to
#: ``vendor_rate_limit``) and ``rate_limit_type``, which is ``None`` unless the
#: limiter that fired named one. ``litellm.exceptions.RateLimitType`` is the
#: closed set this keys on.
#:
#: **Spelled as strings rather than imported.** This module stays free of the
#: provider libraries at import time, which is the same reason it reads a status
#: code off the exception and compares a finish reason as bare text.
#: ``tests/test_retry.py`` drives the installed enum against this table, so a
#: dimension added by a library bump fails there rather than defaulting quietly
#: to "ask again".
#:
#: An absent or unrecognised dimension is retried, which is the safe direction:
#: a genuine throttle must survive a vocabulary this table has not met.
_RATE_LIMIT_CLEARS_WITH_TIME: Mapping[str, bool] = {
    "requests": True,  # a requests-per-minute window, which reopens
    "tokens": True,  # a tokens-per-minute window, the same
    "concurrent_requests": True,  # clears as this process's own calls finish
    "budget": False,  # a spend cap; time does not refill money
    "max_iterations": False,  # a per-session cap, which the session cannot leave
}


def _is_transient(exc: BaseException) -> bool:
    """Whether the provider said something that asking again could fix.

    **Keyed on the status code, not on the exception class.** The class is what
    litellm's mapper chooses, and it chooses differently per provider: on the
    pinned library an upstream ``500`` becomes ``InternalServerError`` on
    ``anthropic`` and ``APIError`` on ``openrouter``, and an upstream ``502``
    becomes ``BadGatewayError`` on both — a class no tuple of transient types
    ever named. The status code is the one part of the exchange every provider
    spells the same way, and every litellm exception carries it, including as a
    constructor default when nothing mapped it (``Timeout`` is ``408``,
    ``APIConnectionError`` is ``500``).

    So there is no per-vendor retry table and no branch. Keying on the thing
    that does not vary is what removes the need for one;
    ``tests/test_retry.py`` drives the installed mapper over every registered
    vendor and holds that claim.

    Everything else fails on the first attempt, which is the point rather than
    an omission — a malformed request, a rejected credential or an over-long
    context is not transient, and retrying it spends quota to reach the
    identical answer. That is the same reasoning the graph applies to a rejected
    job: a second identical ask is not a recovery strategy.

    An exception carrying no readable status code is not transient. A raised
    object from outside the provider library — this module's own
    :class:`TruncatedCompletionError` among them — says nothing about transport,
    and a missing number is not evidence of one.

    A 429 is read one step further, because the code alone conflates a moment
    with a ceiling — see :data:`_RATE_LIMIT_CLEARS_WITH_TIME`.
    """
    status = getattr(exc, "status_code", None)
    try:
        code = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # A provider SDK that carries no status, or carries an unparseable one.
        # An unreadable number is no number, the same call
        # ``_retry_after_seconds`` makes about an unreadable ``Retry-After``.
        return False
    if code in _TRANSIENT_CLIENT_STATUS_CODES:
        return _clears_with_time(exc)
    return code >= _LOWEST_SERVER_STATUS_CODE


def _clears_with_time(exc: BaseException) -> bool:
    """Whether the limit this error names is one that reopens on its own.

    ``True`` where the provider named no dimension, which is the ordinary case
    and the safe one: an unstated limit is judged by its status code exactly as
    before, and a genuine throttle must survive a vocabulary this table has not
    met.

    The value arrives as an enum member or as its string — litellm ships
    ``validate_rate_limit_type`` for exactly that duck-typed read — so the
    member's ``value`` is taken where there is one.
    """
    stated = getattr(exc, "rate_limit_type", None)
    if stated is None:
        return True
    dimension = getattr(stated, "value", stated)
    if not isinstance(dimension, str):
        return True
    return _RATE_LIMIT_CLEARS_WITH_TIME.get(dimension, True)


#: Where a raised exception carries the provider's response headers, in the
#: order they are read. **Two attributes, because the obvious one is empty on
#: every real failure.** litellm declines to copy a vendor's response headers
#: onto ``exc.headers`` — its own comment says a malicious upstream could
#: otherwise inject browser-interpreted headers through a proxy that forwarded
#: them — and fills ``exc.headers`` only from the ``headers=`` kwarg a proxy
#: supplies. What it does instead is attach every mapped exception's response
#: headers as ``litellm_response_headers``, which is where a real
#: ``Retry-After`` arrives. Reading ``headers`` alone therefore found nothing on
#: every provider failure this service can meet, while a test that built the
#: exception with ``headers={...}`` agreed with the rule.
#:
#: ``tests/test_provider_contract.py`` drives a 429 from an HTTP transport up to
#: this function, so the attribute name is held against the installed library
#: rather than against a remembered one.
_HEADER_ATTRIBUTES = ("headers", "litellm_response_headers")


def _response_headers(exc: BaseException) -> Mapping | None:
    """The provider's response headers off a raised exception, or ``None``.

    Every shape the producers emit, handled rather than assumed: a plain
    ``dict`` from a proxy-supplied ``headers=``, an ``httpx.Headers`` from
    litellm's own attachment — a ``Mapping`` and not a ``dict``, which an
    ``isinstance(..., dict)`` test silently refused — ``None`` where the
    attribute exists and was never filled, and no attribute at all on an
    exception from outside the provider library.

    An empty mapping is passed over rather than returned, so a first attribute
    that exists and says nothing does not hide a second one that speaks.
    """
    for attribute in _HEADER_ATTRIBUTES:
        headers = getattr(exc, attribute, None)
        if isinstance(headers, Mapping) and headers:
            return headers
    return None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """What the provider asked us to wait, if it said anything readable.

    The only authoritative number in the exchange: a computed curve is a guess
    about when capacity returns, and this is the answer. Both spellings are
    accepted because providers disagree on which they send, and header lookup is
    case-insensitive because HTTP is.

    **Finite, and not merely non-negative.** ``float()`` reads ``"inf"`` and
    ``"nan"``, and ``float("inf") >= 0`` is true — so an ``inf`` here reached
    :func:`asyncio.sleep` and parked the node forever, on a call that had a
    deadline. It is the same rule ``evals/harness/baseline.py`` states for a
    recorded figure, applied to a number a third party sends rather than to one
    a contributor writes, and it belongs in both places for the same reason: a
    non-finite value poisons whatever it reaches.

    A value this cannot read is no hint at all, and the caller backs off on its
    own curve. That covers a date-formatted ``Retry-After``, which this does not
    parse — guessing at a date is worse than the curve.
    """
    headers = _response_headers(exc)
    if headers is None:
        return None
    lowered = {str(key).lower(): value for key, value in headers.items()}
    for name, scale in (("retry-after", 1.0), ("retry-after-ms", 0.001)):
        raw = lowered.get(name)
        if raw is None:
            continue
        try:
            seconds = float(raw) * scale
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


def _asks_a_longer_wait_than_we_take(exc: BaseException) -> bool:
    """Whether the provider asked for a wait past :data:`_RETRY_AFTER_CEILING_SECONDS`.

    Read as evidence rather than as a delay. A provider that says "come back in
    an hour" has answered the question a retry exists to ask — will asking again
    shortly work — and the answer is no. Sleeping the hour would park a paid job
    on a third party's number; sleeping less and asking anyway spends an attempt
    and a budget token to reach the same refusal.

    Only a stated hint decides this. An error carrying none is judged by
    :func:`_is_transient` as before, because an absent header says nothing about
    how long capacity will take to return.
    """
    stated = _retry_after_seconds(exc)
    return stated is not None and stated > _RETRY_AFTER_CEILING_SECONDS


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
        """Wait out one backoff interval, preferring the provider's own answer.

        Bounded by :data:`_RETRY_AFTER_CEILING_SECONDS` whatever the provider
        said. :meth:`should_retry` already refuses an error asking for longer,
        so this clamp is the second reader of one rule and is here on purpose:
        the two are called by different code paths, and a caller that slept
        before asking whether to retry would hand a header the process's
        schedule.
        """
        delay = _retry_after_seconds(exc)
        if delay is None:
            delay = _backoff_seconds(attempt)
        await asyncio.sleep(min(delay, _RETRY_AFTER_CEILING_SECONDS))

    def should_retry(self, attempt: int, exc: BaseException) -> bool:
        """Whether ``exc`` on ``attempt`` earns another try, budget included.

        Order matters: the transient check comes first so a non-transient
        failure never spends a token it was never going to benefit from.
        """
        if attempt >= self.attempts:
            return False
        if not _is_transient(exc):
            return False
        if _asks_a_longer_wait_than_we_take(exc):
            logger.warning(
                "provider asked for a wait longer than %.0fs; failing the node"
                " rather than sleeping on its number",
                _RETRY_AFTER_CEILING_SECONDS,
            )
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
                and _is_transient(exc)
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
