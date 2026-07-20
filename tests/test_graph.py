"""Wiring checks on the assembled graph, and the deterministic node functions.

The FunctionNode bodies are plain functions, so they are tested directly
against a fake context; the graph itself is checked for the shape ticket 004
decided — the routes that exist, the fan-out width, and every LLM node bound
to the model its canonical name resolves to.
"""

from __future__ import annotations

import re

import pytest
from google.adk.agents import LlmAgent
from google.adk.workflow import FunctionNode, JoinNode

from stride_service import graph
from stride_service.critic import CriticOutputError, DraftJoinError
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import LLM_NODES, load_model_tiers
from stride_service.report import STRIDE_CATEGORIES, DraftThreat, Threat
from stride_service.system_model import SystemModel
from tests.factories import sample_draft, sample_threat, valid_model

PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

# Placeholders ADK templates from session state at run time. Anything else
# left in a composed instruction would raise at the first LLM call.
RUNTIME_PLACEHOLDERS = frozenset(
    {
        graph.STATE_SYSTEM_MODEL,
        graph.STATE_BOUNDARY_CROSSINGS,
        graph.STATE_DRAFT_THREATS,
        graph.STATE_INPUT_TEXT,
        graph.STATE_PREVIOUS_MODEL,
        graph.STATE_VALIDATION_ISSUES,
    }
)


class FakeContext:
    """Stands in for ADK's node context: the node functions only touch state."""

    def __init__(self, **state: object) -> None:
        self.state = dict(state)


@pytest.fixture
def skill_loader() -> MarkdownLoader:
    return MarkdownLoader(PROJECT_ROOT / "skills")


@pytest.fixture
def prompt_loader() -> MarkdownLoader:
    return MarkdownLoader(PROJECT_ROOT / "prompts")


@pytest.fixture
def pipeline(skill_loader: MarkdownLoader, prompt_loader: MarkdownLoader):
    tiers = load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})
    return graph.build_pipeline(
        skill_loader=skill_loader,
        prompt_loader=prompt_loader,
        resolve_model=tiers.resolve_model,
    )


def nodes_by_name(pipeline) -> dict:
    return {node.name: node for node in pipeline.workflow.graph.nodes}


def routed_targets(pipeline, source: str) -> dict:
    return {
        edge.route: edge.to_node.name
        for edge in pipeline.workflow.graph.edges
        if edge.from_node.name == source
    }


# --- Wiring -----------------------------------------------------------------


def test_every_canonical_llm_node_is_a_graph_node(pipeline):
    """The tier config's node names and the graph's LLM nodes are one set."""
    assert set(graph.TIER_NODE_BY_GRAPH_NODE.values()) == set(LLM_NODES)
    assert set(graph.TIER_NODE_BY_GRAPH_NODE) <= set(nodes_by_name(pipeline))


def test_llm_nodes_bind_their_resolved_model(pipeline):
    tiers = load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})
    for name, node in nodes_by_name(pipeline).items():
        if not isinstance(node, LlmAgent):
            continue
        assert node.model == tiers.resolve_model(graph.TIER_NODE_BY_GRAPH_NODE[name])
        assert pipeline.node_models[name] == node.model


def test_deterministic_bookends_carry_no_model(pipeline):
    for name in (
        graph.VALIDATE_NODE,
        graph.REVALIDATE_NODE,
        graph.PREPARE_NODE,
        graph.MERGE_NODE,
        graph.ROUTER_NODE,
        graph.ASSEMBLE_NODE,
        graph.REJECT_NODE,
    ):
        assert isinstance(nodes_by_name(pipeline)[name], FunctionNode)
        assert name not in pipeline.node_models
    assert isinstance(nodes_by_name(pipeline)[graph.JOIN_NODE], JoinNode)


