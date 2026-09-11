"""The retry loop and the shared budget that keeps it from becoming a storm.

Driven against a fake **executor** rather than a fake translator. The loop runs
above :mod:`analysis_service.provider`'s seam, so what a test has to supply is
one method returning results or raising — no provider library, no credential
and no request. The adapter itself is the shipped
:class:`~analysis_service.provider.ExecutedLlm`.

The litellm exception *types* are real, because the classification is the one
part that genuinely depends on them.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import ClassVar

import litellm
import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from litellm import APIConnectionError, RateLimitError
from litellm.exceptions import RateLimitType

# Imported through ``model_gate`` so the model-cost map is pinned before
# anything here reaches litellm's own tables.
from analysis_service import model_gate  # noqa: F401
from analysis_service.provider import (
    ExecutedLlm,
    GenerationResult,
    ProviderCallFailed,
)
from analysis_service.retry import (
    _RATE_LIMIT_CLEARS_WITH_TIME,
    _RETRY_AFTER_CEILING_SECONDS,
    RetryBudget,
    RetryBudgetExhausted,
    RetryPolicy,
    RetryRefusal,
    TruncatedCompletionError,
    _backoff_seconds,
    _is_transient,
    _retry_after_seconds,
    classify,
)
from analysis_service.vendors import VENDOR_NAMES, vendor_for

#: The loop projects whatever request it is handed and passes the projection
#: to the executor, which here ignores it. One empty request stands in for
#: every call, in ADK's own type because that is what the adapter takes.
_ANY_REQUEST = LlmRequest(
    contents=[types.Content(role="user", parts=[types.Part(text="hi")])]
)


def mapped_provider_exception(provider: str, model: str, status_code: int):
    """What the installed litellm turns one provider failure into.

    The shape ``convert_to_model_response_object`` raises is a bare
    ``Exception`` carrying ``status_code`` and ``message``, so this is the real
    input the mapper sees rather than a stand-in for it. Defined here, beside
    the rule that reads the result, and imported by the vendor-specific
    compatibility suite rather than copied into it.
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


def rate_limited(**headers) -> RateLimitError:
    return RateLimitError(
        message="slow down",
        llm_provider="openai",
        model="gpt-4o",
        headers=headers or None,
    )


def response(text: str, finish_reason: str | None = None) -> LlmResponse:
    """One answer, in **ADK's own type** rather than a stand-in for it.

    A bare string stood in for a response until the truncation check needed to
    read a ``finish_reason``, and that substitution was the same shape as the
    bug it hid: what a provider says about *how* a completion ended is part of
    the answer rather than metadata around it. The real type is what the seam
    reduces to a :class:`~analysis_service.provider.GenerationResult`, so using
    it here means every field the projection reads is a field that exists.
    """
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        finish_reason=finish_reason,  # type: ignore[arg-type]
    )


def truncated(text: str = "half a doc") -> LlmResponse:
    """What a provider that returns its partial output hands back at the cap."""
    return response(text, finish_reason="MAX_TOKENS")


def texts(responses) -> list[str]:
    return ["".join(part.text or "" for part in r.content.parts) for r in responses]


class FakeExecutor:
    """A stand-in for whatever runs a generation: scripted outcomes per call.

    The seam's own interface, which is a smaller thing to fake than a
    translator: ``outcomes`` is consumed one entry per call — an exception to
    raise, an :class:`LlmResponse` to return, or a string wrapped into one. It
    counts calls, which is the number every claim in this module is about.

    It raises the provider's exception rather than a
    :class:`~analysis_service.provider.ProviderCallFailed`, because that
    translation is :class:`~analysis_service.provider.InProcessExecutor`'s job
    and it is the thing under test here as much as the loop is.
    """

    def __init__(self, model: str = "fake/model", outcomes=()) -> None:
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, BaseException):
            raise ProviderCallFailed(classify(outcome)) from outcome
        if isinstance(outcome, str):
            outcome = response(outcome)
        return [GenerationResult.of(outcome)]


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """Backoff is exercised, never waited out."""
    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


