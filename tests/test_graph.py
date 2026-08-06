"""Wiring checks on the assembled graph, and the deterministic node functions.

The FunctionNode bodies are plain functions, so they are tested directly
against a fake context; the graph itself is checked for its shape — the routes
that exist, the fan-out width, and every LLM node bound to the model its
canonical name resolves to.
"""

from __future__ import annotations

import json
import re

import pytest
from google.adk.agents import LlmAgent
from google.adk.workflow import FunctionNode, JoinNode

from stride_service import graph
from stride_service.binding import NodeBinding
from stride_service.critic import CriticOutputError, DraftJoinError
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import LLM_NODES
from stride_service.report import (
    STRIDE_CATEGORIES,
    DraftThreats,
    ThreatRulings,
    UnknownRef,
    Verdict,
)
from stride_service.resilience import load_resilience
from stride_service.sampling import load_sampling
from stride_service.system_model import SystemModel
from tests.factories import repo_tiers, sample_draft, sample_ruling, valid_model

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
        graph.STATE_PREVIOUS_REVIEW,
        graph.STATE_CRITIC_ISSUES,
        graph.STATE_DRAFT_ROSTER,
        graph.STATE_UNRECONCILED_DRAFTS,
    }
)


class FakeContext:
    """Stands in for ADK's node context: the node functions only touch state."""

    def __init__(self, **state: object) -> None:
        self.state = dict(state)


def analyze_state(**drafts_by_category: list) -> dict[str, object]:
    """State as six category agents that all ran leave it.

    Every lane gets a key, because ``merge_drafts`` distinguishes a category agent
    that found nothing (``{"threats": []}``, its key written) from one that
    emitted nothing (no key at all, a truncated completion). A test seeding only
    the lanes it cares about would be asserting against the second, which is a
    failed job.
    """
    return {
        graph.analyze_state_key(category): {
            "threats": [
                draft.model_dump(mode="json")
                for draft in drafts_by_category.get(category.replace("-", "_"), [])
            ]
        }
        for category in STRIDE_CATEGORIES
    }


@pytest.fixture
def skill_loader() -> MarkdownLoader:
    return MarkdownLoader(PROJECT_ROOT / "skills")


@pytest.fixture
def prompt_loader() -> MarkdownLoader:
    return MarkdownLoader(PROJECT_ROOT / "prompts")


def _route_resolver(tiers):
    """Bind nodes to their tier's route string, standing in for the adapter.

    Production binds one ``LiteLlm`` per tier; these tests only need a stable
    per-tier identity on the node, and building real adapters would run the
    credential check against credentials an offline test does not have.
    """
    return lambda node: tiers.resolve_model(node).route


