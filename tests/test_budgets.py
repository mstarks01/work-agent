"""Per-subject and global consumption budgets over a rolling window (#503)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from analysis_service.budgets import (
    BudgetPolicy,
    estimate,
    llm_calls_for,
    measured_tokens,
    spent_tokens,
)
from analysis_service.frameworks import PACKAGES
from analysis_service.jobs import Admission, InMemoryJobStore, JobRecord
from analysis_service.report import FrameworkSelection, NodeRun, Report, TokenUsage
from analysis_service.sources import Source
from tests.factories import sample_report, sample_selection

POLICY = BudgetPolicy(
    window_seconds=3600,
    max_jobs_per_window=3,
    max_tokens_per_window=1000,
    global_max_tokens_per_window=2000,
)


def record(subject: str = "alice", reserved: int = 0) -> JobRecord:
    return JobRecord.create(
        owner_subject=subject,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        reserved_tokens=reserved,
    )


def reserve(store, rec, *, ceiling: int = 100, budget: BudgetPolicy = POLICY):
    return asyncio.run(store.reserve(rec, ceiling=ceiling, budget=budget))


def metered_node(total: int) -> NodeRun:
    """One node execution the provider metered at ``total`` tokens."""
    return NodeRun(
        node="extract",
        duration_ms=10,
        usage=TokenUsage(prompt_tokens=total, total_tokens=total),
    )


def report_measuring(total: int) -> Report:
    """A complete report whose node runs measure ``total`` tokens between them."""
    return sample_report().model_copy(update={"nodes": [metered_node(total)]})


class TestTheEstimate:
    def test_it_counts_every_call_the_selection_implies(self):
        # Derived from PACKAGES, so a framework registered tomorrow moves it
        # with no edit here -- the reason widest_fan_out() exists.
        one = [FrameworkSelection(name="stride", options={})]
        expected = 2 + len(PACKAGES["stride"].lanes) + 2
        assert llm_calls_for(one) == expected

    def test_naming_two_frameworks_costs_both(self):
        one = [FrameworkSelection(name="stride", options={})]
        both = [
            FrameworkSelection(name="stride", options={}),
            FrameworkSelection(name="asvs", options={}),
        ]
        assert llm_calls_for(both) > llm_calls_for(one)

    def test_a_longer_submission_reserves_more(self):
        selection = [FrameworkSelection(name="stride", options={})]
        small = [Source.description("one two three")]
        large = [Source.description(" ".join(["word"] * 500))]
        assert estimate(large, selection) > estimate(small, selection)

    def test_it_over_counts_rather_than_under(self):
        # A bound that must hold before anything is spent has to err upward: the
        # alternative is a gate that admits a job it cannot afford and finds out
        # at node nine.
        selection = [FrameworkSelection(name="stride", options={})]
        sources = [Source.description(" ".join(["word"] * 100))]
        submitted = estimate(sources, selection) / llm_calls_for(selection)
        assert estimate(sources, selection) > submitted


class TestReconciliation:
    def test_a_live_job_charges_its_reservation(self):
        assert spent_tokens([(500, None)]) == 500

    def test_a_finished_job_charges_what_it_measured(self):
        assert spent_tokens([(500, 120)]) == 120

    def test_a_measurement_may_exceed_its_reservation(self):
        # The estimate is not a cap on the run, only on admission. A job that
        # cost more than expected charges what it cost.
        assert spent_tokens([(100, 900)]) == 900

    def test_nothing_accumulates_so_nothing_double_counts(self):
        # The window total is a scan over records, so re-reading it cannot add a
        # job twice and cannot fall below zero however a job ended.
        charges = [(500, None), (500, 0), (200, 50)]
        assert spent_tokens(charges) == spent_tokens(charges) == 550

    def test_an_unmetered_run_settles_to_zero_not_to_its_reservation(self):
        # Leaving the reservation standing would charge a caller forever for a
        # job whose cost nobody knows, since a terminal job never reports again.
        assert measured_tokens([None, None]) == 0

    def test_a_terminal_transition_settles_what_the_run_measured(self):
        rec = record(reserved=900)
        assert rec.measured_tokens is None
        rec.transition("running")
        assert rec.measured_tokens is None, "a running job still holds its reservation"
        rec.unreported_nodes = [metered_node(120)]
        rec.transition("rejected")
        assert rec.measured_tokens == 120

    @pytest.mark.parametrize("terminal", ["completed", "rejected"])
    def test_every_measured_terminal_state_settles(self, terminal):
        # transition() is the single funnel, so no path that has a measurement
        # can forget to apply it.
        rec = record(reserved=900)
        rec.transition("running")
        rec.unreported_nodes = [metered_node(70)]
        if terminal == "completed":
            rec.report = report_measuring(70)
        rec.transition(terminal)
        assert rec.measured_tokens == 70

    def test_a_job_nothing_measured_keeps_its_reservation(self):
        # The gap this closes. A job killed on the deadline has already paid for
        # every node it reached, so settling it to zero would free the whole
        # estimate and make the token budget a bound any failing job clears.
        rec = record(reserved=900)
        rec.transition("running")
        rec.transition("failed")
        assert rec.measured_tokens is None
        assert spent_tokens([(rec.reserved_tokens, rec.measured_tokens)]) == 900

    def test_serial_failing_jobs_cannot_outspend_the_token_budget(self):
        # The attack the reservation stands against: a caller whose submissions
        # outrun the deadline, each freeing its estimate on the way out.
        store = InMemoryJobStore()
        budget = POLICY.model_copy(update={"max_jobs_per_window": 100})
        outcomes = []
        for _ in range(4):
            rec = record(reserved=400)
            outcome = reserve(store, rec, budget=budget).outcome
            outcomes.append(outcome)
            if outcome != "admitted":
                continue
            rec.transition("running")
            rec.transition("failed")
            asyncio.run(store.save(rec))
        assert outcomes == [
            "admitted",
            "admitted",
            "over_subject_budget",
            "over_subject_budget",
        ]

    def test_a_rejected_job_still_pays_for_the_nodes_that_ran(self):
        # Extraction and repair ran before the validity gate refused their
        # output. A rejection is the submitter's fault and still costs tokens.
        rec = record(reserved=900)
        rec.transition("running")
        rec.unreported_nodes = [metered_node(40), metered_node(35)]
        rec.transition("rejected")
        assert rec.measured_tokens == 75


class TestTheRateBound:
    def test_a_burst_of_finished_jobs_still_hits_the_rate(self):
        # What the concurrency ceiling cannot do. Each of these finished before
        # the next was sent, so every one of them cleared a ceiling of 1.
        store = InMemoryJobStore()
        for _ in range(POLICY.max_jobs_per_window):
            rec = record()
            reserve(store, rec, ceiling=1)
            rec.transition("running")
            rec.transition("completed")
            asyncio.run(store.save(rec))
        assert reserve(store, record(), ceiling=1).outcome == "over_rate"

    def test_the_rate_is_per_subject(self):
        store = InMemoryJobStore()
        for _ in range(POLICY.max_jobs_per_window):
            reserve(store, record("alice"))
        assert reserve(store, record("bob")).outcome == "admitted"

    def test_a_job_outside_the_window_does_not_count(self):
        store = InMemoryJobStore()
        for _ in range(POLICY.max_jobs_per_window):
            rec = record()
            reserve(store, rec)
            # Backdate past the window, as the clock would have.
            stale = rec.model_copy(deep=True)
            stale.created_at = datetime.now(UTC) - timedelta(seconds=7200)
            asyncio.run(store.save(stale))
        assert reserve(store, record()).outcome == "admitted"


class TestTheTokenBounds:
    def test_one_large_job_can_exhaust_a_budget_a_count_would_admit(self):
        # The bound a job count cannot express: this is the first submission,
        # so the rate is nowhere near its limit.
        store = InMemoryJobStore()
        assert (
            reserve(store, record(reserved=POLICY.max_tokens_per_window + 1)).outcome
            == "over_subject_budget"
        )

    def test_the_reservation_of_the_job_being_admitted_counts(self):
        # Checking only what is already committed would admit a job that takes
        # the window over on its own.
        store = InMemoryJobStore()
        reserve(store, record(reserved=600))
        assert reserve(store, record(reserved=600)).outcome == "over_subject_budget"

    def test_settling_below_the_estimate_frees_the_difference(self):
        # Reconciliation is what stops a coarse estimate from being a hard cap:
        # a job that reserved 900 and cost 10 leaves the window nearly empty.
        store = InMemoryJobStore()
        rec = record(reserved=900)
        reserve(store, rec)
        rec.transition("running")
        rec.transition("completed")
        rec.measured_tokens = 10
        asyncio.run(store.save(rec))
        assert reserve(store, record(reserved=900)).outcome == "admitted"

    def test_the_subject_budget_is_per_subject(self):
        store = InMemoryJobStore()
        reserve(store, record("alice", reserved=900))
        assert reserve(store, record("bob", reserved=900)).outcome == "admitted"

    def test_the_global_budget_holds_when_the_per_subject_ones_do_not(self):
        # Three subjects each well inside their own allowance, together past the
        # deployment's. This is the guardrail a per-subject bound cannot be.
        store = InMemoryJobStore()
        for subject in ("alice", "bob", "carol"):
            outcome = reserve(store, record(subject, reserved=800))
        assert outcome.outcome == "over_global_budget"

    def test_a_global_refusal_reports_the_callers_own_figure(self):
        # `subject_tokens` is the caller's; `global_tokens` is the operator's.
        # A caller learning the deployment's load learns about other callers.
        store = InMemoryJobStore()
        reserve(store, record("alice", reserved=800))
        reserve(store, record("bob", reserved=800))
        outcome = reserve(store, record("carol", reserved=800))
        assert outcome.outcome == "over_global_budget"
        assert outcome.subject_tokens == 0
        assert outcome.global_tokens == 1600


class TestOrderingAndAtomicity:
    def test_the_soonest_clearing_bound_is_the_one_reported(self):
        # A submission over several bounds hears about the ceiling first,
        # because that is the one a job of theirs finishing will clear.
        store = InMemoryJobStore()
        reserve(store, record(reserved=900))
        assert reserve(store, record(reserved=900), ceiling=1).outcome == "at_ceiling"

    def test_a_refusal_writes_nothing(self):
        store = InMemoryJobStore()
        refused = record(reserved=POLICY.max_tokens_per_window + 1)
        reserve(store, refused)
        assert asyncio.run(store.get(refused.id)) is None

    def test_simultaneous_reservations_never_exceed_the_token_budget(self):
        # The same barrier the ceiling is tested at, on the budget: a check
        # separated from the insert would let every task read an empty window.
        async def scenario():
            store = InMemoryJobStore()
            start = asyncio.Event()

            async def contend():
                await start.wait()
                return await store.reserve(
                    record(reserved=400), ceiling=100, budget=POLICY
                )

            racers = [asyncio.create_task(contend()) for _ in range(20)]
            start.set()
            outcomes = await asyncio.gather(*racers)
            return sum(1 for o in outcomes if o.outcome == "admitted")

        # 1000 // 400 == 2, and the third would take the window past it.
        assert asyncio.run(scenario()) == 2

    def test_a_duplicate_is_settled_before_every_bound(self):
        # A record the store already holds is inside the budgets it charged.
        store = InMemoryJobStore()
        rec = record(reserved=900)
        reserve(store, rec)
        assert reserve(store, rec).outcome == "duplicate"


def test_the_admission_result_carries_no_other_subjects_activity():
    """Every field on `Admission` but one describes the caller.

    `global_tokens` is the exception, is the only one, and is what the API
    withholds from the response and puts in the operator's log instead.
    """
    fields = set(Admission.__dataclass_fields__)
    assert fields == {"outcome", "active", "subject_tokens", "global_tokens"}


def test_a_report_settles_to_what_its_nodes_measured():
    from analysis_service.report import NodeRun

    usages = [
        TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        TokenUsage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
        None,
    ]
    runs = [NodeRun(node=f"n{i}", duration_ms=1, usage=u) for i, u in enumerate(usages)]
    assert measured_tokens(run.usage for run in runs) == 40
