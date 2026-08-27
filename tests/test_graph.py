"""Wiring checks on the assembled graph, and the deterministic node functions.

The FunctionNode bodies are plain functions, so they are tested directly
against a fake context; the graph itself is checked for its shape — the routes
that exist, the fan-out width, and every LLM node bound to the model its
canonical name resolves to.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

import pytest
from google.adk.agents import LlmAgent
from google.adk.workflow import FunctionNode, JoinNode

from stride_service import graph
from stride_service.binding import NodeBinding
from stride_service.critic import CriticOutputError, DraftJoinError
from stride_service.frameworks import PreconditionError, package_for
from stride_service.frameworks.stride import STRIDE
from stride_service.frameworks.stride.record import (
    STRIDE_CATEGORIES,
    ThreatProposals,
    ThreatRulings,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import LLM_NODES
from stride_service.report import (
    AnalysisMarks,
    FrameworkName,
    InputRef,
    Job,
    NodeRun,
    Report,
    UnknownRef,
    Verdict,
)
from stride_service.resilience import load_resilience
from stride_service.sampling import load_sampling
from stride_service.sources import Source
from stride_service.system_model import SystemModel
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    carrying,
    package_answering,
    repo_package_loaders,
    repo_tiers,
    sample_draft,
    sample_proposal,
    sample_ruling,
    sample_selection,
    valid_model,
)

PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

#: The selection every test here builds a graph for. A graph is built for one
#: selection now, so a test asserting about "the nodes" has to say which.
FRAMEWORKS = DEFAULT_FRAMEWORKS
NODES = graph.FrameworkNodes("stride")
LANES = NODES.lanes
KEYS = graph.GraphKeys.of(FRAMEWORKS)

CRITIC_NODE = NODES.node(graph.CRITIC_ROLE)
RECRITIC_NODE = NODES.node(graph.RECRITIC_ROLE)
ROUTER_NODE = NODES.node(graph.ROUTER_ROLE)
REREVIEW_NODE = NODES.node(graph.REREVIEW_ROLE)
MERGE_NODE = NODES.node(graph.MERGE_ROLE)
JOIN_NODE = NODES.node(graph.JOIN_ROLE)
CRITIC_FAILED_NODE = NODES.node(graph.CRITIC_FAILED_ROLE)
ANALYZE_NODES = tuple(lane.node_name for lane in LANES)
TIER_NODES = graph.tier_node_by_graph_node(FRAMEWORKS)

# Placeholders ADK templates from session state at run time. Anything else
# left in a composed instruction would raise at the first LLM call.
#
# Derived from this graph's own key families rather than listed: which keys
# exist is a function of the selection, so a second framework's per-lane and
# per-framework keys arrive here by the graph carrying them rather than by
# somebody remembering to add twelve more names.
RUNTIME_PLACEHOLDERS = KEYS.rendered


class FakeContext:
    """Stands in for ADK's node context: the node functions only touch state.

    ``Any`` rather than ``object``, matching ADK's own state: a node parks
    validated Pydantic dumps here and reads them back through
    ``model_validate``, so a caller that narrows the value is the normal case.
    """

    def __init__(self, **state: Any) -> None:
        self.state: dict[str, Any] = dict(state)


def analyze_state(**proposals_by_category: list) -> dict[str, object]:
    """State as STRIDE's six lane agents, all of which ran, leave it.

    Proposals, not drafts: a lane agent's node emits this package's own
    :class:`~stride_service.frameworks.stride.record.ThreatProposals`, and
    ``merge_drafts`` resolves each proposal's references into the grounds a
    draft carries. A test seeding drafts here would be asserting against a shape
    no agent can produce.

    ``claims`` rather than ``threats`` is the batch's field name: one prompt body
    serves every framework's agents, so the word it spells has to be one a second
    framework can answer in.

    Every lane gets a key, because ``merge_drafts`` distinguishes a lane agent
    that found nothing (``{"claims": []}``, its key written) from one that
    emitted nothing (no key at all, a truncated completion). A test seeding only
    the lanes it cares about would be asserting against the second, which is a
    failed job.
    """
    return {
        graph.Lane("stride", category).drafts_key: {
            "claims": [
                proposal.model_dump(mode="json")
                for proposal in proposals_by_category.get(
                    category.replace("-", "_"), []
                )
            ]
        }
        for category in STRIDE_CATEGORIES
    }


@pytest.fixture
def package_loaders() -> dict:
    """One loader per selected package, rooted at ``frameworks/<name>/``."""
    return repo_package_loaders(FRAMEWORKS)


@pytest.fixture
def package_loader(package_loaders) -> MarkdownLoader:
    """This install's one package's loader, for the tests about one lane."""
    return package_loaders["stride"]


@pytest.fixture
def prompt_loader() -> MarkdownLoader:
    return MarkdownLoader(PROJECT_ROOT / "prompts")


@pytest.fixture
def domain_loader() -> MarkdownLoader:
    return MarkdownLoader(PROJECT_ROOT / "domains")


def _route_resolver(tiers):
    """Bind nodes to their tier's route string, standing in for the adapter.

    Production binds one ``LiteLlm`` per tier; these tests only need a stable
    per-tier identity on the node, and building real adapters would run the
    credential check against credentials an offline test does not have.
    """
    return lambda node: tiers.resolve_model(node).route


@pytest.fixture
def pipeline(
    prompt_loader: MarkdownLoader,
    domain_loader: MarkdownLoader,
    package_loaders: dict,
):
    tiers = repo_tiers()
    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    return graph.build_pipeline(
        prompt_loader=prompt_loader,
        domain_loader=domain_loader,
        package_loaders=package_loaders,
        frameworks=FRAMEWORKS,
        binding=NodeBinding.from_configs(tiers, sampling, _route_resolver(tiers)),
    )


# --- Driving the per-framework nodes ----------------------------------------
#
# The three nodes below read their framework's artifacts off that framework's
# own state keys rather than taking them as parameters. That is not a style
# choice: ADK binds a FunctionNode's parameters by name, and a name derived per
# framework — ``drafts_stride``, ``reviewed_stride`` — cannot be spelled in a
# signature. So a test that used to pass drafts and rulings parks them first.
#
# One helper per node rather than seeding inline, so each test below still
# states only the artifacts it is about, and so "what this node reads" is
# written once.

DISCLAIMERS = {"stride": "AI-generated STRIDE threat model."}


def _park(
    ctx, drafts=None, reviewed_threats=None, marks=None, coverage=None, retrieved=None
):
    """Park one framework's artifacts where its own nodes read them.

    A parked ruling set stands for a critic the router accepted, so the
    router's own marker is parked beside it.
    """
    for artifact, value in (
        ("drafts", drafts),
        ("reviewed", reviewed_threats),
        ("accepted", reviewed_threats is not None or None),
        ("marks", marks),
        ("coverage", coverage),
        ("retrieved", retrieved),
    ):
        if value is not None:
            ctx.state[NODES.key(artifact)] = value
    return ctx


def assemble(
    model,
    drafts,
    ctx,
    reviewed_threats=None,
    marks=None,
    coverage=None,
    retrieved=None,
    domain_packs=None,
):
    """``assemble_report`` over one framework's parked artifacts.

    ``domain_packs`` stays a parameter because it is the *job's* — one selection
    of shared reference material serves every framework — while ``retrieved``,
    which carries the fired rules and the documents they pulled, is the
    package's and so is parked under the framework's own key.
    """
    _park(ctx, drafts, reviewed_threats, marks, coverage, retrieved)
    return graph.assemble_report(
        model, ctx, KEYS, FRAMEWORKS, DISCLAIMERS, domain_packs=domain_packs
    )


def route(model, drafts, ctx, reviewed_threats=None):
    """``route_review`` over one framework's parked artifacts."""
    _park(ctx, drafts, reviewed_threats)
    return graph.route_review(model, ctx, KEYS, NODES)


def fail(model, drafts, ctx=None, reviewed_threats=None):
    """``fail_review`` over one framework's parked artifacts."""
    ctx = _park(ctx if ctx is not None else FakeContext(), drafts, reviewed_threats)
    return graph.fail_review(model, ctx, KEYS, NODES)


def nodes_by_name(pipeline) -> dict:
    return {node.name: node for node in pipeline.workflow.graph.nodes}