@pytest.fixture
def pipeline(skill_loader: MarkdownLoader, prompt_loader: MarkdownLoader):
    tiers = repo_tiers()
    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    return graph.build_pipeline(
        skill_loader=skill_loader,
        prompt_loader=prompt_loader,
        binding=NodeBinding.from_configs(tiers, sampling, _route_resolver(tiers)),
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
    tiers = repo_tiers()
    for name, node in nodes_by_name(pipeline).items():
        if not isinstance(node, LlmAgent):
            continue
        selection = tiers.resolve_model(graph.TIER_NODE_BY_GRAPH_NODE[name])
        assert node.model == selection.route
        assert pipeline.node_models[name] == node.model


def test_the_recorded_model_carries_its_vendor(pipeline):
    # A bare served identifier carries no vendor, and two vendors can serve the
    # same build — so the prefix is part of the identity, not decoration. The
    # test selection runs a different vendor per tier, which is what lets this
    # also pin that each node records *its own* tier's vendor: against the old
    # Vertex-on-both default, one global prefix would have passed identically.
    tiers = repo_tiers()
    for node in (graph.EXTRACT_NODE, graph.CRITIC_NODE):
        selection = tiers.resolve_model(graph.TIER_NODE_BY_GRAPH_NODE[node])
        assert pipeline.node_models[node].startswith(selection.vendor_entry.prefix)

    base, strong = (
        pipeline.node_models[node].partition("/")[0]
        for node in (graph.EXTRACT_NODE, graph.CRITIC_NODE)
    )
    assert base != strong


# The two tiers differ on every param the node config carries, so a node bound
# to the wrong tier's sampling would read wrong here.
_DIVERGENT_SAMPLING = """\
version = 4
[tiers.base]
temperature = 0.0
top_p = 0.5
max_output_tokens = 1024
seed = 11
thinking = "low"
[tiers.strong]
temperature = 1.0
top_p = 0.9
max_output_tokens = 4096
seed = 22
thinking = "high"
"""


def _pipeline_with_sampling(skill_loader, prompt_loader, sampling_path, resilience):
    tiers = repo_tiers()
    sampling = load_sampling(sampling_path, env={})
    return graph.build_pipeline(
        skill_loader=skill_loader,
        prompt_loader=prompt_loader,
        binding=NodeBinding.from_configs(
            tiers, sampling, _route_resolver(tiers), resilience
        ),
    )


def test_each_llm_node_binds_its_own_tier_sampling(
    skill_loader, prompt_loader, tmp_path
):
    """Sampling is resolved per node, not shared graph-wide.

    ``extract``/``repair`` run on base, the category agents and critic/recritic on
    strong; with the two tiers pinned to different decoding params, each node
    must carry *its* tier's ``GenerateContentConfig``.
    """
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    resilience = load_resilience(PROJECT_ROOT / "config" / "resilience.toml", env={})
    nodes = nodes_by_name(
        _pipeline_with_sampling(skill_loader, prompt_loader, sampling_path, resilience)
    )

    base_nodes = (graph.EXTRACT_NODE, graph.REPAIR_NODE)
    strong_nodes = (
        graph.CRITIC_NODE,
        graph.RECRITIC_NODE,
        *graph.ANALYZE_GRAPH_NODES,
    )
    for name in base_nodes:
        config = nodes[name].generate_content_config
        assert config.temperature == 0.0
        assert config.top_p == 0.5
        assert config.max_output_tokens == 1024
    for name in strong_nodes:
        config = nodes[name].generate_content_config
        assert config.temperature == 1.0
        assert config.top_p == 0.9
        assert config.max_output_tokens == 4096


def test_seed_and_reasoning_stay_off_the_node_config(
    skill_loader, prompt_loader, tmp_path
):
    """They ride the tier's adapter constructor instead.

    ADK's request map forwards neither, so putting them here would let them
    vanish silently while the fingerprint went on attesting to a seed the
    request never carried.
    """
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    nodes = nodes_by_name(
        _pipeline_with_sampling(skill_loader, prompt_loader, sampling_path, None)
    )
    config = nodes[graph.EXTRACT_NODE].generate_content_config
    assert config.seed is None
    assert config.thinking_config is None


def test_per_node_sampling_composes_with_the_resilience_timeout(
    skill_loader, prompt_loader, tmp_path
):
    """The node's tier sampling and the resilience timeout ride the one config.

    ``http_options`` stays owned by ``resilience.toml`` — folding
    per-node sampling in must not drop it, nor sampling source it.
    """
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    resilience = load_resilience(PROJECT_ROOT / "config" / "resilience.toml", env={})
    nodes = nodes_by_name(
        _pipeline_with_sampling(skill_loader, prompt_loader, sampling_path, resilience)
    )

    extract = nodes[graph.EXTRACT_NODE].generate_content_config
    assert extract.http_options.timeout == resilience.timeout_ms
    assert extract.temperature == 0.0  # sampling survives the http_options fold-in


def test_llm_nodes_carry_no_http_options_without_resilience(
    skill_loader, prompt_loader, tmp_path
):
    """Resilience is optional (offline stand-ins); its absence leaves no timeout."""
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    nodes = nodes_by_name(
        _pipeline_with_sampling(skill_loader, prompt_loader, sampling_path, None)
    )
    assert nodes[graph.CRITIC_NODE].generate_content_config.http_options is None


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


def test_six_category_agents_fan_out_from_prepare_and_join(pipeline):
    edges = pipeline.workflow.graph.edges
    fanned = {
        edge.to_node.name for edge in edges if edge.from_node.name == graph.PREPARE_NODE
    }
    joined = {
        edge.from_node.name for edge in edges if edge.to_node.name == graph.JOIN_NODE
    }
    assert fanned == set(graph.ANALYZE_GRAPH_NODES) == joined
    assert len(graph.ANALYZE_GRAPH_NODES) == len(STRIDE_CATEGORIES) == 6


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


def test_critic_re_ask_mirrors_the_one_repair_pass(pipeline):
    """router/recritic/rereview is the critic's validate/repair/revalidate."""
    assert routed_targets(pipeline, graph.ROUTER_NODE) == {
        graph.ROUTE_ACCEPT: graph.ASSEMBLE_NODE,
        graph.ROUTE_REVISE: graph.RECRITIC_NODE,
    }
    assert routed_targets(pipeline, graph.RECRITIC_NODE) == {None: graph.REREVIEW_NODE}
    assert routed_targets(pipeline, graph.REREVIEW_NODE) == {
        graph.ROUTE_ACCEPT: graph.ASSEMBLE_NODE,
        graph.ROUTE_REVISE: graph.CRITIC_FAILED_NODE,
    }
    # critic_failed is terminal: it raises rather than routing anywhere.
    assert not routed_targets(pipeline, graph.CRITIC_FAILED_NODE)


def test_recritic_runs_on_the_critic_tier_and_emits_the_review_schema(pipeline):
    by_name = nodes_by_name(pipeline)
    recritic = by_name[graph.RECRITIC_NODE]
    assert isinstance(recritic, LlmAgent)
    assert recritic.output_schema is ThreatRulings
    assert recritic.output_key == graph.STATE_REVIEWED_THREATS
    assert recritic.model == by_name[graph.CRITIC_NODE].model


def test_llm_nodes_see_no_history(pipeline):
    for node in nodes_by_name(pipeline).values():
        if isinstance(node, LlmAgent):
            assert node.include_contents == "none"


def test_llm_nodes_emit_their_schema(pipeline):
    by_name = nodes_by_name(pipeline)
    assert by_name[graph.EXTRACT_NODE].output_schema is SystemModel
    assert by_name[graph.REPAIR_NODE].output_schema is SystemModel
    assert by_name[graph.CRITIC_NODE].output_schema is ThreatRulings
    for name in graph.ANALYZE_GRAPH_NODES:
        assert by_name[name].output_schema is DraftThreats


def test_every_node_schema_survives_the_trip_to_a_response_format(pipeline):
    """The regression this wrapper exists for.

    A node's ``output_schema`` is only worth anything on the wire if the
    adapter can turn it into a response format. A bare ``list[...]`` cannot be
    turned into one: ADK logs a warning, returns ``None``, and the node
    generates unconstrained — with nothing in the request, the response or the
    report to show for it. That is why the category agents and both review passes
    carry wrapper models rather than lists.

    Asserted against ADK's own converter rather than a shape of our choosing,
    because it is that function's answer that decides whether a schema is sent.
    """
    from google.adk.models.lite_llm import _to_litellm_response_format

    for name, node in nodes_by_name(pipeline).items():
        if not isinstance(node, LlmAgent):
            continue
        response_format = _to_litellm_response_format(
            node.output_schema, "claude-sonnet-4-6"
        )
        assert response_format is not None, f"{name} would send no schema"
        # An object root, not an array: OpenAI's structured outputs require one.
        assert response_format["json_schema"]["schema"]["type"] == "object"


def test_no_node_is_ever_asked_for_a_derived_severity_band(pipeline):
    """The band is derived, so the model must not be given the field.

    OpenAI's strict structured outputs require every property to be listed as
    required, and ADK's converter obliges — including for an optional one. A
    ``level`` left in the schema is therefore not a field the model *may* fill
    in but one it *must*, which is the model asserting a band the matrix owns.
    Every threat it rules is then a chance to contradict the validator and kill
    the node.

    Read out of the converted response format rather than off the model, since
    the schema on the wire is the thing that does the asking.
    """
    from google.adk.models.lite_llm import _to_litellm_response_format

    for name, node in nodes_by_name(pipeline).items():
        if not isinstance(node, LlmAgent):
            continue
        schema = _to_litellm_response_format(node.output_schema, "openai/gpt-4o")
        severity = schema["json_schema"]["schema"].get("$defs", {}).get("Severity")
        if severity is None:
            continue
        assert "level" not in severity["properties"], f"{name} asks for the band"
        assert "level" not in severity["required"], f"{name} requires the band"


def test_extract_and_repair_share_one_output_key(pipeline):
    """One validate function serves both passes because both land in one key."""
    by_name = nodes_by_name(pipeline)
    assert by_name[graph.EXTRACT_NODE].output_key == graph.STATE_EXTRACTED_MODEL
    assert by_name[graph.REPAIR_NODE].output_key == graph.STATE_EXTRACTED_MODEL


# --- Instructions -----------------------------------------------------------


def test_analyze_instruction_carries_skill_then_prompt_then_exemplars(
    skill_loader, prompt_loader
):
    instruction = graph.analyze_instruction(skill_loader, prompt_loader, "spoofing")
    scope = instruction.index("# Spoofing")
    role = instruction.index("# STRIDE Category Agent")
    exemplars = instruction.index("Exemplars")
    assert scope < role < exemplars


def test_category_placeholder_is_filled_at_build_time(skill_loader, prompt_loader):
    """Six category agents share one session state, which cannot hold six categories."""
    for category in STRIDE_CATEGORIES:
        instruction = graph.analyze_instruction(skill_loader, prompt_loader, category)
        assert "{category}" not in instruction
        assert f"**{category}** agent" in instruction


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
    event = graph.validate_extraction(ctx, valid_model().model_dump(mode="json"))
    assert event.actions.route == graph.ROUTE_VALID
    assert ctx.state[graph.STATE_VALID_MODEL]["processes"][0]["id"] == "process:web-app"


def test_validate_routes_invalid_and_feeds_the_repair_prompt():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, broken)

    assert event.actions.route == graph.ROUTE_INVALID
    assert "process:does-not-exist" in ctx.state[graph.STATE_PREVIOUS_MODEL]
    assert "process:does-not-exist" in ctx.state[graph.STATE_VALIDATION_ISSUES]
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_validate_derives_ids_rather_than_spending_the_repair_pass():
    """An abbreviated ID is not a reason to route to ``repair``."""
    abbreviated = valid_model().model_dump(mode="json")
    abbreviated["processes"][0]["name"] = "Web App Frontend Service"
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, abbreviated)

    assert event.actions.route == graph.ROUTE_VALID
    published = ctx.state[graph.STATE_VALID_MODEL]
    assert published["processes"][0]["id"] == "process:web-app-frontend-service"
    assert published["data_flows"][0]["destination"] == (
        "process:web-app-frontend-service"
    )