def test_six_analysts_fan_out_from_prepare_and_join(pipeline):
    edges = pipeline.workflow.graph.edges
    fanned = {
        edge.to_node.name for edge in edges if edge.from_node.name == graph.PREPARE_NODE
    }
    joined = {
        edge.from_node.name for edge in edges if edge.to_node.name == graph.JOIN_NODE
    }
    assert fanned == set(graph.ANALYST_GRAPH_NODES) == joined
    assert len(graph.ANALYST_GRAPH_NODES) == len(STRIDE_CATEGORIES) == 6


def test_one_repair_pass_then_rejection(pipeline):
    """Invalid twice ends at reject: the graph cannot spend a second repair."""
    assert routed_targets(pipeline, graph.VALIDATE_NODE) == {
        graph.ROUTE_VALID: graph.PREPARE_NODE,
        graph.ROUTE_INVALID: graph.REPAIR_NODE,
    }
    assert routed_targets(pipeline, graph.REVALIDATE_NODE) == {
        graph.ROUTE_VALID: graph.PREPARE_NODE,
        graph.ROUTE_INVALID: graph.REJECT_NODE,
    }
    assert not routed_targets(pipeline, graph.REJECT_NODE)


def test_revise_route_is_reserved_but_unwired(pipeline):
    assert routed_targets(pipeline, graph.ROUTER_NODE) == {
        graph.ROUTE_ACCEPT: graph.ASSEMBLE_NODE
    }
    assert graph.ROUTE_REVISE not in routed_targets(pipeline, graph.ROUTER_NODE)


def test_llm_nodes_see_no_history(pipeline):
    for node in nodes_by_name(pipeline).values():
        if isinstance(node, LlmAgent):
            assert node.include_contents == "none"


def test_llm_nodes_emit_their_schema(pipeline):
    by_name = nodes_by_name(pipeline)
    assert by_name[graph.EXTRACT_NODE].output_schema is SystemModel
    assert by_name[graph.REPAIR_NODE].output_schema is SystemModel
    assert by_name[graph.CRITIC_NODE].output_schema == list[Threat]
    for name in graph.ANALYST_GRAPH_NODES:
        assert by_name[name].output_schema == list[DraftThreat]


def test_extract_and_repair_share_one_output_key(pipeline):
    """One validate function serves both passes because both land in one key."""
    by_name = nodes_by_name(pipeline)
    assert by_name[graph.EXTRACT_NODE].output_key == graph.STATE_EXTRACTED_MODEL
    assert by_name[graph.REPAIR_NODE].output_key == graph.STATE_EXTRACTED_MODEL


# --- Instructions -----------------------------------------------------------


def test_analyst_instruction_carries_skill_then_prompt_then_exemplars(
    skill_loader, prompt_loader
):
    instruction = graph.analyst_instruction(skill_loader, prompt_loader, "spoofing")
    scope = instruction.index("# Spoofing")
    role = instruction.index("# STRIDE Category Analyst")
    exemplars = instruction.index("Exemplars")
    assert scope < role < exemplars


def test_category_placeholder_is_filled_at_build_time(skill_loader, prompt_loader):
    """Six analysts share one session state, which cannot hold six categories."""
    for category in STRIDE_CATEGORIES:
        instruction = graph.analyst_instruction(skill_loader, prompt_loader, category)
        assert "{category}" not in instruction
        assert f"**{category}** analyst" in instruction


def test_only_known_state_keys_remain_as_placeholders(pipeline):
    """A stray ``{identifier}`` would be a KeyError at the first LLM call."""
    for node in nodes_by_name(pipeline).values():
        if not isinstance(node, LlmAgent):
            continue
        found = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", node.instruction))
        assert found <= RUNTIME_PLACEHOLDERS, (node.name, found - RUNTIME_PLACEHOLDERS)


# --- Node functions ---------------------------------------------------------


def test_validate_routes_valid_and_publishes_the_model():
    ctx = FakeContext()
    event = graph.validate_extraction(valid_model().model_dump(mode="json"), ctx)
    assert event.actions.route == graph.ROUTE_VALID
    assert ctx.state[graph.STATE_VALID_MODEL]["processes"][0]["id"] == "process:web-app"


