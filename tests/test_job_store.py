"""The ``JobStore`` contract: every backend pair answers these the same way.

Each test gets its store from :func:`new_store`, which takes one parameter for
each backend pair. A new backend adds its parameter there and proves the same
behaviour as ``memory``. No test here reads a private attribute of a store.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from analysis_service.answer_sets import NO_ANSWERS, AnswerSet
from analysis_service.budgets import BudgetPolicy
from analysis_service.fact_answers import FactAnswer
from analysis_service.jobs import (
    Admission,
    Checkpoint,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    Resumption,
)
from analysis_service.sources import Source
from tests import test_resume
from tests.factories import (
    SEEDING_BUDGET,
    admit,
    sample_report,
    sample_selection,
    valid_model,
)
from tests.test_pause import waiting

POLICY = BudgetPolicy(
    window_seconds=3600,
    max_jobs_per_window=3,
    max_tokens_per_window=1000,
    global_max_tokens_per_window=2000,
)

#: A fact key the store keeps without reading it.
KEY = ("df-1", "encryption_in_transit", "", "", "", "")


@pytest.fixture(params=["memory"])
def new_store(request):
    """A maker of fresh stores of one backend pair."""

    def make():
        return InMemoryJobStore()

    return make


@pytest.fixture
def store(new_store):
    return new_store()


def new_record(subject: str = "alice", reserved: int = 0) -> JobRecord:
    return JobRecord.create(
        owner_subject=subject,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        reserved_tokens=reserved,
    )


def reserve(store, rec, *, ceiling: int = 100, budget: BudgetPolicy = POLICY):
    return asyncio.run(store.reserve(rec, ceiling=ceiling, budget=budget))


def completed(store, subject="alice", **create) -> str:
    """The ID of a completed job with a report, admitted to ``store``."""
    record = JobRecord.create(
        owner_subject=subject,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        **create,
    )
    record.transition("running")
    record.report = sample_report()
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id


def follow_up(parent: str) -> JobRecord:
    """A report's follow-up of ``parent``, still queued."""
    return JobRecord.create(
        owner_subject="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        resumption=Resumption(
            parent_id=parent,
            checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
            follow_up=True,
        ),
        parent_revision=0,
    )


def final(store, parent: str) -> str:
    """The ID of a completed follow-up of ``parent``: a final report."""
    return completed(store, resumption=follow_up(parent).resumption, parent_revision=0)


def amendment(parent: str, revision: int = 0) -> JobRecord:
    """A job an amendment of ``parent`` started, still queued."""
    return JobRecord.create(
        owner_subject="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        amends=parent,
        parent_revision=revision,
        ask_questions=True,
    )