def test_validate_parks_the_normalized_model_the_issues_cite():
    """Repair reads {previous_model}; it must contain the IDs it is sent."""
    broken = valid_model().model_dump(mode="json")
    broken["processes"][0]["name"] = "Web App Frontend Service"
    broken["data_flows"][0]["source"] = "entity:does-not-exist"
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, broken)

    assert event.actions.route == graph.ROUTE_INVALID
    assert "process:web-app-frontend-service" in ctx.state[graph.STATE_PREVIOUS_MODEL]
    assert "id-mismatch" not in ctx.state[graph.STATE_VALIDATION_ISSUES]


def test_validate_routes_invalid_on_unparseable_output():
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, {"processes": "not a list"})
    assert event.actions.route == graph.ROUTE_INVALID
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_reject_parks_the_issues_for_the_runner():
    ctx = FakeContext()
    graph.reject_model('[{"code": "schema", "message": "bad"}]', ctx)
    issues = graph.rejection_issues(ctx.state[graph.STATE_REJECTION])
    assert [issue.code for issue in issues] == ["schema"]


def test_validate_names_a_silent_extraction_rather_than_binding_against_it():
    """The same silence as the category agents', one node earlier.

    Without the default this surfaces as ADK failing to bind an argument to
    ``validate``, which reads as a graph defect rather than as a truncated
    extraction. It raises rather than routing to ``repair``: an absent model is
    not an invalid one, and repair runs on the same tier under the same cap.
    """
    ctx = FakeContext()

    with pytest.raises(graph.SilentNodeError) as excinfo:
        graph.validate_extraction(ctx)

    message = str(excinfo.value)
    assert graph.STATE_EXTRACTED_MODEL in message
    assert "max_output_tokens" in message
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_prepare_derives_crossings_rather_than_trusting_them():
    ctx = FakeContext()
    output = graph.prepare_analysis(valid_model().model_dump(mode="json"), ctx)

    assert output == {"element_count": 7, "crossing_count": 1}
    assert "flow:customer-to-web-app:login" in ctx.state[graph.STATE_BOUNDARY_CROSSINGS]
    assert "process:web-app" in ctx.state[graph.STATE_SYSTEM_MODEL]