def policy(attempts: int = 3, capacity: float = 10, ratio: float = 0.1) -> RetryPolicy:
    return RetryPolicy(
        attempts=attempts, budget=RetryBudget(capacity=capacity, ratio=ratio)
    )


def drive(base: FakeExecutor, pol: RetryPolicy, stream: bool = False) -> list:
    """Run one call through the shipped adapter, over a scripted executor.

    The adapter is :class:`~analysis_service.provider.ExecutedLlm` — the one
    ``binding`` builds — so the loop under test is the one that runs in
    production, with only what sits on the far side of the seam replaced.
    """
    adapter = ExecutedLlm(model=base.model, executor=base, retry_policy=pol)

    async def scenario():
        return [r async for r in adapter.generate_content_async(_ANY_REQUEST, stream)]

    return asyncio.run(scenario())


class TestRetryBudget:
    def test_it_starts_full_so_an_isolated_failure_is_still_retried(self):
        assert RetryBudget(capacity=10, ratio=0.1).tokens == 10

    def test_a_retry_costs_one_token(self):
        budget = RetryBudget(capacity=10, ratio=0.1)
        assert budget.withdraw()
        assert budget.tokens == 9

    def test_an_empty_budget_refuses_rather_than_going_negative(self):
        budget = RetryBudget(capacity=1, ratio=0.1)
        assert budget.withdraw()
        assert not budget.withdraw()
        assert budget.tokens == 0

    def test_success_refills_at_the_ratio(self):
        budget = RetryBudget(capacity=10, ratio=0.1, tokens=0)
        budget.tokens = 0
        for _ in range(10):
            budget.credit()
        assert budget.tokens == pytest.approx(1.0)

    def test_refill_stops_at_capacity(self):
        budget = RetryBudget(capacity=2, ratio=0.5)
        for _ in range(20):
            budget.credit()
        assert budget.tokens == 2

    def test_the_ratio_is_the_sustained_ceiling_on_retrying(self):
        """The property the whole mechanism exists for.

        Over a long run of successes, retries can never exceed ``ratio`` of
        them — so a retry policy cannot amplify traffic without bound no matter
        how the failure rate moves.
        """
        budget = RetryBudget(capacity=5, ratio=0.1)
        retries = 0
        for _ in range(1000):
            budget.credit()
            if budget.withdraw():
                retries += 1
        assert retries <= 1000 * 0.1 + 5


class TestNotEveryRateLimitIsAMoment:
    """A 429 conflates a window that reopens with a ceiling that does not.

    The pinned translator states which. Measured on litellm 1.97.0: every
    ``RateLimitError`` carries ``category``, defaulting to
    ``vendor_rate_limit``, and ``rate_limit_type``, which is ``None`` unless the
    limiter that fired named a dimension.
    """

    @pytest.mark.parametrize("dimension", ["requests", "tokens", "concurrent_requests"])
    def test_a_window_that_reopens_is_retried(self, dimension):
        limited = rate_limited()
        limited.rate_limit_type = dimension

        assert _is_transient(limited)

    @pytest.mark.parametrize("dimension", ["budget", "max_iterations"])
    def test_a_ceiling_that_time_does_not_clear_is_not_retried(self, dimension):
        """Asking again in eight seconds reaches the same refusal.

        It costs an attempt, a budget token and the wall-clock of a paid job to
        find that out, every time.
        """
        limited = rate_limited()
        limited.rate_limit_type = dimension

        assert not _is_transient(limited)

    def test_the_dimension_is_read_from_an_enum_member_too(self):
        """litellm ships the value as an enum, and ``validate_rate_limit_type``
        exists precisely because read sites meet both spellings."""
        limited = rate_limited()
        limited.rate_limit_type = RateLimitType.BUDGET

        assert not _is_transient(limited)

    def test_an_unstated_dimension_is_judged_by_its_status_alone(self):
        """The ordinary case, and the safe one.

        A provider that names no dimension is throttling as far as anything
        here can tell, and #824 asks that an ambiguous code keep its throttle.
        """
        assert rate_limited().rate_limit_type is None
        assert _is_transient(rate_limited())

    @pytest.mark.parametrize("stated", ["some_new_limit", 7, object()])
    def test_a_dimension_this_table_has_not_met_is_retried(self, stated):
        """A vocabulary that grew must not silently stop a genuine throttle."""
        limited = rate_limited()
        limited.rate_limit_type = stated

        assert _is_transient(limited)

    def test_the_table_answers_for_every_dimension_the_library_names(self):
        """The table against its registry, which is what keeps it honest.

        The rule is spelled as strings so this module stays free of the provider
        libraries at import time. That leaves the table and the enum as two
        readers of one vocabulary, so they are tested against each other: a
        dimension added by a library bump fails here rather than defaulting
        quietly to "ask again".
        """
        shipped = {member.value for member in RateLimitType}
        missing = sorted(shipped - set(_RATE_LIMIT_CLEARS_WITH_TIME))

        assert not missing, (
            f"litellm names rate-limit dimensions this table does not:"
            f" {missing}. Each one has to say whether waiting reopens it —"
            " an absent entry is retried, which is safe for a throttle and"
            " wrong for a cap."
        )

    def test_the_table_names_nothing_the_library_does_not(self):
        """The other direction: an entry for a dimension that no longer exists
        is a rule nobody can trigger, and it reads as coverage."""
        shipped = {member.value for member in RateLimitType}

        assert not sorted(set(_RATE_LIMIT_CLEARS_WITH_TIME) - shipped)