class TestReadsAndSaves:
    def test_create_get_roundtrip(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_get_unknown_returns_none(self, store):
        assert asyncio.run(store.get("job-missing")) is None

    def test_reserve_get_roundtrip_keeps_the_record_queued(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_store_round_trips_a_resumed_job(self, store):
        record = test_resume.TestTheResumedRun().resumed_job()
        asyncio.run(admit(store, record))
        assert asyncio.run(store.get(record.id)).resumption == record.resumption

    def test_owned_answers_none_for_a_foreign_job(self, store):
        """A foreign job and a missing one are the same answer, so a job id
        cannot be probed for existence."""

        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.owned(record.id, "mallory")

        assert asyncio.run(scenario()) is None

    def test_owned_leaves_the_report_behind(self, store):
        """The status and event routes read the envelope and never the
        analysis. Copying the report for them is the whole cost of a read, and
        a read carries none of the bounds admission does."""

        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            record.report = sample_report()
            record.transition("completed")
            await store.save(record)
            return await store.owned(record.id, "alice"), await store.get(record.id)

        trimmed, whole = asyncio.run(scenario())
        assert trimmed is not None and whole is not None
        assert trimmed.status == "completed"
        assert trimmed.report is None
        assert whole.report is not None, "get still answers with the whole record"

    def test_owned_leaves_the_checkpoint_and_the_resumption_behind(self, store):
        """A waiting job's checkpoint and a resumed job's parent checkpoint are
        each a whole model and catalog, and the envelope reads neither (run 11,
        finding 2). The one route that needs a checkpoint asks for it by name."""

        async def scenario():
            held = Checkpoint(system_model=valid_model(), assertions=None)
            record = new_record()
            record.resumption = Resumption(
                follow_up=False, parent_id="parent", checkpoint=held
            )
            await admit(store, record)
            record.transition("running")
            record.checkpoint = held
            record.transition("awaiting-answers")
            await store.save(record)
            return (
                await store.owned(record.id, "alice"),
                await store.checkpoint(record.id, "alice"),
                await store.checkpoint(record.id, "mallory"),
                await store.resumption(record.id, "alice"),
                await store.resumption(record.id, "mallory"),
            )

        envelope, asked, foreign, resumption, foreign_resumption = asyncio.run(
            scenario()
        )
        assert envelope is not None
        assert envelope.status == "awaiting-answers"
        assert envelope.checkpoint is None
        assert envelope.resumption is None
        assert asked is not None and asked.system_model == valid_model()
        assert foreign is None
        assert resumption is not None and resumption.parent_id == "parent"
        assert foreign_resumption is None

    def test_report_is_one_copy_and_rechecks_ownership(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            record.report = sample_report()
            record.transition("completed")
            await store.save(record)
            mine = await store.report(record.id, "alice")
            mine.system_model.external_entities.clear()
            return (
                mine,
                await store.report(record.id, "alice"),
                await store.report(record.id, "mallory"),
            )

        mutated, again, foreign = asyncio.run(scenario())
        assert again == sample_report(), "the caller's copy aliases nothing stored"
        assert mutated != again
        assert foreign is None

    def test_report_json_serializes_without_a_second_copy(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            record.report = sample_report()
            record.transition("completed")
            await store.save(record)
            return (
                await store.report_json(record.id, "alice"),
                await store.report_json(record.id, "mallory"),
            )

        mine, foreign = asyncio.run(scenario())
        assert isinstance(mine, dict) and mine
        assert foreign is None, "ownership is re-checked, not assumed of the caller"

    def test_report_json_answers_none_when_no_report_is_attached(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.report_json(record.id, "alice")

        assert asyncio.run(scenario()) is None

    def test_events_after_answers_the_unseen_events_as_copies(self, store):
        record = new_record()
        asyncio.run(admit(store, record))
        record.transition("running")
        asyncio.run(store.save(record))
        status, events = asyncio.run(store.events_after(record.id, 1))
        assert status == "running"
        assert [event.seq for event in events] == [2]
        events.clear()
        assert len(asyncio.run(store.events_after(record.id, 0))[1]) == 2
        assert asyncio.run(store.events_after("job-missing", 0)) is None

    def test_save_requires_existing_record(self, store):
        with pytest.raises(ValueError, match="does not exist"):
            asyncio.run(store.save(new_record()))

    def test_mutations_invisible_until_saved(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            before_save = await store.get(record.id)
            await store.save(record)
            after_save = await store.get(record.id)
            return before_save.status, after_save.status

        assert asyncio.run(scenario()) == ("queued", "running")


class TestReserve:
    """The seam the per-caller concurrency ceiling is enforced through (#113).

    It lives on the store rather than beside the API so the ceiling is exactly
    as shared as the deployment's storage is: an in-process counter would be
    per-instance whatever backend was configured, so two instances would enforce
    double the configured number and reset it on every deploy.

    Counting and creating is **one** call (#505). There is no bare count to ask,
    so what these tests read is the count the reservation itself observed.
    """

    @pytest.fixture(autouse=True)
    def _stores(self, new_store):
        self.new_store = new_store

    def counts(self, statuses: list[JobStatus], subject: str = "alice") -> int:
        """How many jobs a fresh reservation for ``subject`` sees in flight."""

        async def scenario():
            store = self.new_store()
            for status in statuses:
                record = new_record()
                await admit(store, record)
                if status != "queued":
                    record.transition("running")
                if status not in ("queued", "running"):
                    record.transition(status)
                await store.save(record)
            probe = JobRecord.create(
                owner_subject=subject,
                sources=[Source.description("an app")],
                frameworks=sample_selection(),
            )
            return (await admit(store, probe)).active

        return asyncio.run(scenario())

    def test_an_empty_store_holds_nothing_in_flight(self):
        assert self.counts([]) == 0

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_a_job_before_a_terminal_state_is_in_flight(self, status):
        assert self.counts([status]) == 1

    @pytest.mark.parametrize("status", ["completed", "failed", "rejected"])
    def test_a_terminal_job_is_not(self, status):
        # Every terminal state releases the slot, not just the happy one: a
        # failed run holds no provider capacity, and counting it would let a
        # burst of failures lock a caller out until the process restarted.
        assert self.counts([status]) == 0

    def test_only_the_named_subject_is_counted(self, store):
        async def scenario():
            seen = {}
            for owner in ("alice", "alice", "bob", "alice", "bob"):
                record = JobRecord.create(
                    owner_subject=owner,
                    sources=[Source.description("an app")],
                    frameworks=sample_selection(),
                )
                seen[owner] = (await admit(store, record)).active
            return seen["alice"], seen["bob"]

        # alice's third reservation saw her first two; bob's second saw only his
        # first. One store, two budgets.
        assert asyncio.run(scenario()) == (2, 1)

    def test_an_unknown_subject_holds_nothing(self):
        assert self.counts(["queued"], subject="carol") == 0

    def test_the_count_follows_the_records_rather_than_a_counter(self):
        # The records are the truth. A maintained counter is a second copy that
        # drifts the moment one path that ends a job forgets to decrement it.
        assert self.counts(["running"]) == 1
        assert self.counts(["completed"]) == 0

    def test_a_reservation_at_the_ceiling_admits_nothing(self, store):
        async def scenario():
            await admit(store, new_record())
            refused = new_record()
            outcome = await store.reserve(refused, ceiling=1, budget=SEEDING_BUDGET)
            return outcome, await store.get(refused.id)

        outcome, stored = asyncio.run(scenario())
        assert outcome == Admission(outcome="at_ceiling", active=1)
        # A refusal that still wrote the record would be a queue with extra
        # steps: the caller's place in the provider quota would be held anyway.
        assert stored is None

    def test_a_reservation_below_the_ceiling_is_admitted(self, store):
        async def scenario():
            await admit(store, new_record())
            record = new_record()
            return await store.reserve(
                record, ceiling=2, budget=SEEDING_BUDGET
            ), await store.get(record.id)

        outcome, stored = asyncio.run(scenario())
        assert outcome == Admission(outcome="admitted", active=1)
        assert stored is not None

    def test_a_ceiling_another_subject_fills_does_not_refuse_this_one(self, store):
        async def scenario():
            for _ in range(3):
                await admit(store, new_record())
            bob = JobRecord.create(
                owner_subject="bob",
                sources=[Source.description("an app")],
                frameworks=sample_selection(),
            )
            return await store.reserve(bob, ceiling=1, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "admitted"

    def test_a_duplicate_id_is_named_rather_than_raised(self, store):
        # The API mints the id, so this is a defect report and not a caller
        # error; it is distinct from at_ceiling so the API can answer 500
        # rather than 429 for it.
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.reserve(record, ceiling=2, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "duplicate"

    def test_a_duplicate_is_settled_before_the_ceiling(self, store):
        # A record the store already holds is inside the budget already.
        # Counting it against the ceiling as well would report at_ceiling for
        # what is really a collision, and send a defect out as a 429.
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.reserve(record, ceiling=1, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "duplicate"

    def test_simultaneous_reservations_never_exceed_the_ceiling(self, store):
        # The check-then-act race, run at a barrier: every task is released at
        # the same moment and they contend for one slot. Two calls -- a count
        # then a create -- would let all of them read 0 before any wrote.
        async def scenario():
            start = asyncio.Event()

            async def contend():
                await start.wait()
                return await store.reserve(
                    new_record(), ceiling=2, budget=SEEDING_BUDGET
                )

            racers = [asyncio.create_task(contend()) for _ in range(20)]
            start.set()
            outcomes = await asyncio.gather(*racers)
            return sum(1 for outcome in outcomes if outcome.outcome == "admitted")

        assert asyncio.run(scenario()) == 2

    def test_the_store_holds_exactly_what_it_admitted(self, store):
        # The count and the records cannot disagree: an admitted reservation is
        # a written record, and a refused one is nothing at all.
        async def scenario():
            async def contend():
                record = new_record()
                outcome = await store.reserve(record, ceiling=3, budget=SEEDING_BUDGET)
                return record.id, outcome.outcome

            results = await asyncio.gather(
                *(asyncio.create_task(contend()) for _ in range(20))
            )
            written = [
                job_id for job_id, _ in results if await store.get(job_id) is not None
            ]
            admitted = [job_id for job_id, outcome in results if outcome == "admitted"]
            return sorted(written), sorted(admitted)

        written, admitted = asyncio.run(scenario())
        assert written == admitted
        assert len(admitted) == 3


class TestTheRateBound:
    def test_a_burst_of_finished_jobs_still_hits_the_rate(self, store):
        # What the concurrency ceiling cannot do. Each of these finished before
        # the next was sent, so every one of them cleared a ceiling of 1.
        for _ in range(POLICY.max_jobs_per_window):
            rec = new_record()
            reserve(store, rec, ceiling=1)
            rec.transition("running")
            rec.transition("completed")
            asyncio.run(store.save(rec))
        assert reserve(store, new_record(), ceiling=1).outcome == "over_rate"

    def test_the_rate_is_per_subject(self, store):
        for _ in range(POLICY.max_jobs_per_window):
            reserve(store, new_record("alice"))
        assert reserve(store, new_record("bob")).outcome == "admitted"

    def test_a_job_outside_the_window_does_not_count(self, store):
        for _ in range(POLICY.max_jobs_per_window):
            rec = new_record()
            reserve(store, rec)
            # Backdate past the window, as the clock would have.
            stale = rec.model_copy(deep=True)
            stale.created_at = datetime.now(UTC) - timedelta(seconds=7200)
            asyncio.run(store.save(stale))
        assert reserve(store, new_record()).outcome == "admitted"


class TestTheTokenBounds:
    def test_serial_failing_jobs_cannot_outspend_the_token_budget(self, store):
        # The attack the reservation stands against: a caller whose submissions
        # outrun the deadline, each freeing its estimate on the way out.
        budget = POLICY.model_copy(update={"max_jobs_per_window": 100})
        outcomes = []
        for _ in range(4):
            rec = new_record(reserved=400)
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

    def test_one_large_job_can_exhaust_a_budget_a_count_would_admit(self, store):
        # The bound a job count cannot express: this is the first submission,
        # so the rate is nowhere near its limit.
        assert (
            reserve(
                store, new_record(reserved=POLICY.max_tokens_per_window + 1)
            ).outcome
            == "over_subject_budget"
        )

    def test_the_reservation_of_the_job_being_admitted_counts(self, store):
        # Checking only what is already committed would admit a job that takes
        # the window over on its own.
        reserve(store, new_record(reserved=600))
        assert reserve(store, new_record(reserved=600)).outcome == "over_subject_budget"

    def test_settling_below_the_estimate_frees_the_difference(self, store):
        # Reconciliation is what stops a coarse estimate from being a hard cap:
        # a job that reserved 900 and cost 10 leaves the window nearly empty.
        rec = new_record(reserved=900)
        reserve(store, rec)
        rec.transition("running")
        rec.transition("completed")
        rec.measured_tokens = 10
        asyncio.run(store.save(rec))
        assert reserve(store, new_record(reserved=900)).outcome == "admitted"

    def test_the_subject_budget_is_per_subject(self, store):
        reserve(store, new_record("alice", reserved=900))
        assert reserve(store, new_record("bob", reserved=900)).outcome == "admitted"

    def test_the_global_budget_holds_when_the_per_subject_ones_do_not(self, store):
        # Three subjects each well inside their own allowance, together past the
        # deployment's. This is the guardrail a per-subject bound cannot be.
        for subject in ("alice", "bob", "carol"):
            outcome = reserve(store, new_record(subject, reserved=800))
        assert outcome.outcome == "over_global_budget"

    def test_a_global_refusal_reports_the_callers_own_figure(self, store):
        # `subject_tokens` is the caller's; `global_tokens` is the operator's.
        # A caller learning the deployment's load learns about other callers.
        reserve(store, new_record("alice", reserved=800))
        reserve(store, new_record("bob", reserved=800))
        outcome = reserve(store, new_record("carol", reserved=800))
        assert outcome.outcome == "over_global_budget"
        assert outcome.subject_tokens == 0
        assert outcome.global_tokens == 1600


class TestOrderingAndAtomicity:
    def test_the_soonest_clearing_bound_is_the_one_reported(self, store):
        # A submission over several bounds hears about the ceiling first,
        # because that is the one a job of theirs finishing will clear.
        reserve(store, new_record(reserved=900))
        assert (
            reserve(store, new_record(reserved=900), ceiling=1).outcome == "at_ceiling"
        )

    def test_a_refusal_writes_nothing(self, store):
        refused = new_record(reserved=POLICY.max_tokens_per_window + 1)
        reserve(store, refused)
        assert asyncio.run(store.get(refused.id)) is None

    def test_simultaneous_reservations_never_exceed_the_token_budget(self, store):
        # The same barrier the ceiling is tested at, on the budget: a check
        # separated from the insert would let every task read an empty window.
        async def scenario():
            start = asyncio.Event()

            async def contend():
                await start.wait()
                return await store.reserve(
                    new_record(reserved=400), ceiling=100, budget=POLICY
                )

            racers = [asyncio.create_task(contend()) for _ in range(20)]
            start.set()
            outcomes = await asyncio.gather(*racers)
            return sum(1 for o in outcomes if o.outcome == "admitted")

        # 1000 // 400 == 2, and the third would take the window past it.
        assert asyncio.run(scenario()) == 2

    def test_a_duplicate_is_settled_before_every_bound(self, store):
        # A record the store already holds is inside the budgets it charged.
        rec = new_record(reserved=900)
        reserve(store, rec)
        assert reserve(store, rec).outcome == "duplicate"


class TestTheParentLock:
    """A resumed job and an amendment's job each lock their parent (ADR 0072)."""

    @pytest.mark.parametrize("child", [follow_up, amendment])
    def test_a_child_in_flight_locks_its_parent(self, store, child):
        parent = waiting(store) if child is amendment else completed(store)
        locking = child(parent)
        asyncio.run(admit(store, locking))
        assert asyncio.run(store.resumed_by(parent, "alice")) == locking.id
        assert asyncio.run(store.resumed_by(parent, "mallory")) is None
        second = reserve(store, child(parent))
        assert second == Admission(outcome="resumed_already", active=0)

    @pytest.mark.parametrize("status", ["failed", "rejected"])
    def test_a_child_that_read_nothing_into_a_report_releases_its_parent(
        self, store, status
    ):
        parent = completed(store)
        failed = follow_up(parent)
        asyncio.run(admit(store, failed))
        failed.transition("running")
        failed.transition(status)
        asyncio.run(store.save(failed))
        assert asyncio.run(store.resumed_by(parent, "alice")) is None
        asyncio.run(admit(store, follow_up(parent)))

    def test_a_child_read_off_an_older_revision_is_refused(self, store):
        parent = waiting(store)
        assert asyncio.run(store.save_round(parent, "alice", NO_ANSWERS, [], [], 0))
        moved = reserve(store, amendment(parent, revision=0))
        assert moved == Admission(outcome="parent_moved", active=0)
        asyncio.run(admit(store, amendment(parent, revision=1)))


class TestTheSaves:
    def test_the_store_refuses_a_save_against_an_old_revision(self, store):
        job = waiting(store)
        record = asyncio.run(store.get(job))

        def save(revision):
            return asyncio.run(
                store.save_round(
                    job, record.owner_subject, NO_ANSWERS, [], [], revision
                )
            )

        assert save(0)
        assert not save(0)
        assert save(1)

    def test_a_saved_round_keeps_its_answers_and_its_checkpoint(self, store):
        job = waiting(store)
        given = AnswerSet(facts=(FactAnswer(key=KEY, value="unknown"),))
        assert asyncio.run(store.save_round(job, "alice", given, [KEY], [], 0))
        saved = asyncio.run(store.get(job))
        assert saved.round_revision == 1
        assert saved.answers == given
        assert saved.shown_early == [KEY]
        assert saved.checkpoint is not None

    def test_a_round_is_refused_on_a_foreign_finished_or_locked_job(self, store):
        job = waiting(store)
        assert not asyncio.run(store.save_round(job, "mallory", NO_ANSWERS, [], [], 0))
        done = completed(store)
        assert not asyncio.run(store.save_round(done, "alice", NO_ANSWERS, [], [], 0))
        asyncio.run(admit(store, amendment(job)))
        assert not asyncio.run(store.save_round(job, "alice", NO_ANSWERS, [], [], 0))
        assert asyncio.run(store.get(job)).round_revision == 0

    def test_a_draft_lands_once_for_each_revision(self, store):
        job = completed(store)
        draft = AnswerSet(facts=(FactAnswer(key=KEY, value="TLS 1.3"),))
        assert asyncio.run(store.save_draft(job, "alice", draft, 0))
        assert not asyncio.run(store.save_draft(job, "alice", NO_ANSWERS, 0))
        saved = asyncio.run(store.get(job))
        assert saved.round_revision == 1
        assert saved.draft == draft

    def test_a_draft_is_refused_on_a_foreign_final_or_locked_report(self, store):
        job = completed(store)
        assert not asyncio.run(store.save_draft(job, "mallory", NO_ANSWERS, 0))
        final_job = final(store, job)
        assert not asyncio.run(store.save_draft(final_job, "alice", NO_ANSWERS, 0))
        assert not asyncio.run(store.save_draft(job, "alice", NO_ANSWERS, 0))

    def test_a_correction_lands_only_on_a_final_report(self, store):
        corrections = [FactAnswer(key=KEY, value="TLS 1.2")]
        job = completed(store)
        assert not asyncio.run(store.save_corrections(job, "alice", corrections))
        final_job = final(store, job)
        assert not asyncio.run(
            store.save_corrections(final_job, "mallory", corrections)
        )
        assert asyncio.run(store.save_corrections(final_job, "alice", corrections))
        assert asyncio.run(store.get(final_job)).corrections == corrections


class TestADeletedJobLeavesAUsageRow:
    """ADR 0066: a delete removes the content and keeps what admission reads."""

    def test_no_read_serves_a_deleted_job(self, store):
        job = completed(store)
        assert asyncio.run(store.delete(job, "alice"))
        assert asyncio.run(store.get(job)) is None
        assert asyncio.run(store.owned(job, "alice")) is None
        assert asyncio.run(store.report(job, "alice")) is None
        assert asyncio.run(store.events_after(job, 0)) is None
        assert asyncio.run(store.owned_jobs("alice")) == []

    def test_only_the_owner_deletes_and_only_a_terminal_job(self, store):
        job = completed(store)
        queued = new_record()
        asyncio.run(admit(store, queued))
        assert not asyncio.run(store.delete(job, "mallory"))
        assert not asyncio.run(store.delete(queued.id, "alice"))
        assert asyncio.run(store.get(job)) is not None

    def test_a_deleted_job_still_counts_toward_the_rate(self, store):
        job = completed(store)
        asyncio.run(store.delete(job, "alice"))
        one_a_window = BudgetPolicy(
            window_seconds=3600,
            max_jobs_per_window=1,
            max_tokens_per_window=10**9,
            global_max_tokens_per_window=10**9,
        )
        admission = asyncio.run(
            store.reserve(new_record(), ceiling=100, budget=one_a_window)
        )
        assert admission.outcome == "over_rate"

    def test_a_deleted_follow_up_still_locks_its_parent(self, store):
        parent = completed(store)
        child = follow_up(parent)
        child.transition("running")
        child.report = sample_report()
        child.transition("completed")
        asyncio.run(admit(store, child))
        assert asyncio.run(store.delete(child.id, "alice"))
        assert asyncio.run(store.resumed_by(parent, "alice")) == child.id

    def test_the_listing_is_the_owner_s_jobs_oldest_first_as_envelopes(self, store):
        first = completed(store)
        second = completed(store)
        completed(store, subject="bob")
        listed = asyncio.run(store.owned_jobs("alice"))
        assert [record.id for record in listed] == [first, second]
        assert all(record.report is None for record in listed)