def routed_targets(pipeline, source: str) -> dict:
    return {
        edge.route: edge.to_node.name
        for edge in pipeline.workflow.graph.edges
        if edge.from_node.name == source
    }


def prepare(ctx, model, domain_loader, package_loaders) -> Any:
    """Drive ``prepare`` over one model and hand back what it reported.

    The node routes as well as prepares — it is the run-time precondition gate —
    so it returns an ``Event`` carrying the report rather than the report itself.
    A test about the artifacts reads that report through here; a test about the
    gate needs ``actions.route`` and calls ``graph.prepare_analysis`` directly.
    """
    return graph.prepare_analysis(
        model.model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    ).output


def routed_fan_out(pipeline, source: str) -> dict[Any, set[str]]:
    """Every route out of one node against *all* the nodes it reaches.

    :func:`routed_targets` keeps one target per route, which is enough for the
    routers. ``prepare`` fans one route out to a whole framework's lane agents,
    so a test about it needs the set.
    """
    fanned: dict[Any, set[str]] = {}
    for edge in pipeline.workflow.graph.edges:
        if edge.from_node.name == source:
            fanned.setdefault(edge.route, set()).add(edge.to_node.name)
    return fanned


# --- Wiring -----------------------------------------------------------------


def test_every_canonical_llm_node_is_a_graph_node(pipeline):
    """This graph's LLM node names are tier keys, and they are all in the graph.

    A subset rather than an equality, and the reason is what ``LLM_NODES`` is:
    it names every key the tier config must carry, which is three per framework
    this build can spell. This pipeline is built for one selection, so it
    exercises that selection's keys and no others — an install running STRIDE
    alone still configures ASVS's three, and no graph of its will ever bind
    them.
    """
    assert set(TIER_NODES.values()) <= set(LLM_NODES)
    assert set(TIER_NODES) <= set(nodes_by_name(pipeline))


def test_llm_nodes_bind_their_resolved_model(pipeline):
    tiers = repo_tiers()
    for name, node in nodes_by_name(pipeline).items():
        if not isinstance(node, LlmAgent):
            continue
        selection = tiers.resolve_model(TIER_NODES[name])
        assert node.model == selection.route
        assert pipeline.node_models[name] == node.model