class TestRetryAfter:
    def test_seconds_header_is_honoured(self):
        assert _retry_after_seconds(rate_limited(**{"retry-after": "12"})) == 12.0

    def test_millisecond_header_is_honoured(self):
        assert _retry_after_seconds(rate_limited(**{"retry-after-ms": "1500"})) == 1.5

    def test_header_lookup_is_case_insensitive(self):
        assert _retry_after_seconds(rate_limited(**{"Retry-After": "3"})) == 3.0

    def test_an_unparseable_value_falls_through_to_the_curve(self):
        # A date-formatted Retry-After: an unreadable hint is no hint, and
        # guessing at a date is worse than backing off.
        assert (
            _retry_after_seconds(rate_limited(**{"retry-after": "Wed, 21 Oct"})) is None
        )

    def test_no_headers_at_all_is_no_hint(self):
        assert _retry_after_seconds(rate_limited()) is None
        assert _retry_after_seconds(ValueError("nothing")) is None

    def test_a_mapping_that_is_not_a_dict_is_still_read(self):
        """The shape a real failure arrives in, and the one that was refused.

        litellm attaches a vendor's response headers as ``httpx.Headers``,
        which is a ``Mapping`` and not a ``dict`` — so an ``isinstance(...,
        dict)`` test discarded every real hint. Written as a plain ``Mapping``
        rather than as ``httpx.Headers`` because the rule is about the shape,
        and ``tests/test_provider_contract.py`` is what drives the library's
        own object up to this function.
        """
        exc = ValueError("slow down")
        exc.litellm_response_headers = MappingProxyType({"retry-after": "4"})

        assert _retry_after_seconds(exc) == 4.0

    def test_the_attribute_a_proxy_fills_is_read_before_the_attached_one(self):
        """Two attributes carry headers, so which one wins is a rule.

        ``headers`` is what a caller set deliberately; the attached copy is what
        the library found on the response. The explicit one wins.
        """
        exc = rate_limited(**{"retry-after": "2"})
        exc.litellm_response_headers = MappingProxyType({"retry-after": "9"})

        assert _retry_after_seconds(exc) == 2.0

    def test_an_empty_mapping_does_not_hide_the_next_attribute(self):
        """A first attribute that exists and says nothing is not an answer."""
        exc = rate_limited()
        exc.headers = {}
        exc.litellm_response_headers = MappingProxyType({"retry-after": "6"})

        assert _retry_after_seconds(exc) == 6.0

    @pytest.mark.parametrize("value", ["inf", "-inf", "nan", "Infinity"])
    def test_a_value_that_is_not_a_finite_number_is_no_hint(self, value):
        """``float()`` reads these, and ``float("inf") >= 0`` is true.

        An ``inf`` here reached ``asyncio.sleep`` and parked the node forever,
        on a call that had a deadline. The same rule the recorded-money reader
        states, applied to a number a third party sends.
        """
        assert _retry_after_seconds(rate_limited(**{"retry-after": value})) is None

    def test_a_wait_past_the_ceiling_is_not_retried(self):
        """The provider has answered the question a retry exists to ask.

        Sleeping an hour would park a paid job on a third party's number, and
        sleeping less and asking anyway spends an attempt and a budget token to
        reach the same refusal.
        """
        pol = policy()
        long_wait = rate_limited(**{"retry-after": "3600"})

        assert pol.refuse_retry(1, classify(long_wait)) is RetryRefusal.WAIT_TOO_LONG
        assert pol.budget.tokens == pytest.approx(policy().budget.tokens), (
            "a refusal that spends a token charges the storm budget for a call"
            " it never made"
        )

    def test_a_wait_inside_the_ceiling_is_still_retried(self):
        """The rule refuses a long wait, not every stated one."""
        assert (
            policy().refuse_retry(1, classify(rate_limited(**{"retry-after": "5"})))
            is None
        )

    def test_a_full_token_window_is_still_retried(self):
        """What the ceiling was raised for, named as the case rather than as a
        number: a tokens-per-minute window is 60 seconds wide, so a provider
        asking for 45 names a limit that really does reopen."""
        assert (
            policy().refuse_retry(1, classify(rate_limited(**{"retry-after": "45"})))
            is None
        )

    def test_the_ceiling_covers_a_whole_per_minute_window(self):
        """The reasoning behind the constant, held rather than written down.

        Every limit that sends this header and clears on its own clears within
        the minute it is measured over. A ceiling under that refuses the
        commonest real throttle; the value is what makes the rule mean "not
        soon" rather than "not immediately"."""
        assert _RETRY_AFTER_CEILING_SECONDS >= 60.0

    def test_an_error_with_no_hint_is_judged_as_before(self):
        """An absent header says nothing about when capacity returns."""
        assert policy().refuse_retry(1, classify(rate_limited())) is None

    def test_the_sleep_is_bounded_whatever_the_header_says(self):
        """The second reader of one rule, and deliberately so.

        ``refuse_retry`` refuses a long wait, and this clamp is what stops a
        caller that slept first from handing a header the process's schedule.
        """
        pol = policy()
        slept: list[float] = []

        async def scenario():
            await pol.sleep_before_retry(
                1, classify(rate_limited(**{"retry-after": "86400"}))
            )

        async def fake_sleep(delay):
            slept.append(delay)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", fake_sleep)
            asyncio.run(scenario())

        assert slept == [_RETRY_AFTER_CEILING_SECONDS]

    def test_the_provider_beats_the_computed_curve(self):
        pol = policy()
        slept: list[float] = []

        async def scenario():
            await pol.sleep_before_retry(
                1, classify(rate_limited(**{"retry-after": "7"}))
            )

        async def fake_sleep(delay):
            slept.append(delay)

        asyncio.get_event_loop_policy()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", fake_sleep)
            asyncio.run(scenario())
        assert slept == [7.0]


