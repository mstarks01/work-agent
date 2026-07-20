"""End-to-end runs of the real graph against a scripted model.

No Vertex endpoint is involved: ``build_pipeline`` takes the model resolver,
so each LLM node is bound to a fake that replays a canned emission. That
keeps the whole topology under test — routes, the fan-out, the join, the
one repair pass, and the rejection path — while the only thing faked is the
model's text.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from stride_service import graph
from stride_service.api import create_app
from stride_service.jobs import JobRecord, PipelineCompleted, PipelineRejected
from stride_service.markdown_loader import MarkdownLoader
from stride_service.pipeline import (
    AdkPipelineRunner,
    PipelineError,
    build_default_pipeline,
)
from stride_service.report import STRIDE_CATEGORIES
from tests.factories import sample_draft, sample_threat, valid_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FLASH = "fake-flash-001"
PRO = "fake-pro-001"


class ScriptedLlm(BaseLlm):
    """One node's model: replays a fixed emission and records the request."""

    reply: str
    seen: list[str] = []

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)])
        )


def draft_json(threat_id: str, category: str) -> str:
    return json.dumps([sample_draft(threat_id, category).model_dump(mode="json")])


def build(replies: dict[str, str]) -> tuple[graph.Pipeline, dict[str, ScriptedLlm]]:
    """The real graph, with every LLM node bound to its scripted stand-in."""
    models: dict[str, ScriptedLlm] = {}

    graph_node_of = {
        tier_node: graph_node
        for graph_node, tier_node in graph.TIER_NODE_BY_GRAPH_NODE.items()
    }

    def resolve(tier_node: str) -> BaseLlm:
        # ``build_pipeline`` resolves by canonical tier name; the scripts here
        # are keyed by graph node name, which is what the tests talk about.
        node = graph_node_of[tier_node]
        model_name = FLASH if node in (graph.EXTRACT_NODE, graph.REPAIR_NODE) else PRO
        models[node] = ScriptedLlm(
            model=model_name, reply=replies.get(node, "[]"), seen=[]
        )
        return models[node]

    pipeline = graph.build_pipeline(
        skill_loader=MarkdownLoader(PROJECT_ROOT / "skills"),
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        resolve_model=resolve,
    )
    return pipeline, models


def job(description: str = "Customers log in to the web app.") -> JobRecord:
    record = JobRecord.create(
        owner_subject="ping|user-1",
        description=description,
        system_name="Order Service",
    )
    record.transition("running")
    return record


def run(pipeline: graph.Pipeline, record: JobRecord) -> tuple[object, list[str]]:
    """Drive one job to its outcome, collecting the nodes it reported."""
    visited: list[str] = []

    async def on_node(node: str) -> None:
        visited.append(node)

    async def scenario():
        return await AdkPipelineRunner(pipeline).run(record, on_node)

    return asyncio.run(scenario()), visited


def happy_replies() -> dict[str, str]:
    """Extraction succeeds; spoofing drafts one threat; the critic confirms it."""
    return {
        "extract": valid_model().model_dump_json(),
        graph.analyst_node_name("spoofing"): draft_json("S-01", "spoofing"),
        "critic": json.dumps([sample_threat("S-01").model_dump(mode="json")]),
    }


def test_a_clean_run_produces_a_report():
    pipeline, _ = build(happy_replies())
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    report = outcome.report
    assert [threat.id for threat in report.threats] == ["S-01"]
    assert report.summary.threat_count == 1
    assert report.input.system_name == "Order Service"
    assert report.system_model.get("process:web-app") is not None
    # Self-containment is re-checked by StrideReport itself on construction.
    assert report.boundary_crossings == report.system_model.boundary_crossings()

    assert visited[0] == graph.EXTRACT_NODE
    assert visited[-1] == graph.ASSEMBLE_NODE
    assert set(graph.ANALYST_GRAPH_NODES) <= set(visited)
    assert graph.REPAIR_NODE not in visited
    assert graph.REJECT_NODE not in visited


def test_report_nodes_record_the_model_each_node_ran_on():
    pipeline, _ = build(happy_replies())
    outcome, _ = run(pipeline, job())

    by_node = {run_.node: run_ for run_ in outcome.report.nodes}
    assert by_node[graph.EXTRACT_NODE].model == FLASH
    assert by_node[graph.CRITIC_NODE].model == PRO
    assert by_node[graph.ASSEMBLE_NODE].model is None
    assert all(run_.duration_ms >= 0 for run_ in outcome.report.nodes)


