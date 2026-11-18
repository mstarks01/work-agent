"""The regression matrix's scripted-graph rows (#2273).

Each test runs the real graph over scripted model replies and takes a
``fault`` flag: with the fault the graph meets the failure, and without it the
same run is the valid control. So each test proves the fault is what the
assertion detects, not a snapshot of what the run happens to emit.
``docs/regression-matrix.md`` lists each row with its provenance and contract.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from analysis_service import graph
from analysis_service.claims import ProposedVerdict
from analysis_service.frameworks import PACKAGES, FrameworkName
from analysis_service.jobs import PipelineCompleted
from tests.factories import (
    EMPTY_CLAIMS,
    SCRIPTED_FRAMEWORKS,
    adk_runner,
    claims_json,
    emitted,
    sample_proposal,
    sample_ruling,
    valid_model,
)
from tests.factories import scripted_pipeline as build
from tests.source_tree import REPO_ROOT
from tests.test_pipeline import pair_job


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
        with pytest.raises(Exception, match=fixture.claim_id):
            _run(framework, replies)
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
def test_a_re_ask_keeps_the_first_pass_ruling_it_was_not_asked_to_change(fault):
    """Preservation across the re-ask, for a package whose critic rules on
    each threat: the first pass drops T-01, and a re-ask that also changes
    S-01 keeps S-01's first-pass ruling and records the unbriefed change."""
    tampering = graph.Lane("stride", "tampering").node_name
    rejected = sample_ruling(
        "S-01", verdict=ProposedVerdict(reason="r", rejected_because="reasoning")
    )
    replies = _replies(
        "stride",
        claims_json(sample_ruling("S-01")),
        claims_json(
            rejected if fault else sample_ruling("S-01"), sample_ruling("T-01")
        ),
    )
    replies[tampering] = claims_json(sample_proposal("T-01", "tampering", verb="alter"))

    outcome, _ = _run("stride", replies)

    assert isinstance(outcome, PipelineCompleted)
    (block,) = outcome.report.analyses
    assert sorted(claim.id for claim in block.claims) == ["S-01", "T-01"]
    unbriefed = [
        mark.claim_id
        for mark in block.unreconciled_rulings
        if mark.kind == "unbriefed-change"
    ]
    assert unbriefed == (["S-01"] if fault else [])


#: The test IDs the matrix names, as ``file.py::name`` or ``file.py::Class::name``.
_NAMED = re.compile(r"`(test_\w+\.py)((?:::\w+)+)`")


def test_each_test_the_matrix_names_exists():
    """A row whose test is renamed or deleted fails here, not silently."""
    text = (REPO_ROOT / "docs/agents/regression-matrix.md").read_text("utf-8")
    named = _NAMED.findall(text)
    assert named
    for path, parts in named:
        source = (REPO_ROOT / "tests" / path).read_text("utf-8")
        for part in parts.split("::")[1:]:
            assert re.search(rf"^\s*(def|class) {part}\b", source, re.MULTILINE), (
                path,
                part,
            )
