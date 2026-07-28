"""The in-process engine facade: input guards, outcome passthrough, sync bridge.

The facade owns no analysis logic — it shapes inputs into a job and relays the
runner's outcome — so most of these run against tiny stand-in runners. One test
drives the real scripted graph (reusing ``test_pipeline``'s offline harness) to
prove the facade reaches a genuine report end to end.
"""

from __future__ import annotations

import asyncio

import pytest

from stride_service.engine import (
    DEFAULT_CALLER,
    MAX_SYSTEM_NAME_CHARS,
    EngineInputError,
    StrideEngine,
)
from stride_service.jobs import (
    MAX_DESCRIPTION_BYTES,
    JobRecord,
    PipelineCompleted,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
)
from stride_service.pipeline import AdkPipelineRunner
from tests.test_pipeline import build, happy_replies


class RecordingRunner:
    """Captures the job it is handed and the nodes it reports, then answers."""

    def __init__(self, outcome: PipelineOutcome | Exception) -> None:
        self._outcome = outcome
        self.jobs: list[JobRecord] = []
        self.nodes: list[str] = []

    async def run(self, job, on_node) -> PipelineOutcome:
        self.jobs.append(job)
        for node in ("extract", "critic"):
            self.nodes.append(node)
            await on_node(node)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def analyze(engine: StrideEngine, description: str, **kwargs) -> PipelineOutcome:
    return asyncio.run(engine.analyze(description, **kwargs))


def test_a_completed_run_returns_the_report():
    engine = StrideEngine(StubPipelineRunner())

    outcome = analyze(engine, "Customers log in to the web app.")

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.job.id.startswith("job-")


def test_system_name_flows_into_the_report():
    engine = StrideEngine(StubPipelineRunner())

    outcome = analyze(engine, "text", system_name="Checkout")

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.system_name == "Checkout"


def test_a_rejection_is_passed_through_not_raised():
    engine = StrideEngine(RecordingRunner(PipelineRejected(issues=[])))

    outcome = analyze(engine, "text")

    assert isinstance(outcome, PipelineRejected)


def test_an_internal_failure_propagates():
    engine = StrideEngine(RecordingRunner(RuntimeError("node exploded")))

    with pytest.raises(RuntimeError, match="node exploded"):
        analyze(engine, "text")


def test_on_node_receives_every_node():
    seen: list[str] = []

    async def on_node(node: str) -> None:
        seen.append(node)

    runner = RecordingRunner(PipelineRejected(issues=[]))
    engine = StrideEngine(runner)

    asyncio.run(engine.analyze("text", on_node=on_node))

    assert seen == ["extract", "critic"]


def test_caller_becomes_the_job_owner_subject():
    runner = RecordingRunner(PipelineRejected(issues=[]))
    engine = StrideEngine(runner)

    analyze(engine, "text", caller="tenant-42")

    assert runner.jobs[0].owner_subject == "tenant-42"
    assert DEFAULT_CALLER == "in-process"


@pytest.mark.parametrize("description", ["", "   ", "\n\t "])
def test_empty_description_is_a_caller_error(description):
    engine = StrideEngine(StubPipelineRunner())

    with pytest.raises(EngineInputError, match="non-empty"):
        analyze(engine, description)


def test_oversized_description_is_a_caller_error():
    engine = StrideEngine(StubPipelineRunner())

    with pytest.raises(EngineInputError, match="byte cap"):
        analyze(engine, "x" * (MAX_DESCRIPTION_BYTES + 1))


def test_a_description_at_the_cap_is_accepted():
    engine = StrideEngine(StubPipelineRunner())

    outcome = analyze(engine, "x" * MAX_DESCRIPTION_BYTES)

    assert isinstance(outcome, PipelineCompleted)


def test_blank_system_name_falls_through_to_the_default():
    engine = StrideEngine(StubPipelineRunner())

    outcome = analyze(engine, "text", system_name="   ")

    # StubPipelineRunner substitutes its own default for a None name.
    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.system_name == "Stub System"


def test_over_long_system_name_is_a_caller_error():
    engine = StrideEngine(StubPipelineRunner())

    with pytest.raises(EngineInputError, match="system_name"):
        analyze(engine, "text", system_name="s" * (MAX_SYSTEM_NAME_CHARS + 1))


def test_analyze_sync_runs_outside_a_loop():
    engine = StrideEngine(StubPipelineRunner())

    outcome = engine.analyze_sync("text", system_name="Checkout")

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.system_name == "Checkout"


def test_analyze_sync_refuses_a_running_loop():
    engine = StrideEngine(StubPipelineRunner())

    async def inside_loop() -> None:
        engine.analyze_sync("text")

    with pytest.raises(RuntimeError, match="active event loop"):
        asyncio.run(inside_loop())


def test_from_config_builds_an_adk_runner():
    # Vertex's credential mode is ADC, and the check is a build-time gate.
    engine = StrideEngine.from_config(
        env={
            "STRIDE_VERTEX_PROJECT": "test-project",
            "STRIDE_VERTEX_LOCATION": "us-central1",
            "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
        }
    )

    assert isinstance(engine._runner, AdkPipelineRunner)


def test_engine_drives_the_real_graph_to_a_report():
    pipeline, _ = build(happy_replies())
    engine = StrideEngine(AdkPipelineRunner(pipeline))

    outcome = analyze(engine, "Customers log in to the web app.")

    assert isinstance(outcome, PipelineCompleted)
    assert any(threat.id == "S-01" for threat in outcome.report.threats)