def test_the_recorded_model_carries_its_vendor(pipeline):
    # A bare served identifier carries no vendor, and two vendors can serve the
    # same build — so the prefix is part of the identity, not decoration. The
    # test selection runs a different vendor per tier, which is what lets this
    # also pin that each node records *its own* tier's vendor: against the old
    # Vertex-on-both default, one global prefix would have passed identically.
    tiers = repo_tiers()
    for node in (graph.EXTRACT_NODE, CRITIC_NODE):
        selection = tiers.resolve_model(TIER_NODES[node])
        assert pipeline.node_models[node].startswith(selection.vendor_entry.prefix)

    base, strong = (
        pipeline.node_models[node].partition("/")[0]
        for node in (graph.EXTRACT_NODE, CRITIC_NODE)
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


def _pipeline_with_sampling(
    prompt_loader, domain_loader, package_loaders, sampling_path, resilience
):
    tiers = repo_tiers()
    sampling = load_sampling(sampling_path, env={})
    return graph.build_pipeline(
        prompt_loader=prompt_loader,
        domain_loader=domain_loader,
        package_loaders=package_loaders,
        frameworks=FRAMEWORKS,
        binding=NodeBinding.from_configs(
            tiers, sampling, _route_resolver(tiers), resilience
        ),
    )


def test_each_llm_node_binds_its_own_tier_sampling(
    prompt_loader, domain_loader, package_loaders, tmp_path
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
        _pipeline_with_sampling(
            prompt_loader, domain_loader, package_loaders, sampling_path, resilience
        )
    )

    base_nodes = (graph.EXTRACT_NODE, graph.REPAIR_NODE)
    strong_nodes = (
        CRITIC_NODE,
        RECRITIC_NODE,
        *ANALYZE_NODES,
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
    prompt_loader, domain_loader, package_loaders, tmp_path
):
    """They ride the tier's adapter constructor instead.

    ADK's request map forwards neither, so putting them here would let them
    vanish silently while the fingerprint went on attesting to a seed the
    request never carried.
    """
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    nodes = nodes_by_name(
        _pipeline_with_sampling(
            prompt_loader, domain_loader, package_loaders, sampling_path, None
        )
    )
    config = nodes[graph.EXTRACT_NODE].generate_content_config
    assert config.seed is None
    assert config.thinking_config is None


def test_per_node_sampling_composes_with_the_resilience_timeout(
    prompt_loader, domain_loader, package_loaders, tmp_path
):
    """The node's tier sampling and the resilience timeout ride the one config.

    ``http_options`` stays owned by ``resilience.toml`` — folding
    per-node sampling in must not drop it, nor sampling source it.
    """
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    resilience = load_resilience(PROJECT_ROOT / "config" / "resilience.toml", env={})
    nodes = nodes_by_name(
        _pipeline_with_sampling(
            prompt_loader, domain_loader, package_loaders, sampling_path, resilience
        )
    )

    extract = nodes[graph.EXTRACT_NODE].generate_content_config
    assert extract.http_options.timeout == resilience.timeout_ms
    assert extract.temperature == 0.0  # sampling survives the http_options fold-in


def test_llm_nodes_carry_no_http_options_without_resilience(
    prompt_loader, domain_loader, package_loaders, tmp_path
):
    """Resilience is optional (offline stand-ins); its absence leaves no timeout."""
    sampling_path = tmp_path / "sampling.toml"
    sampling_path.write_text(_DIVERGENT_SAMPLING, encoding="utf-8")
    nodes = nodes_by_name(
        _pipeline_with_sampling(
            prompt_loader, domain_loader, package_loaders, sampling_path, None
        )
    )
    assert nodes[CRITIC_NODE].generate_content_config.http_options is None


def test_deterministic_bookends_carry_no_model(pipeline):
    for name in (
        graph.VALIDATE_NODE,
        graph.REVALIDATE_NODE,
        graph.PREPARE_NODE,
        MERGE_NODE,
        ROUTER_NODE,
        graph.ASSEMBLE_NODE,
        graph.REJECT_NODE,
    ):
        assert isinstance(nodes_by_name(pipeline)[name], FunctionNode)
        assert name not in pipeline.node_models
    assert isinstance(nodes_by_name(pipeline)[JOIN_NODE], JoinNode)


def test_six_category_agents_fan_out_from_prepare_and_join(pipeline):
    """The fan-out is the ``run`` route's; the ``skip`` route bypasses the lanes."""
    fanned = routed_fan_out(pipeline, graph.PREPARE_NODE)
    joined = {
        edge.from_node.name
        for edge in pipeline.workflow.graph.edges
        if edge.to_node.name == JOIN_NODE
    }
    assert fanned[NODES.run_route] == set(ANALYZE_NODES) == joined
    assert fanned[NODES.skip_route] == {graph.ASSEMBLE_NODE}
    assert len(ANALYZE_NODES) == len(STRIDE_CATEGORIES) == 6


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
    assert routed_targets(pipeline, ROUTER_NODE) == {
        graph.ROUTE_ACCEPT: graph.ASSEMBLE_NODE,
        graph.ROUTE_REVISE: RECRITIC_NODE,
    }
    assert routed_targets(pipeline, RECRITIC_NODE) == {None: REREVIEW_NODE}
    assert routed_targets(pipeline, REREVIEW_NODE) == {
        graph.ROUTE_ACCEPT: graph.ASSEMBLE_NODE,
        graph.ROUTE_REVISE: CRITIC_FAILED_NODE,
    }
    # critic_failed is terminal: it raises rather than routing anywhere.
    assert not routed_targets(pipeline, CRITIC_FAILED_NODE)


def test_recritic_runs_on_the_critic_tier_and_emits_the_review_schema(pipeline):
    by_name = nodes_by_name(pipeline)
    recritic = by_name[RECRITIC_NODE]
    assert isinstance(recritic, LlmAgent)
    assert recritic.output_schema is ThreatRulings
    assert recritic.output_key == NODES.key("reviewed")
    assert recritic.model == by_name[CRITIC_NODE].model


def test_llm_nodes_see_no_history(pipeline):
    for node in nodes_by_name(pipeline).values():
        if isinstance(node, LlmAgent):
            assert node.include_contents == "none"


def test_llm_nodes_emit_their_schema(pipeline):
    by_name = nodes_by_name(pipeline)
    assert by_name[graph.EXTRACT_NODE].output_schema is SystemModel
    assert by_name[graph.REPAIR_NODE].output_schema is SystemModel
    assert by_name[CRITIC_NODE].output_schema is ThreatRulings
    for name in ANALYZE_NODES:
        assert by_name[name].output_schema is ThreatProposals


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


def test_analyze_instruction_carries_skill_then_prompt_then_contract_then_exemplars(
    prompt_loader, package_loader
):
    instruction = graph.analyze_instruction(
        package_loader, prompt_loader, STRIDE, graph.Lane("stride", "spoofing")
    )
    scope = instruction.index("# Spoofing")
    role = instruction.index("# Lane Agent")
    contract = instruction.index("# STRIDE Output Contract")
    exemplars = instruction.index("Exemplars")
    assert scope < role < contract < exemplars


def test_category_placeholder_is_filled_at_build_time(prompt_loader, package_loader):
    """Six category agents share one session state, which cannot hold six categories."""
    for category in STRIDE_CATEGORIES:
        instruction = graph.analyze_instruction(
            package_loader, prompt_loader, STRIDE, graph.Lane("stride", category)
        )
        assert "{category}" not in instruction
        assert f"**{category}** agent" in instruction


def test_only_known_state_keys_remain_as_placeholders(pipeline):
    """A stray ``{identifier}`` would be a KeyError at the first LLM call."""
    for node in nodes_by_name(pipeline).values():
        if not isinstance(node, LlmAgent):
            continue
        found = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", node.instruction))
        assert found <= RUNTIME_PLACEHOLDERS, (node.name, found - RUNTIME_PLACEHOLDERS)


# --- Session state ----------------------------------------------------------


class TestSessionState:
    """The two key families, and the rule that keeps them from drifting.

    A rendered key holds bytes a model reads and Python does not. Keeping the
    two copies of an artifact honest used to rest on a comment; it now rests on
    there being no operation that reads a rendered key back.
    """

    def state(self) -> tuple[FakeContext, graph.SessionState]:
        ctx = FakeContext()
        return ctx, KEYS.state(ctx)

    def test_the_two_families_are_disjoint_and_cover_every_key(self):
        assert not KEYS.rendered & KEYS.structured

        declared = {
            value
            for name, value in vars(graph).items()
            if name.startswith("STATE_") and isinstance(value, str)
        }
        assert declared <= KEYS.rendered | KEYS.structured

    def test_a_rendered_key_is_written_and_never_read(self):
        ctx, state = self.state()

        state.prompt(graph.STATE_SYSTEM_MODEL, "rendered")

        assert ctx.state[graph.STATE_SYSTEM_MODEL] == "rendered"
        # The whole of the invariant: there is no method that reads it back.
        with pytest.raises(graph.UndeclaredStateKey):
            state.get(graph.STATE_SYSTEM_MODEL)

    def test_a_structured_key_round_trips(self):
        _, state = self.state()

        state.put(graph.STATE_VALID_MODEL, {"processes": []})

        assert state.get(graph.STATE_VALID_MODEL) == {"processes": []}

    def test_a_structured_key_cannot_be_written_as_a_rendered_one(self):
        _, state = self.state()

        with pytest.raises(graph.UndeclaredStateKey):
            state.prompt(graph.STATE_VALID_MODEL, "rendered")

    def test_a_mistyped_key_fails_at_the_node_that_wrote_it(self):
        """Rather than as a KeyError at the first LLM call that templates it."""
        _, state = self.state()

        with pytest.raises(graph.UndeclaredStateKey, match="candidtaes_spoofing"):
            state.prompt("candidtaes_spoofing", "rendered")

    def test_an_unwritten_structured_key_reads_as_none(self):
        """Which is how a silent node is told from one that wrote an empty value."""
        _, state = self.state()

        assert state.get(graph.STATE_ANALYSIS) is None


def test_no_node_writes_session_state_directly():
    """The interface is only an interface while every node goes through it.

    A source lint, because the alternative is a comment: ADK hands each node a
    context whose ``state`` is a plain dict, so ``ctx.state[key] = value`` stays
    available and silently bypasses both family checks.
    """
    source = (PROJECT_ROOT / "src" / "stride_service" / "graph.py").read_text()
    direct = [
        line.strip()
        for line in source.splitlines()
        if "ctx.state[" in line or "ctx.state.update(" in line
    ]

    assert direct == [], direct


class TestReadingAFinishedRun:
    """One reader for the graph's two terminal shapes, shared by both drivers."""

    def analysis_state(self) -> dict:
        ctx = FakeContext()
        assemble(
            valid_model().model_dump(mode="json"),
            [sample_draft("S-01").model_dump(mode="json")],
            ctx,
            reviewed_threats={
                "claims": [sample_ruling("S-01").model_dump(mode="json")]
            },
        )
        return ctx.state

    def test_an_assembled_run_reads_as_its_analysis(self):
        result = graph.result_of(self.analysis_state())

        assert isinstance(result, graph.Analysis)
        # One block per selected framework, and the claims are the block's:
        # a claim belongs to the framework that ruled it, not to the run.
        assert [block.framework for block in result.analyses] == list(FRAMEWORKS)
        assert [claim.id for claim in result.analyses[0].claims] == ["S-01"]

    def test_a_rejected_run_reads_as_its_issues(self):
        ctx = FakeContext()
        graph.reject_model('[{"code": "schema", "message": "bad"}]', ctx, KEYS)

        result = graph.result_of(ctx.state)

        assert isinstance(result, graph.Rejected)
        assert [issue.code for issue in result.issues] == ["schema"]

    def test_neither_outcome_is_ours_to_own(self):
        """Every path ends at ``assemble`` or ``reject``, so this is a defect."""
        with pytest.raises(graph.GraphProducedNothing):
            graph.result_of({})


# --- Node functions ---------------------------------------------------------


def test_validate_routes_valid_and_publishes_the_model():
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, KEYS, valid_model().model_dump(mode="json"))
    assert event.actions.route == graph.ROUTE_VALID
    assert ctx.state[graph.STATE_VALID_MODEL]["processes"][0]["id"] == "process:web-app"


def test_validate_routes_invalid_and_feeds_the_repair_prompt():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, KEYS, broken)

    assert event.actions.route == graph.ROUTE_INVALID
    assert "process:does-not-exist" in ctx.state[graph.STATE_PREVIOUS_MODEL]
    assert "process:does-not-exist" in ctx.state[graph.STATE_VALIDATION_ISSUES]
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_validate_derives_ids_rather_than_spending_the_repair_pass():
    """An abbreviated ID is not a reason to route to ``repair``."""
    abbreviated = valid_model().model_dump(mode="json")
    abbreviated["processes"][0]["name"] = "Web App Frontend Service"
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, KEYS, abbreviated)

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
    event = graph.validate_extraction(ctx, KEYS, broken)

    assert event.actions.route == graph.ROUTE_INVALID
    assert "process:web-app-frontend-service" in ctx.state[graph.STATE_PREVIOUS_MODEL]
    assert "id-mismatch" not in ctx.state[graph.STATE_VALIDATION_ISSUES]


def test_validate_routes_invalid_on_unparseable_output():
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, KEYS, {"processes": "not a list"})
    assert event.actions.route == graph.ROUTE_INVALID
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_reject_parks_the_issues_for_the_runner():
    ctx = FakeContext()
    graph.reject_model('[{"code": "schema", "message": "bad"}]', ctx, KEYS)
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
        graph.validate_extraction(ctx, KEYS)

    message = str(excinfo.value)
    assert graph.STATE_EXTRACTED_MODEL in message
    assert "max_output_tokens" in message
    assert graph.STATE_VALID_MODEL not in ctx.state


def test_prepare_derives_crossings_rather_than_trusting_them(
    domain_loader, package_loaders
):
    ctx = FakeContext()
    output = prepare(ctx, valid_model(), domain_loader, package_loaders)

    assert output["element_count"] == 7
    assert output["crossing_count"] == 1
    assert output["evidence_count"] == 3
    assert "flow:customer-to-web-app:login" in ctx.state[graph.STATE_BOUNDARY_CROSSINGS]
    assert "process:web-app" in ctx.state[graph.STATE_SYSTEM_MODEL]