def test_prepare_strips_the_source_fields_from_the_rendered_model():
    """The agents and the critic read the model; only ``{input_text}`` has quotes.

    Once the full source text is in the same request, an element's excerpt is a
    lossy duplicate of it — and leaving it in preserves exactly the failure
    finding-level attribution was written against: an agent reaching for the
    nearest excerpt instead of the span that triggered its finding.
    """
    valid = valid_model().model_dump(mode="json")
    excerpt = valid["processes"][0]["source_excerpt"]
    assert excerpt, "the fixture must carry an excerpt for this to prove anything"
    ctx = FakeContext()

    graph.prepare_analysis(valid, ctx)

    rendered = ctx.state[graph.STATE_SYSTEM_MODEL]
    assert excerpt not in rendered
    for field in ("source_excerpt", "source_label", "source_speaker"):
        assert field not in rendered
    # notes stays: the prompt binds it, and it is what lets the critic spot a
    # quote lifted out of a note — which the ladder verifies happily.
    assert "notes" in rendered
    # The report's copy is untouched; only the model's view was narrowed.
    assert valid["processes"][0]["source_excerpt"] == excerpt


def test_merge_joins_drafts_in_canonical_order():
    ctx = FakeContext(
        **analyze_state(
            spoofing=[sample_draft("S-01", "spoofing")],
            tampering=[sample_draft("T-01", "tampering")],
        )
    )
    output = graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)

    assert output == {"draft_count": 2, "unverified_count": 0}
    assert [d["id"] for d in ctx.state[graph.STATE_MERGED_DRAFTS]] == ["S-01", "T-01"]
    assert "S-01" in ctx.state[graph.STATE_DRAFT_THREATS]


