"""The regression matrix's scripted-graph rows (#2273).

Each test runs the real graph over scripted model replies and takes a
``fault`` flag: with the fault the graph meets the failure, and without it the
same run is the valid control. So each test proves the fault is what the
assertion detects, not a snapshot of what the run happens to emit.
``docs/agents/regression-matrix.md`` lists each row with its provenance and contract.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re

import pytest

from analysis_service import fan_in as fan_in_module
from analysis_service import graph
from analysis_service.claims import ProposedVerdict
from analysis_service.critic import CriticOutputError
from analysis_service.frameworks import PACKAGES, FrameworkName, schemas_for
from analysis_service.frameworks.stride import STRIDE
from analysis_service.jobs import PipelineCompleted
from evals.harness import fan_in_accounting
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
    """Each test the matrix names, by its full ID, is in the tree: a renamed or
    deleted test fails here. A cell that names a test with no file fails too."""
    text = (REPO_ROOT / "docs/agents/regression-matrix.md").read_text("utf-8")
    bare = re.findall(r"`(test_\w+)`", text)
    assert bare == [], f"name each test with its file: {bare}"
    named = _NAMED.findall(text)
    assert named
    for path, parts in named:
        tree = ast.parse((REPO_ROOT / "tests" / path).read_text("utf-8"))
        scope: list[ast.stmt] = tree.body
        for part in parts.split("::")[1:]:
            found = [
                node
                for node in scope
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name == part
            ]
            assert found, (path, part)
            scope = found[0].body if isinstance(found[0], ast.ClassDef) else []


@pytest.mark.parametrize("fault", [True, False])
def test_a_proposal_the_fan_in_drops_silently_is_unexplained(monkeypatch, fault):
    """The fan-in loses a draft and records no reason. The accounting must
    name it, against a count taken from the lanes' own emissions."""
    stored = {
        "spoofing": schemas_for("stride").proposals(claims=[sample_proposal("S-01")]),
        "tampering": schemas_for("stride").proposals(
            claims=[sample_proposal("T-01", "tampering", verb="alter")]
        ),
    }
    emitted_count = sum(len(batch.claims) for batch in stored.values())
    if fault:
        real = fan_in_module.join_drafts

        def loses_the_first(*args, **kwargs):
            joined = real(*args, **kwargs)
            return joined._replace(drafts=joined.drafts[1:])

        monkeypatch.setattr(fan_in_module, "join_drafts", loses_the_first)

    merged = fan_in_module.fan_in(stored, STRIDE, valid_model(), {}, assertions=None)
    row = fan_in_accounting.account("case", stored, STRIDE, merged)

    assert len(row.proposals) == emitted_count
    assert emitted_count - row.accounted == int(fault)
    assert row.unexplained == (("S-01",) if fault else ())
    assert "T-01" in row.kept