def test_prepare_shows_the_agents_the_evidence_catalog_as_references(
    domain_loader, package_loaders
):
    """The closed set an agent picks from, and no more than that.

    Rendered as a table of IDs and what each asserts, never as the resolved
    objects: the fields behind an entry are element and flow IDs of the model in
    the block above, so sending those too would restate what the agent is
    already reading. The table shape is load-bearing rather than cosmetic — a
    JSON array of IDs read as a specimen of the format and got composed from
    (#138).
    """
    ctx = FakeContext()
    graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )

    rendered = ctx.state[graph.STATE_EVIDENCE_CATALOG]
    assert "| `crossing:flow:customer-to-web-app:login` |" in rendered
    assert "crosses a trust boundary" in rendered
    assert not rendered.lstrip().startswith("["), "a list is what agents composed from"
    assert all(isinstance(ref, str) for ref in rendered)


def test_prepare_strips_the_source_fields_from_the_rendered_model(
    domain_loader, package_loaders
):
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

    graph.prepare_analysis(valid, ctx, KEYS, FRAMEWORKS, domain_loader, package_loaders)

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
            spoofing=[sample_proposal("S-01", "spoofing")],
            tampering=[sample_proposal("T-01", "tampering")],
        )
    )
    output = graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)

    # The fan-in names its own framework: a run carries one of these per
    # selection, and a trace that did not say which would read as one merge.
    assert output == {
        "framework": "stride",
        "draft_count": 2,
        "unverified_count": 0,
        "unresolved_mention_count": 0,
    }
    assert [d["id"] for d in ctx.state[NODES.key("drafts")]] == ["S-01", "T-01"]
    assert "S-01" in ctx.state[NODES.key("draft_view")]


def test_merge_parks_every_mark_kind_under_one_key():
    """One key, whatever the fan-in found.

    The marks have one owner, one standing and one policy, so they travel as
    one :class:`~stride_service.report.AnalysisMarks`. A sixth kind is a field
    on that model and no new key here.

    The key is the *framework's*, because the fan-in that produced them is: two
    frameworks rule different drafts against different questions, so a mark
    about one framework's claim has no standing in the other's block.
    """
    ctx = FakeContext(**analyze_state(spoofing=[sample_proposal("S-01", "spoofing")]))

    graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)

    parked = ctx.state[NODES.key("marks")]
    marks = AnalysisMarks.model_validate(parked)
    assert set(parked) == set(AnalysisMarks.model_fields)
    assert marks.unverified_grounds == []
    assert marks.unresolved_mentions == []
    assert marks.unresolved_evidence == []
    assert marks.missing_mitigations == []


def test_the_marks_reach_the_report_through_assemble():
    """What ``merge`` parked lands on the block; the envelope keeps its own.

    A mark goes where the thing it is about goes. ``missing_mitigations`` is
    about one framework's claim, and only STRIDE's block declares the field, so
    the fan-in's marks are filtered by what the block type carries.
    ``shared_element_names`` is about the *shared* model, so it stays on the
    analysis and is derived here rather than carried from any fan-in.
    """
    ctx = FakeContext()
    assemble(
        valid_model().model_dump(mode="json"),
        [sample_draft("S-01").model_dump(mode="json")],
        ctx,
        reviewed_threats={"claims": [sample_ruling("S-01").model_dump(mode="json")]},
        marks={
            "missing_mitigations": [{"claim_id": "S-01"}],
        },
    )

    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    (block,) = analysis.analyses

    assert [m.claim_id for m in block.missing_mitigations] == ["S-01"]
    # Derived from the model this node holds, never carried from the fan-in,
    # so it is present on an analysis assembled from marks that omit it.
    assert analysis.marks.shared_element_names == []


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
    ctx = FakeContext(**analyze_state(spoofing=[sample_proposal("S-01", "spoofing")]))

    graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)

    assert "Set HttpOnly" not in ctx.state[NODES.key("draft_view")]
    assert ctx.state[NODES.key("drafts")][0]["mitigations"] == [
        {"summary": "Set HttpOnly and Secure on cookies", "detail": ""}
    ]


def test_merge_accepts_a_lane_that_ran_and_found_nothing():
    """Empty is a finding; absent is a failure. The check is on the key."""
    ctx = FakeContext(**analyze_state(spoofing=[sample_proposal("S-01", "spoofing")]))

    output = graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)

    assert output == {
        "framework": "stride",
        "draft_count": 1,
        "unverified_count": 0,
        "unresolved_mention_count": 0,
    }


def test_merge_fails_closed_when_a_category_agent_emitted_nothing():
    """The silent lane the report could not have shown.

    A truncated agent writes no output key. Defaulting that to an empty list
    deletes a sixth of the method and finishes green: the critic rules what it
    is handed, and the block summary omits a lane with no claims rather than
    carrying a zero, so nothing downstream can see the hole.

    The message names the framework as well as the count, because a run carries
    one fan-in per selected framework and "1 of 6 lane agents" would not say
    whose.
    """
    state = analyze_state(spoofing=[sample_proposal("S-01", "spoofing")])
    del state[graph.Lane("stride", "denial-of-service").drafts_key]
    ctx = FakeContext(**state)

    with pytest.raises(graph.SilentNodeError) as excinfo:
        graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)

    message = str(excinfo.value)
    assert "1 of 6 stride lane agents wrote nothing" in message
    assert "drafts_stride_denial_of_service" in message
    # The message has to name the knob, like every other wall in this service.
    assert "max_output_tokens" in message


def test_merge_fails_closed_on_a_hallucinated_element():
    proposal = sample_proposal("S-01", affected_element_ids=["process:invented"])
    ctx = FakeContext(**analyze_state(spoofing=[proposal]))
    with pytest.raises(DraftJoinError, match="process:invented"):
        graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)


def test_router_accepts_well_formed_critic_output():
    drafts = [sample_draft("S-01")]
    rulings = [sample_ruling("S-01")]
    ctx = FakeContext()
    event = route(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "claims": [ruling.model_dump(mode="json") for ruling in rulings]
        },
    )
    assert event.actions.route == graph.ROUTE_ACCEPT
    assert NODES.key("critic_issues") not in ctx.state


def test_router_revises_and_feeds_the_re_ask_prompt():
    """A dropped draft routes to the re-ask, parking the ruling and the issues."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    rulings = [sample_ruling("S-01")]  # T-01 dropped
    ctx = FakeContext()
    event = route(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "claims": [ruling.model_dump(mode="json") for ruling in rulings]
        },
    )
    assert event.actions.route == graph.ROUTE_REVISE
    assert "T-01" in ctx.state[NODES.key("critic_issues")]
    assert "S-01" in ctx.state[NODES.key("previous_review")]


def _revise(drafts, rulings):
    """Drive the router to its ``revise`` edge and hand back the parked state."""
    ctx = FakeContext()
    event = route(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "claims": [ruling.model_dump(mode="json") for ruling in rulings]
        },
    )
    assert event.actions.route == graph.ROUTE_REVISE
    return ctx.state


def test_the_re_ask_roster_names_every_drafted_id():
    """The covering set the re-ask must reproduce, in canonical order."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

    state = _revise(drafts, [sample_ruling("S-01")])

    assert json.loads(state[NODES.key("draft_roster")]) == ["S-01", "T-01"]


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

    unreconciled = json.loads(state[NODES.key("unreconciled_drafts")])
    assert [draft["id"] for draft in unreconciled] == ["T-01"]
    assert "THE DROPPED ONE" in state[NODES.key("unreconciled_drafts")]
    # S-01 was ruled correctly, so its prose is not re-sent — the roster is the
    # whole of what the re-ask is told about it.
    assert "RULED CORRECTLY" not in state[NODES.key("unreconciled_drafts")]
    assert "S-01" in state[NODES.key("draft_roster")]


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

    unreconciled = json.loads(state[NODES.key("unreconciled_drafts")])
    assert [draft["id"] for draft in unreconciled] == ["S-01"]


def test_a_duplicate_ruling_sends_no_draft_at_all():
    """Dropping one of two rulings on one ID is answerable from the rulings."""
    drafts = [sample_draft("S-01")]

    state = _revise(drafts, [sample_ruling("S-01"), sample_ruling("S-01")])

    assert json.loads(state[NODES.key("unreconciled_drafts")]) == []
    assert json.loads(state[NODES.key("draft_roster")]) == ["S-01"]