class TestBackoff:
    def test_full_jitter_draws_across_the_whole_interval(self):
        """Not a fixed delay plus noise — the draw starts at zero.

        That is what actually decorrelates six category agents that failed at the same
        instant; a curve with jitter merely added keeps them clustered.
        """
        draws = [_backoff_seconds(3) for _ in range(200)]
        assert min(draws) < 1.0
        assert max(draws) <= 4.0
        assert all(d >= 0 for d in draws)

    def test_the_interval_grows_with_the_attempt(self):
        assert max(_backoff_seconds(1) for _ in range(200)) <= 1.0
        assert max(_backoff_seconds(2) for _ in range(200)) <= 2.0

    def test_the_interval_is_capped(self):
        assert all(_backoff_seconds(50) <= 30.0 for _ in range(200))


class TestRefuseRetry:
    def test_a_transient_failure_earns_another_try(self):
        assert policy().refuse_retry(1, classify(rate_limited())) is None

    def test_a_non_transient_failure_does_not(self):
        assert (
            policy().refuse_retry(1, classify(ValueError("malformed request")))
            is RetryRefusal.NOT_RETRYABLE
        )

    def test_a_non_transient_failure_spends_no_budget(self):
        # Order matters: a token spent on a failure that will never benefit
        # from a retry is a token the next real outage cannot draw on.
        pol = policy()
        pol.refuse_retry(1, classify(ValueError("malformed request")))
        assert pol.budget.tokens == 10

    def test_the_last_attempt_does_not_retry(self):
        assert (
            policy(attempts=3).refuse_retry(3, classify(rate_limited()))
            is RetryRefusal.ATTEMPTS_SPENT
        )

    def test_a_long_wait_is_named_as_such_even_on_an_empty_budget(self):
        """The regression: four conditions overlap, and only one fired.

        ``give_up`` used to re-derive why the loop stopped, from ``attempt``,
        ``retryable`` and ``budget.tokens``. All three hold here while the
        rule that actually refused is the ``Retry-After``, so it raised
        ``RetryBudgetExhausted`` in place of the provider's own exception and
        logged that the service was failing. The refusal is one value now.
        """
        pol = policy(attempts=3, capacity=0.5)
        long_wait = classify(rate_limited(**{"retry-after": "120"}))

        refusal = pol.refuse_retry(1, long_wait)

        assert refusal is RetryRefusal.WAIT_TOO_LONG
        raised = pol.give_up(refusal, 1, long_wait, "openrouter/m")
        assert raised is long_wait.cause, (
            "the caller loses the provider's own exception to a budget error"
            " the budget did not cause"
        )

    def test_an_exhausted_budget_stops_retrying_a_transient_failure(self):
        pol = policy(capacity=1)
        assert pol.refuse_retry(1, classify(rate_limited())) is None
        assert (
            pol.refuse_retry(1, classify(rate_limited())) is RetryRefusal.BUDGET_SPENT
        )


