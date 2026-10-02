"""The harness's reader of the schema facts ADR 0058 records on each node.

The facts come from a real graph run: one STRIDE lane falls back to the tool
path and answers outside its schema once, through :class:`ExecutedLlm`, the
path every node's model call takes.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from google.adk.agents import LlmAgent

from analysis_service import graph
from analysis_service.provider import ExecutedLlm, GenerationRequest
from analysis_service.report import Report
from analysis_service.retry import SchemaRefusal
from evals.harness.run import COMMANDS
from evals.harness.schema_paths import NO_SCHEMA, schema_paths, sweep
from tests.factories import PROJECT_ROOT, STRONG_MODEL, scripted_pipeline
from tests.test_execution import drive, happy_replies
from tests.test_schema_reask import _policy, _Replies

LANE = graph.analyze_node_name("stride", "spoofing")

ARCHIVED = next((PROJECT_ROOT / "evals" / "baselines").rglob("*.report.json"))


class _ToolReplies(_Replies):
    """Scripted answers on the tool path, the first after a native refusal."""

    async def generate(self, request: GenerationRequest):
        results = await super().generate(request)
        fallback: SchemaRefusal | None = (
            "schema_field_refused" if len(self.seen) == 1 else None
        )
        return [
            replace(result, schema_path="tool", schema_fallback=fallback)
            for result in results
        ]


def _lane_run():
    replies = happy_replies()
    pipeline, _ = scripted_pipeline(replies)
    agents = {
        node.name: node
        for node in pipeline.workflow.graph.nodes
        if isinstance(node, LlmAgent)
    }
    executor = _ToolReplies('{"claims": "not a list"}', replies[LANE])
    agents[LANE].model = ExecutedLlm(
        model=STRONG_MODEL, executor=executor, retry_policy=_policy()
    )
    return drive(pipeline)


def test_a_graph_run_s_schema_facts_are_counted_per_node():
    totals = schema_paths(_lane_run().node_runs)

    lane = totals[LANE].to_json()
    assert lane == {
        "executions": 1,
        "reasks": 1,
        "paths": {"tool": 1},
        "fallbacks": {"schema_field_refused": 1},
    }
    assert totals[graph.EXTRACT_NODE].paths == {NO_SCHEMA: 1}


def test_the_sweep_reads_every_report_under_the_root(tmp_path):
    report = Report.model_validate_json(ARCHIVED.read_text(encoding="utf-8"))
    nodes = _lane_run().node_runs
    for name in ("a", "b"):
        written = report.model_copy(update={"nodes": nodes})
        (tmp_path / f"{name}.report.json").write_text(
            written.model_dump_json(), encoding="utf-8"
        )

    totals = sweep(tmp_path)

    assert totals[LANE].reasks == 2
    assert totals[LANE].fallbacks == {"schema_field_refused": 2}


def test_an_archived_report_reads_as_no_schema_and_no_reask():
    totals = sweep(ARCHIVED.parent)

    assert all(node.reasks == 0 for node in totals.values())
    assert {path for node in totals.values() for path in node.paths} == {NO_SCHEMA}


def test_the_command_refuses_an_empty_root(tmp_path, capsys):
    args = argparse.Namespace(root=tmp_path)

    assert COMMANDS["schema-paths"].run(args) == 1
    assert "no archived report" in capsys.readouterr().err