def test_ruling_view_keeps_every_field_the_critic_rules_on():
    """The guard on ``_ruling_view``'s ``exclude_defaults``.

    Narrowing the prompt view is only free while nothing a verdict is reached
    from can fall through it. A ``DraftThreat`` field added with a default
    would vanish here whenever it held that default — so this names the fields
    the five steps read and fails the moment one stops arriving.
    """
    draft = sample_draft("S-01", "spoofing")

    (view,) = graph._ruling_view([draft])

    for field in ("id", "category", "description", "affected_element_ids"):
        assert field in view, f"the critic rules on {field!r}"
    assert view["severity"]["likelihood"] and view["severity"]["justification"]
    # Step 5 reads grounds for relevance, so each entry keeps its own branch.
    assert [ground["kind"] for ground in view["grounds"]] == ["quote", "derived-fact"]
    assert view["grounds"][0]["text"] == "Customers log in to the web app"
    assert view["grounds"][1]["flow_id"] == "flow:customer-to-web-app:login"


def test_ruling_view_drops_what_no_verdict_is_reached_from():
    """Mitigations and a Ground's empty branches, gone from the prompt only."""
    draft = sample_draft("S-01", "spoofing")
    assert draft.mitigations, "the fixture must carry one for this to prove anything"

    (view,) = graph._ruling_view([draft])

    assert "mitigations" not in view
    # A quote carries text and source_label; the other four fields are the
    # empty string its own validator requires them to be.
    assert set(view["grounds"][0]) == {"kind", "text", "source_label"}
    assert set(view["grounds"][1]) == {"kind", "flow_id"}


def test_merge_keeps_mitigations_out_of_the_prompt_but_in_the_report():
    """The drafts the report is built from are not the drafts the critic reads."""
    ctx = FakeContext(**analyze_state(spoofing=[sample_draft("S-01", "spoofing")]))

    graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)

    assert "Set HttpOnly" not in ctx.state[graph.STATE_DRAFT_THREATS]
    assert ctx.state[graph.STATE_MERGED_DRAFTS][0]["mitigations"] == [
        {"summary": "Set HttpOnly and Secure on cookies", "detail": ""}
    ]