def test_the_re_ask_never_sees_a_field_the_critic_could_not_rule_on():
    """``_ruling_view`` narrows here too — one view of a draft, not two."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

    state = _revise(drafts, [sample_ruling("S-01")])

    (dropped,) = json.loads(state[NODES.key("unreconciled_drafts")])
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

    event = route(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
    )

    assert event.actions.route == graph.ROUTE_REVISE
    assert "S-01" in ctx.state[NODES.key("critic_issues")]
    assert "T-01" in ctx.state[NODES.key("critic_issues")]


def test_a_silent_re_ask_fails_naming_the_drafts_it_never_ruled():
    """The second silence is terminal, and says what did not reconcile.

    ``critic_failed`` is what a silent critic reaches after its one re-ask, so
    it has to survive the same absent key — this is the node whose whole job is
    reporting why the run died.
    """
    drafts = [sample_draft("S-01")]

    with pytest.raises(CriticOutputError, match="dropped draft 'S-01'"):
        fail(
            valid_model().model_dump(mode="json"),
            [draft.model_dump(mode="json") for draft in drafts],
        )


def test_fail_review_raises_the_still_unreconciled_issues():
    """The second look reached the terminal: the job fails, naming what is wrong."""
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    rulings = [sample_ruling("S-01")]  # T-01 still dropped after the re-ask
    with pytest.raises(CriticOutputError, match="dropped draft 'T-01'"):
        fail(
            valid_model().model_dump(mode="json"),
            [draft.model_dump(mode="json") for draft in drafts],
            reviewed_threats={
                "claims": [ruling.model_dump(mode="json") for ruling in rulings]
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
    output = assemble(
        valid_model().model_dump(mode="json"),
        [draft.model_dump(mode="json") for draft in drafts],
        ctx,
        reviewed_threats={
            "claims": [r.model_dump(mode="json") for r in (confirmed, rejected)]
        },
    )

    # The node's own output counts across every block it fanned in, because one
    # of it serves the whole selection; the arrays and the summary are per block.
    assert output == {"claim_count": 1, "rejected_count": 1, "framework_count": 1}
    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    (block,) = analysis.analyses
    assert [claim.id for claim in block.claims] == ["S-01"]
    assert [claim.id for claim in block.rejected_claims] == ["T-01"]
    assert block.summary.claim_count == 1
    assert block.summary.rejected_count == 1


# --- Deterministic candidates, domain packs, coverage -----------------------


def test_prepare_parks_each_lanes_candidates_under_its_own_key(
    domain_loader, package_loaders
):
    """Six agents, one session state: a lane cannot read another's leads."""
    ctx = FakeContext()
    graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )

    for category in STRIDE_CATEGORIES:
        parked = ctx.state[graph.Lane("stride", category).key("candidates")]
        assert f'"lane": "{category}"' in parked
        others = set(STRIDE_CATEGORIES) - {category}
        assert not any(f'"lane": "{other}"' in parked for other in others)


def test_prepare_gives_each_lane_its_own_denominators(domain_loader, package_loaders):
    """The scope line is per lane because the rule counts are.

    Elements and crossings are properties of the model and identical across the
    six, but which rules ran and how many fired is not — and a lane reading
    another's firing count would be told it had leads it never got.
    """
    ctx = FakeContext()
    graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )

    for category in STRIDE_CATEGORIES:
        parked = ctx.state[graph.Lane("stride", category).key("scope")]
        assert f"{category} rules ran" in parked
        assert "7 elements" in parked


def test_prepare_fences_candidate_facts(domain_loader, package_loaders):
    """Facts carry caller words, so they get the model's own fencing."""
    ctx = FakeContext()
    graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )

    parked = ctx.state[graph.Lane("stride", "information-disclosure").key("candidates")]
    assert parked.startswith("```")


def test_prepare_selects_domain_packs_from_the_model(domain_loader, package_loaders):
    ctx = FakeContext()
    output = prepare(ctx, valid_model(), domain_loader, package_loaders)

    # The fixture runs FastAPI over HTTPS against Cloud SQL Postgres.
    assert output["domain_packs"] == ["http-api", "databases"]
    assert "# HTTP and API Security" in ctx.state[graph.STATE_DOMAIN_SKILLS]


def test_prepare_renders_no_pack_block_when_none_is_earned(
    domain_loader, package_loaders
):
    model = valid_model()
    model.processes[0].technology = "a shell script"
    model.data_stores[0].technology = "a flat file"
    for flow in model.data_flows:
        flow.protocol = "local"
        flow.authentication = "a shared unix account"
    ctx = FakeContext()

    graph.prepare_analysis(
        model.model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )

    assert ctx.state[graph.STATE_DOMAIN_SKILLS] == ""


def test_each_agent_is_bound_to_its_own_candidate_key(prompt_loader, package_loader):
    for category in STRIDE_CATEGORIES:
        instruction = graph.analyze_instruction(
            package_loader, prompt_loader, STRIDE, graph.Lane("stride", category)
        )
        key = graph.Lane("stride", category).key("candidates")
        assert f"{{{key}}}" in instruction
        assert "{candidates}" not in instruction


class TestALane:
    """One category agent's names, all of them derived from the category.

    The claim worth testing is not the spelling of any one key. It is that the
    key a lane *writes* and the key its instruction *reads* are the same key,
    for every artifact — which is what putting both behind one declaration buys.
    """

    def test_what_a_lane_writes_is_what_its_prompt_reads(self):
        for lane in LANES:
            written = set(lane.state(dict.fromkeys(graph.LANE_ARTIFACTS, "text")))
            read = {
                binding.strip("{}")
                for placeholder, binding in lane.prompt_bindings.items()
                if placeholder != "lane"
            }
            assert read == written, lane.lane

    def test_no_two_lanes_share_a_key(self):
        keys = [
            lane.key(artifact) for lane in LANES for artifact in graph.LANE_ARTIFACTS
        ]

        assert len(set(keys)) == len(keys)

    def test_a_partial_set_of_artifacts_fails_closed(self):
        """A key nothing wrote would raise at the first LLM call, not here."""
        with pytest.raises(ValueError, match="missing"):
            graph.Lane("stride", "spoofing").state({"candidates": "text"})

    def test_an_unknown_artifact_fails_closed(self):
        with pytest.raises(ValueError, match="unknown"):
            graph.Lane("stride", "spoofing").state(
                {**dict.fromkeys(graph.LANE_ARTIFACTS, "text"), "invented": "text"}
            )

    def test_the_node_name_is_an_identifier(self):
        """Unlike a category, which carries a hyphen ADK cannot take."""
        for lane in LANES:
            assert lane.node_name.isidentifier()
            assert lane.drafts_key.isidentifier()


def test_merge_accounts_for_coverage_over_the_drafts():
    ctx = FakeContext(**analyze_state(spoofing=[sample_proposal("S-01", "spoofing")]))

    graph.merge_drafts(valid_model().model_dump(mode="json"), ctx, KEYS, NODES)

    rows = ctx.state[NODES.key("coverage")]
    assert [row["lane"] for row in rows] == list(STRIDE_CATEGORIES)
    spoofing = next(row for row in rows if row["lane"] == "spoofing")
    assert spoofing["drafts"] == 1
    assert spoofing["elements"] == 7


def test_coverage_reaches_the_report_through_assemble():
    ctx = FakeContext()
    assemble(
        valid_model().model_dump(mode="json"),
        [sample_draft("S-01").model_dump(mode="json")],
        ctx,
        reviewed_threats={"claims": [sample_ruling("S-01").model_dump(mode="json")]},
        coverage=[
            {
                "lane": "spoofing",
                "drafts": 1,
                "rules": 2,
                "rules_fired": 0,
                "candidates": 0,
                "candidates_cited": 0,
                "elements": 7,
                "elements_cited": 2,
                "boundary_crossings": 1,
                "boundary_crossings_cited": 1,
                "unknown_controls": 2,
                "unknown_controls_cited": 1,
            }
        ],
    )

    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    (block,) = analysis.analyses
    assert [row.lane for row in block.coverage] == ["spoofing"]
    assert block.coverage[0].boundary_crossings_cited == 1


