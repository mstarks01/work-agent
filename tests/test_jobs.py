"""Job lifecycle, event log, store selection, and executor behavior."""

import asyncio
import json
import logging

import pytest

from analysis_service.assertions import AssertionCatalog, AssertionRecord
from analysis_service.execution import GraphFailed
from analysis_service.frameworks import PACKAGES
from analysis_service.jobs import (
    DEADLINE_FAILURE_MESSAGE,
    GENERIC_FAILURE_MESSAGE,
    InMemoryJobStore,
    InvalidTransitionError,
    JobRecord,
    JobStoreConfigError,
    NodeCallback,
    PipelineCompleted,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
    build_store,
    execute_job,
)
from analysis_service.report import (
    FrameworkSelection,
    InputRef,
    NodeRun,
    Report,
    TokenUsage,
)
from analysis_service.sources import Source
from analysis_service.validation import ValidationIssue
from tests.factories import (
    admit,
    sample_selection,
)
from tests.test_webapp import COMPLETE_OPTIONS


def make_record() -> JobRecord:
    return JobRecord.create(
        owner_id="alice",
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


class MidGraphFailingRunner:
    """A lane raised after extraction ran and was metered at ``tokens``."""

    def __init__(self, tokens: int = 100) -> None:
        self.tokens = tokens

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        await on_node("extract")
        raise GraphFailed(
            RuntimeError("provider refused"),
            [
                NodeRun(
                    node="extract",
                    duration_ms=10,
                    usage=TokenUsage(
                        prompt_tokens=self.tokens, total_tokens=self.tokens
                    ),
                )
            ],
        )


class TestTheStubRunner:
    """The stand-in runner's report is a report the envelope reads back."""

    @pytest.mark.parametrize("name", sorted(PACKAGES))
    def test_its_report_survives_a_round_trip_for_each_framework(self, name):
        record = JobRecord.create(
            owner_id="alice",
            sources=[Source.description("a web app storing orders")],
            frameworks=[FrameworkSelection(name=name, options=COMPLETE_OPTIONS[name])],
        )

        async def ignore(node: str) -> None:
            pass

        outcome = asyncio.run(StubPipelineRunner().run(record, ignore))
        assert isinstance(outcome, PipelineCompleted)
        report = outcome.report
        assert Report.model_validate_json(report.model_dump_json()) == report


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

    def test_a_queued_job_can_fail(self):
        # The restart scan fails a job that never started (#1529).
        record = make_record()
        record.transition("failed")
        assert record.status == "failed"

    @pytest.mark.parametrize(
        ("start", "target"),
        [
            ("queued", "completed"),
            ("queued", "rejected"),
            ("queued", "awaiting-answers"),
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
        with pytest.raises(
            JobStoreConfigError, match="unknown value 'redis' for ANALYSIS_JOB_STORE"
        ):
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
    def run_with(runner, deadline_seconds: float = 30, reserved: int = 0) -> JobRecord:
        async def scenario():
            store = InMemoryJobStore()
            record = make_record()
            record.reserved_tokens = reserved
            await admit(store, record)
            await execute_job(
                store, runner, record.id, deadline_seconds=deadline_seconds
            )
            return await store.get(record.id)

        return asyncio.run(scenario())

    def test_a_report_the_record_refuses_fails_the_job(self, caplog):
        """A report that does not answer the job's selection is refused by the
        record. The job ends ``failed`` with the generic message, so it frees
        its slot and its stream ends, rather than staying ``running``."""

        class ForeignReportRunner(StubPipelineRunner):
            async def run(self, job, on_node):
                outcome = await super().run(job, on_node)
                return PipelineCompleted(
                    report=outcome.report.model_copy(update={"analyses": []})
                )

        with caplog.at_level(logging.ERROR, logger="analysis_service.jobs"):
            record = self.run_with(ForeignReportRunner())

        assert record is not None
        assert record.status == "failed"
        assert record.error == GENERIC_FAILURE_MESSAGE
        assert record.report is None
        assert "refused its outcome" in caplog.text

    def test_a_completed_job_logs_its_use_of_the_catalog(self, caplog):
        """The job store keeps no history, so the log is where the figures go."""

        class CatalogRunner(StubPipelineRunner):
            async def run(self, job, on_node):
                outcome = await super().run(job, on_node)
                record = AssertionRecord(proposed=0, catalog=AssertionCatalog())
                return PipelineCompleted(
                    report=outcome.report.model_copy(update={"assertions": record})
                )

        with caplog.at_level(logging.INFO, logger="analysis_service.jobs"):
            record = self.run_with(CatalogRunner())

        (line,) = [
            entry.getMessage()
            for entry in caplog.records
            if "assertion use" in entry.getMessage()
        ]
        assert line.startswith(f"job {record.id} assertion use: ")
        assert json.loads(line.split(": ", 1)[1])["rows"] == 0

    def test_a_job_that_ran_no_pass_logs_no_use(self, caplog):
        with caplog.at_level(logging.INFO, logger="analysis_service.jobs"):
            self.run_with(StubPipelineRunner())

        assert not any(
            "assertion use" in entry.getMessage() for entry in caplog.records
        )

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

    def test_a_graph_that_raised_mid_run_keeps_a_larger_reservation(self):
        """The runs that finished are a floor on the spend: the raising node's
        own call is not among them. So the reservation stays where it is the
        larger figure, and a bound that must hold before anything is spent
        errs upward. `transition` states the rule."""
        record = self.run_with(MidGraphFailingRunner(tokens=1), reserved=3_857)
        assert record.status == "failed"
        assert record.error == GENERIC_FAILURE_MESSAGE
        assert "provider refused" not in record.error
        assert record.measured_tokens == 3_857

    def test_a_graph_that_raised_mid_run_settles_to_a_larger_floor(self):
        """A job that fails mid-run settles to its measured tokens, not its
        reservation: a small job reserves 3,857 tokens while its instructions
        alone are about 456,000 (run 11)."""
        record = self.run_with(MidGraphFailingRunner(tokens=456_000), reserved=3_857)
        assert record.status == "failed"
        assert record.measured_tokens == 456_000


class TestJobDeadline:
    """The one bound on a job as a whole (wayfinder #61).

    The per-call knobs compose to a worst case in the hours while each is
    individually respected. Without a job deadline, the tail is a product of
    numbers nobody chose, and a wedged run holds ``running`` until the process
    dies.
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
