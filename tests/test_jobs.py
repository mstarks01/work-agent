"""Job lifecycle, event log, in-memory store, and executor behavior."""

import asyncio

import pytest

from analysis_service.jobs import (
    DEADLINE_FAILURE_MESSAGE,
    GENERIC_FAILURE_MESSAGE,
    Admission,
    InMemoryJobStore,
    InvalidTransitionError,
    JobRecord,
    JobStatus,
    JobStoreConfigError,
    NodeCallback,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
    build_store,
    execute_job,
)
from analysis_service.report import InputRef
from analysis_service.sources import Source
from analysis_service.validation import ValidationIssue
from tests.factories import SEEDING_BUDGET, admit, sample_selection


def make_record() -> JobRecord:
    return JobRecord.create(
        owner_subject="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
    )


class RejectingRunner:
    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        await on_node("extract")
        return PipelineRejected(
            issues=[
                ValidationIssue(
                    code="no-trust-zones",
                    message="the model declares no Trust Boundary",
                )
            ]
        )


class FailingRunner:
    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        raise RuntimeError("db password hunter2 leaked in traceback")


class TestLifecycle:
    def test_new_job_is_queued_with_initial_status_event(self):
        record = make_record()
        assert record.status == "queued"
        assert [(e.kind, e.status) for e in record.events] == [("status", "queued")]

    def test_legal_path_to_completed(self):
        record = make_record()
        record.transition("running")
        record.transition("completed")
        assert [e.status for e in record.events if e.kind == "status"] == [
            "queued",
            "running",
            "completed",
        ]

    @pytest.mark.parametrize("terminal", ["completed", "failed", "rejected"])
    def test_running_reaches_each_terminal_state(self, terminal):
        record = make_record()
        record.transition("running")
        record.transition(terminal)
        assert record.status == terminal

    @pytest.mark.parametrize(
        ("start", "target"),
        [
            ("queued", "completed"),
            ("queued", "failed"),
            ("queued", "rejected"),
            ("running", "queued"),
            ("completed", "running"),
            ("failed", "queued"),
            ("rejected", "running"),
        ],
    )
    def test_illegal_transitions_are_refused(self, start, target):
        record = make_record()
        if start != "queued":
            record.transition("running")
            if start != "running":
                record.transition(start)
        with pytest.raises(InvalidTransitionError):
            record.transition(target)

    def test_events_have_dense_monotonic_seq(self):
        record = make_record()
        record.transition("running")
        record.record_node("extract")
        record.record_node("critic")
        record.transition("completed")
        assert [e.seq for e in record.events] == [1, 2, 3, 4, 5]

    def test_transition_bumps_updated_at(self):
        record = make_record()
        before = record.updated_at
        record.transition("running")
        assert record.updated_at >= before