class TestWhatCountsAsTransient:
    """The rule itself, over every shape a raised object can arrive in.

    ``_is_transient`` reads one attribute off an exception raised by a third
    party, so the shapes are the producer's, not this suite's: an integer, a
    string of digits from an SDK that stringifies it, something unparseable,
    and nothing at all.
    """

    @pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504, 529, 522])
    def test_a_transient_code_earns_another_try(self, code):
        assert _is_transient(litellm.APIError(code, "declined", "p", "m"))

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422, 499])
    def test_a_permanent_code_does_not(self, code):
        assert not _is_transient(litellm.APIError(code, "declined", "p", "m"))

    def test_a_stringified_status_is_still_read(self):
        exc = ValueError("declined")
        exc.status_code = "503"
        assert _is_transient(exc)

    @pytest.mark.parametrize("status", [None, "gateway", object()])
    def test_an_unreadable_status_is_not_transient(self, status):
        """An unreadable number is no number — the call ``_retry_after_seconds``
        already makes about a date-formatted ``Retry-After``."""
        exc = ValueError("declined")
        exc.status_code = status
        assert not _is_transient(exc)

    def test_an_exception_with_no_status_at_all_is_not_transient(self):
        assert not _is_transient(ValueError("malformed request"))

    def test_a_truncated_completion_is_not_transient(self):
        assert not _is_transient(TruncatedCompletionError("stopped at the cap"))


