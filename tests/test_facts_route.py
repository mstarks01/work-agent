"""The facts-first head: what it builds, what it refuses, and what it writes.

#1003 arm B replaces ``extract`` with ``facts`` and ``resolve`` and changes
nothing after them. Three groups say so. The builder group holds the topology —
which nodes exist, which tier they resolve on, and the three combinations the
builder refuses rather than composing a graph nobody asked for. The node group
drives ``resolve`` over a bundle and checks the three keys it writes, including
that what it writes at :data:`~analysis_service.graph.STATE_EXTRACTED_MODEL`
passes the gate ``extract``'s emission passes. The deployment group holds the
variable to one route and the report to recording which route ran.
"""

from typing import Any

import pytest

from analysis_service import graph
from analysis_service.assertions import CatalogProposal, QuoteProposal
from analysis_service.binding import NodeBinding
from analysis_service.compact import COMPACT_FORMAT
from analysis_service.deployment import FACTS_FIRST_EXTRACTION_VAR, Deployment
from analysis_service.factbundle import (
    EmittedFactBundle,
    FactProposal,
    MentionProposal,
    SourceFactBundle,
)
from analysis_service.graph import FACTS_FIRST, GRAPH_FIRST
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import compose_facts_prompt
from analysis_service.sampling import load_sampling
from analysis_service.validation import validate
from tests.factories import PROJECT_ROOT, repo_package_loaders, repo_tiers
from tests.test_deployment import VERTEX_ENV
from tests.test_graph import (
    FRAMEWORKS,
    KEYS,
    FakeContext,
    _route_resolver,
    nodes_by_name,
)

LABEL = "Architecture note"
NOTE = "A worker in the core network reads jobs from a queue."


def quote(text: str) -> list[QuoteProposal]:
    """One proposed quote against the note every bundle here reads."""
    return [QuoteProposal(source_label=LABEL, quote=text)]


def bundle() -> SourceFactBundle:
    """The smallest bundle that resolves to a model the gate accepts."""
    return SourceFactBundle(
        mentions=[
            MentionProposal(
                handle="z1",
                text="core network",
                roles=["network-zone"],
                quotes=quote("the core network"),
            ),
            MentionProposal(
                handle="m2",
                text="queue",
                roles=["store"],
                quotes=quote("from a queue"),
            ),
        ],
        facts=[
            FactProposal(
                handle="p2",
                subject_kind="mention",
                subject="m2",
                predicate="network-membership",
                value="z1",
                basis="stated",
                quotes=quote(NOTE),
            )
        ],
    )


#: The three Markdown roots, built once: every test here reads the repo's own.
PROMPTS = MarkdownLoader(PROJECT_ROOT / "prompts")
DOMAINS = MarkdownLoader(PROJECT_ROOT / "domains")


def built(**overrides: Any):
    """One pipeline, built the way the production deployment builds it."""
    tiers = repo_tiers()
    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    return graph.build_pipeline(
        prompt_loader=PROMPTS,
        domain_loader=DOMAINS,
        package_loaders=repo_package_loaders(FRAMEWORKS),
        frameworks=FRAMEWORKS,
        binding=NodeBinding.from_configs(tiers, sampling, _route_resolver(tiers)),
        **overrides,
    )


class TestTheBuiltGraph:
    """Which nodes a facts-first graph carries, and which it does not."""

    def test_the_head_is_two_nodes_and_the_rest_is_unchanged(self) -> None:
        pipeline = built(
            extraction_strategy=FACTS_FIRST,
        )
        nodes = nodes_by_name(pipeline)

        assert graph.FACTS_NODE in nodes
        assert graph.RESOLVE_NODE in nodes
        assert graph.EXTRACT_NODE not in nodes
        # The emission, not the bundle: a model is asked for rows and never
        # for the schema's own version.
        assert nodes[graph.FACTS_NODE].output_schema is EmittedFactBundle
        # The same gate, the same bounded repair and the same rejection.
        for name in (graph.VALIDATE_NODE, graph.REPAIR_NODE, graph.REJECT_NODE):
            assert name in nodes

    def test_the_default_graph_is_the_one_it_always_was(self) -> None:
        pipeline = built()
        nodes = nodes_by_name(pipeline)

        assert pipeline.extraction_strategy == GRAPH_FIRST
        assert graph.EXTRACT_NODE in nodes
        assert graph.FACTS_NODE not in nodes
        assert graph.RESOLVE_NODE not in nodes

    def test_the_reading_node_resolves_on_the_extraction_tier(self) -> None:
        """One tier row for both strategies, so no operator keeps two in step."""
        tiers = graph.tier_node_by_graph_node(FRAMEWORKS)
        assert tiers[graph.FACTS_NODE] == tiers[graph.EXTRACT_NODE]

    def test_the_prompt_carries_both_rendered_tables(self) -> None:
        composed = compose_facts_prompt(PROMPTS)
        assert "## The roles" in composed
        assert "## The predicates" in composed
        assert "`network-zone`" in composed
        assert "`network-membership`" in composed

    def test_the_extraction_eval_entry_reads_facts_too(self) -> None:
        pipeline = built(
            entry=graph.ENTRY_EXTRACT_ONLY,
            extraction_strategy=FACTS_FIRST,
        )
        assert graph.FACTS_NODE in nodes_by_name(pipeline)
        assert graph.EXTRACT_NODE not in nodes_by_name(pipeline)
        assert set(pipeline.node_models) == {graph.FACTS_NODE}


