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

import pytest

from stride_service import graph
from stride_service.execution import GraphExecutor
from stride_service.report import Ground, usage_by_node
from stride_service.sampling import (
    TierSampling,
    load_sampling,
    sampling_fingerprint,
)
from stride_service.sources import Source, render_sources
from tests.factories import (
    BASE_MODEL,
    DESCRIPTION_TEXT,
    PROJECT_ROOT,
    STRONG_MODEL,
    SilentLlm,
    SlowLlm,
    UnmeteredLlm,
    repo_tiers,
    sample_draft,
    sample_ruling,
    scripted_pipeline,
    served_build,
    threats_json,
    valid_model,
)

DESCRIPTION = DESCRIPTION_TEXT


def happy_replies() -> dict[str, str]:
    """Extraction succeeds; spoofing drafts one threat; the critic confirms it."""
    return {
        "extract": valid_model().model_dump_json(),
        # A category agent emits a draft — the critic's two rulings are not its to make.
        graph.analyze_node_name("spoofing"): threats_json(
            sample_draft("S-01", "spoofing")
        ),
        "critic": threats_json(sample_ruling("S-01")),
    }


def drive(pipeline, *, visited: list[str] | None = None, sources=None):
    """One Graph Run over the scripted pipeline."""

    async def on_node(node: str) -> None:
        if visited is not None:
            visited.append(node)

    async def scenario():
        executor = GraphExecutor(pipeline, app_name="stride-test")
        return await executor.run(
            sources if sources is not None else [Source.description(DESCRIPTION)],
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


def test_node_runs_record_what_the_provider_says_each_call_cost(graph_run):
    """Each provider counter lands in its own vendor-neutral field.

    The scripted usage block carries five distinct values precisely so a
    transposed pair in the mapping fails here rather than being averaged away
    in a roll-up later.
    """
    critic = by_node(graph_run)[graph.CRITIC_NODE]

    assert critic.usage is not None
    assert critic.usage.prompt_tokens == 1100
    assert critic.usage.cached_prompt_tokens == 700
    assert critic.usage.completion_tokens == 300
    # The number the accounting exists for: spent against max_output_tokens
    # and invisible in the emission that node produced.
    assert critic.usage.reasoning_tokens == 9000
    assert critic.usage.total_tokens == 10400


def test_a_deterministic_node_costs_nothing_and_records_nothing(graph_run):
    assert by_node(graph_run)[graph.ASSEMBLE_NODE].usage is None


def test_usage_is_recorded_even_when_the_served_build_is_not():
    """The two are independent: a missing build costs the fingerprint only."""
    pipeline, _ = scripted_pipeline(happy_replies(), llm_class=SilentLlm)
    extract = by_node(drive(pipeline))[graph.EXTRACT_NODE]

    assert extract.model is None
    assert extract.sampling_fingerprint is None
    assert extract.usage is not None
    assert extract.usage.prompt_tokens == 1100


def test_a_provider_that_meters_nothing_yields_no_usage_at_all():
    """An unmeasured call must not read as a free one."""
    pipeline, _ = scripted_pipeline(happy_replies(), llm_class=UnmeteredLlm)
    extract = by_node(drive(pipeline))[graph.EXTRACT_NODE]

    assert extract.model == served_build(BASE_MODEL)
    assert extract.usage is None


def test_usage_by_node_sums_a_nodes_executions(graph_run):
    """A sweep runs one node once per case; the question is what it all cost."""
    critic = by_node(graph_run)[graph.CRITIC_NODE]
    twice = [critic, critic.model_copy()]

    totals = usage_by_node(twice)

    assert totals[graph.CRITIC_NODE].reasoning_tokens == 18000
    assert totals[graph.CRITIC_NODE].total_tokens == 20800


def test_usage_by_node_omits_what_was_never_measured(graph_run):
    """Absent, not zeroed — a node with no usage is not a node that cost nothing."""
    assert graph.ASSEMBLE_NODE not in usage_by_node(graph_run.node_runs)
    assert graph.CRITIC_NODE in usage_by_node(graph_run.node_runs)


def test_every_llm_node_fingerprint_recomputes_from_its_tier_sampling(graph_run):
    """The hash is derivable from the served build and the tier's clear values."""
    pipeline, _ = scripted_pipeline(happy_replies())
    tiers = repo_tiers()

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


def test_an_llm_nodes_latency_is_charged_to_that_node():
    """The node that waited is the node whose duration shows the wait.

    ADK builds an LlmAgent's response event *before* the request goes out, and
    ``_finalize_model_response_event`` copies that timestamp onto the event it
    yields — so ``event.timestamp`` marks when the call was issued, not when it
    came back. Stamping from it charged every model's latency to whichever node
    ran next: a 58-second critic was reported as 19 ms on ``critic`` and 58,035
    ms on the ``router`` FunctionNode after it. The executor stamps observation
    time instead.

    This is the only test that can see the difference. Every other stand-in
    answers in ~0 ms, which reads identically under both rules.
    """
    pipeline, _ = scripted_pipeline(happy_replies(), llm_class=SlowLlm)
    nodes = by_node(drive(pipeline))

    # The LLM nodes waited; the deterministic nodes that follow them did not.
    assert nodes[graph.EXTRACT_NODE].duration_ms >= 40
    assert nodes[graph.VALIDATE_NODE].duration_ms < 40
    assert nodes[graph.CRITIC_NODE].duration_ms >= 40
    assert nodes[graph.ROUTER_NODE].duration_ms < 40


def test_extract_only_entry_stamps_just_the_one_node():
    """Mode 1's graph: one LLM node, and an identity for it all the same."""
    pipeline, _ = scripted_pipeline(happy_replies(), entry=graph.ENTRY_EXTRACT_ONLY)
    run = drive(pipeline)

    assert [node_run.node for node_run in run.node_runs] == [graph.EXTRACT_NODE]
    assert run.node_runs[0].sampling_fingerprint is not None


class TestSourceRendering:
    """The executor is where a job becomes prompt bytes (#54).

    The render rule itself is tested in ``test_sources`` against the pure
    function. What can only be seen here is the wiring: that the executor calls
    it, and that no caller can hand the graph text of their own instead.
    """

    def test_what_extraction_sees_is_the_rendered_sources(self):
        sources = [
            Source.description(
                f"{DESCRIPTION_TEXT} A web app storing orders.", label="Doc"
            ),
            Source.transcript("Ana: it writes to Postgres.", label="Kickoff call"),
        ]
        # The scripted models must cite labels this job carries: the gate checks
        # each element's excerpt citation, and the draft fan-in checks each
        # finding's quote against the source it names.
        draft = sample_draft(
            "S-01",
            "spoofing",
            grounds=[
                Ground(
                    kind="quote",
                    text="a web app storing orders",
                    source_label="Doc",
                )
            ],
        )
        replies = happy_replies() | {
            "extract": valid_model("Doc").model_dump_json(),
            graph.analyze_node_name("spoofing"): threats_json(draft),
        }
        pipeline, models = scripted_pipeline(replies)

        drive(pipeline, sources=sources)

        assert render_sources(sources) in models[graph.EXTRACT_NODE].seen[0]

    def test_seeding_the_input_text_directly_is_refused(self):
        # Seeding raw text is what would let the service and the eval harness
        # show one job to a model differently, so it is inexpressible.
        pipeline, _ = scripted_pipeline(happy_replies())
        executor = GraphExecutor(pipeline, app_name="stride-test")

        async def scenario():
            return await executor.run(
                [Source.description("text")],
                user_id="test-user",
                extra_state={graph.STATE_INPUT_TEXT: "text of my own"},
            )

        with pytest.raises(ValueError, match=graph.STATE_INPUT_TEXT):
            asyncio.run(scenario())

    def test_other_state_still_seeds(self):
        # Mode 2 injects an already-blessed model at a later entry point.
        pipeline, _ = scripted_pipeline(happy_replies())
        executor = GraphExecutor(pipeline, app_name="stride-test")

        async def scenario():
            return await executor.run(
                [Source.description(DESCRIPTION_TEXT)],
                user_id="test-user",
                extra_state={
                    graph.STATE_VALID_MODEL: valid_model().model_dump(mode="json")
                },
            )

        run = asyncio.run(scenario())
        assert graph.STATE_VALID_MODEL in run.final_state