def test_each_analyst_gets_its_own_category_and_the_shared_model():
    pipeline, models = build(happy_replies())
    run(pipeline, job())

    for category in STRIDE_CATEGORIES:
        instruction = models[graph.analyst_node_name(category)].seen[0]
        assert f"**{category}** analyst" in instruction
        assert "process:web-app" in instruction  # {system_model} templated in
        assert "{" not in instruction.split("## Procedure")[0].split("```")[-1]


def test_the_critic_sees_every_analysts_drafts_once():
    replies = happy_replies()
    replies[graph.analyst_node_name("tampering")] = draft_json("T-01", "tampering")
    replies["critic"] = json.dumps(
        [
            sample_threat("S-01").model_dump(mode="json"),
            sample_threat("T-01", category="tampering").model_dump(mode="json"),
        ]
    )
    pipeline, models = build(replies)
    outcome, _ = run(pipeline, job())

    critic_instruction = models["critic"].seen[0]
    assert critic_instruction.count('"id": "S-01"') == 1
    assert critic_instruction.count('"id": "T-01"') == 1
    assert {threat.id for threat in outcome.report.threats} == {"S-01", "T-01"}


def test_an_invalid_extraction_is_repaired_once_and_then_analyzed():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    replies = happy_replies() | {"extract": json.dumps(broken)}
    replies["repair"] = valid_model().model_dump_json()

    pipeline, models = build(replies)
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    assert visited[:4] == [
        graph.EXTRACT_NODE,
        graph.VALIDATE_NODE,
        graph.REPAIR_NODE,
        graph.REVALIDATE_NODE,
    ]
    repair_instruction = models["repair"].seen[0]
    assert "process:does-not-exist" in repair_instruction  # the failed model
    assert "Customers log in to the web app." in repair_instruction  # original text


def test_a_model_that_fails_twice_is_rejected_with_its_issues():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    replies = happy_replies() | {
        "extract": json.dumps(broken),
        "repair": json.dumps(broken),
    }

    pipeline, _ = build(replies)
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineRejected)
    assert any("process:does-not-exist" in issue.message for issue in outcome.issues)
    assert visited[-1] == graph.REJECT_NODE
    assert graph.PREPARE_NODE not in visited
    assert not set(graph.ANALYST_GRAPH_NODES) & set(visited)


def test_a_hallucinated_element_reference_fails_the_job_loudly():
    """The merge seam refuses drafts the System Model cannot account for."""
    replies = happy_replies()
    replies[graph.analyst_node_name("spoofing")] = json.dumps(
        [
            sample_draft("S-01", affected_element_ids=["process:invented"]).model_dump(
                mode="json"
            )
        ]
    )
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="process:invented"):
        run(pipeline, job())


def test_a_critic_that_invents_a_threat_fails_the_job_loudly():
    replies = happy_replies()
    replies["critic"] = json.dumps(
        [
            sample_threat("S-01").model_dump(mode="json"),
            sample_threat("T-02", category="tampering").model_dump(mode="json"),
        ]
    )
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="T-02"):
        run(pipeline, job())


def test_the_default_pipeline_binds_the_pinned_models_from_config():
    pipeline = build_default_pipeline(env={})
    assert pipeline.node_models[graph.EXTRACT_NODE] == "gemini-2.5-flash-002"
    assert pipeline.node_models[graph.CRITIC_NODE] == "gemini-2.5-pro-002"
    assert set(pipeline.node_models) == set(graph.TIER_NODE_BY_GRAPH_NODE)


def test_the_default_pipeline_fails_closed_on_a_missing_tier_config(tmp_path):
    with pytest.raises(OSError):
        build_default_pipeline(env={"STRIDE_MODEL_TIERS": str(tmp_path / "gone.toml")})


def test_the_api_runs_jobs_through_the_real_graph_by_default():
    """The seam ticket 018 left for the graph now defaults to the graph."""

    class NoVerifier:
        def verify(self, token: str) -> str:
            raise AssertionError("not reached")

    app = create_app(verifier=NoVerifier())
    assert isinstance(app.state.runner, AdkPipelineRunner)


def test_pipeline_error_names_the_job_when_the_graph_produces_nothing():
    assert "graph produced neither" in str(
        PipelineError("job x: graph produced neither")
    )
