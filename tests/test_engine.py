"""The in-process engine facade: input guards, outcome passthrough, sync bridge.

The facade owns no analysis logic — it shapes inputs into a job and relays the
runner's outcome — so most of these run against tiny stand-in runners. One test
drives the real scripted graph (reusing ``test_pipeline``'s offline harness) to
prove the facade reaches a genuine report end to end.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from stride_service.engine import (
    DEFAULT_CALLER,
    MAX_SYSTEM_NAME_CHARS,
    EngineInputError,
    StrideEngine,
)
from stride_service.jobs import (
    JobRecord,
    PipelineCompleted,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
)
from stride_service.pipeline import AdkPipelineRunner
from stride_service.sources import Source, SourceLimits
from tests.factories import DESCRIPTION_TEXT
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


# Small numbers keep the ladder tests readable; the shipped values live in
# config/resilience.toml and are asserted in test_resilience.
TEST_LIMITS = SourceLimits(max_total_bytes=512, max_sources=3)


def engine_for(runner) -> StrideEngine:
    return StrideEngine(runner, limits=TEST_LIMITS)


def analyze(engine: StrideEngine, text: str, **kwargs) -> PipelineOutcome:
    return asyncio.run(engine.analyze([Source.description(text)], **kwargs))


def test_a_completed_run_returns_the_report():
    engine = engine_for(StubPipelineRunner())

    outcome = analyze(engine, DESCRIPTION_TEXT)

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.job.id.startswith("job-")


def test_system_name_flows_into_the_report():
    engine = engine_for(StubPipelineRunner())

    outcome = analyze(engine, "text", system_name="Checkout")

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.system_name == "Checkout"


def test_a_rejection_is_passed_through_not_raised():
    engine = engine_for(RecordingRunner(PipelineRejected(issues=[])))

    outcome = analyze(engine, "text")

    assert isinstance(outcome, PipelineRejected)


def test_an_internal_failure_propagates():
    engine = engine_for(RecordingRunner(RuntimeError("node exploded")))

    with pytest.raises(RuntimeError, match="node exploded"):
        analyze(engine, "text")


def test_on_node_receives_every_node():
    seen: list[str] = []

    async def on_node(node: str) -> None:
        seen.append(node)

    runner = RecordingRunner(PipelineRejected(issues=[]))
    engine = engine_for(runner)

    asyncio.run(engine.analyze([Source.description("text")], on_node=on_node))

    assert seen == ["extract", "critic"]


def test_caller_becomes_the_job_owner_subject():
    runner = RecordingRunner(PipelineRejected(issues=[]))
    engine = engine_for(runner)

    analyze(engine, "text", caller="tenant-42")

    assert runner.jobs[0].owner_subject == "tenant-42"
    assert DEFAULT_CALLER == "in-process"


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_an_empty_source_is_rejected_before_it_becomes_a_job(text):
    # Rung one is the Source type's own: a caller cannot build the bad
    # submission in the first place, so the engine never sees it.
    with pytest.raises(ValidationError):
        Source.description(text)


def test_a_bare_string_is_refused_with_the_call_to_write_instead():
    # The removed contract's analyze(text). A string satisfies Sequence, so
    # without this it would iterate characters and report a nonsense count —
    # and this is the first call an integrator port makes.
    engine = engine_for(StubPipelineRunner())

    with pytest.raises(EngineInputError, match="Source.description"):
        asyncio.run(engine.analyze("a web app storing orders"))


def test_no_sources_at_all_is_a_caller_error():
    engine = engine_for(StubPipelineRunner())

    with pytest.raises(EngineInputError, match="at least one source"):
        asyncio.run(engine.analyze([]))


def test_a_repeated_label_is_a_caller_error():
    # The in-process path enforces the same shapes as the HTTP one, from the
    # same SourceLimits — a label is a citation key, so two sources sharing one
    # would make every excerpt citing it ambiguous.
    engine = engine_for(StubPipelineRunner())
    sources = [
        Source.description("one", label="Notes"),
        Source.transcript("two", label="Notes"),
    ]

    with pytest.raises(EngineInputError, match="unique"):
        asyncio.run(engine.analyze(sources))


def test_too_many_sources_is_a_caller_error():
    engine = engine_for(StubPipelineRunner())
    sources = [
        Source.description("x", label=f"Doc {n}")
        for n in range(TEST_LIMITS.max_sources + 1)
    ]

    with pytest.raises(EngineInputError, match="source limit"):
        asyncio.run(engine.analyze(sources))


def test_over_budget_sources_are_a_caller_error_naming_every_label():
    engine = engine_for(StubPipelineRunner())
    half = TEST_LIMITS.max_total_bytes
    sources = [
        Source.description("a" * half, label="Doc"),
        Source.transcript("b" * half, label="Call"),
    ]

    with pytest.raises(EngineInputError) as caught:
        asyncio.run(engine.analyze(sources))
    assert "Doc" in str(caught.value)
    assert "Call" in str(caught.value)


def test_a_submission_at_the_budget_is_accepted():
    engine = engine_for(StubPipelineRunner())

    outcome = analyze(engine, "x" * TEST_LIMITS.max_total_bytes)

    assert isinstance(outcome, PipelineCompleted)


def test_the_engine_takes_a_transcript_and_a_document_together():
    runner = RecordingRunner(PipelineRejected(issues=[]))
    engine = engine_for(runner)

    asyncio.run(
        engine.analyze(
            [
                Source.description("a web app", label="Doc"),
                Source.transcript("Ana: it writes to Postgres.", label="Call"),
            ]
        )
    )

    assert [source.label for source in runner.jobs[0].sources] == ["Doc", "Call"]


def test_blank_system_name_falls_through_to_the_default():
    engine = engine_for(StubPipelineRunner())

    outcome = analyze(engine, "text", system_name="   ")

    # StubPipelineRunner substitutes its own default for a None name.
    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.system_name == "Stub System"


def test_over_long_system_name_is_a_caller_error():
    engine = engine_for(StubPipelineRunner())

    with pytest.raises(EngineInputError, match="system_name"):
        analyze(engine, "text", system_name="s" * (MAX_SYSTEM_NAME_CHARS + 1))


def test_analyze_sync_runs_outside_a_loop():
    engine = engine_for(StubPipelineRunner())

    outcome = engine.analyze_sync([Source.description("text")], system_name="Checkout")

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.system_name == "Checkout"


def test_analyze_sync_refuses_a_running_loop():
    engine = engine_for(StubPipelineRunner())

    async def inside_loop() -> None:
        engine.analyze_sync([Source.description("text")])

    with pytest.raises(RuntimeError, match="active event loop"):
        asyncio.run(inside_loop())


def test_from_config_builds_an_adk_runner():
    # Nothing is selected by default, so the vendor is named here too. Vertex's
    # credential mode is ADC, and the check is a build-time gate.
    engine = StrideEngine.from_config(
        env={
            "STRIDE_MODEL_BASE_VENDOR": "vertex",
            "STRIDE_MODEL_BASE_MODEL": "gemini-2.5-flash",
            "STRIDE_MODEL_STRONG_VENDOR": "vertex",
            "STRIDE_MODEL_STRONG_MODEL": "gemini-2.5-pro",
            "STRIDE_VERTEX_PROJECT": "test-project",
            "STRIDE_VERTEX_LOCATION": "us-central1",
            "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
        }
    )

    assert isinstance(engine._runner, AdkPipelineRunner)


def test_engine_drives_the_real_graph_to_a_report():
    pipeline, _ = build(happy_replies())
    engine = StrideEngine(AdkPipelineRunner(pipeline), limits=TEST_LIMITS)

    outcome = analyze(engine, DESCRIPTION_TEXT)

    assert isinstance(outcome, PipelineCompleted)
    assert any(threat.id == "S-01" for threat in outcome.report.threats)