def test_assemble_fails_closed_when_the_critic_drops_a_draft():
    drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
    ctx = FakeContext()
    with pytest.raises(CriticOutputError, match="dropped draft 'T-01'"):
        assemble(
            valid_model().model_dump(mode="json"),
            [draft.model_dump(mode="json") for draft in drafts],
            ctx,
            reviewed_threats={
                "claims": [sample_ruling("S-01").model_dump(mode="json")]
            },
        )


# --- Analysis context: what informed the run, never what proves a finding ----


def test_prepare_records_the_packs_and_rules_the_agents_were_given(
    domain_loader, package_loaders
):
    """The record is written where the selection happens, and split by owner.

    Both halves are already computed here to build the prompt; recomputing them
    at the fan-in would be a second derivation that could disagree with what
    the agents actually read, which is the failure the evidence catalog is
    derived-once-and-resolved-against-itself to avoid.

    The packs are the *job's* — one selection of shared reference material
    serves every framework — so they land on one key. The fired rules and the
    documents they pulled are the *package's*, so they land under the
    framework's own key and reach that framework's block.
    """
    ctx = FakeContext()
    graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )

    assert ctx.state[graph.STATE_DOMAIN_PACKS] == ["http-api", "databases"]

    retrieved = ctx.state[NODES.key("retrieved")]
    assert retrieved["fired_rules"] == sorted(set(retrieved["fired_rules"]))
    assert "information-disclosure-store-at-rest-unverified" in retrieved["fired_rules"]


# --- The run-time precondition gate -----------------------------------------
#
# ``prepare`` is the gate: it runs each selected framework's precondition over
# the one **Valid System Model** and routes on the answer. Every test here swaps
# STRIDE's total precondition for one that can refuse
# (:func:`tests.factories.package_answering`), because a gate whose only
# exerciser always answers ``satisfied`` proves nothing — that is the shape of
# the gap this seam was written to close.


def _prepare_over(monkeypatch, result, domain_loader, package_loaders):
    """Drive ``prepare`` with the one carried package answering ``result``."""
    carrying(monkeypatch, package_answering(result))
    ctx = FakeContext()
    event = graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        KEYS,
        FRAMEWORKS,
        domain_loader,
        package_loaders,
    )
    return ctx, event


def test_a_satisfied_precondition_routes_to_the_lane_agents(
    monkeypatch, domain_loader, package_loaders
):
    ctx, event = _prepare_over(monkeypatch, "satisfied", domain_loader, package_loaders)

    assert event.actions.route == [NODES.run_route]
    assert ctx.state[NODES.key("precondition")] == "satisfied"
    assert LANES[0].key("candidates") in ctx.state


@pytest.mark.parametrize("result", ["refuted", "undecidable"])
def test_a_refused_precondition_routes_past_the_lane_agents(
    monkeypatch, domain_loader, package_loaders, result
):
    """Both refusing states stop the lanes, and nothing is derived for them.

    A framework that will not run needs no candidates, no retrieval and no lane
    prompts, so the gate sits where it does: before the work, not beside it.
    """
    ctx, event = _prepare_over(monkeypatch, result, domain_loader, package_loaders)

    assert event.actions.route == [NODES.skip_route]
    assert ctx.state[NODES.key("precondition")] == result
    assert not [key for key in ctx.state if key.startswith("candidates_")]
    assert NODES.key("retrieved") not in ctx.state


def test_the_shared_view_is_still_derived_for_a_refused_framework(
    monkeypatch, domain_loader, package_loaders
):
    """The model, its crossings and the packs are the job's, not a framework's.

    They serve every framework a job selects, so a refusal of one cannot be
    allowed to withhold them from another.
    """
    ctx, _ = _prepare_over(monkeypatch, "refuted", domain_loader, package_loaders)

    assert "process:web-app" in ctx.state[graph.STATE_SYSTEM_MODEL]
    assert ctx.state[graph.STATE_DOMAIN_PACKS] == ["http-api", "databases"]


def test_a_precondition_the_contract_cannot_read_fails_the_run(
    monkeypatch, domain_loader, package_loaders
):
    """A build defect, and it must not be read as a refusal.

    Refusing the framework here would drop the whole analysis the caller asked
    for and leave them no sign of it, so the gate raises instead — after one
    extraction, which is the honest cost of a check that cannot run earlier.
    """
    with pytest.raises(PreconditionError) as caught:
        _prepare_over(monkeypatch, "maybe", domain_loader, package_loaders)

    assert "'maybe'" in str(caught.value)


def test_a_refused_framework_still_produces_a_block_that_states_the_reason(
    monkeypatch,
):
    """The envelope needs the block; the reader needs to know why it is empty.

    ``analyses`` must answer the job's frameworks in order with none dropped, so
    a refused framework cannot vanish. What it carries instead of claims is a
    ``scope`` list naming every lane that did not run.
    """
    carrying(monkeypatch, package_answering("refuted"))
    ctx = FakeContext(**{NODES.key("precondition"): "refuted"})

    graph.assemble_report(
        valid_model().model_dump(mode="json"), ctx, KEYS, FRAMEWORKS, DISCLAIMERS
    )
    block = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS]).analyses[0]

    assert block.framework == "stride"
    assert block.claims == []
    assert [entry.unit for entry in block.scope] == list(STRIDE.lanes)
    assert all(entry.state == "not-applicable" for entry in block.scope)
    assert "does not apply" in block.scope[0].reason


def test_the_two_refusing_states_state_different_reasons(monkeypatch):
    """Never collapsed, because the remedy differs.

    ``refuted`` says do not name this framework for this system. ``undecidable``
    says the input never said, which the submitter answers by submitting more.
    """
    reasons = {}
    for result in ("refuted", "undecidable"):
        carrying(monkeypatch, package_answering(result))
        ctx = FakeContext(**{NODES.key("precondition"): result})
        graph.assemble_report(
            valid_model().model_dump(mode="json"), ctx, KEYS, FRAMEWORKS, DISCLAIMERS
        )
        analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
        reasons[result] = analysis.analyses[0].scope[0].reason

    assert reasons["refuted"] != reasons["undecidable"]
    assert "never says" in reasons["undecidable"]


# --- Two frameworks, one of them refused -------------------------------------
#
# The tests above swap STRIDE's precondition for one that can refuse, because
# until ASVS landed there was no shipped package that could. These run the real
# pair: ASVS refuses a system whose flows carry no web protocol, STRIDE's is
# total, and one job asks for both.

BOTH: tuple[FrameworkName, ...] = ("asvs", "stride")
BOTH_KEYS = graph.GraphKeys.of(BOTH)
BOTH_DISCLAIMERS = {
    "asvs": "AI-generated ASVS applicability analysis.",
    "stride": "AI-generated STRIDE threat model.",
}
ASVS_OPTIONS = {graph.STATE_FRAMEWORK_OPTIONS: {"asvs": {"level": 1}}}


def non_web_model():
    """The shared model with every process presenting something other than the web.

    Every ``interface_kind`` is *stated*, which is what makes ASVS answer
    ``refuted`` rather than ``undecidable``: the input said, and what it said was
    not a web application. The flows are given a stated non-web protocol too, so
    the model is decided whichever half of the precondition reads it.
    """
    model = valid_model()
    return model.model_copy(
        update={
            "processes": [
                process.model_copy(update={"interface_kind": "non-web"})
                for process in model.processes
            ],
            "data_flows": [
                flow.model_copy(update={"protocol": "AMQP"})
                for flow in model.data_flows
            ],
        }
    )


def test_a_two_framework_graph_builds(prompt_loader, domain_loader):
    """The topology holds for two, and the refusal route is what made it not.

    Every framework's ``skip`` reaches ``assemble``, so a route per framework
    declared N copies of one ``prepare -> assemble`` edge — which ADK refuses
    outright. A build carrying one framework could never hit it, which is why the
    second framework is what found it.
    """
    tiers = repo_tiers()
    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    pipeline = graph.build_pipeline(
        prompt_loader=prompt_loader,
        domain_loader=domain_loader,
        package_loaders=repo_package_loaders(BOTH),
        frameworks=BOTH,
        binding=NodeBinding.from_configs(tiers, sampling, _route_resolver(tiers)),
    )

    names = set(nodes_by_name(pipeline))
    assert {"analyze_asvs_webrtc", "analyze_stride_spoofing"} <= names
    assert {"critic_asvs", "critic_stride"} <= names
    fanned = routed_fan_out(pipeline, graph.PREPARE_NODE)
    assert fanned[graph.SKIP_ROUTE] == {graph.ASSEMBLE_NODE}
    assert len(fanned["run_asvs"]) == len(package_for("asvs").lanes)