class TestTheLadderIsVendorNeutral:
    """The claim the rule is built to make, driven against litellm's own mapper.

    Which exception *class* a provider failure becomes is decided per provider
    by litellm, and it differs: an upstream 500 is ``InternalServerError`` on
    ``anthropic`` and ``APIError`` on ``openrouter``, and an upstream 502 is
    ``BadGatewayError`` on both. A ladder keyed on the class therefore retried
    an upstream 500 on five vendors and not on the sixth, and an upstream 502 on
    none of them.

    Keying on the status code removes the difference rather than tabulating it,
    so what this asserts is **agreement across the registry**, one row per
    ``(vendor, upstream status)``. It is a cross-reader test in the sense
    ``CLAUDE.md`` means: this repository's rule against the installed library's
    behaviour, never against a remembered list of class names. A litellm bump
    that re-maps any of it fails here rather than on node one of a paid job.
    """

    #: Upstream codes litellm passes through with the code intact on every
    #: registered vendor, beside what the ladder must decide about each.
    DECIDED: ClassVar[dict[int, bool]] = {
        400: False,
        401: False,
        404: False,
        408: True,
        429: True,
        500: True,
        502: True,
        503: True,
        504: True,
        529: True,
    }

    #: Upstream codes litellm does *not* agree on across vendors, recorded
    #: rather than quietly left out of ``DECIDED``, beside the statuses it
    #: actually produces. It rewrites each of these to ``APIConnectionError``
    #: with a 500 on the vendors whose providers it maps that way, so a rejected
    #: permission is retried on those and refused on the rest.
    #:
    #: **That asymmetry is litellm's and predates this rule**, which cannot see
    #: past the number the library hands it. Keying on the class did not fix it
    #: either: ``APIConnectionError`` is retryable under both rules. It is
    #: written down here because a reader who trusts the neutrality claim above
    #: needs to know exactly how far it reaches.
    FLATTENED: ClassVar[dict[int, set[int]]] = {
        403: {403, 500},
        409: {409, 500},
        422: {400, 500},
    }

    @pytest.mark.parametrize("status,retried", sorted(DECIDED.items()))
    @pytest.mark.parametrize("name", sorted(VENDOR_NAMES))
    def test_every_vendor_decides_one_upstream_code_the_same_way(
        self, name, status, retried
    ):
        mapped = mapped_provider_exception(
            vendor_for(name).litellm_provider, "some-model", status
        )
        assert _is_transient(mapped) is retried

    @pytest.mark.parametrize("status,rewritten", sorted(FLATTENED.items()))
    def test_the_flattened_codes_are_the_ones_litellm_does_not_agree_on(
        self, status, rewritten
    ):
        """Pinned so ``DECIDED`` cannot grow a row the library cannot support,
        and so a bump that repairs the flattening shows up as a failure here."""
        seen = {
            getattr(
                mapped_provider_exception(
                    vendor_for(name).litellm_provider, "some-model", status
                ),
                "status_code",
                None,
            )
            for name in VENDOR_NAMES
        }
        assert seen == rewritten

    def test_no_code_is_both_decided_and_flattened(self):
        """The two tables are one statement split in two, so they must not
        overlap: a code litellm rewrites per vendor cannot also be one every
        vendor decides the same way."""
        assert not set(self.DECIDED) & set(self.FLATTENED)


