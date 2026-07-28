"""Concurrent runs of the real graph must stay isolated from one another.

Verifies the guarantee documented in docs/Architecture.md ("Concurrency and
isolation"): analyses driven at the same time through one shared runner never
read or overwrite each other's state.

The test is built to *fail* if isolation is broken. Every job carries a unique
marker in its input text; a marker-aware ``extract`` stamps that marker into the
System Model it emits, so the marker travels through the job's ADK session state
and out into its report. A barrier holds all jobs inside ``extract`` at once, so
their sessions are provably live simultaneously. Each report must then carry its
own marker and no other job's — leaked state would surface as a foreign marker.
No Vertex endpoint is involved: every LLM node is a scripted stand-in.
"""

from __future__ import annotations

import asyncio
import json
import re

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from stride_service import graph
from stride_service.jobs import JobRecord, PipelineCompleted
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import load_model_tiers
from stride_service.pipeline import AdkPipelineRunner
from stride_service.sampling import load_sampling, make_resolve_sampling
from tests.factories import sample_threat, valid_model
from tests.test_pipeline import (
    BASE_MODEL,
    PROJECT_ROOT,
    STRONG_MODEL,
    ScriptedLlm,
    draft_json,
    served_build,
)

MARKER = re.compile(r"MARK-[0-9a-f]{4}")

# Every concurrent job meets here inside ``extract``, so all of their sessions
# are demonstrably in flight together before any of them produces a model.
_barrier: asyncio.Barrier | None = None


class MarkerExtractLlm(BaseLlm):
    """An ``extract`` stand-in whose output is derived from the job's own input.

    Reads the per-job marker out of the extraction prompt (which templates the
    submitted text), waits at the shared barrier so every concurrent job is
    inside ``extract`` at once, then emits a valid model stamped with that
    marker. If a job saw another job's input, it would stamp the wrong marker.
    """

    async def generate_content_async(self, llm_request, stream: bool = False):
        instruction = llm_request.config.system_instruction or ""
        match = MARKER.search(instruction)
        assert match, "extract prompt did not carry the job's input text"
        marker = match.group(0)
        if _barrier is not None:
            await _barrier.wait()
        model = valid_model()
        model.assumptions[0].basis = f"carried:{marker}"
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=model.model_dump_json())]
            ),
            model_version=served_build(self.model),
        )


def _build_shared_pipeline() -> graph.Pipeline:
    """The real graph: a marker-aware ``extract``, fixed drafts downstream."""
    graph_node_of = {
        tier_node: graph_node
        for graph_node, tier_node in graph.TIER_NODE_BY_GRAPH_NODE.items()
    }
    replies = {
        graph.analyst_node_name("spoofing"): draft_json("S-01", "spoofing"),
        graph.CRITIC_NODE: json.dumps([sample_threat("S-01").model_dump(mode="json")]),
    }

    def resolve(tier_node: str) -> BaseLlm:
        node = graph_node_of[tier_node]
        if node == graph.EXTRACT_NODE:
            return MarkerExtractLlm(model=BASE_MODEL)
        model_name = BASE_MODEL if node == graph.REPAIR_NODE else STRONG_MODEL
        return ScriptedLlm(model=model_name, reply=replies.get(node, "[]"), seen=[])

    tiers = load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})
    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    return graph.build_pipeline(
        skill_loader=MarkdownLoader(PROJECT_ROOT / "skills"),
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        resolve_model=resolve,
        resolve_sampling=make_resolve_sampling(sampling, tiers.resolve_tier),
        tier_sampling=sampling.tiers,
    )


def _marker_job(index: int) -> tuple[str, JobRecord]:
    """One job carrying a unique marker. All jobs share one caller on purpose.

    A shared ``owner_subject`` proves isolation is per session, not per caller:
    two jobs from the same caller still must not see each other.
    """
    marker = f"MARK-{index:04x}"
    record = JobRecord.create(
        owner_subject="ping|shared-caller",
        description=f"Customers log in to the web app. Job token {marker}.",
        system_name=f"System-{index:04x}",
    )
    record.transition("running")
    return marker, record


async def _ignore_node(node: str) -> None:
    return None


async def _run_all(count: int) -> list[tuple[int, str, object]]:
    global _barrier
    _barrier = asyncio.Barrier(count)
    runner = AdkPipelineRunner(_build_shared_pipeline())  # one shared runner + session service

    async def one(index: int) -> tuple[int, str, object]:
        marker, record = _marker_job(index)
        outcome = await runner.run(record, _ignore_node)
        return index, marker, outcome

    # A timeout so a broken-isolation deadlock fails loudly instead of hanging.
    return await asyncio.wait_for(
        asyncio.gather(*(one(i) for i in range(count))), timeout=60
    )


def test_concurrent_analyses_do_not_cross_contaminate():
    count = 8
    results = asyncio.run(_run_all(count))

    assert len(results) == count
    all_markers = {f"MARK-{i:04x}" for i in range(count)}

    for index, marker, outcome in results:
        assert isinstance(outcome, PipelineCompleted), f"job {index} did not complete"
        report = outcome.report

        # The report is the one for *this* job: its system_name rode the job
        # record through the shared runner untouched.
        assert report.input.system_name == f"System-{index:04x}"

        # This job's marker travelled through its own session state into its
        # model — proving the extract output landed in the right session.
        bases = [assumption.basis for assumption in report.system_model.assumptions]
        assert any(f"carried:{marker}" in basis for basis in bases), (
            f"job {index} lost its own marker {marker}"
        )

        # No other job's marker appears anywhere in this report.
        blob = report.model_dump_json()
        leaked = sorted(m for m in (all_markers - {marker}) if m in blob)
        assert not leaked, f"job {index} leaked markers from other jobs: {leaked}"


def test_each_concurrent_job_gets_its_own_threats_and_fingerprints():
    """Beyond the model: each report's threats and provenance are its own."""
    count = 6
    results = asyncio.run(_run_all(count))

    system_names = {outcome.report.input.system_name for _, _, outcome in results}
    assert system_names == {f"System-{i:04x}" for i in range(count)}

    for index, _, outcome in results:
        report = outcome.report
        # The scripted critic confirms exactly S-01 for every job; each report
        # holds its own single copy, never a doubled-up or missing set from a
        # neighbour's drafts bleeding across.
        assert [threat.id for threat in report.threats] == ["S-01"]
        # Sampling provenance is present and well-formed on every concurrent run.
        assert report.sampling
        extract_run = next(n for n in report.nodes if n.node == graph.EXTRACT_NODE)
        assert extract_run.sampling_fingerprint is not None
