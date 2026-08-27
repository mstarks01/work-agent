"""A sweep survives the failures it is measuring, and still fails the run.

Some of #91's measurements only exist on the path where the job dies — a threat
that loses every ground and an invented evidence reference both raise out of
``merge_drafts``. A sweep that aborts on the first one reports neither, so
these pin the two properties that make the numbers obtainable at all: the
remaining cases still run, and the case that died is still a Tier 1 failure.

Driven offline against scripted models, like :mod:`tests.test_evals_modes`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import Field

import stride_service.graph as graph_module
from evals.harness import modes
from evals.harness.coverage import aggregate_coverage, coverage_totals
from evals.harness.reference import load_case
from evals.harness.run import _run_mode
from stride_service.critic import DraftJoinError
from stride_service.deployment import Deployment
from stride_service.evidence import evidence_catalog
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES
from stride_service.graph import (
    ENTRY_PREPARE,
    analyze_node_name,
    tier_node_by_graph_node,
)
from stride_service.sampling import load_sampling
from tests.factories import DEFAULT_FRAMEWORKS, TEST_TIER_ENV, ScriptedLlm
from tests.test_evals_modes import lane_of, scripted_ruling

TIER_NODE_BY_GRAPH_NODE = tier_node_by_graph_node(DEFAULT_FRAMEWORKS)

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "evals" / "corpus" / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


class QueuedLlm(ScriptedLlm):
    """One adapter for every lane, replying by lane and by how often it was asked.

    ``_run_mode`` builds the pipeline once for the whole sweep, which is what
    makes "the next case still runs" a property worth testing — and what means
    a per-case difference has to come from the model rather than the build.

    Two things have to be told apart through one adapter since every lane
    shares an ``analyze/stride`` tier key: *which* lane is asking, recovered
    from its instruction, and *which case* is asking, counted per lane. The
    broken emission is queued against one lane and consumed on that lane's
    first call, so the second case runs clean without the queue being drained
    by whichever lane happened to run first.
    """

    lane_replies: dict[str, str] = Field(default_factory=dict)
    queued: dict[str, list[str]] = Field(default_factory=dict)
    #: Replies for a node that is not a lane, consumed in order — the critic's
    #: ruling on the first case, when that case drops a draft.
    first_replies: list[str] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream: bool = False):
        instruction = llm_request.config.system_instruction or ""
        default = self.reply
        self.reply = self._next_reply(instruction, default)
        try:
            async for response in super().generate_content_async(llm_request, stream):
                yield response
        finally:
            self.reply = default

    def _next_reply(self, instruction: str, default: str) -> str:
        lane = lane_of(instruction, self.lane_replies or self.queued)
        if lane is None:
            return self.first_replies.pop(0) if self.first_replies else default
        pending = self.queued.get(lane)
        if pending:
            return pending.pop(0)
        return self.lane_replies.get(lane, default)


def proposal(case, category, evidence: dict[str, Any], sequence: int = 1) -> dict:
    reference = next(
        ref for ref in case.claims_for("stride") if ref.category == category
    )
    return {
        "sequence": sequence,
        "title": reference.claim,
        "description": f"{reference.claim} Scripted for the sweep test.",
        "affected_element_ids": list(reference.affected_element_ids),
        # From the reference this stands in for; these tests grade grounds.
        "verb": reference.verb,
        "severity": {
            "likelihood": reference.severity.likelihood,
            "impact": reference.severity.impact,
            "justification": "scripted",
        },
        **evidence,
    }


def sound_evidence(case) -> dict[str, Any]:
    """One real ``unknown-attribute`` entry: verified by set membership, always."""
    return {
        "evidence_refs": [
            next(
                ref
                for ref in evidence_catalog(case.model)
                if ref.startswith("unknown:")
            )
        ]
    }


FABRICATED = {
    "quotes": [
        {
            "text": "a sentence that appears in no source this job carries",
            "source_label": "",
        }
    ]
}
# The successor to the mis-shaped ``Ground``. An agent selects from a closed
# set, so the only way its evidence can fail is by naming something outside it.
INVENTED = {"evidence_refs": ["crossing:flow:not-a-flow-in-this-model"]}
# A sentinel for the one way a case still dies: the fan-in raising on a fault
# no agent can produce any more. ``sweep`` makes the first join raise it.
DEAD: dict[str, Any] = {"dead": True}


def sweep(monkeypatch, case, spoofing_first: dict[str, Any] | None) -> Any:
    """Two cases through one pipeline: the first optionally broken, then a clean one.

    ``spoofing_first`` is the spoofing agent's evidence on the *first* case
    only; ``None`` runs both cases clean.
    """
    label = case.sources[0].label
    first = spoofing_first
    if first is DEAD:
        first = None
        real_join = graph_module.join_drafts
        calls: list[int] = []

        def join_once_dead(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise DraftJoinError("draft 'S-01' cites something no agent can")
            return real_join(*args, **kwargs)

        monkeypatch.setattr(graph_module, "join_drafts", join_once_dead)
    if first is not None and "quotes" in first:
        first = first | {
            "quotes": [{**quote, "source_label": label} for quote in first["quotes"]]
        }

    def resolve(tier_node: str) -> BaseLlm:
        graph_node = next(
            node for node, tier in TIER_NODE_BY_GRAPH_NODE.items() if tier == tier_node
        )
        return QueuedLlm(
            model="fake-pro-001",
            reply=_reply_for(case, graph_node),
            lane_replies=_lane_replies(case, graph_node),
            queued=_queued_for(case, graph_node, first),
            first_replies=_critic_first(graph_node, first),
        )

    pipeline = modes.build_eval_pipeline(
        ENTRY_PREPARE,
        resolve_model=resolve,
        sampling=load_sampling(REPO_ROOT / "config" / "sampling.toml"),
    )
    monkeypatch.setattr(modes, "build_eval_pipeline", lambda *a, **k: pipeline)
    second = replace(case, meta=case.meta.model_copy(update={"id": "case-second"}))
    # A real deployment even though the pipeline is scripted: the sweep folds
    # each execution's tier and sampling into its provenance record, and both
    # come from the deployment rather than from the graph.
    deployment = Deployment.from_env(env=TEST_TIER_ENV)
    return asyncio.run(_run_mode([case, second], "analysis", deployment))


def _reply_for(case, graph_node: str) -> str:
    if graph_node == "critic_stride":
        return json.dumps(
            {"claims": [scripted_ruling(category) for category in STRIDE_CATEGORIES]}
        )
    for category in STRIDE_CATEGORIES:
        if graph_node == analyze_node_name("stride", category):
            return json.dumps(
                {"claims": [proposal(case, category, sound_evidence(case))]}
            )
    return '{"claims": []}'


def _critic_first(graph_node: str, first: dict[str, Any] | None) -> list[str]:
    """The critic's ruling on a first case whose spoofing draft was dropped.

    A broken spoofing emission loses its only ground, so the service drops
    ``S-01`` before the critic sees it, and a ruling on it would be a ruling on
    a claim nobody drafted.
    """
    if graph_node != "critic_stride" or first is None:
        return []
    rulings = [
        scripted_ruling(category)
        for category in STRIDE_CATEGORIES
        if category != "spoofing"
    ]
    return [json.dumps({"claims": rulings})]


def _lane_replies(case, graph_node: str) -> dict[str, str]:
    """The clean per-lane emissions, for the adapter every lane agent shares."""
    if graph_node not in {
        analyze_node_name("stride", category) for category in STRIDE_CATEGORIES
    }:
        return {}
    return {
        category: json.dumps(
            {"claims": [proposal(case, category, sound_evidence(case))]}
        )
        for category in STRIDE_CATEGORIES
    }


def _queued_for(
    case, graph_node: str, first: dict[str, Any] | None
) -> dict[str, list[str]]:
    """The spoofing agent's first emission, when the test wants it broken."""
    if first is None or graph_node not in {
        analyze_node_name("stride", category) for category in STRIDE_CATEGORIES
    }:
        return {}
    return {"spoofing": [json.dumps({"claims": [proposal(case, "spoofing", first)]})]}