class TestRetryingAdapter:
    def test_a_clean_call_makes_one_request(self):
        base = FakeExecutor(outcomes=["ok"])
        assert texts(drive(base, policy())) == ["ok"]
        assert base.calls == 1

    def test_a_transient_failure_is_retried_and_the_answer_survives(self):
        base = FakeExecutor(outcomes=[rate_limited(), "ok"])
        assert texts(drive(base, policy())) == ["ok"]
        assert base.calls == 2

    def test_the_answer_says_which_attempt_produced_it(self):
        # A failed attempt meters nothing, so the count on the answer is what
        # lets a settlement charge the prompts the failed attempts sent.
        assert drive(FakeExecutor(outcomes=["ok"]), policy())[0].custom_metadata == {
            "attempts": 1
        }
        retried = drive(
            FakeExecutor(outcomes=[rate_limited(), rate_limited(), "ok"]), policy()
        )
        assert retried[0].custom_metadata == {"attempts": 3}

    def test_success_credits_the_budget(self):
        pol = policy()
        pol.budget.tokens = 5
        drive(FakeExecutor(outcomes=["ok"]), pol)
        assert pol.budget.tokens == pytest.approx(5.1)

    def test_a_non_transient_failure_fails_on_the_first_request(self):
        base = FakeExecutor(outcomes=[ValueError("malformed"), "ok"])
        with pytest.raises(ValueError, match="malformed"):
            drive(base, policy())
        assert base.calls == 1

    def test_attempts_is_the_request_count_per_node(self):
        """The claim ``attempts`` could not make while LiteLLM retried beneath."""
        base = FakeExecutor(outcomes=[rate_limited()] * 5)
        with pytest.raises(RateLimitError):
            drive(base, policy(attempts=3))
        assert base.calls == 3

    def test_an_exhausted_budget_fails_fast_and_says_which_it_was(self):
        base = FakeExecutor(outcomes=[rate_limited()] * 5)
        with pytest.raises(RetryBudgetExhausted):
            drive(base, policy(attempts=3, capacity=0.5))
        assert base.calls == 1

    def test_a_partial_generator_never_reaches_the_caller(self):
        """Buffering is what makes the retry safe rather than duplicating."""
        base = FakeExecutor(
            outcomes=[APIConnectionError("dropped", "openai", "m"), "ok"]
        )
        assert texts(drive(base, policy())) == ["ok"]

    def test_a_streaming_call_is_refused_rather_than_passed_through(self):
        """A change of behaviour, and a deliberate one.

        The old adapter passed a streamed call straight down to the translator:
        no retry, because a replayed half-stream is worse than no retry at all,
        and no truncation check, because a chunk carries no finish reason until
        the caller has already seen the text. It now crosses no seam either,
        which is one silent skip too many. Every node here binds an output
        schema and so never streams, so this branch was untravelled under both
        rules — and an untravelled branch that refuses is found by whoever
        first turns streaming on, while one that succeeds quietly is not.
        """
        base = FakeExecutor(outcomes=["ok"])
        with pytest.raises(NotImplementedError):
            drive(base, policy(), stream=True)
        assert base.calls == 0


class TestTruncationIsRefused:
    """The vendor difference that used to reach a validator as a parse error.

    A provider that returns its partial output at ``max_output_tokens`` produces
    a response nothing downstream can tell from a complete one — the text is
    there, it is simply half of a document. ADK hands it to ``validate_schema``,
    and the run dies inside pydantic naming a column offset, having already paid
    for every node that ran first. ``finish_reason`` is the one place the
    difference is still legible, and this is the only object in the service that
    sees it for every node.
    """

    def test_a_length_stop_fails_the_node(self):
        base = FakeExecutor(outcomes=[truncated()])
        with pytest.raises(TruncatedCompletionError):
            drive(base, policy())

    def test_the_partial_output_never_reaches_the_caller(self):
        """The whole point: a fragment must not be validated as an answer."""
        base = FakeExecutor(outcomes=[truncated('{"threats":[{"id":')])
        with pytest.raises(TruncatedCompletionError):
            drive(base, policy())

    def test_it_is_not_retried(self):
        """The same request against the same cap truncates again."""
        base = FakeExecutor(outcomes=[truncated(), "ok"])
        with pytest.raises(TruncatedCompletionError):
            drive(base, policy(attempts=3))
        assert base.calls == 1

    def test_it_spends_no_retry_budget(self):
        pol = policy()
        pol.budget.tokens = 5
        with pytest.raises(TruncatedCompletionError):
            drive(FakeExecutor(outcomes=[truncated()]), pol)
        assert pol.budget.tokens <= 5.1

    def test_the_message_names_the_model_and_the_knob(self):
        """An operator reading this should not have to find the cap themselves."""
        base = FakeExecutor(model="openai/gpt-5.6-sol", outcomes=[truncated()])
        with pytest.raises(TruncatedCompletionError) as excinfo:
            drive(base, policy())
        message = str(excinfo.value)
        assert "openai/gpt-5.6-sol" in message
        assert "max_output_tokens" in message
        assert "config/sampling.toml" in message

    def test_a_normal_stop_is_untouched(self):
        base = FakeExecutor(outcomes=[response("ok", finish_reason="STOP")])
        assert texts(drive(base, policy())) == ["ok"]

    def test_an_absent_finish_reason_is_not_truncation(self):
        """Vendors that say nothing are the silent half, and graph.py's to catch."""
        base = FakeExecutor(outcomes=[response("ok")])
        assert texts(drive(base, policy())) == ["ok"]

    def test_a_truncation_anywhere_in_the_sequence_counts(self):
        """One call yields one response today; the collection is still a sequence.

        Answered at the seam, which is where a call that produced more than one
        result would arrive — the executor's contract is a sequence, and the
        refusal has to scan all of it rather than the last entry.
        """

        class TwoParts:
            async def generate(self, request):
                return [
                    GenerationResult.of(response("first", finish_reason="STOP")),
                    GenerationResult.of(truncated("second")),
                ]

        adapter = ExecutedLlm(
            model="fake/model", executor=TwoParts(), retry_policy=policy()
        )
        with pytest.raises(TruncatedCompletionError):
            asyncio.run(_drain(adapter))


