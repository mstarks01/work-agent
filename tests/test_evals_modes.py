"""The three eval modes, driven offline against scripted models.

No Vertex endpoint is involved: ``build_pipeline`` takes the model resolver, so
each LLM node is bound to a fake replaying a canned emission. What is under
test is that the *shipped* graph runs from each mode's entry point and yields
the artifact that mode scores, including the analysis mode's blessed-model
injection at ``prepare``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm

from evals.harness import modes
from evals.harness.reference import load_case
from evals.harness.structural import report_issues
from stride_service.certification import fingerprints_of
from stride_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    EXTRACT_NODE,
    TIER_NODE_BY_GRAPH_NODE,
    analyst_node_name,
)
from stride_service.report import (
    CATEGORY_LETTERS,
    STRIDE_CATEGORIES,
    Mitigation,
    Severity,
    Verdict,
)
from stride_service.sampling import load_sampling
from tests.factories import ScriptedLlm

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "evals" / "corpus" / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


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
            {
                "threats": [
                    scripted_threat(case, category, promoted=True)
                    for category in STRIDE_CATEGORIES
                ]
            }
        )
    for category in STRIDE_CATEGORIES:
        if graph_node == analyst_node_name(category):
            return json.dumps(
                {"threats": [scripted_threat(case, category, promoted=False)]}
            )
    return '{"threats": []}'


def test_analysis_mode_injects_the_blessed_model_at_prepare(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_PREPARE, models)

    report = asyncio.run(modes.run_analysis(case, pipeline)).report

    # No extraction ran, and the analysts saw exactly the blessed model — the
    # whole point of the mode.
    assert "extract" not in models
    assert report.system_model == case.model
    spoofing = models[analyst_node_name("spoofing")].seen[0]
    assert "flow:shopper-to-storefront-api:place-order" in spoofing


def test_analysis_mode_output_passes_the_tier_1_gates(case):
    pipeline = build(case, ENTRY_PREPARE, {})

    report = asyncio.run(modes.run_analysis(case, pipeline)).report

    assert report_issues(report) == []
    assert len(report.threats) == len(STRIDE_CATEGORIES)


def test_analysis_mode_scores_against_the_reference_set(case):
    from evals.harness.scorer import score_case
    from tests.eval_factories import ScriptedJudge

    pipeline = build(case, ENTRY_PREPARE, {})
    report = asyncio.run(modes.run_analysis(case, pipeline)).report
    # The scripted threats are titled with reference claims verbatim, so a
    # judge that matches identical strings is the honest stand-in here.
    judge = ScriptedJudge(
        (threat.title, threat.title) for threat in report.threats
    )

    score = score_case(case, report.threats, judge)

    assert len(score.matched) == len(STRIDE_CATEGORIES)
    assert score.element_accuracy == 1.0
    assert score.severity_exact_rate == 1.0


def test_analysis_mode_surfaces_the_pre_critic_drafts(case):
    # The union the critic was handed, read off the state key ``merge_drafts``
    # already writes — no production seam moves for it.
    pipeline = build(case, ENTRY_PREPARE, {})

    run = asyncio.run(modes.run_analysis(case, pipeline))

    assert len(run.merged_drafts) == len(STRIDE_CATEGORIES)
    assert {draft.id for draft in run.merged_drafts} == {
        threat.id for threat in run.report.threats
    }
    # Drafts, not threats: the critic's two rulings are absent by construction.
    assert not any(hasattr(draft, "verdict") for draft in run.merged_drafts)


def test_end_to_end_mode_surfaces_the_pre_critic_drafts(case):
    pipeline = build(case, ENTRY_EXTRACT, {})

    run = asyncio.run(modes.run_end_to_end(case, pipeline))

    assert len(run.merged_drafts) == len(STRIDE_CATEGORIES)


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

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    assert "extract" in models
    assert report_issues(report) == []


# --- Provenance -------------------------------------------------------------
#
# A sweep is one of certification's two callers. Without a stamped NodeRun per
# execution an eval report carries no fingerprint, ``fingerprints_of`` returns an
# empty mapping, and ``certify`` announces every fingerprint blessed having seen
# none.


def test_an_eval_report_stamps_the_nodes_that_actually_ran(case):
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    stamped = {node_run.node for node_run in report.nodes}
    assert EXTRACT_NODE in stamped
    assert "critic" in stamped
    assert stamped <= {node.name for node in pipeline.workflow.graph.nodes}
    # The placeholder this replaced.
    assert "eval" not in stamped


def test_an_eval_report_presents_fingerprints_to_certify(case):
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report
    observations = fingerprints_of(report.nodes)

    assert observations, "a sweep with no observations certifies nothing"
    assert all(prints for prints in observations.values())
    assert observations.keys() <= set(TIER_NODE_BY_GRAPH_NODE)


def test_an_eval_reports_fingerprints_recompute_from_its_own_clear_block(case):
    """Evidence, not assertion: the artifact carries what verifies it."""
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    assert report.sampling == {
        tier: params.model_dump() for tier, params in pipeline.tier_sampling.items()
    }


def test_extraction_mode_observes_its_one_node(case):
    """The mode produces no report, and its ``extract`` identity counts anyway."""
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})

    result = asyncio.run(modes.run_extraction(case, pipeline))
    observations = fingerprints_of(result.node_runs)

    assert set(observations) == {EXTRACT_NODE}


def test_every_mode_maps_to_a_graph_entry():
    assert modes.MODE_ENTRIES == {
        "extraction": ENTRY_EXTRACT_ONLY,
        "analysis": ENTRY_PREPARE,
        "end-to-end": ENTRY_EXTRACT,
    }
