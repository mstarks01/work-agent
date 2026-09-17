"""The head-only mode: one arm's head, driven to a catalog and stopped.

#1003's primary endpoint is scored on the catalog, and no lane writes one, so
the comparison runs the half it grades. These drive that half over real corpus
text with scripted models: arm A through `extract` and `assert`, arm B through
`facts` and the resolver behind it. What each asserts is the same two things —
the graph carries no lane agent and no critic, and it ends holding a catalog
`prepare` would have accepted.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest
from google.adk.models.base_llm import BaseLlm

from analysis_service import graph
from analysis_service.assertions import AssertionRecord
from analysis_service.deployment import Deployment
from analysis_service.factbundle import OWNED_BY, ROLES, SourceFactBundle, joined
from analysis_service.graph import FACTS_FIRST, GRAPH_FIRST, ExtractionStrategy
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import compose_inventory_prompt, compose_rows_prompt
from analysis_service.sampling import load_sampling
from analysis_service.system_model import (
    DataStore,
    Element,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)
from evals.harness import modes
from evals.harness.artifact import REPO_ROOT
from tests.factories import EVAL_MODEL, ScriptedLlm
from tests.test_deployment import VERTEX_ENV
from tests.test_evals_modes import scripted_assertions
from tests.test_facts_route import FRAMEWORKS


def role_of(element: Element) -> str:
    """The bundle role that names one blessed element's type.

    Read off :data:`~analysis_service.factbundle.ROLES` rather than written out,
    so a role added to the vocabulary is available here the day it lands and a
    role removed fails rather than scripting a value nothing accepts.
    """
    for role, rule in ROLES.items():
        if rule.element is not type(element):
            continue
        if all(getattr(element, field) == value for field, value in rule.fixed.items()):
            return role
    raise AssertionError(f"no role names {type(element).__name__}")


def scripted_bundle(model: SystemModel) -> str:
    """The Source Fact Bundle a model could write for one blessed model.

    Every mention cites the element's own ``source_excerpt``, which verifies
    against its source by construction, so this exercises the span locator on
    real submitted text rather than on a fixture nobody wrote. Placement rides
    on ``network-membership`` facts, which is the only way this route states it.
    """
    handles = {
        element.id: f"h{index}"
        for index, element in enumerate(model.elements())
        if element.source_excerpt
    }
    mentions = [
        {
            "handle": handles[element.id],
            "text": element.name,
            "roles": [role_of(element)],
            "quotes": [
                {
                    "source_label": element.source_label,
                    "quote": element.source_excerpt,
                }
            ],
        }
        for element in model.elements()
        if element.id in handles
        and isinstance(element, ExternalEntity | Process | DataStore | TrustBoundary)
    ]
    facts = [
        {
            "handle": f"p{index}",
            "subject_kind": "mention",
            "subject": handles[element.id],
            "predicate": "network-membership",
            "value": handles[element.trust_zone],
            "basis": "stated",
            "quotes": [
                {
                    "source_label": element.source_label,
                    "quote": element.source_excerpt,
                }
            ],
        }
        for index, element in enumerate(model.zoned_elements())
        if element.id in handles and element.trust_zone in handles
    ]
    return json.dumps({"mentions": mentions, "interactions": [], "facts": facts})


def head_pipeline(
    corpus_case, strategy: ExtractionStrategy, models: dict[str, ScriptedLlm]
):
    """A head-only pipeline for one strategy, on scripted models.

    The resolver is keyed by **tier** node, which is all a built graph hands it.
    That is enough here because one arm's head never carries two graph nodes on
    one tier: `extract` and `facts` are the same tier row and no graph has both.
    """
    replies = {
        "extract": (
            scripted_bundle(corpus_case.model)
            if strategy == FACTS_FIRST
            else json.dumps(corpus_case.model.model_dump(mode="json"))
        ),
        "assert": scripted_assertions(corpus_case),
    }

    def resolve(tier_node: str) -> BaseLlm:
        models[tier_node] = ScriptedLlm(
            model=EVAL_MODEL, reply=replies.get(tier_node, "{}"), seen=[]
        )
        return models[tier_node]

    env = dict(VERTEX_ENV)
    if strategy == FACTS_FIRST:
        env["ANALYSIS_FACTS_FIRST_EXTRACTION"] = "true"
    else:
        env["ANALYSIS_ASSERTIONS"] = "true"
    return modes.build_eval_pipeline(
        graph.ENTRY_HEAD_ONLY,
        deployment=Deployment.from_env(env=env),
        resolve_model=resolve,
        sampling=load_sampling(REPO_ROOT / "config" / "sampling.toml"),
    )


def carried(pipeline) -> set[str]:
    return {node.name for node in pipeline.workflow.graph.nodes}


LANES = {
    graph.analyze_node_name("stride", category)
    for category in ("spoofing", "tampering", "repudiation")
}


class TestTheHeadOnlyGraph:
    """What it carries, and what it deliberately does not."""

    @pytest.mark.parametrize("strategy", (GRAPH_FIRST, FACTS_FIRST))
    def test_it_carries_no_lane_and_no_critic(self, corpus_case, strategy) -> None:
        """The endpoint reads the catalog, so the judgement tier is not billed."""
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, strategy, models)
        nodes = carried(pipeline)

        assert not nodes & LANES
        assert graph.PREPARE_NODE not in nodes
        assert graph.ASSEMBLE_NODE not in nodes
        assert graph.CATALOG_NODE in nodes

    @pytest.mark.parametrize("strategy", (GRAPH_FIRST, FACTS_FIRST))
    def test_its_llm_nodes_are_the_head_s(self, corpus_case, strategy) -> None:
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, strategy, models)
        wanted = (
            {graph.FACTS_NODE, graph.REPAIR_NODE}
            if strategy == FACTS_FIRST
            else {graph.EXTRACT_NODE, graph.ASSERT_NODE, graph.REPAIR_NODE}
        )
        assert set(pipeline.node_models) == wanted


class TestRunningIt:
    """Driven to completion on scripted models, over real corpus text."""

    def run(self, corpus_case, strategy) -> tuple[modes.AssertionResult, Mapping]:
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, strategy, models)
        return asyncio.run(modes.run_heads(corpus_case, pipeline)), models

    def test_the_graph_first_head_ends_holding_a_catalog(self, corpus_case) -> None:
        result, _ = self.run(corpus_case, GRAPH_FIRST)

        assert result.case_id == corpus_case.id
        assert result.catalog.entries
        assert {entry.predicate for entry in result.catalog.entries} <= {
            "authentication-mechanism",
            "mfa-requirement",
        }

    def test_the_facts_first_head_ends_holding_a_catalog(self, corpus_case) -> None:
        """A bundle in local handles, resolved and gated, with no assert node."""
        result, models = self.run(corpus_case, FACTS_FIRST)

        assert "assert" not in models
        assert result.catalog.entries
        assert all(
            entry.predicate == "network-membership" for entry in result.catalog.entries
        )

    def test_the_facts_first_head_keeps_what_each_stage_wrote(
        self, corpus_case
    ) -> None:
        """The proposal is code's, so the bundle behind it has to be kept.

        Without these a replay cannot re-run the resolver over what the model
        emitted, and a fact missing from the catalog cannot be charged to the
        reading, the resolution or the gate.
        """
        result, _ = self.run(corpus_case, FACTS_FIRST)

        assert set(result.stages) <= set(modes.ARCHIVED_STATE)
        assert modes.STATE_SOURCE_FACTS in result.stages
        assert modes.STATE_BUNDLE_DISPOSITIONS in result.stages
        assert modes.STATE_EXTRACTED_MODEL in result.stages

    def test_the_catalog_it_parks_is_the_one_prepare_would_gate(
        self, corpus_case
    ) -> None:
        """The terminal node reads the seam, so a head run is gated like a job."""
        result, _ = self.run(corpus_case, FACTS_FIRST)
        refused = {issue.code for issue in result.issues}

        assert "wrong-registry-version" not in refused
        record = AssertionRecord(
            proposed=result.proposal
            and len(result.proposal.get("assertions", ()))
            or 0,
            catalog=result.catalog,
            issues=list(result.issues),
        )
        assert record.catalog.subjects

    def test_every_node_it_ran_is_a_head_node(self, corpus_case) -> None:
        """Code nodes run too; what never runs is a lane or the tail below it."""
        result, _ = self.run(corpus_case, FACTS_FIRST)
        ran = {run.node for run in result.node_runs}

        assert ran <= {
            graph.FACTS_NODE,
            graph.RESOLVE_NODE,
            graph.VALIDATE_NODE,
            graph.REPAIR_NODE,
            graph.REVALIDATE_NODE,
            graph.CATALOG_NODE,
        }
        assert graph.CATALOG_NODE in ran
        assert not ran & LANES
        assert graph.PREPARE_NODE not in ran


class TestTheSplitHead:
    """#1003 arm E: the facts-first order, spread over two calls.

    Its arms move the reading order and the number of calls together, so no
    comparison between them can say which a difference belongs to. This is the
    cell that separates them, and what these hold is that it really is that
    cell: the facts-first order, two model calls, one bundle out.
    """

    def built(self):
        env = dict(VERTEX_ENV) | {"ANALYSIS_FACTS_SPLIT_EXTRACTION": "true"}
        return modes.build_eval_pipeline(
            graph.ENTRY_HEAD_ONLY,
            deployment=Deployment.from_env(env=env),
            resolve_model=lambda tier: ScriptedLlm(
                model=EVAL_MODEL, reply="{}", seen=[]
            ),
            sampling=load_sampling(REPO_ROOT / "config" / "sampling.toml"),
        )

    def test_it_runs_two_calls_where_one_call_runs_one(self) -> None:
        pipeline = self.built()
        calls = set(pipeline.node_models) - {graph.REPAIR_NODE}

        assert calls == {graph.INVENTORY_NODE, graph.ROWS_NODE}

    def test_both_calls_resolve_on_the_extraction_tier(self) -> None:
        """One tier row for the stage, however the work is divided."""
        tiers = graph.tier_node_by_graph_node(FRAMEWORKS)
        assert (
            tiers[graph.INVENTORY_NODE]
            == tiers[graph.ROWS_NODE]
            == tiers[graph.EXTRACT_NODE]
        )

    def test_it_reads_facts_first(self) -> None:
        pipeline = self.built()
        assert pipeline.extraction_strategy == graph.FACTS_SPLIT
        assert graph.EXTRACT_NODE not in pipeline.node_models
        assert graph.ASSERT_NODE not in pipeline.node_models

    def test_the_second_call_is_shown_the_first_call_s_inventory(self) -> None:
        composed = compose_rows_prompt(MarkdownLoader(REPO_ROOT / "prompts"))
        assert "{inventory}" in composed
        assert "## The predicates" in composed

    def test_the_first_call_is_told_no_predicate(self) -> None:
        """It writes no fact, so a table of what a fact may say is text it
        is paid for and told not to use."""
        composed = compose_inventory_prompt(MarkdownLoader(REPO_ROOT / "prompts"))
        assert "## The roles" in composed
        assert "## The predicates" not in composed


class TestTheJoin:
    """Each call read for the half it owns, and what the other half costs."""

    def bundle(self, **rows) -> SourceFactBundle:
        return SourceFactBundle.model_validate(rows)

    def mention(self, handle: str) -> dict:
        return {
            "handle": handle,
            "text": "worker",
            "roles": ["process"],
            "quotes": [{"source_label": "S", "quote": "a worker"}],
        }

    def fact(self, handle: str) -> dict:
        return {
            "handle": handle,
            "subject_kind": "mention",
            "subject": "m1",
            "predicate": "internet-exposure",
            "value": "internal",
            "basis": "stated",
            "quotes": [{"source_label": "S", "quote": "a worker"}],
        }

    def test_each_list_comes_from_the_call_that_owns_it(self) -> None:
        inventory = self.bundle(mentions=[self.mention("m1")])
        rows = self.bundle(facts=[self.fact("f1")])
        built, ignored = joined(inventory, rows)

        assert [row.handle for row in built.mentions] == ["m1"]
        assert [row.handle for row in built.facts] == ["f1"]
        assert ignored == ()

    def test_a_call_that_answered_the_other_half_is_reported(self) -> None:
        """A second producer of one list would let a later call restate it."""
        inventory = self.bundle(
            mentions=[self.mention("m1")], facts=[self.fact("stray")]
        )
        rows = self.bundle(mentions=[self.mention("late")], facts=[self.fact("f1")])
        built, ignored = joined(inventory, rows)

        assert [row.handle for row in built.mentions] == ["m1"]
        assert [row.handle for row in built.facts] == ["f1"]
        assert {row.handle for row in ignored} == {"stray", "late"}
        assert {row.code for row in ignored} == {"wrong-call"}

    def test_either_call_may_raise_a_question(self) -> None:
        question = {"handle": "u1", "question": "which queue?"}
        built, ignored = joined(
            self.bundle(unresolved=[question]),
            self.bundle(unresolved=[{**question, "handle": "u2"}]),
        )
        assert [row.handle for row in built.unresolved] == ["u1", "u2"]
        assert ignored == ()

    def test_every_list_is_owned_by_someone(self) -> None:
        assert set(OWNED_BY) == set(SourceFactBundle.model_fields) - {
            "bundle_version",
            "role_version",
        }