def test_a_clean_sweep_measures_every_case(monkeypatch, case):
    run = sweep(monkeypatch, case, None)

    assert [entry.case_id for entry in run.grounds] == [case.id, "case-second"]
    assert run.grounds_failures == []
    assert all(entry.ground_count == len(STRIDE_CATEGORIES) for entry in run.grounds)


def test_the_measurement_rides_in_the_case_payload(monkeypatch, case):
    """The report never reaches ``mode_output``, so a number not written at run
    time is a number nobody can recover from a finished sweep."""
    run = sweep(monkeypatch, case, None)

    payload = run.payloads[0]
    assert payload["case"] == case.id
    # One entry per block the job selected: grounds is per framework, because
    # ADR 0002 exempts none of them from finding-level attribution.
    measured = {entry["framework"]: entry for entry in payload["grounds"]}
    assert set(measured) == {"stride"}
    stride = measured["stride"]
    assert stride["counts"]["unknown-attribute"] == len(STRIDE_CATEGORIES)
    assert stride["metrics"]["grounds_per_threat"] == 1.0


@pytest.mark.parametrize("broken", [FABRICATED, INVENTED])
def test_a_claim_that_lost_every_ground_is_dropped_and_the_case_still_measures(
    monkeypatch, case, broken
):
    """A fabricated quote and an invented reference both cost the claim, never
    the case: the report is built, the drop is counted, and the sweep goes on."""
    run = sweep(monkeypatch, case, broken)

    assert run.grounds_failures == []
    assert [entry.case_id for entry in run.grounds] == [case.id, "case-second"]
    first, second = run.grounds
    assert [mark.claim_id for mark in first.dropped] == ["S-01"]
    assert first.threat_count == len(STRIDE_CATEGORIES) - 1
    assert second.dropped == ()
    assert run.payloads[0]["grounds"][0]["counts"]["dropped_claims"] == 1