class TestWhatTheBuilderRefuses:
    """Three combinations that would compose a graph nobody asked for."""

    @pytest.mark.parametrize("entry", (graph.ENTRY_PREPARE, graph.ENTRY_ASSERT_ONLY))
    def test_an_entry_that_extracts_nothing(self, entry) -> None:
        with pytest.raises(ValueError, match="builds no extraction node"):
            built(
                entry=entry,
                extraction_strategy=FACTS_FIRST,
            )

    def test_a_transport_the_strategy_does_not_write(self) -> None:
        with pytest.raises(ValueError, match="no 'compact-v4' transport"):
            built(
                extraction_format=COMPACT_FORMAT,
                extraction_strategy=FACTS_FIRST,
            )

    def test_an_assertion_pass_appended_to_it(self) -> None:
        """#1003: no second assertion extraction is silently appended to B."""
        with pytest.raises(ValueError, match="extract one thing twice"):
            built(
                extraction_strategy=FACTS_FIRST,
                assertions=True,
            )

    def test_a_strategy_nobody_registered(self) -> None:
        with pytest.raises(ValueError, match="unknown extraction strategy"):
            built(
                extraction_strategy="bundle-first",
            )


class TestTheResolveNode:
    """What ``resolve`` writes, and that the gate reads it unchanged."""

    def driven(self, held: SourceFactBundle):
        """Run the node over one bundle and hand back the context it wrote."""
        ctx = FakeContext(
            source_facts=held.model_dump(mode="json"), source_texts={LABEL: NOTE}
        )
        report = graph.resolve_source_facts(
            ctx, KEYS, ctx.state["source_facts"], ctx.state["source_texts"]
        )
        return ctx, report

    def test_the_model_it_writes_passes_the_gate(self) -> None:
        ctx, report = self.driven(bundle())
        written = ctx.state[graph.STATE_EXTRACTED_MODEL]
        model, issues = graph.parse_extraction(
            written, graph.FULL_FORMAT, sources={LABEL: NOTE}
        )
        assert issues == []
        assert model is not None
        assert validate(model, sources={LABEL: NOTE}) == []
        assert report["elements"] == 2

    def test_it_writes_a_proposal_at_the_key_prepare_reads(self) -> None:
        """One seam: ``prepare`` resolves this exactly as it resolves ``assert``."""
        ctx, _ = self.driven(bundle())
        proposal = CatalogProposal.model_validate(
            ctx.state[graph.STATE_ASSERTION_PROPOSAL]
        )
        assert [row.predicate for row in proposal.assertions] == ["network-membership"]
        assert proposal.assertions[0].subject == "store:queue"

    def test_it_writes_a_disposition_for_every_input_row(self) -> None:
        held = bundle()
        ctx, report = self.driven(held)
        rows = ctx.state[graph.STATE_BUNDLE_DISPOSITIONS]
        assert len(rows) == len(held.mentions) + len(held.facts)
        assert report["gaps"] == 0

    def test_a_silent_node_is_named(self) -> None:
        ctx = FakeContext(source_texts={LABEL: NOTE})
        with pytest.raises(graph.SilentNodeError, match="source_facts"):
            graph.resolve_source_facts(ctx, KEYS, None, ctx.state["source_texts"])


class TestTheDeployment:
    """One variable, and the report that says which route ran."""

    def test_an_install_that_sets_nothing_reads_graph_first(self) -> None:
        deployment = Deployment.from_env(env=VERTEX_ENV)
        pipeline = deployment.pipeline(FRAMEWORKS)

        assert deployment.extraction_strategy == GRAPH_FIRST
        assert pipeline.extraction_strategy == GRAPH_FIRST
        assert graph.FACTS_NODE not in nodes_by_name(pipeline)

    def test_the_variable_selects_the_facts_first_head(self) -> None:
        env = VERTEX_ENV | {FACTS_FIRST_EXTRACTION_VAR: "true"}
        deployment = Deployment.from_env(env=env)
        pipeline = deployment.pipeline(FRAMEWORKS)

        assert pipeline.extraction_strategy == FACTS_FIRST
        assert graph.FACTS_NODE in nodes_by_name(pipeline)

    def test_both_variables_together_append_no_assertion_pass(self) -> None:
        """The builder refuses the pair, so the deployment must not ask for it."""
        env = VERTEX_ENV | {
            FACTS_FIRST_EXTRACTION_VAR: "true",
            "ANALYSIS_ASSERTIONS": "true",
        }
        pipeline = Deployment.from_env(env=env).pipeline(FRAMEWORKS)

        assert pipeline.extraction_strategy == FACTS_FIRST
        assert graph.ASSERT_NODE not in nodes_by_name(pipeline)
