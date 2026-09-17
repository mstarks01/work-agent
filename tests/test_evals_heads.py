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
from analysis_service.factbundle import ROLES
from analysis_service.graph import FACTS_FIRST, GRAPH_FIRST, ExtractionStrategy
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
from evals.harness.reference import load_corpus
from tests.factories import EVAL_MODEL, ScriptedLlm
from tests.test_deployment import VERTEX_ENV
from tests.test_evals_modes import scripted_assertions


@pytest.fixture(scope="module")
def case():
    """One corpus case, whose blessed model every scripted reply is built from."""
    return load_corpus(REPO_ROOT / "evals" / "corpus")[0]


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


def head_pipeline(case, strategy: ExtractionStrategy, models: dict[str, ScriptedLlm]):
    """A head-only pipeline for one strategy, on scripted models.

    The resolver is keyed by **tier** node, which is all a built graph hands it.
    That is enough here because one arm's head never carries two graph nodes on
    one tier: `extract` and `facts` are the same tier row and no graph has both.
    """
    replies = {
        "extract": (
            scripted_bundle(case.model)
            if strategy == FACTS_FIRST
            else json.dumps(case.model.model_dump(mode="json"))
        ),
        "assert": scripted_assertions(case),
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
    def test_it_carries_no_lane_and_no_critic(self, case, strategy) -> None:
        """The endpoint reads the catalog, so the judgement tier is not billed."""
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(case, strategy, models)
        nodes = carried(pipeline)

        assert not nodes & LANES
        assert graph.PREPARE_NODE not in nodes
        assert graph.ASSEMBLE_NODE not in nodes
        assert graph.CATALOG_NODE in nodes

    @pytest.mark.parametrize("strategy", (GRAPH_FIRST, FACTS_FIRST))
    def test_its_llm_nodes_are_the_head_s(self, case, strategy) -> None:
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(case, strategy, models)
        wanted = (
            {graph.FACTS_NODE, graph.REPAIR_NODE}
            if strategy == FACTS_FIRST
            else {graph.EXTRACT_NODE, graph.ASSERT_NODE, graph.REPAIR_NODE}
        )
        assert set(pipeline.node_models) == wanted


class TestRunningIt:
    """Driven to completion on scripted models, over real corpus text."""

    def run(self, case, strategy) -> tuple[modes.AssertionResult, Mapping]:
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(case, strategy, models)
        return asyncio.run(modes.run_heads(case, pipeline)), models

    def test_the_graph_first_head_ends_holding_a_catalog(self, case) -> None:
        result, _ = self.run(case, GRAPH_FIRST)

        assert result.case_id == case.id
        assert result.catalog.entries
        assert {entry.predicate for entry in result.catalog.entries} <= {
            "authentication-mechanism",
            "mfa-requirement",
        }

    def test_the_facts_first_head_ends_holding_a_catalog(self, case) -> None:
        """A bundle in local handles, resolved and gated, with no assert node."""
        result, models = self.run(case, FACTS_FIRST)

        assert "assert" not in models
        assert result.catalog.entries
        assert all(
            entry.predicate == "network-membership" for entry in result.catalog.entries
        )

    def test_the_catalog_it_parks_is_the_one_prepare_would_gate(self, case) -> None:
        """The terminal node reads the seam, so a head run is gated like a job."""
        result, _ = self.run(case, FACTS_FIRST)
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

    def test_every_node_it_ran_is_a_head_node(self, case) -> None:
        """Code nodes run too; what never runs is a lane or the tail below it."""
        result, _ = self.run(case, FACTS_FIRST)
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