def test_the_sweep_collects_every_case_s_coverage_rows(monkeypatch, case):
    """One case's rows are unreadable; the sweep is where they become a rate."""
    run = sweep(monkeypatch, case, None)

    assert len(run.coverage) == 2 * len(STRIDE_CATEGORIES)
    assert {framework for framework, _ in run.coverage} == {"stride"}
    lanes = aggregate_coverage(run.coverage, run.frameworks)
    assert all(lane.cases == 2 for lane in lanes)
    assert coverage_totals(lanes)["drafts"] == 2 * len(STRIDE_CATEGORIES)


def test_a_dead_case_is_counted_and_the_sweep_continues(monkeypatch, case):
    """The fan-in still raises on a fault no agent can produce — a catalogued
    ground the service built wrongly. That is counted as a failure, named in
    the failure list so the run exits non-zero, and the next case still runs."""
    run = sweep(monkeypatch, case, DEAD)

    assert [f.kind for f in run.grounds_failures] == ["other"]
    assert any(failure.startswith(f"{case.id}:") for failure in run.failures)
    assert run.payloads[0]["grounds_failure"]["kind"] == "other"
    assert "grounds" not in run.payloads[0]
    assert [entry.case_id for entry in run.grounds] == ["case-second"]


def test_a_failed_case_contributes_no_coverage(monkeypatch, case):
    """No report, no accounting — a lane cannot be credited for a dead case."""
    run = sweep(monkeypatch, case, DEAD)

    assert len(run.coverage) == len(STRIDE_CATEGORIES)


def test_the_sweep_folds_latency_over_every_node_it_ran(monkeypatch, case):
    """``duration_ms`` is per execution and read back by nothing else."""
    run = sweep(monkeypatch, case, None)

    critic = run.latency["critic_stride"]
    assert critic.executions == 2
    assert critic.slowest_ms <= critic.total_ms
    # The deterministic derivations are absent from the token totals and
    # present here, which is the difference the two folds exist to show.
    assert ENTRY_PREPARE in run.latency
    assert ENTRY_PREPARE not in run.usage
