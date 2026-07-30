"""Driving the real graph and stamping what each node execution presented.

These test :class:`~stride_service.execution.GraphExecutor` at its own
interface, because that is where stamping now lives and both drivers — the
service and the eval harness — cross it. No Vertex endpoint is involved: each
LLM node is bound to a scripted stand-in that reports a ``model_version`` the
way a real provider does, so the served-build and generation-identity paths are
exercised without a live call.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from stride_service import graph
from stride_service.execution import GraphExecutor
from stride_service.model_tiers import load_model_tiers
from stride_service.sampling import (
    TierSampling,
    load_sampling,
    sampling_fingerprint,
)
from tests.factories import (
    BASE_MODEL,
    PROJECT_ROOT,
    STRONG_MODEL,
    SilentLlm,
    sample_draft,
    sample_threat,
    scripted_pipeline,
    served_build,
    valid_model,
)

DESCRIPTION = "Customers log in to the web app."


def happy_replies() -> dict[str, str]:
    """Extraction succeeds; spoofing drafts one threat; the critic confirms it."""
    return {
        "extract": valid_model().model_dump_json(),
        # An analyst emits a draft — the critic's two rulings are not its to make.
        graph.analyst_node_name("spoofing"): json.dumps(
            [sample_draft("S-01", "spoofing").model_dump(mode="json")]
        ),
        "critic": json.dumps([sample_threat("S-01").model_dump(mode="json")]),
    }


def drive(pipeline, *, visited: list[str] | None = None):
    """One Graph Run over the scripted pipeline."""

    async def on_node(node: str) -> None:
        if visited is not None:
            visited.append(node)

    async def scenario():
        executor = GraphExecutor(pipeline, app_name="stride-test")
        return await executor.run(
            {graph.STATE_INPUT_TEXT: DESCRIPTION},
            DESCRIPTION,
            user_id="test-user",
            on_node=on_node,
        )

    return asyncio.run(scenario())


@pytest.fixture
def graph_run():
    pipeline, _ = scripted_pipeline(happy_replies())
    return drive(pipeline)


def by_node(run) -> dict:
    return {node_run.node: node_run for node_run in run.node_runs}


def test_the_run_carries_the_final_state_and_every_node_that_ran():
    visited: list[str] = []
    pipeline, _ = scripted_pipeline(happy_replies())
    run = drive(pipeline, visited=visited)

    assert graph.STATE_ANALYSIS in run.final_state
    assert [node_run.node for node_run in run.node_runs] == visited
    assert visited[0] == graph.EXTRACT_NODE
    assert visited[-1] == graph.ASSEMBLE_NODE


def test_node_runs_record_the_served_and_the_requested_model(graph_run):
    """``model`` is what answered; ``requested_model`` is what was asked for.

    Both are recorded and neither is compared — drift falls out of
    certification, not out of a comparison here.
    """
    nodes = by_node(graph_run)
    assert nodes[graph.EXTRACT_NODE].model == served_build(BASE_MODEL)
    assert nodes[graph.EXTRACT_NODE].requested_model == BASE_MODEL
    assert nodes[graph.CRITIC_NODE].model == served_build(STRONG_MODEL)
    assert nodes[graph.CRITIC_NODE].requested_model == STRONG_MODEL


def test_a_deterministic_node_carries_no_model_and_no_fingerprint(graph_run):
    assemble = by_node(graph_run)[graph.ASSEMBLE_NODE]
    assert assemble.model is None
    assert assemble.requested_model is None
    assert assemble.sampling_fingerprint is None


def test_a_node_with_no_served_build_carries_no_fingerprint():
    """Nothing honest to hash: better no identity than one keyed on a guess."""
    pipeline, _ = scripted_pipeline(happy_replies(), llm_class=SilentLlm)
    extract = by_node(drive(pipeline))[graph.EXTRACT_NODE]

    assert extract.model is None
    assert extract.sampling_fingerprint is None
    # What was asked for is still known, and still recorded.
    assert extract.requested_model == BASE_MODEL


def test_every_llm_node_fingerprint_recomputes_from_its_tier_sampling(graph_run):
    """The hash is derivable from the served build and the tier's clear values."""
    pipeline, _ = scripted_pipeline(happy_replies())
    tiers = load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})

    for node_run in graph_run.node_runs:
        canonical = graph.TIER_NODE_BY_GRAPH_NODE.get(node_run.node)
        if canonical is None:  # deterministic FunctionNode
            assert node_run.sampling_fingerprint is None
            continue
        tier = tiers.resolve_tier(canonical)
        clear = pipeline.tier_sampling[tier].model_dump()
        expected = sampling_fingerprint(node_run.model, TierSampling(**clear))
        assert node_run.sampling_fingerprint == expected


def test_base_and_strong_nodes_get_different_identities(graph_run):
    """Different served build and tier sampling → distinct generation identities."""
    nodes = by_node(graph_run)
    extract_fp = nodes[graph.EXTRACT_NODE].sampling_fingerprint
    critic_fp = nodes[graph.CRITIC_NODE].sampling_fingerprint

    assert extract_fp and critic_fp and extract_fp != critic_fp

    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    assert critic_fp == sampling_fingerprint(
        served_build(STRONG_MODEL), sampling.for_tier("strong")
    )


def test_durations_are_measured_from_the_last_predecessor(graph_run):
    """Never negative, and never the whole run's elapsed time for a late node."""
    nodes = by_node(graph_run)
    assert all(node_run.duration_ms >= 0 for node_run in graph_run.node_runs)
    # assemble runs last, so measuring from the run's start rather than from its
    # predecessor would make it the longest node in the graph.
    assert nodes[graph.ASSEMBLE_NODE].duration_ms <= max(
        node_run.duration_ms for node_run in graph_run.node_runs
    )


def test_extract_only_entry_stamps_just_the_one_node():
    """Mode 1's graph: one LLM node, and an identity for it all the same."""
    pipeline, _ = scripted_pipeline(happy_replies(), entry=graph.ENTRY_EXTRACT_ONLY)
    run = drive(pipeline)

    assert [node_run.node for node_run in run.node_runs] == [graph.EXTRACT_NODE]
    assert run.node_runs[0].sampling_fingerprint is not None
