"""Job lifecycle, event log, in-memory store, and executor behavior."""

import asyncio

import pytest

from stride_service.jobs import (
    GENERIC_FAILURE_MESSAGE,
    InMemoryJobStore,
    InvalidTransitionError,
    JobRecord,
    JobStoreConfigError,
    NodeCallback,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
    build_store,
    execute_job,
)
from stride_service.report import InputRef
from stride_service.sources import Source
from stride_service.validation import ValidationIssue


def make_record() -> JobRecord:
    return JobRecord.create(
        owner_subject="alice",
        sources=[Source.description("a web app storing orders")],
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
            await store.create(record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_get_unknown_returns_none(self):
        store = InMemoryJobStore()
        assert asyncio.run(store.get("job-missing")) is None

    def test_create_duplicate_rejected(self):
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await store.create(record)
            await store.create(record)

        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(scenario())

    def test_save_requires_existing_record(self):
        with pytest.raises(ValueError, match="does not exist"):
            asyncio.run(InMemoryJobStore().save(make_record()))

    def test_mutations_invisible_until_saved(self):
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await store.create(record)
            record.transition("running")
            before_save = await store.get(record.id)
            await store.save(record)
            after_save = await store.get(record.id)
            return before_save.status, after_save.status

        assert asyncio.run(scenario()) == ("queued", "running")


class TestBuildStore:
    def test_builds_configured_backend(self):
        store = build_store({"STRIDE_JOB_STORE": "memory"})
        assert isinstance(store, InMemoryJobStore)

    def test_backend_selection_is_case_insensitive(self):
        store = build_store({"STRIDE_JOB_STORE": "  Memory  "})
        assert isinstance(store, InMemoryJobStore)

    def test_unset_backend_fails_closed(self):
        with pytest.raises(JobStoreConfigError, match="STRIDE_JOB_STORE"):
            build_store({})

    def test_unknown_backend_fails_closed(self):
        with pytest.raises(JobStoreConfigError, match="unknown job store 'redis'"):
            build_store({"STRIDE_JOB_STORE": "redis"})


class TestExecuteJob:
    @staticmethod
    def run_with(runner) -> JobRecord:
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            await store.create(record)
            await execute_job(store, runner, record.id)
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
        assert ref.sources[0].sha256 == hashlib.sha256(
            record.sources[0].text.encode("utf-8")
        ).hexdigest()
        # Recomputable from the report alone: the aggregate is taken over the
        # refs, which the report carries.
        assert ref.source_sha256 == InputRef.aggregate_digest(ref.sources)

    def test_rejection_embeds_validation_issues(self):
        record = self.run_with(RejectingRunner())
        assert record.status == "rejected"
        assert record.report is None
        assert [issue.code for issue in record.validation_issues] == [
            "no-trust-zones"
        ]

    def test_runner_exception_fails_closed_with_generic_error(self):
        record = self.run_with(FailingRunner())
        assert record.status == "failed"
        assert record.error == GENERIC_FAILURE_MESSAGE
        assert "hunter2" not in record.error
        assert record.report is None