def test_one_framework_s_refusal_routes_only_its_own_half_past_the_lanes(
    domain_loader,
):
    """A partial refusal is expressible: one route per framework, N edges fire."""
    ctx = FakeContext(**ASVS_OPTIONS)

    event = graph.prepare_analysis(
        non_web_model().model_dump(mode="json"),
        ctx,
        BOTH_KEYS,
        BOTH,
        domain_loader,
        repo_package_loaders(BOTH),
    )

    assert set(event.actions.route) == {
        graph.FrameworkNodes("asvs").skip_route,
        graph.FrameworkNodes("stride").run_route,
    }
    assert ctx.state[graph.FrameworkNodes("asvs").key("precondition")] == "refuted"
    assert not [key for key in ctx.state if key.startswith("candidates_asvs_")]
    assert LANES[0].key("candidates") in ctx.state


def test_a_refusal_does_not_cost_the_other_framework_its_answer(domain_loader):
    """One framework's precondition does not spend another framework's judgement.

    The whole point of the gate sitting before the fan-out rather than inside
    it: a job naming both against a non-web system still gets its STRIDE
    analysis, and the ASVS block says why it has none.
    """
    model = non_web_model().model_dump(mode="json")
    ctx = FakeContext(**ASVS_OPTIONS)
    graph.prepare_analysis(
        model, ctx, BOTH_KEYS, BOTH, domain_loader, repo_package_loaders(BOTH)
    )
    _park(
        ctx,
        [sample_draft("S-01").model_dump(mode="json")],
        {"claims": [sample_ruling("S-01").model_dump(mode="json")]},
    )
    graph.assemble_report(model, ctx, BOTH_KEYS, BOTH, BOTH_DISCLAIMERS)

    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    asvs_block, stride_block = analysis.analyses

    assert [block.framework for block in analysis.analyses] == list(BOTH)
    assert [claim.id for claim in stride_block.claims] == ["S-01"]
    assert asvs_block.claims == []
    assert len(asvs_block.scope) == 70
    assert asvs_block.scope[0].unit == "V1.2.1"
    assert "does not apply" in asvs_block.scope[0].reason


def test_the_refused_block_records_the_level_the_job_asked_for(domain_loader):
    """The option reaches the block, so a reader can tell which set was ruled on.

    It is job data rather than graph shape — two jobs with different levels share
    one built graph — so it arrives in state and the block declares a field for
    it.
    """
    model = non_web_model().model_dump(mode="json")
    ctx = FakeContext(**{graph.STATE_FRAMEWORK_OPTIONS: {"asvs": {"level": 2}}})
    graph.prepare_analysis(
        model, ctx, BOTH_KEYS, BOTH, domain_loader, repo_package_loaders(BOTH)
    )
    graph.assemble_report(model, ctx, BOTH_KEYS, BOTH, BOTH_DISCLAIMERS)

    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])

    assert analysis.analyses[0].level == 2
    assert len(analysis.analyses[0].scope) == 253


def test_assemble_runs_once_per_framework_and_the_last_run_is_the_whole_report(
    domain_loader,
):
    """The topology's own cost, pinned rather than assumed.

    ADK schedules a ``FunctionNode`` on each incoming trigger, so two frameworks
    trigger ``assemble`` twice: the earlier run builds the unfinished
    framework's block from keys nothing wrote. What matters is that the later run
    overwrites it, and that is what this holds. A join node is the wrong fix —
    it waits for every predecessor, and a refused framework's subgraph never
    completes.
    """
    model = valid_model().model_dump(mode="json")
    ctx = FakeContext(**ASVS_OPTIONS)
    graph.prepare_analysis(
        model, ctx, BOTH_KEYS, BOTH, domain_loader, repo_package_loaders(BOTH)
    )

    # The first trigger: ASVS's half has landed, STRIDE's has not.
    graph.assemble_report(model, ctx, BOTH_KEYS, BOTH, BOTH_DISCLAIMERS)
    early = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    assert early.analyses[1].claims == []

    # The second: every framework's artifacts are parked, so this is the report.
    _park(ctx, [sample_draft("S-01").model_dump(mode="json")], None)
    _park(
        ctx,
        reviewed_threats={"claims": [sample_ruling("S-01").model_dump(mode="json")]},
    )
    graph.assemble_report(model, ctx, BOTH_KEYS, BOTH, BOTH_DISCLAIMERS)
    final = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])

    assert [block.framework for block in final.analyses] == list(BOTH)
    assert [claim.id for claim in final.analyses[1].claims] == ["S-01"]


@pytest.mark.parametrize(
    "reviewed",
    [
        pytest.param(None, id="critic-still-running"),
        pytest.param({"claims": []}, id="re-ask-replacing-a-malformed-ruling"),
    ],
)
def test_an_early_assemble_run_reads_an_unaccepted_framework_as_unfinished(
    domain_loader, reviewed
):
    """The earlier trigger can land while a framework is still running.

    Two windows: its drafts are parked and its critic has not written
    ``reviewed`` yet, or the critic wrote a malformed ruling set that the router
    sent to the re-ask, and ``reviewed`` still holds it. Reading the drafts
    against either would raise ``CriticOutputError`` for every draft and fail
    a job whose critic was still running. A framework its own router did not
    accept reads as unfinished, and the later run builds its block.
    """
    model = valid_model().model_dump(mode="json")
    ctx = FakeContext(**ASVS_OPTIONS)
    graph.prepare_analysis(
        model, ctx, BOTH_KEYS, BOTH, domain_loader, repo_package_loaders(BOTH)
    )
    ctx.state[NODES.key("drafts")] = [sample_draft("S-01").model_dump(mode="json")]
    if reviewed is not None:
        ctx.state[NODES.key("reviewed")] = reviewed

    graph.assemble_report(model, ctx, BOTH_KEYS, BOTH, BOTH_DISCLAIMERS)
    early = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    assert early.analyses[1].claims == []

    event = route(
        model, None, ctx, {"claims": [sample_ruling("S-01").model_dump(mode="json")]}
    )
    assert event.actions.route == graph.ROUTE_ACCEPT
    graph.assemble_report(model, ctx, BOTH_KEYS, BOTH, BOTH_DISCLAIMERS)
    final = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    assert [claim.id for claim in final.analyses[1].claims] == ["S-01"]


def test_prepare_refuses_a_selection_whose_options_are_missing(domain_loader):
    """Refused before the lane agents run, not after.

    ``prepare`` is the earliest node holding the selection and its options
    together. Letting the run continue would tell 17 ASVS lane agents to rule at
    a level nobody supplied and then fail at assembly, with every call paid for.
    """
    with pytest.raises(graph.MissingFrameworkOptions) as caught:
        graph.prepare_analysis(
            valid_model().model_dump(mode="json"),
            FakeContext(),
            BOTH_KEYS,
            BOTH,
            domain_loader,
            repo_package_loaders(BOTH),
        )

    assert "asvs: level" in str(caught.value)


def test_a_driver_that_seeds_no_options_is_told_which_framework_needs_what():
    """A driver contract, and it must not surface as a Pydantic error.

    Options are job data, so a driver seeds them per run. A framework whose
    package declares a required option cannot have its block built without one,
    because no package field carries a default. Without this check the failure
    arrived from inside a scope helper naming a model rather than the framework,
    the option or the key to seed.
    """
    with pytest.raises(graph.MissingFrameworkOptions) as caught:
        graph.assemble_report(
            valid_model().model_dump(mode="json"),
            FakeContext(),
            BOTH_KEYS,
            BOTH,
            BOTH_DISCLAIMERS,
        )

    message = str(caught.value)
    assert "asvs: level" in message
    assert graph.STATE_FRAMEWORK_OPTIONS in message


def test_a_framework_needing_no_options_needs_nothing_seeded():
    """The STRIDE-only path is unchanged: an empty options model validates."""
    ctx = FakeContext()

    graph.assemble_report(
        valid_model().model_dump(mode="json"), ctx, KEYS, FRAMEWORKS, DISCLAIMERS
    )

    assert graph.STATE_ANALYSIS in ctx.state