class TestInMemoryJobStore:
    def test_create_get_roundtrip(self):
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await admit(store, record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_get_unknown_returns_none(self):
        store = InMemoryJobStore()
        assert asyncio.run(store.get("job-missing")) is None

    def test_reserve_get_roundtrip_keeps_the_record_queued(self):
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await admit(store, record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_save_requires_existing_record(self):
        with pytest.raises(ValueError, match="does not exist"):
            asyncio.run(InMemoryJobStore().save(make_record()))

    def test_mutations_invisible_until_saved(self):
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
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

    def counts(self, statuses: list[JobStatus], subject: str = "alice") -> int:
        """How many jobs a fresh reservation for ``subject`` sees in flight."""

        async def scenario():
            store = InMemoryJobStore()
            for status in statuses:
                record = make_record()
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

    def test_only_the_named_subject_is_counted(self):
        async def scenario():
            store = InMemoryJobStore()
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

    def test_a_reservation_at_the_ceiling_admits_nothing(self):
        async def scenario():
            store = InMemoryJobStore()
            await admit(store, make_record())
            refused = make_record()
            outcome = await store.reserve(refused, ceiling=1, budget=SEEDING_BUDGET)
            return outcome, await store.get(refused.id)

        outcome, stored = asyncio.run(scenario())
        assert outcome == Admission(outcome="at_ceiling", active=1)
        # A refusal that still wrote the record would be a queue with extra
        # steps: the caller's place in the provider quota would be held anyway.
        assert stored is None

    def test_a_reservation_below_the_ceiling_is_admitted(self):
        async def scenario():
            store = InMemoryJobStore()
            await admit(store, make_record())
            record = make_record()
            return await store.reserve(
                record, ceiling=2, budget=SEEDING_BUDGET
            ), await store.get(record.id)

        outcome, stored = asyncio.run(scenario())
        assert outcome == Admission(outcome="admitted", active=1)
        assert stored is not None

    def test_a_ceiling_another_subject_fills_does_not_refuse_this_one(self):
        async def scenario():
            store = InMemoryJobStore()
            for _ in range(3):
                await admit(store, make_record())
            bob = JobRecord.create(
                owner_subject="bob",
                sources=[Source.description("an app")],
                frameworks=sample_selection(),
            )
            return await store.reserve(bob, ceiling=1, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "admitted"

    def test_a_duplicate_id_is_named_rather_than_raised(self):
        # The API mints the id, so this is a defect report and not a caller
        # error; it is distinct from at_ceiling so the API can answer 500
        # rather than 429 for it.
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await admit(store, record)
            return await admit(store, record)

        assert asyncio.run(scenario()).outcome == "duplicate"

    def test_a_duplicate_is_settled_before_the_ceiling(self):
        # A record the store already holds is inside the budget already.
        # Counting it against the ceiling as well would report at_ceiling for
        # what is really a collision, and send a defect out as a 429.
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await admit(store, record)
            return await store.reserve(record, ceiling=1, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "duplicate"

    def test_simultaneous_reservations_never_exceed_the_ceiling(self):
        # The check-then-act race, run at a barrier: every task is released at
        # the same moment and they contend for one slot. Two calls -- a count
        # then a create -- would let all of them read 0 before any wrote.
        async def scenario():
            store = InMemoryJobStore()
            start = asyncio.Event()

            async def contend():
                await start.wait()
                return await store.reserve(
                    make_record(), ceiling=2, budget=SEEDING_BUDGET
                )

            racers = [asyncio.create_task(contend()) for _ in range(20)]
            start.set()
            outcomes = await asyncio.gather(*racers)
            return sum(1 for outcome in outcomes if outcome.outcome == "admitted")

        assert asyncio.run(scenario()) == 2

    def test_the_store_holds_exactly_what_it_admitted(self):
        # The count and the records cannot disagree: an admitted reservation is
        # a written record, and a refused one is nothing at all.
        async def scenario():
            store = InMemoryJobStore()

            async def contend():
                record = make_record()
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


class TestBuildStore:
    def test_builds_configured_backend(self):
        store = build_store({"ANALYSIS_JOB_STORE": "memory"})
        assert isinstance(store, InMemoryJobStore)

    def test_backend_selection_is_case_insensitive(self):
        store = build_store({"ANALYSIS_JOB_STORE": "  Memory  "})
        assert isinstance(store, InMemoryJobStore)

    def test_unset_backend_fails_closed(self):
        with pytest.raises(JobStoreConfigError, match="ANALYSIS_JOB_STORE"):
            build_store({})

    def test_unknown_backend_fails_closed(self):
        with pytest.raises(JobStoreConfigError, match="unknown job store 'redis'"):
            build_store({"ANALYSIS_JOB_STORE": "redis"})


class HangingRunner:
    """A runner that never returns: the wedged run the deadline exists for.

    It records whether it was cancelled, because "the job was marked failed" and
    "the work actually stopped" are different claims — a deadline that gives up
    on the await while the provider calls run on would free no worker.
    """

    def __init__(self, nodes: tuple[str, ...] = ()) -> None:
        self.nodes = nodes
        self.cancelled = False

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        for node in self.nodes:
            await on_node(node)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("the hanging runner returned")


class TestExecuteJob:
    @staticmethod
    def run_with(runner, deadline_seconds: float = 30) -> JobRecord:
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await admit(store, record)
            await execute_job(
                store, runner, record.id, deadline_seconds=deadline_seconds
            )
            return await store.get(record.id)

        return asyncio.run(scenario())

    def test_stub_runner_completes_with_report(self):
        record = self.run_with(StubPipelineRunner())
        assert record.status == "completed"
        assert record.report is not None
        assert record.report.job.id == record.id
        node_events = [e.node for e in record.events if e.kind == "node"]
        assert node_events == list(StubPipelineRunner.nodes)
        assert record.events[-1].status == "completed"

    def test_stub_report_references_every_source_it_was_given(self):
        import hashlib

        record = self.run_with(StubPipelineRunner())
        ref = record.report.input

        assert [r.label for r in ref.sources] == [s.label for s in record.sources]
        assert (
            ref.sources[0].sha256
            == hashlib.sha256(record.sources[0].text.encode("utf-8")).hexdigest()
        )
        # Recomputable from the report alone: the aggregate is taken over the
        # refs, which the report carries.
        assert ref.source_sha256 == InputRef.aggregate_digest(ref.sources)

    def test_rejection_embeds_validation_issues(self):
        record = self.run_with(RejectingRunner())
        assert record.status == "rejected"
        assert record.report is None
        assert [issue.code for issue in record.validation_issues] == ["no-trust-zones"]

    def test_runner_exception_fails_closed_with_generic_error(self):
        record = self.run_with(FailingRunner())
        assert record.status == "failed"
        assert record.error == GENERIC_FAILURE_MESSAGE
        assert "hunter2" not in record.error
        assert record.report is None


class TestJobDeadline:
    """The one bound on a job as a whole (wayfinder #61).

    The per-call knobs compose to a worst case in the hours while each is
    individually respected, so before this the tail was a product of numbers
    nobody chose and a wedged run held ``running`` until the process died.
    """

    def test_a_wedged_run_fails_at_the_deadline_instead_of_hanging(self):
        record = TestExecuteJob.run_with(HangingRunner(), deadline_seconds=0.05)
        assert record.status == "failed"
        assert record.report is None

    def test_the_deadline_actually_stops_the_work(self):
        """Marking the job failed is not the same as freeing the worker."""
        runner = HangingRunner()
        TestExecuteJob.run_with(runner, deadline_seconds=0.05)
        assert runner.cancelled

    def test_a_deadline_failure_says_so_rather_than_reading_as_a_crash(self):
        """Distinct from the generic message, and it names no internals.

        "internal error" would send a caller straight back to retry an identical
        submission against an identical bound; the deadline is a fact about this
        deployment's configuration, not about how the pipeline is built.
        """
        record = TestExecuteJob.run_with(HangingRunner(), deadline_seconds=0.05)
        assert record.error == DEADLINE_FAILURE_MESSAGE
        assert record.error != GENERIC_FAILURE_MESSAGE

    def test_the_nodes_that_landed_before_the_deadline_are_kept(self):
        """The partial progress is the diagnostic; it is not a partial report."""
        runner = HangingRunner(nodes=("extract", "validate", "prepare"))
        record = TestExecuteJob.run_with(runner, deadline_seconds=0.05)

        assert [e.node for e in record.events if e.kind == "node"] == [
            "extract",
            "validate",
            "prepare",
        ]
        assert record.report is None
        assert record.events[-1].status == "failed"

    def test_a_run_inside_the_deadline_is_untouched(self):
        record = TestExecuteJob.run_with(StubPipelineRunner(), deadline_seconds=30)
        assert record.status == "completed"
        assert record.report is not None
