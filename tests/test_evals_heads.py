"""The head-only mode: the head, driven to a catalog and stopped.

Required-fact recall is scored on the catalog, and no lane writes one, so the
mode runs the half it grades. These drive that half over real corpus text with
scripted models, through `extract` and `assert`. What each asserts is the same
two things: the graph carries no lane agent and no critic, and it ends holding
a catalog `prepare` would have accepted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest
from google.adk.models.base_llm import BaseLlm

from analysis_service import graph
from analysis_service.assertions import AssertionRecord
from analysis_service.deployment import Deployment
from analysis_service.sampling import load_sampling
from evals.harness import modes
from evals.harness.artifact import REPO_ROOT
from tests.factories import EVAL_MODEL, ScriptedLlm, emitted
from tests.test_deployment import VERTEX_ENV
from tests.test_evals_modes import scripted_assertions


def head_pipeline(corpus_case, models: dict[str, ScriptedLlm], **overrides: str):
    """A head-only pipeline on scripted models.

    The resolver is keyed by **tier** node, which is all a built graph hands it.
    That is enough here because the head never carries two graph nodes on one
    tier.
    """
    replies = {
        "extract": emitted(corpus_case.model),
        "assert": scripted_assertions(corpus_case),
    }
    # A head that emits nothing usable is how a failed run is driven: the node
    # answers and the graph still reaches no catalog.
    replies.update(overrides)

    def resolve(tier_node: str) -> BaseLlm:
        models[tier_node] = ScriptedLlm(
            model=EVAL_MODEL, reply=replies.get(tier_node, "{}"), seen=[]
        )
        return models[tier_node]

    env = dict(VERTEX_ENV) | {"ANALYSIS_ASSERTIONS": "true"}
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

    def test_the_heads_mode_refuses_a_deployment_that_builds_no_catalog(self):
        with pytest.raises(modes.EvalRunError, match="no pass that makes one"):
            modes.build_eval_pipeline(
                graph.ENTRY_HEAD_ONLY,
                deployment=Deployment.from_env(
                    env=VERTEX_ENV | {"ANALYSIS_ASSERTIONS": "false"}
                ),
                resolve_model=lambda tier_node: ScriptedLlm(
                    model=EVAL_MODEL, reply="{}", seen=[]
                ),
            )

    def test_it_carries_no_lane_and_no_critic(self, corpus_case) -> None:
        """The endpoint reads the catalog, so the judgement tier is not billed."""
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, models)
        nodes = carried(pipeline)

        assert not nodes & LANES
        assert graph.PREPARE_NODE not in nodes
        assert graph.ASSEMBLE_NODE not in nodes
        assert graph.CATALOG_NODE in nodes

    def test_its_llm_nodes_are_the_head_s(self, corpus_case) -> None:
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, models)

        assert set(pipeline.node_models) == {
            graph.EXTRACT_NODE,
            graph.ASSERT_NODE,
            graph.REPAIR_NODE,
        }


class TestRunningIt:
    """Driven to completion on scripted models, over real corpus text."""

    def run(self, corpus_case) -> tuple[modes.AssertionResult, Mapping]:
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, models)
        return asyncio.run(modes.run_heads(corpus_case, pipeline)), models

    def test_the_head_ends_holding_a_catalog(self, corpus_case) -> None:
        result, _ = self.run(corpus_case)

        assert result.case_id == corpus_case.id
        assert result.catalog.entries
        assert {entry.predicate for entry in result.catalog.entries} <= {
            "authentication-mechanism",
            "mfa-requirement",
        }

    def test_the_head_keeps_what_each_stage_wrote(self, corpus_case) -> None:
        """The graph the rows bound against is kept beside the catalog."""
        result, _ = self.run(corpus_case)

        assert set(result.stages) <= set(modes.ARCHIVED_STATE)
        assert modes.STATE_EXTRACTED_MODEL in result.stages
        assert modes.STATE_VALID_MODEL in result.stages

    def test_a_head_that_reaches_no_catalog_still_carries_its_stages(
        self, corpus_case
    ) -> None:
        """The failed run is the one most worth reading back.

        A head that produced nothing is where a reader most needs the stages.
        The failure carries them out the way a refused model is carried out.
        """
        models: dict[str, ScriptedLlm] = {}
        pipeline = head_pipeline(corpus_case, models, extract="{}")

        with pytest.raises(modes.CaseFailure) as failed:
            asyncio.run(modes.run_heads(corpus_case, pipeline))

        assert isinstance(failed.value.cause, modes.EvalRunError)
        carried = failed.value.assertion
        assert carried is not None
        assert carried.case_id == corpus_case.id
        assert not carried.catalog.entries
        assert set(carried.stages) <= set(modes.ARCHIVED_STATE)

    def test_the_catalog_it_parks_is_the_one_prepare_would_gate(
        self, corpus_case
    ) -> None:
        """The terminal node reads the seam, so a head run is gated like a job."""
        result, _ = self.run(corpus_case)
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
        result, _ = self.run(corpus_case)
        ran = {run.node for run in result.node_runs}

        assert ran <= {
            graph.EXTRACT_NODE,
            graph.VALIDATE_NODE,
            graph.REPAIR_NODE,
            graph.REVALIDATE_NODE,
            graph.READ_MODEL_NODE,
            graph.ASSERT_NODE,
            graph.CATALOG_NODE,
        }
        assert graph.CATALOG_NODE in ran
        assert not ran & LANES
        assert graph.PREPARE_NODE not in ran
