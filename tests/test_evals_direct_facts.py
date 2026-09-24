"""The ``direct-facts`` mode: ``analysis`` mode with the signed facts as the catalog.

The quality-audit skill's third condition. The claims worth testing are that
the ``assert`` node's rows are exactly the case's signed rows, that no model
wrote them, that every other node still runs, and that a case without signed
facts is refused before anything is spent.
"""

from __future__ import annotations

import asyncio

import pytest
from google.adk.models.base_llm import BaseLlm

from analysis_service.assertions import assertion_id
from analysis_service.deployment import Deployment
from analysis_service.graph import ASSERT_NODE
from evals.harness import modes, replay, run
from evals.harness.audit import CONDITION_STAGES
from evals.harness.provenance import REPO_ROOT
from evals.reference_facts import load_facts, signed_proposal
from tests.factories import EVAL_MODEL, TEST_TIER_ENV
from tests.test_evals_modes import (
    CASE_DIR,
    TIER_NODE_BY_GRAPH_NODE,
    LaneAwareLlm,
    _lane_replies,
    _reply_for,
    case,  # noqa: F401  (fixture)
)

CORPUS = REPO_ROOT / "evals" / "corpus"


def scripted(case):  # noqa: F811
    """Every node but ``assert`` on a scripted model, as the offline tests run it."""

    def resolve(tier_node: str) -> BaseLlm:
        graph_node = next(
            node for node, tier in TIER_NODE_BY_GRAPH_NODE.items() if tier == tier_node
        )
        return LaneAwareLlm(
            model=EVAL_MODEL,
            reply=_reply_for(case, graph_node),
            seen=[],
            replies=_lane_replies(case, graph_node),
        )

    return resolve


def test_the_catalog_is_the_signed_reference_and_no_model_wrote_it(case):  # noqa: F811
    pipeline = modes.build_direct_facts_pipeline(
        Deployment.from_env(env=TEST_TIER_ENV), ("stride",), shipped=scripted(case)
    )

    result = asyncio.run(
        modes.run_direct_facts(case, pipeline, signed_proposal(load_facts(CASE_DIR)))
    )

    report = result.report
    signed = replay.signed_reference(CORPUS, case)
    assert report.assertions is not None and signed is not None
    assert {assertion_id(row) for row in report.assertions.catalog.entries} == {
        assertion_id(row) for row in signed.catalog.entries
    }
    served = {run.node: run.model for run in report.nodes}
    assert served[ASSERT_NODE] is None, "no provider answered the assert node"
    requested = {run.node: run.requested_model for run in report.nodes}
    assert requested[ASSERT_NODE] == EVAL_MODEL, "the route the tier configured"
    # The report is written to disk after a sweep, and every node's route is
    # parsed on the way; a bare name there cost the first paid run its report.
    assert type(report).model_validate_json(report.model_dump_json()) == report
    assert all(model for node, model in served.items() if node.startswith("analyze_"))
    assert report.analyses[0].claims, "the lanes and the critic still ran"


def test_the_signed_model_refuses_to_answer_with_nothing():
    """A route that forgot to set the case's rows fails, never answers empty."""
    llm = modes.SignedFactsLlm(model=EVAL_MODEL)

    async def ask():
        async for _ in llm.generate_content_async(None):
            pass

    with pytest.raises(modes.EvalRunError, match="no signed proposal"):
        asyncio.run(ask())


def test_a_case_with_unsigned_rows_is_refused_before_anything_is_spent(
    monkeypatch,
    case,  # noqa: F811
):
    monkeypatch.setattr(replay, "unsigned_rows", lambda corpus, one: 1)

    with pytest.raises(modes.EvalRunError, match=case.id):
        run.signed_proposals(CORPUS, [case])


def test_the_mode_reaches_a_final_report_so_the_ledger_admits_one():
    assert "direct-facts" in modes.REPORTING_MODES
    assert CONDITION_STAGES["direct-facts"] == ("analysis", "final-report")