def test_merge_accepts_a_lane_that_ran_and_found_nothing():
    """Empty is a finding; absent is a failure. The check is on the key."""
    ctx = FakeContext(**analyze_state(spoofing=[sample_draft("S-01", "spoofing")]))

    output = graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)

    assert output == {"draft_count": 1, "unverified_count": 0}


def test_merge_fails_closed_when_a_category_agent_emitted_nothing():
    """The silent lane the report could not have shown.

    A truncated agent writes no output key. Defaulting that to an empty list
    deletes a sixth of the method and finishes green: the critic rules what it
    is handed, and ``build_summary`` omits a category with no threats rather
    than carrying a zero, so nothing downstream can see the hole.
    """
    state = analyze_state(spoofing=[sample_draft("S-01", "spoofing")])
    del state[graph.analyze_state_key("denial-of-service")]
    ctx = FakeContext(**state)

    with pytest.raises(graph.SilentNodeError) as excinfo:
        graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)

    message = str(excinfo.value)
    assert "1 of 6 category agents wrote nothing" in message
    assert "drafts_denial_of_service" in message
    # The message has to name the knob, like every other wall in this service.
    assert "max_output_tokens" in message


def test_merge_fails_closed_on_a_hallucinated_element():
    draft = sample_draft("S-01", affected_element_ids=["process:invented"])
    ctx = FakeContext(**analyze_state(spoofing=[draft]))
    with pytest.raises(DraftJoinError, match="process:invented"):
        graph.merge_drafts(valid_model().model_dump(mode="json"), ctx)


def test_router_accepts_well_formed_critic_output():
    drafts = [sample_draft("S-01")]
    rulings = [sample_ruling("S-01")]
    ctx = FakeContext()
    event = graph.route_review(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "threats": [ruling.model_dump(mode="json") for ruling in rulings]
        },
    )
    assert event.actions.route == graph.ROUTE_ACCEPT
    assert graph.STATE_CRITIC_ISSUES not in ctx.state


def test_router_revises_and_feeds_the_re_ask_prompt():
    """A dropped draft routes to the re-ask, parking the ruling and the issues."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    rulings = [sample_ruling("S-01")]  # T-01 dropped
    ctx = FakeContext()
    event = graph.route_review(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "threats": [ruling.model_dump(mode="json") for ruling in rulings]
        },
    )
    assert event.actions.route == graph.ROUTE_REVISE
    assert "T-01" in ctx.state[graph.STATE_CRITIC_ISSUES]
    assert "S-01" in ctx.state[graph.STATE_PREVIOUS_REVIEW]


def _revise(drafts, rulings):
    """Drive the router to its ``revise`` edge and hand back the parked state."""
    ctx = FakeContext()
    event = graph.route_review(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "threats": [ruling.model_dump(mode="json") for ruling in rulings]
        },
    )
    assert event.actions.route == graph.ROUTE_REVISE
    return ctx.state


def test_the_re_ask_roster_names_every_drafted_id():
    """The covering set the re-ask must reproduce, in canonical order."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

    state = _revise(drafts, [sample_ruling("S-01")])

    assert json.loads(state[graph.STATE_DRAFT_ROSTER]) == ["S-01", "T-01"]


def test_the_re_ask_reads_only_the_drafts_it_cannot_fix_blind():
    """A dropped draft travels in full; one already ruled correctly does not.

    The two drafts carry distinguishable titles, so "the correct one's prose is
    absent" is checked against its own words rather than against a default the
    fixture shares with every other draft.
    """
    drafts = [
        sample_draft("S-01", title="RULED CORRECTLY"),
        sample_draft("T-01", category="tampering", title="THE DROPPED ONE"),
    ]

    state = _revise(drafts, [sample_ruling("S-01")])  # T-01 dropped

    unreconciled = json.loads(state[graph.STATE_UNRECONCILED_DRAFTS])
    assert [draft["id"] for draft in unreconciled] == ["T-01"]
    assert "THE DROPPED ONE" in state[graph.STATE_UNRECONCILED_DRAFTS]
    # S-01 was ruled correctly, so its prose is not re-sent — the roster is the
    # whole of what the re-ask is told about it.
    assert "RULED CORRECTLY" not in state[graph.STATE_UNRECONCILED_DRAFTS]
    assert "S-01" in state[graph.STATE_DRAFT_ROSTER]


