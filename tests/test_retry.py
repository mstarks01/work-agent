"""The retry loop and the shared budget that keeps it from becoming a storm.

Driven against a fake base class rather than ``LiteLlm``: ``retrying_llm_class``
takes the class it wraps, so the loop is testable without a provider library,
credentials or a request. The litellm exception *types* are real, because the
classification is the one part that genuinely depends on them.
"""

from __future__ import annotations

import asyncio

import pytest
from litellm import APIConnectionError, RateLimitError

from stride_service.retry import (
    RetryBudget,
    RetryBudgetExhausted,
    RetryPolicy,
    _backoff_seconds,
    _retry_after_seconds,
    retrying_llm_class,
)


def rate_limited(**headers) -> RateLimitError:
    return RateLimitError(
        message="slow down",
        llm_provider="openai",
        model="gpt-4o",
        headers=headers or None,
    )


class FakeLlm:
    """A stand-in provider adapter: a scripted sequence of outcomes per call.

    ``outcomes`` is consumed one entry per call — an exception to raise, or a
    string to yield as the response. It counts calls, which is the number every
    claim in this module is really about.
    """

    def __init__(self, model: str = "fake/model", outcomes=(), **_kwargs) -> None:
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate_content_async(self, llm_request, stream: bool = False):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        yield outcome


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


def drive(base: FakeLlm, pol: RetryPolicy, stream: bool = False) -> list:
    """Run one call through the retrying adapter, reflecting the count back.

    The count is copied in a ``finally`` because the interesting cases are the
    ones that raise, and a request count only observable on the happy path would
    assert nothing about them.
    """
    cls = retrying_llm_class(FakeLlm, pol)
    adapter = cls(model=base.model, outcomes=base.outcomes)

    async def scenario():
        return [r async for r in adapter.generate_content_async(None, stream)]

    try:
        return asyncio.run(scenario())
    finally:
        base.calls = adapter.calls


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
        assert _retry_after_seconds(rate_limited(**{"retry-after": "Wed, 21 Oct"})) is None

    def test_no_headers_at_all_is_no_hint(self):
        assert _retry_after_seconds(rate_limited()) is None
        assert _retry_after_seconds(ValueError("nothing")) is None

    def test_the_provider_beats_the_computed_curve(self):
        pol = policy()
        slept: list[float] = []

        async def scenario():
            await pol.sleep_before_retry(1, rate_limited(**{"retry-after": "7"}))

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

        That is what actually decorrelates six analysts that failed at the same
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


class TestShouldRetry:
    def test_a_transient_failure_earns_another_try(self):
        assert policy().should_retry(1, rate_limited())

    def test_a_non_transient_failure_does_not(self):
        assert not policy().should_retry(1, ValueError("malformed request"))

    def test_a_non_transient_failure_spends_no_budget(self):
        # Order matters: a token spent on a failure that will never benefit
        # from a retry is a token the next real outage cannot draw on.
        pol = policy()
        pol.should_retry(1, ValueError("malformed request"))
        assert pol.budget.tokens == 10

    def test_the_last_attempt_does_not_retry(self):
        assert not policy(attempts=3).should_retry(3, rate_limited())

    def test_an_exhausted_budget_stops_retrying_a_transient_failure(self):
        pol = policy(capacity=1)
        assert pol.should_retry(1, rate_limited())
        assert not pol.should_retry(1, rate_limited())


class TestRetryingAdapter:
    def test_a_clean_call_makes_one_request(self):
        base = FakeLlm(outcomes=["ok"])
        assert drive(base, policy()) == ["ok"]
        assert base.calls == 1

    def test_a_transient_failure_is_retried_and_the_answer_survives(self):
        base = FakeLlm(outcomes=[rate_limited(), "ok"])
        assert drive(base, policy()) == ["ok"]
        assert base.calls == 2

    def test_success_credits_the_budget(self):
        pol = policy()
        pol.budget.tokens = 5
        drive(FakeLlm(outcomes=["ok"]), pol)
        assert pol.budget.tokens == pytest.approx(5.1)

    def test_a_non_transient_failure_fails_on_the_first_request(self):
        base = FakeLlm(outcomes=[ValueError("malformed"), "ok"])
        with pytest.raises(ValueError, match="malformed"):
            drive(base, policy())
        assert base.calls == 1

    def test_attempts_is_the_request_count_per_node(self):
        """The claim ``attempts`` could not make while LiteLLM retried beneath."""
        base = FakeLlm(outcomes=[rate_limited()] * 5)
        with pytest.raises(RateLimitError):
            drive(base, policy(attempts=3))
        assert base.calls == 3

    def test_an_exhausted_budget_fails_fast_and_says_which_it_was(self):
        base = FakeLlm(outcomes=[rate_limited()] * 5)
        with pytest.raises(RetryBudgetExhausted):
            drive(base, policy(attempts=3, capacity=0.5))
        assert base.calls == 1

    def test_a_partial_generator_never_reaches_the_caller(self):
        """Buffering is what makes the retry safe rather than duplicating."""
        base = FakeLlm(outcomes=[APIConnectionError("dropped", "openai", "m"), "ok"])
        assert drive(base, policy()) == ["ok"]

    def test_a_streaming_call_is_passed_straight_through(self):
        # A replayed half-stream would be worse than no retry at all.
        base = FakeLlm(outcomes=[rate_limited(), "ok"])
        with pytest.raises(RateLimitError):
            drive(base, policy(), stream=True)
        assert base.calls == 1


class TestTheStormItself:
    """The property the whole change is for.

    Six analysts hitting a provider that is refusing everything is the shape
    that used to become thirty requests. What bounds it is that the budget is
    *shared*: a per-node allowance gives every node its full count regardless
    of what the others are seeing, which is exactly the wrong response to a
    failure that is by definition correlated.
    """

    @staticmethod
    def fan_out(pol: RetryPolicy, lanes: int = 6) -> int:
        cls = retrying_llm_class(FakeLlm, pol)
        adapters = [
            cls(model="fake/model", outcomes=[rate_limited()] * 10)
            for _ in range(lanes)
        ]

        async def scenario():
            async def one(adapter):
                async for _ in adapter.generate_content_async(None):
                    pass

            await asyncio.gather(
                *(one(a) for a in adapters), return_exceptions=True
            )

        asyncio.run(scenario())
        return sum(adapter.calls for adapter in adapters)

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
        base = FakeLlm(outcomes=[rate_limited(), "ok"])
        assert drive(base, pol) == ["ok"]
        assert base.calls == 2

    def test_the_budget_is_shared_across_lanes_not_per_lane(self):
        """One bucket for the process: a second fan-out finds it already spent."""
        pol = policy(attempts=3, capacity=4)
        first = self.fan_out(pol, lanes=6)
        second = self.fan_out(pol, lanes=6)
        assert second < first
        assert pol.budget.tokens < 1.0