def test_validate_routes_invalid_and_feeds_the_repair_prompt():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    ctx = FakeContext()
    event = graph.validate_extraction(broken, ctx)

    assert event.actions.route == graph.ROUTE_INVALID
    assert "process:does-not-exist" in ctx.state[graph.STATE_PREVIOUS_MODEL]
    assert "process:does-not-exist" in ctx.state[graph.STATE_VALIDATION_ISSUES]
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_validate_routes_invalid_on_unparseable_output():
    ctx = FakeContext()
    event = graph.validate_extraction({"processes": "not a list"}, ctx)
    assert event.actions.route == graph.ROUTE_INVALID
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_reject_parks_the_issues_for_the_runner():
    ctx = FakeContext()
    graph.reject_model('[{"code": "schema", "message": "bad"}]', ctx)
    issues = graph.rejection_issues(ctx.state[graph.STATE_REJECTION])
    assert [issue.code for issue in issues] == ["schema"]


def test_prepare_derives_crossings_rather_than_trusting_them():
    ctx = FakeContext()
    output = graph.prepare_analysis(valid_model().model_dump(mode="json"), ctx)

    assert output == {"element_count": 7, "crossing_count": 1}
    assert "flow:customer-to-web-app:login" in ctx.state[graph.STATE_BOUNDARY_CROSSINGS]
    assert "process:web-app" in ctx.state[graph.STATE_SYSTEM_MODEL]


def test_merge_joins_drafts_in_canonical_order():
    drafts = {
        "spoofing": [sample_draft("S-01", "spoofing")],
        "tampering": [sample_draft("T-01", "tampering")],
    }
    ctx = FakeContext(
        **{
            graph.analyst_state_key(category): [
                draft.model_dump(mode="json") for draft in category_drafts
            ]
            for category, category_drafts in drafts.items()
        }
    )
    output = graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)

    assert output == {"draft_count": 2}
    assert [d["id"] for d in ctx.state[graph.STATE_MERGED_DRAFTS]] == ["S-01", "T-01"]
    assert "S-01" in ctx.state[graph.STATE_DRAFT_THREATS]


def test_merge_fails_closed_on_a_hallucinated_element():
    draft = sample_draft("S-01", affected_element_ids=["process:invented"])
    ctx = FakeContext(
        **{graph.analyst_state_key("spoofing"): [draft.model_dump(mode="json")]}
    )
    with pytest.raises(DraftJoinError, match="process:invented"):
        graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)


def test_router_accepts():
    event = graph.route_review([])
    assert event.actions.route == graph.ROUTE_ACCEPT


def test_assemble_splits_rulings_and_builds_the_summary():
    confirmed = sample_threat("S-01")
    rejected = sample_threat(
        "T-01",
        category="tampering",
        verdict={"status": "rejected", "reason": "duplicate of S-01"},
    )
    drafts = [
        sample_draft("S-01"),
        sample_draft("T-01", category="tampering"),
    ]
    ctx = FakeContext()
    output = graph.assemble_report(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        [t.model_dump(mode="json") for t in (confirmed, rejected)],
        ctx,
    )

    assert output == {"threat_count": 1, "rejected_count": 1}
    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    assert [t.id for t in analysis.threats] == ["S-01"]
    assert [t.id for t in analysis.rejected_threats] == ["T-01"]
    assert analysis.summary.threat_count == 1
    assert analysis.summary.rejected_count == 1


def test_assemble_fails_closed_when_the_critic_drops_a_draft():
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    ctx = FakeContext()
    with pytest.raises(CriticOutputError, match="dropped draft 'T-01'"):
        graph.assemble_report(
            valid_model().model_dump(mode="json"),
            [draft.model_dump(mode="json") for draft in drafts],
            [sample_threat("S-01").model_dump(mode="json")],
            ctx,
        )