def test_an_unresolved_unknown_sends_the_draft_it_hangs_on():
    """Repointing or replacing a needs-info verdict needs the threat's substance."""
    drafts = [sample_draft("S-01")]
    ruling = sample_ruling(
        "S-01",
        verdict=Verdict(
            status="needs-info",
            reason="encryption unstated",
            related_unknowns=[UnknownRef(element_id="store:ghost", attribute="x")],
        ),
    )

    state = _revise(drafts, [ruling])

    unreconciled = json.loads(state[graph.STATE_UNRECONCILED_DRAFTS])
    assert [draft["id"] for draft in unreconciled] == ["S-01"]


def test_a_duplicate_ruling_sends_no_draft_at_all():
    """Dropping one of two rulings on one ID is answerable from the rulings."""
    drafts = [sample_draft("S-01")]

    state = _revise(drafts, [sample_ruling("S-01"), sample_ruling("S-01")])

    assert json.loads(state[graph.STATE_UNRECONCILED_DRAFTS]) == []
    assert json.loads(state[graph.STATE_DRAFT_ROSTER]) == ["S-01"]


def test_the_re_ask_never_sees_a_field_the_critic_could_not_rule_on():
    """``_ruling_view`` narrows here too — one view of a draft, not two."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

    state = _revise(drafts, [sample_ruling("S-01")])

    (dropped,) = json.loads(state[graph.STATE_UNRECONCILED_DRAFTS])
    assert "mitigations" not in dropped


def test_a_critic_that_emitted_nothing_routes_to_the_re_ask():
    """The shape a truncated completion takes: no output key written at all.

    An LLM node that emits no text — a completion cut off at
    ``max_output_tokens``, reasoning tokens included — leaves ``output_key``
    unwritten rather than empty, so the router binds nothing for it. That is the
    critic dropping every draft, and it belongs on the ``revise`` edge the graph
    already has, not on an ADK parameter-binding error naming ``router``.
    """
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    ctx = FakeContext()

    event = graph.route_review(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
    )

    assert event.actions.route == graph.ROUTE_REVISE
    assert "S-01" in ctx.state[graph.STATE_CRITIC_ISSUES]
    assert "T-01" in ctx.state[graph.STATE_CRITIC_ISSUES]


def test_a_silent_re_ask_fails_naming_the_drafts_it_never_ruled():
    """The second silence is terminal, and says what did not reconcile.

    ``critic_failed`` is what a silent critic reaches after its one re-ask, so
    it has to survive the same absent key — this is the node whose whole job is
    reporting why the run died.
    """
    drafts = [sample_draft("S-01")]

    with pytest.raises(CriticOutputError, match="dropped draft 'S-01'"):
        graph.fail_review(
            valid_model().model_dump(mode="json"),
            [draft.model_dump(mode="json") for draft in drafts],
        )


def test_fail_review_raises_the_still_unreconciled_issues():
    """The second look reached the terminal: the job fails, naming what is wrong."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    rulings = [sample_ruling("S-01")]  # T-01 still dropped after the re-ask
    with pytest.raises(CriticOutputError, match="dropped draft 'T-01'"):
        graph.fail_review(
            valid_model().model_dump(mode="json"),
            [draft.model_dump(mode="json") for draft in drafts],
            reviewed_threats={
                "threats": [ruling.model_dump(mode="json") for ruling in rulings]
            },
        )


def test_assemble_splits_rulings_and_builds_the_summary():
    confirmed = sample_ruling("S-01")
    rejected = sample_ruling(
        "T-01",
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
        ctx,
        reviewed_threats={
            "threats": [r.model_dump(mode="json") for r in (confirmed, rejected)]
        },
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
            ctx,
            reviewed_threats={
                "threats": [sample_ruling("S-01").model_dump(mode="json")]
            },
        )