def test_a_lane_agent_is_told_which_level_its_job_asked_for(domain_loader):
    """A framework whose options select what applies has to tell its agents.

    Rendered neutrally, from the package's own field names: this seam knows no
    package, and a framework with no options renders nothing extra.
    """
    ctx = FakeContext(**ASVS_OPTIONS)
    graph.prepare_analysis(
        valid_model().model_dump(mode="json"),
        ctx,
        BOTH_KEYS,
        BOTH,
        domain_loader,
        repo_package_loaders(BOTH),
    )

    asvs_scope = ctx.state[graph.Lane("asvs", "cryptography").key("scope")]
    stride_scope = ctx.state[LANES[0].key("scope")]

    assert "asvs with level 1" in asvs_scope
    assert "asked for" not in stride_scope


def test_a_graph_entered_past_the_gate_reports_nothing_out_of_scope():
    """The analysis eval mode seeds a blessed model and runs no gate.

    An absent answer is not a refusal, and filling ``scope`` there would describe
    one that never happened.
    """
    ctx = FakeContext()

    graph.assemble_report(
        valid_model().model_dump(mode="json"), ctx, KEYS, FRAMEWORKS, DISCLAIMERS
    )
    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])

    assert analysis.analyses[0].scope == []


def test_the_context_reaches_the_report_through_assemble():
    """Each half arrives on the side that owns it, from the key that owns it."""
    ctx = FakeContext()
    assemble(
        valid_model().model_dump(mode="json"),
        [sample_draft("S-01").model_dump(mode="json")],
        ctx,
        reviewed_threats={"claims": [sample_ruling("S-01").model_dump(mode="json")]},
        domain_packs=["http-api"],
        retrieved={
            "fired_rules": ["spoofing-unverified-boundary-auth"],
            "knowledge_docs": ["notes/spoofing.md"],
        },
    )

    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    (block,) = analysis.analyses

    assert analysis.domain_packs == ["http-api"]
    assert block.fired_rules == ["spoofing-unverified-boundary-auth"]
    assert block.knowledge_docs == ["notes/spoofing.md"]


def test_a_graph_entered_past_prepare_records_an_empty_context():
    """Absent, not fabricated.

    The analysis eval mode seeds a blessed model and enters at ``prepare``; a
    graph driven from a state that never ran it was given no packs and no
    candidates. An empty record says exactly that, where a missing block would
    leave a reader guessing whether the run had them.
    """
    ctx = FakeContext()
    assemble(
        valid_model().model_dump(mode="json"),
        [sample_draft("S-01").model_dump(mode="json")],
        ctx,
        reviewed_threats={"claims": [sample_ruling("S-01").model_dump(mode="json")]},
    )

    analysis = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])
    context = analysis.context("0" * 64)
    (block,) = analysis.analyses

    assert context.domain_packs == []
    assert block.fired_rules == []
    assert block.knowledge_docs == []


class TestIntoReport:
    """The one mapping from an :class:`Analysis` to a report.

    Both drivers — the service over a job, the eval harness over a corpus case
    — reach the report through this method, so what these tests hold is what
    used to be held by two field lists agreeing with each other.
    """

    def analysis(self) -> graph.Analysis:
        ctx = FakeContext()
        assemble(
            valid_model().model_dump(mode="json"),
            [sample_draft("S-01").model_dump(mode="json")],
            ctx,
            reviewed_threats={
                "claims": [sample_ruling("S-01").model_dump(mode="json")]
            },
            domain_packs=["http-api"],
            retrieved={"fired_rules": ["r-01"], "knowledge_docs": []},
        )
        return graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS])

    def report(self, pipeline):
        analysis = self.analysis()
        return analysis.into_report(
            job=Job(
                id="job-01",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                completed_at=datetime(2026, 1, 1, tzinfo=UTC),
                # The envelope checks the blocks answer this list, in order, so
                # the job a report is built for has to name the selection the
                # graph was built for.
                frameworks=sample_selection(FRAMEWORKS),
            ),
            input_ref=InputRef.of(
                system_name="Test system",
                sources=[Source(kind="description", label="brief", text="text")],
            ),
            nodes=[NodeRun(node="extract", duration_ms=1)],
            pipeline=pipeline,
        )

    def test_every_shared_field_is_carried(self, pipeline):
        """A field on both sides that ``into_report`` forgets fails here.

        Mechanical rather than enumerated, because an enumerated list is the
        second field list this method exists to remove. Any name an
        ``Analysis`` and a ``Report`` both carry must arrive unchanged,
        and the marks count as the ``Analysis``'s own: it holds them on one
        field, and the report spreads them across five.
        """
        analysis = self.analysis()
        report = self.report(pipeline)
        held = {field.name for field in fields(analysis)} | set(
            AnalysisMarks.model_fields
        )
        shared = held & set(Report.model_fields)

        assert shared, "Analysis and Report share no field names"
        for name in sorted(shared):
            holder = analysis if hasattr(analysis, name) else analysis.marks
            assert getattr(report, name) == getattr(holder, name), name

    def test_the_driver_supplies_only_what_the_graph_cannot_know(self, pipeline):
        report = self.report(pipeline)

        assert report.job.id == "job-01"
        assert report.input.system_name == "Test system"
        assert [node.node for node in report.nodes] == ["extract"]
        assert set(report.sampling) == set(pipeline.tier_sampling)

    def test_the_context_joins_the_built_graph_s_digest(self, pipeline):
        report = self.report(pipeline)

        assert report.analysis_context is not None
        assert report.analysis_context.instruction_sha256 == pipeline.instruction_sha256
        assert report.analysis_context.domain_packs == ["http-api"]


class TestTheInstructionDigest:
    """The half of "what produced this report" a fingerprint cannot reach.

    A generation identity attests to the served model and the decoding params.
    Two runs can share one and have been told completely different things —
    the prompts, the category skills and the rubric are not in that hash and
    cannot be, since they are known at build time and the served build is not.
    """

    def nodes(self, **instructions: str):
        return [
            LlmAgent(name=name, model="fake", instruction=text)
            for name, text in instructions.items()
        ]

    def test_the_same_instructions_digest_the_same(self):
        first = graph.instruction_digest(self.nodes(extract="a", critic="b"))
        second = graph.instruction_digest(self.nodes(extract="a", critic="b"))
        assert first == second

    def test_editing_one_instruction_moves_it(self):
        before = graph.instruction_digest(self.nodes(extract="a", critic="b"))
        after = graph.instruction_digest(self.nodes(extract="a", critic="b "))
        assert before != after

    def test_swapping_two_nodes_instructions_moves_it(self):
        """Node identity is part of the payload, not only the text.

        A graph that handed the critic the extraction prompt and vice versa
        would otherwise digest identically to the correct one, since the same
        bytes are present either way.
        """
        forwards = graph.instruction_digest(self.nodes(extract="a", critic="b"))
        crossed = graph.instruction_digest(self.nodes(extract="b", critic="a"))
        assert forwards != crossed

    def test_the_built_graph_digests_its_own_nodes(self, pipeline):
        assert re.fullmatch(r"[0-9a-f]{64}", pipeline.instruction_sha256)

    def test_two_builds_of_the_same_configuration_agree(
        self, pipeline, prompt_loader, domain_loader, package_loaders
    ):
        """The property a comparison across runs depends on.

        Instructions are composed at build time from the same files, so a
        digest that moved between two builds of one checkout would make every
        cross-run comparison meaningless — including the one this exists for.
        """
        tiers = repo_tiers()
        rebuilt = graph.build_pipeline(
            prompt_loader=prompt_loader,
            domain_loader=domain_loader,
            package_loaders=package_loaders,
            frameworks=FRAMEWORKS,
            binding=NodeBinding.from_configs(
                tiers,
                load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={}),
                _route_resolver(tiers),
            ),
        )
        assert rebuilt.instruction_sha256 == pipeline.instruction_sha256

    def test_it_carries_no_submitted_text(self, pipeline):
        """Job bytes are hashed by ``input.source_sha256`` and only there.

        The instructions are digested with their ``{placeholders}`` unexpanded,
        so this identifies the repo-authored text and nothing a submitter
        wrote — which is what makes it safe to publish beside a report.
        """
        instructions = "".join(
            node.instruction
            for node in pipeline.workflow.graph.nodes
            if isinstance(node, LlmAgent)
        )
        assert "{system_model}" in instructions
        assert "{input_text}" in instructions
