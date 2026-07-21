"""The three eval modes, driven offline against scripted models.

No Vertex endpoint is involved: ``build_pipeline`` takes the model resolver, so
each LLM node is bound to a fake replaying a canned emission — the same
technique ticket 021's tests use. What is under test is that the *shipped*
graph runs from each mode's entry point and yields the artifact that mode
scores, including the analysis mode's blessed-model injection at ``prepare``.
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

from evals.harness import modes
from evals.harness.reference import load_case
from evals.harness.structural import report_issues
from stride_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    TIER_NODE_BY_GRAPH_NODE,
    analyst_node_name,
)
from stride_service.report import (
    STRIDE_CATEGORIES,
    CATEGORY_LETTERS,
    Mitigation,
    Severity,
    Threat,
    Verdict,
)
from stride_service.sampling import load_sampling

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "evals" / "corpus" / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


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


def scripted_threat(case, category, *, promoted: bool) -> dict:
    """One threat citing an element the blessed model really contains."""
    reference = next(ref for ref in case.references if ref.category == category)
    fields = {
        "id": f"{CATEGORY_LETTERS[category]}-01",
        "category": category,
        "title": reference.claim,
        "description": f"{reference.claim} Scripted for the offline mode test.",
        "affected_element_ids": list(reference.affected_element_ids),
        "severity": Severity(
            likelihood=reference.severity.likelihood,
            impact=reference.severity.impact,
            justification="scripted",
        ).model_dump(mode="json"),
        "mitigations": [Mitigation(summary="Scripted mitigation").model_dump()],
    }
    if promoted:
        fields["confidence"] = "high"
        fields["verdict"] = Verdict(status="confirmed").model_dump(mode="json")
    return fields


def build(case, entry, models: dict[str, ScriptedLlm]) -> object:
    def resolve(tier_node: str) -> BaseLlm:
        graph_node = next(
            node for node, tier in TIER_NODE_BY_GRAPH_NODE.items() if tier == tier_node
        )
        models[graph_node] = ScriptedLlm(
            model="fake-pro-001", reply=_reply_for(case, graph_node), seen=[]
        )
        return models[graph_node]

    return modes.build_eval_pipeline(
        entry,
        resolve_model=resolve,
        sampling=load_sampling(REPO_ROOT / "config" / "sampling.toml"),
    )


def _reply_for(case, graph_node: str) -> str:
    if graph_node == "extract":
        return json.dumps(case.model.model_dump(mode="json"))
    if graph_node == "critic":
        return json.dumps(
            [
                scripted_threat(case, category, promoted=True)
                for category in STRIDE_CATEGORIES
            ]
        )
    for category in STRIDE_CATEGORIES:
        if graph_node == analyst_node_name(category):
            return json.dumps([scripted_threat(case, category, promoted=False)])
    return "[]"


def test_analysis_mode_injects_the_blessed_model_at_prepare(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_PREPARE, models)

    report = asyncio.run(modes.run_analysis(case, pipeline))

    # No extraction ran, and the analysts saw exactly the blessed model — the
    # whole point of the mode (ticket 009 decision 1).
    assert "extract" not in models
    assert report.system_model == case.model
    spoofing = models[analyst_node_name("spoofing")].seen[0]
    assert "flow:shopper-to-storefront-api:place-order" in spoofing


def test_analysis_mode_output_passes_the_tier_1_gates(case):
    pipeline = build(case, ENTRY_PREPARE, {})

    report = asyncio.run(modes.run_analysis(case, pipeline))

    assert report_issues(report) == []
    assert len(report.threats) == len(STRIDE_CATEGORIES)


def test_analysis_mode_scores_against_the_reference_set(case):
    from tests.eval_factories import ScriptedJudge
    from evals.harness.scorer import score_case

    pipeline = build(case, ENTRY_PREPARE, {})
    report = asyncio.run(modes.run_analysis(case, pipeline))
    # The scripted threats are titled with reference claims verbatim, so a
    # judge that matches identical strings is the honest stand-in here.
    judge = ScriptedJudge(
        (threat.title, threat.title) for threat in report.threats
    )

    score = score_case(case, report.threats, judge)

    assert len(score.matched) == len(STRIDE_CATEGORIES)
    assert score.element_accuracy == 1.0
    assert score.severity_exact_rate == 1.0


def test_extraction_mode_runs_extract_alone(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_EXTRACT_ONLY, models)

    result = asyncio.run(modes.run_extraction(case, pipeline))

    assert set(models) == {"extract"}
    assert result.issues == ()
    assert result.extracted == case.model


def test_extraction_scoring_is_mechanical(case):
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})
    result = asyncio.run(modes.run_extraction(case, pipeline))

    score = modes.score_extraction(case, result)

    assert score.recall == 1.0
    assert score.precision == 1.0
    assert score.crossings_match is True
    assert score.missing == () and score.extra == ()


def test_extraction_scoring_reports_missing_and_extra_elements(case):
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})
    result = asyncio.run(modes.run_extraction(case, pipeline))
    trimmed = result.extracted.model_copy(
        update={"processes": result.extracted.processes[:1]}
    )

    score = modes.score_extraction(
        case, modes.ExtractionResult(case.id, trimmed, ())
    )

    assert score.missing
    assert score.recall < 1.0
    assert score.precision == 1.0  # nothing invented, only dropped


def test_end_to_end_mode_runs_the_production_entry(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_EXTRACT, models)

    report = asyncio.run(modes.run_end_to_end(case, pipeline))

    assert "extract" in models
    assert report_issues(report) == []


def test_every_mode_maps_to_a_graph_entry():
    assert modes.MODE_ENTRIES == {
        "extraction": ENTRY_EXTRACT_ONLY,
        "analysis": ENTRY_PREPARE,
        "end-to-end": ENTRY_EXTRACT,
    }