class TestTheFinishReasonContract:
    """The one assumption the fake cannot carry: what ADK actually puts there.

    Every test above scripts the string ``reject_truncated`` matches on, which
    proves the check works and nothing about whether it will ever fire. These
    two probe the installed library instead — the same reason
    ``test_model_gate.py`` probes litellm rather than mirroring its behaviour.
    A version that changes either mapping shows up here as a failing test
    rather than as a truncation that sails through in production.
    """

    def test_adk_maps_a_length_stop_onto_the_string_we_match(self):
        from google.adk.models.lite_llm import _map_finish_reason

        assert _map_finish_reason("length") == "MAX_TOKENS"

    def test_a_normal_stop_maps_somewhere_else(self):
        from google.adk.models.lite_llm import _map_finish_reason

        assert _map_finish_reason("stop") != "MAX_TOKENS"


async def _collect(agen) -> list:
    return [item async for item in agen]


async def _drain(adapter) -> None:
    async for _ in adapter.generate_content_async(_ANY_REQUEST):
        pass


class TestTheStormItself:
    """The property the whole change is for.

    Six category agents hitting a provider that is refusing everything is the shape
    that used to become thirty requests. What bounds it is that the budget is
    *shared*: a per-node allowance gives every node its full count regardless
    of what the others are seeing, which is exactly the wrong response to a
    failure that is by definition correlated.
    """

    @staticmethod
    def fan_out(pol: RetryPolicy, lanes: int = 6) -> int:
        executors = [FakeExecutor(outcomes=[rate_limited()] * 10) for _ in range(lanes)]
        adapters = [
            ExecutedLlm(model="fake/model", executor=one, retry_policy=pol)
            for one in executors
        ]

        async def scenario():
            async def drain(adapter):
                async for _ in adapter.generate_content_async(_ANY_REQUEST):
                    pass

            await asyncio.gather(*(drain(a) for a in adapters), return_exceptions=True)

        asyncio.run(scenario())
        return sum(one.calls for one in executors)

    def test_a_correlated_outage_costs_far_less_than_every_lane_retrying(self):
        # Six lanes x three attempts is eighteen requests if each lane keeps its
        # own allowance — and thirty under the amplification this replaced. The
        # shared budget caps the whole fan-out near one round plus what the cold
        # bucket allows.
        requests = self.fan_out(policy(attempts=3, capacity=4), lanes=6)
        assert requests <= 6 + 4

    def test_an_isolated_failure_in_a_healthy_process_is_still_retried(self):
        # The budget must not make the service brittle: with capacity to spare,
        # one unlucky lane retries exactly as it always did.
        pol = policy(attempts=3, capacity=10)
        base = FakeExecutor(outcomes=[rate_limited(), "ok"])
        assert texts(drive(base, pol)) == ["ok"]
        assert base.calls == 2

    def test_the_budget_is_shared_across_lanes_not_per_lane(self):
        """One bucket for the process: a second fan-out finds it already spent."""
        pol = policy(attempts=3, capacity=4)
        first = self.fan_out(pol, lanes=6)
        second = self.fan_out(pol, lanes=6)
        assert second < first
        assert pol.budget.tokens < 1.0
