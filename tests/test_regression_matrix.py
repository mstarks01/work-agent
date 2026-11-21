"""The regression matrix's scripted-graph rows (#2273).

Each test runs the real graph over scripted model replies and takes a
``fault`` flag: with the fault the graph meets the failure, and without it the
same run is the comparison arm. So each test proves the fault is what the
assertion detects, not a snapshot of what the run happens to emit.
``docs/agents/regression-matrix.md`` lists each row with its provenance and contract,
and ``test_doc_reference_lints`` holds each test it names to the tree.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from evals.harness import fan_in_accounting
from katra import fan_in as fan_in_module
from katra import graph
from katra.critic import CriticOutputError
from katra.frameworks import PACKAGES, FrameworkName, schemas_for
from katra.jobs import PipelineCompleted
from tests.factories import (
    EMPTY_CLAIMS,
    SCRIPTED_FRAMEWORKS,
    adk_runner,
    emitted,
    valid_model,
)
from tests.factories import scripted_pipeline as build
from tests.test_pipeline import pair_job


def _two_drafts(framework: FrameworkName) -> tuple[dict, str]:
    """The fixture lane's emission with a second draft beside the first, and
    the ID the second draft composes to."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    key_field = schemas_for(framework).key_field
    emission = json.loads(fixture.proposal)
    emission["claims"].append(
        {
            **emission["claims"][0],
            key_field: fixture.second_key,
            "title": "A second scripted draft",
        }
    )
    second_id = PACKAGES[framework].compose_id(fixture.lane, fixture.second_key)
    return emission, second_id


def _replies(framework: FrameworkName, critic: str, recritic: str) -> dict[str, str]:
    """One package's run: its one lane drafts, and both review passes answer."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    nodes = graph.FrameworkNodes(framework)
    return {
        "extract": emitted(valid_model()),
        graph.Lane(framework, fixture.lane).node_name: fixture.proposal,
        nodes.node(graph.CRITIC_ROLE): critic,
        nodes.node(graph.RECRITIC_ROLE): recritic,
    }


def _run(framework: FrameworkName, replies: dict[str, str]):
    pipeline, _ = build(replies, frameworks=(framework,))
    visited: list[str] = []

    async def on_node(node: str) -> None:
        visited.append(node)

    outcome = asyncio.run(adk_runner(pipeline).run(pair_job((framework,)), on_node))
    return outcome, visited


@pytest.mark.parametrize("fault", [True, False])
@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_a_first_pass_that_drops_the_draft_is_repaired_by_the_re_ask(framework, fault):
    """First-pass failure and bounded repair: the critic rules on nothing, the
    re-ask rules on the draft, and the report keeps the claim and says the
    first pass did not reconcile."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    critic = EMPTY_CLAIMS if fault else fixture.ruling
    outcome, visited = _run(framework, _replies(framework, critic, fixture.ruling))

    assert isinstance(outcome, PipelineCompleted)
    (block,) = outcome.report.analyses
    assert [claim.id for claim in block.all_claims()] == [fixture.claim_id]
    recritic = graph.FrameworkNodes(framework).node(graph.RECRITIC_ROLE)
    assert (recritic in visited) is fault
    assert bool(block.unreconciled_rulings) is fault


@pytest.mark.parametrize("fault", [True, False])
@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_a_re_ask_that_still_drops_the_draft_fails_the_job(framework, fault):
    """Terminal failure: the bounded repair runs once, and a second pass that
    still rules on nothing ends the job, naming the draft."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    recritic = EMPTY_CLAIMS if fault else fixture.ruling
    replies = _replies(framework, EMPTY_CLAIMS, recritic)

    if fault:
        with pytest.raises(Exception, match=fixture.claim_id) as caught:
            _run(framework, replies)
        chain = []
        error: BaseException | None = caught.value
        while error is not None:
            chain.append(error)
            error = error.__cause__ or error.__context__
        assert any(isinstance(link, CriticOutputError) for link in chain)
    else:
        outcome, _ = _run(framework, replies)
        assert isinstance(outcome, PipelineCompleted)


@pytest.mark.parametrize("fault", [True, False])
@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_an_invalid_proposal_costs_only_itself(framework, fault):
    """Preservation of unaffected records: an entry that fails its schema is
    dropped with the reason, and the valid draft beside it is ruled on."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    replies = _replies(framework, fixture.ruling, fixture.ruling)
    if fault:
        emission = json.loads(fixture.proposal)
        emission["claims"].append({"title": ""})
        replies[graph.Lane(framework, fixture.lane).node_name] = json.dumps(emission)

    outcome, _ = _run(framework, replies)

    assert isinstance(outcome, PipelineCompleted)
    (block,) = outcome.report.analyses
    assert [claim.id for claim in block.all_claims()] == [fixture.claim_id]
    reasons = [mark.reason for mark in block.dropped_claims]
    assert len(reasons) == int(fault)
    assert all(reason.startswith("fails the proposal schema") for reason in reasons)


@pytest.mark.parametrize("fault", [True, False])
@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_a_re_ask_keeps_the_first_pass_ruling_it_was_not_asked_to_change(
    framework, fault
):
    """Preservation across the re-ask: the first pass drops the second draft,
    and a re-ask that also changes the first draft's ruling keeps the first
    pass's ruling and records the unbriefed change."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    emission, second_id = _two_drafts(framework)
    (ruling,) = json.loads(fixture.ruling)["claims"]
    changed = {**ruling, "verdict": {"reason": "r", "rejected_because": "reasoning"}}
    replies = _replies(
        framework,
        json.dumps({"claims": [ruling]}),
        json.dumps(
            {"claims": [changed if fault else ruling, {**ruling, "id": second_id}]}
        ),
    )
    replies[graph.Lane(framework, fixture.lane).node_name] = json.dumps(emission)

    outcome, _ = _run(framework, replies)

    assert isinstance(outcome, PipelineCompleted)
    (block,) = outcome.report.analyses
    assert sorted(claim.id for claim in block.claims) == sorted(
        [fixture.claim_id, second_id]
    )
    unbriefed = [
        mark.claim_id
        for mark in block.unreconciled_rulings
        if mark.kind == "unbriefed-change"
    ]
    assert unbriefed == ([fixture.claim_id] if fault else [])


@pytest.mark.parametrize("fault", [True, False])
@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_a_proposal_the_fan_in_drops_silently_is_unexplained(
    monkeypatch, framework, fault
):
    """The fan-in loses a draft and records no reason. The accounting must
    name it, against a count taken from the lane's own emission."""
    fixture = SCRIPTED_FRAMEWORKS[framework]
    emission, second_id = _two_drafts(framework)
    stored = {fixture.lane: schemas_for(framework).proposals.model_validate(emission)}
    emitted_count = len(emission["claims"])
    if fault:
        real = fan_in_module.join_drafts

        def loses_the_first(*args, **kwargs):
            joined = real(*args, **kwargs)
            return joined._replace(drafts=joined.drafts[1:])

        monkeypatch.setattr(fan_in_module, "join_drafts", loses_the_first)

    package = PACKAGES[framework]
    merged = fan_in_module.fan_in(stored, package, valid_model(), {}, assertions=None)
    row = fan_in_accounting.account("case", stored, package, merged)

    assert len(row.proposals) == emitted_count
    assert row.unexplained == ((fixture.claim_id,) if fault else ())
    assert second_id in row.kept
