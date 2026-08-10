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

from evals.harness import modes
from evals.harness.coverage import aggregate_coverage, coverage_totals
from evals.harness.reference import load_case
from evals.harness.run import _run_mode
from stride_service.deployment import Deployment
from stride_service.evidence import evidence_catalog
from stride_service.graph import (
    ENTRY_PREPARE,
    TIER_NODE_BY_GRAPH_NODE,
    analyze_node_name,
)
from stride_service.report import STRIDE_CATEGORIES
from stride_service.sampling import load_sampling
from tests.factories import TEST_TIER_ENV, ScriptedLlm
from tests.test_evals_modes import scripted_ruling

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "evals" / "corpus" / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


class QueuedLlm(ScriptedLlm):
    """Replays a different emission per call, so one pipeline serves two cases.

    ``_run_mode`` builds the pipeline once for the whole sweep, which is what
    makes "the next case still runs" a property worth testing — and what means
    a per-case difference has to come from the model rather than the build.
    """

    replies: list[str] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream: bool = False):
        queued = self.replies.pop(0) if self.replies else None
        default = self.reply
        self.reply = queued or default
        try:
            async for response in super().generate_content_async(llm_request, stream):
                yield response
        finally:
            self.reply = default


def proposal(case, category, evidence: dict[str, Any], sequence: int = 1) -> dict:
    reference = next(ref for ref in case.references if ref.category == category)
    return {
        "sequence": sequence,
        "title": reference.claim,
        "description": f"{reference.claim} Scripted for the sweep test.",
        "affected_element_ids": list(reference.affected_element_ids),
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


def sweep(monkeypatch, case, spoofing_first: dict[str, Any] | None) -> Any:
    """Two cases through one pipeline: the first optionally broken, then a clean one.

    ``spoofing_first`` is the spoofing agent's evidence on the *first* case
    only; ``None`` runs both cases clean.
    """
    label = case.sources[0].label
    first = spoofing_first
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
            replies=_replies_for(case, graph_node, first),
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
    if graph_node == "critic":
        return json.dumps(
            {"threats": [scripted_ruling(category) for category in STRIDE_CATEGORIES]}
        )
    for category in STRIDE_CATEGORIES:
        if graph_node == analyze_node_name(category):
            return json.dumps(
                {"threats": [proposal(case, category, sound_evidence(case))]}
            )
    return '{"threats": []}'


def _replies_for(case, graph_node: str, first: dict[str, Any] | None) -> list[str]:
    """The spoofing agent's first emission, when the test wants it broken."""
    if first is None or graph_node != analyze_node_name("spoofing"):
        return []
    return [json.dumps({"threats": [proposal(case, "spoofing", first)]})]


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
    assert payload["grounds"]["counts"]["unknown-attribute"] == len(STRIDE_CATEGORIES)
    assert payload["grounds"]["metrics"]["grounds_per_threat"] == 1.0


def test_a_fail_closed_case_is_counted_and_the_sweep_continues(monkeypatch, case):
    run = sweep(monkeypatch, case, FABRICATED)

    assert [f.kind for f in run.grounds_failures] == ["fail-closed"]
    assert run.grounds_failures[0].threat_ids == ("S-01",)
    assert run.grounds_failures[0].draft_count == len(STRIDE_CATEGORIES)
    # The next case still ran, which is the whole point.
    assert [entry.case_id for entry in run.grounds] == ["case-second"]


def test_an_invented_reference_is_counted_and_the_sweep_continues(monkeypatch, case):
    """An agent cannot mis-shape a ``Ground`` — it never writes one — so this is
    the whole of what its evidence selection can get wrong, and it must be
    counted as its own kind rather than pooled with unrelated join faults."""
    run = sweep(monkeypatch, case, INVENTED)

    assert [f.kind for f in run.grounds_failures] == ["unresolved-evidence"]
    assert [entry.case_id for entry in run.grounds] == ["case-second"]


@pytest.mark.parametrize("broken", [FABRICATED, INVENTED])
def test_a_counted_case_is_still_a_tier_1_failure(monkeypatch, case, broken):
    """Surviving the failure must not turn it green — the run still exits
    non-zero, and the case is named in the failure list."""
    run = sweep(monkeypatch, case, broken)

    assert any(failure.startswith(f"{case.id}:") for failure in run.failures)


def test_a_failed_case_names_its_kind_in_the_payload(monkeypatch, case):
    run = sweep(monkeypatch, case, FABRICATED)

    payload = run.payloads[0]
    assert payload["case"] == case.id
    assert payload["grounds_failure"]["kind"] == "fail-closed"
    assert "grounds" not in payload


def test_the_sweep_collects_every_case_s_coverage_rows(monkeypatch, case):
    """One case's rows are unreadable; the sweep is where they become a rate."""
    run = sweep(monkeypatch, case, None)

    assert len(run.coverage) == 2 * len(STRIDE_CATEGORIES)
    lanes = aggregate_coverage(run.coverage)
    assert all(lane.cases == 2 for lane in lanes)
    assert coverage_totals(lanes)["drafts"] == 2 * len(STRIDE_CATEGORIES)


def test_a_failed_case_contributes_no_coverage(monkeypatch, case):
    """No report, no accounting — a lane cannot be credited for a dead case."""
    run = sweep(monkeypatch, case, FABRICATED)

    assert len(run.coverage) == len(STRIDE_CATEGORIES)


def test_the_sweep_folds_latency_over_every_node_it_ran(monkeypatch, case):
    """``duration_ms`` is per execution and read back by nothing else."""
    run = sweep(monkeypatch, case, None)

    critic = run.latency["critic"]
    assert critic.executions == 2
    assert critic.slowest_ms <= critic.total_ms
    # The deterministic derivations are absent from the token totals and
    # present here, which is the difference the two folds exist to show.
    assert ENTRY_PREPARE in run.latency
    assert ENTRY_PREPARE not in run.usage
