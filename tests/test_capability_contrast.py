"""The contrast cases for multi-factor authentication, through the real graph.

Each case under ``evals/capability_contrast/`` is a short source and the reading
that a reader which follows ``prompts/capabilities.md`` emits. The graph takes
that reading as a scripted reply, so these tests prove what the code does with
it: the request the node sends, the provenance check, and the ASVS decisions
that follow. They prove nothing about what a model emits.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from pydantic import Field

from analysis_service import graph
from analysis_service.capability_reader import (
    CapabilityReading,
    CapabilityRow,
    with_stated_capabilities,
)
from analysis_service.frameworks.asvs.applicability import applicability_for
from analysis_service.jobs import JobRecord, PipelineAwaiting
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import compose_capabilities_prompt
from analysis_service.sources import DEFAULT_DESCRIPTION_LABEL, Source
from analysis_service.system_model import SystemModel
from tests.factories import (
    DESCRIPTION_TEXT,
    PROJECT_ROOT,
    ScriptedLlm,
    adk_runner,
    emitted,
    sample_selection,
    scripted_pipeline,
    valid_model,
)

CASES = PROJECT_ROOT / "evals" / "capability_contrast"
CASE_DIRS = sorted(path for path in CASES.iterdir() if path.is_dir())
MFA = "multi-factor-authentication"


def _case(path: Path) -> dict[str, Any]:
    return json.loads((path / "case.json").read_text())


def _source(path: Path) -> str:
    return (path / "source.md").read_text()


class SchemaLlm(ScriptedLlm):
    """A stand-in that also records the response schema of each request."""

    schemas: list[Any] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[Any, None]:
        self.schemas.append(llm_request.config.response_schema)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def _run(path: Path, reading: dict[str, Any]) -> tuple[SystemModel, SchemaLlm]:
    """One paused ASVS job whose description holds the case's source.

    The scripted extraction is :func:`valid_model`, so the description also
    carries :data:`DESCRIPTION_TEXT`, which its excerpts quote.
    """
    pipeline, llms = scripted_pipeline(
        {
            "extract": emitted(valid_model()),
            graph.CAPABILITIES_NODE: json.dumps(reading),
        },
        llm_class=SchemaLlm,
        entry=graph.ENTRY_HEAD_ONLY,
        frameworks=("asvs",),
        capability_reader=True,
    )
    record = JobRecord.create(
        assertion_pass=True,
        owner_id="alice",
        sources=[Source.description(f"{DESCRIPTION_TEXT}\n\n{_source(path)}")],
        frameworks=sample_selection(("asvs",)),
        ask_questions=True,
    )
    record.transition("running")

    async def on_node(node: str) -> None:
        pass

    outcome = asyncio.run(adk_runner(pipeline).run(record, on_node))
    assert isinstance(outcome, PipelineAwaiting)
    reader = llms[graph.CAPABILITIES_NODE]
    assert isinstance(reader, SchemaLlm)
    return outcome.checkpoint.system_model, reader


def _level_three(model: SystemModel) -> dict[str, str]:
    decisions = applicability_for(3, model.capability_facts())
    return {key: decision.state for key, decision in decisions.items()}


def test_the_cases_hold_each_contrast_the_mfa_rule_draws():
    expected = {_case(path)["expected"]["V6.4.4"] for path in CASE_DIRS}
    assert expected == {"applicable", "unknown"}
    assert {path.name for path in CASE_DIRS} >= {
        "01-one-time-code-only",
        "02-independent-factors",
        "03-support-impersonation",
        "04-third-party-own-users",
    }


@pytest.mark.parametrize("path", CASE_DIRS, ids=lambda path: path.name)
class TestEachCase:
    def test_the_node_sends_the_mfa_rule_the_source_and_the_schema(self, path):
        _, reader = _run(path, _case(path)["reading"])
        prompt = compose_capabilities_prompt(MarkdownLoader(PROJECT_ROOT / "prompts"))
        [rule] = [line for line in prompt.splitlines() if f"State `{MFA}` only" in line]
        [instruction] = reader.seen
        assert rule in instruction
        assert f"- `{MFA}`:" in instruction
        assert _source(path).strip() in instruction
        assert reader.schemas == [CapabilityReading]

    def test_the_reading_survives_the_provenance_check(self, path):
        rows = CapabilityReading.model_validate(_case(path)["reading"]).capabilities
        model, _ = _run(path, _case(path)["reading"])
        readers = {s.capability for s in model.capabilities if s.origin == "reader"}
        assert {row.capability for row in rows} <= readers

    def test_the_level_three_decisions_follow_the_reading(self, path):
        case = _case(path)
        model, _ = _run(path, case["reading"])
        decisions = _level_three(model)
        assert {key: decisions[key] for key in case["expected"]} == case["expected"]
        stated = MFA in {s.capability for s in model.capabilities}
        assert stated == (case["expected"]["V6.4.4"] == "applicable")

    def test_a_quoted_mfa_row_outside_the_rule_still_passes_the_check(self, path):
        """The check reads provenance, not scope: a row that breaks the MFA rule and
        quotes the source exactly is kept, and V6.4.4 applies. The prompt is
        the only stage that keeps such a row out."""
        quote = _case(path)["rule_break_quote"]
        if quote is None:
            pytest.skip("the case states a second factor")
        sources = {DEFAULT_DESCRIPTION_LABEL: _source(path)}
        row = CapabilityRow(
            capability=MFA, source_label=DEFAULT_DESCRIPTION_LABEL, quote=quote
        )
        model = with_stated_capabilities(
            SystemModel(), CapabilityReading(capabilities=[row]), sources
        )
        assert _level_three(model)["V6.4.4"] == "applicable"
