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

import analysis_service.fan_in as fan_in_module
import analysis_service.graph as graph_module
from analysis_service.critic import CriticOutputError
from analysis_service.deployment import Deployment
from analysis_service.fan_in import DraftJoinError
from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.graph import (
    ENTRY_PREPARE,
    STATE_INPUT_TEXT,
    analyze_node_name,
    tier_node_by_graph_node,
)
from analysis_service.sampling import load_sampling
from evals.harness import modes
from evals.harness.archive import kind_of
from evals.harness.bundle import reports_dir, write_failures
from evals.harness.coverage import aggregate_coverage, coverage_totals
from evals.harness.reference import load_case
from evals.harness.run import _run_mode
from tests.factories import DEFAULT_FRAMEWORKS, EVAL_MODEL, TEST_TIER_ENV, ScriptedLlm
from tests.test_evals_modes import lane_of, reaching_refs, scripted_ruling

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


def sound_evidence(case, category) -> dict[str, Any]:
    """Real catalog entries, verified by set membership, always.

    Chosen to reach the elements the category's first reference cites, since
    the fan-in drops a cited element the grounds do not reach (#441).
    """
    reference = next(
        ref for ref in case.claims_for("stride") if ref.category == category
    )
    return {"evidence_refs": reaching_refs(case, reference.affected_element_ids)}


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


def sweep(
    monkeypatch,
    case,
    spoofing_first: dict[str, Any] | None,
    *,
    trailing: int = 0,
    in_flight: int = 1,
    critic_first: list[str] | None = None,
) -> Any:
    """Two cases through one pipeline: the first optionally broken, then a clean one.

    ``spoofing_first`` is the spoofing agent's evidence on the *first* case
    only; ``None`` runs both cases clean.

    ``trailing`` appends further clean cases after those two. A sweep that
    stops on its last case leaves nothing behind it, so proving that the cases
    *after* a stop are named takes a case after the stop.

    ``critic_first`` replaces the critic's replies on the first case.
    """
    label = case.sources[0].label
    first = spoofing_first
    if first is DEAD:
        first = None
        real_join = fan_in_module.join_drafts
        calls: list[int] = []

        def join_once_dead(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise DraftJoinError("draft 'S-01' cites something no agent can")
            return real_join(*args, **kwargs)

        monkeypatch.setattr(fan_in_module, "join_drafts", join_once_dead)
    if first is not None and "quotes" in first:
        first = first | {
            "quotes": [{**quote, "source_label": label} for quote in first["quotes"]]
        }

    def resolve(tier_node: str) -> BaseLlm:
        graph_node = next(
            node for node, tier in TIER_NODE_BY_GRAPH_NODE.items() if tier == tier_node
        )
        return QueuedLlm(
            model=EVAL_MODEL,
            reply=_reply_for(case, graph_node),
            lane_replies=_lane_replies(case, graph_node),
            queued=_queued_for(case, graph_node, first),
            first_replies=(
                critic_first
                if critic_first is not None and graph_node == "critic_stride"
                else _critic_first(case, graph_node, first)
            ),
        )

    pipeline = modes.build_eval_pipeline(
        ENTRY_PREPARE,
        resolve_model=resolve,
        sampling=load_sampling(REPO_ROOT / "config" / "sampling.toml"),
    )
    monkeypatch.setattr(modes, "build_eval_pipeline", lambda *a, **k: pipeline)
    second = replace(case, meta=case.meta.model_copy(update={"id": "case-second"}))
    later = [
        replace(case, meta=case.meta.model_copy(update={"id": f"case-{n + 3}"}))
        for n in range(trailing)
    ]
    # A real deployment even though the pipeline is scripted: the sweep folds
    # each execution's tier and sampling into its provenance record, and both
    # come from the deployment rather than from the graph.
    deployment = Deployment.from_env(env=TEST_TIER_ENV)
    return asyncio.run(
        _run_mode([case, second, *later], "analysis", deployment, in_flight=in_flight)
    )


def _reply_for(case, graph_node: str) -> str:
    if graph_node == "critic_stride":
        return json.dumps(
            {
                "claims": [
                    scripted_ruling(case, category) for category in STRIDE_CATEGORIES
                ]
            }
        )
    for category in STRIDE_CATEGORIES:
        if graph_node == analyze_node_name("stride", category):
            return json.dumps(
                {"claims": [proposal(case, category, sound_evidence(case, category))]}
            )
    return '{"claims": []}'


def _critic_first(case, graph_node: str, first: dict[str, Any] | None) -> list[str]:
    """The critic's ruling on a first case whose spoofing draft was dropped.

    A broken spoofing emission loses its only ground, so the service drops
    ``S-01`` before the critic sees it, and a ruling on it would be a ruling on
    a claim nobody drafted.
    """
    if graph_node != "critic_stride" or first is None:
        return []
    rulings = [
        scripted_ruling(case, category)
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
            {"claims": [proposal(case, category, sound_evidence(case, category))]}
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
    # One catalog ground per draft, whichever kind best reaches the cited
    # elements (``reaching_refs``), so the kinds sum to the drafts and no
    # quote is among them.
    counts = stride["counts"]
    assert counts["quote"] == 0
    assert counts["grounds"] == len(STRIDE_CATEGORIES)
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


def test_a_case_that_fails_at_report_validation_is_still_metered(monkeypatch, case):
    """The provider billed every node before the report was built. The sweep
    read a case's node runs off its finished report, so a case that failed
    there priced at zero: the second ASVS pre-flight of 2026-09-08 ran for
    3.5 minutes and its artifact said nothing was metered (#707)."""
    real_into_report = graph_module.Analysis.into_report
    dead_once = {"pending": True}

    def into_report_once_dead(self, *args, **kwargs):
        if dead_once.pop("pending", False):
            raise DraftJoinError("the report did not validate")
        return real_into_report(self, *args, **kwargs)

    monkeypatch.setattr(graph_module.Analysis, "into_report", into_report_once_dead)

    run = sweep(monkeypatch, case, None)

    assert [f.kind for f in run.grounds_failures] == ["other"]
    assert [entry.case_id for entry in run.grounds] == ["case-second"]
    # Both cases ran the critic; the dead one's execution is counted too, and
    # the usage fold that prices the sweep reads the same executions list.
    assert run.latency["critic_stride"].executions == 2


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


# --- A sweep that cannot go on still returns what it paid for -----------------


def _raise_on_second_report(monkeypatch, error: Exception) -> None:
    """Let the first case finish, then fail the second the way a provider does."""
    real_into_report = graph_module.Analysis.into_report
    seen: list[int] = []

    def into_report(self, *args, **kwargs):
        seen.append(1)
        if len(seen) == 2:
            raise error
        return real_into_report(self, *args, **kwargs)

    monkeypatch.setattr(graph_module.Analysis, "into_report", into_report)


def test_a_provider_fault_stops_the_sweep_without_losing_the_finished_cases(
    monkeypatch, case
):
    """The #886 loss, as a test.

    An end-to-end sweep hit an OpenRouter credit refusal part-way through. The
    fault left ``_run_mode`` as an exception, so ``command_run`` never reached
    ``build_artifact`` and the $2.85 already spent produced no artifact at all.
    The sweep still stops — the next case would be refused the same way — but
    the case that finished is measured, priced and kept.
    """
    _raise_on_second_report(
        monkeypatch, RuntimeError("This request requires more credits")
    )

    run = sweep(monkeypatch, case, None, trailing=1)

    # The first case survived, whole.
    assert [entry.case_id for entry in run.grounds] == [case.id]
    assert run.latency["critic_stride"].executions == 2
    # And the sweep says plainly that it did not finish.
    assert any("the sweep stopped here" in failure for failure in run.failures)
    assert any("more credits" in failure for failure in run.failures)
    # The case that stopped it ran, and the provider billed what finished, so
    # it is the sweep's failure and not one of the cases nobody attempted.
    # Counting it in both prices a billed case as one the sweep never reached.
    assert run.stopped_before == ("case-3",)
    assert run.payloads[-1]["case"] == "case-second"


def test_the_stopping_fault_names_its_type_even_when_its_message_is_empty(
    monkeypatch, case
):
    """A provider error often carries no message, and a blank line helps nobody."""
    _raise_on_second_report(monkeypatch, RuntimeError())

    run = sweep(monkeypatch, case, None, trailing=1)

    assert any("RuntimeError()" in failure for failure in run.failures)
    assert run.payloads[-1]["run_failure"] == "RuntimeError()"


def test_a_measured_fault_still_runs_the_rest_of_the_sweep(monkeypatch, case):
    """The other side of the rule, so the stop is not applied to everything.

    A refused model and a rejected draft are rates somebody asked for. If a
    reader widens the stopping branch to cover them, the sweep goes back to
    reporting neither — which is the failure ``sweep``'s own docstring exists
    for.
    """
    run = sweep(monkeypatch, case, DEAD)

    assert run.stopped_before == ()
    assert [entry.case_id for entry in run.grounds] == ["case-second"]


def test_every_measured_fault_is_one_the_failure_recorder_can_classify():
    """``MEASURED`` and ``record_failure`` are two readers of one rule.

    ``record_failure`` re-raises anything it cannot classify. If ``MEASURED``
    ever admitted such a type, the sweep would route it there and raise from
    inside the handler that exists to stop it raising.
    """
    from evals.harness.grounds import CAUGHT
    from evals.harness.run import MEASURED

    assert set(MEASURED) == {modes.EvalRunError, CriticOutputError, *CAUGHT}


def test_a_critic_that_fails_its_gate_is_captured_and_the_sweep_continues(
    monkeypatch, case, tmp_path
):
    """#1097: a critic gate failure is a measurement, and it leaves a record.

    The critic drops a draft, and its re-ask drops it too, so ``fail_review``
    raises. The case is a failure, the next case still runs, and the file
    beside the artifact holds the drafts and the rulings the gate refused.
    """
    silent_on_spoofing = json.dumps(
        {
            "claims": [
                scripted_ruling(case, category)
                for category in STRIDE_CATEGORIES
                if category != "spoofing"
            ]
        }
    )

    run = sweep(monkeypatch, case, None, critic_first=[silent_on_spoofing])

    assert run.stopped_before == ()
    assert [entry.case_id for entry in run.grounds] == ["case-second"]
    assert run.payloads[0]["run_failure"].startswith(f"{case.id}: ")
    assert list(run.failed_states) == [case.id]
    captured = run.failed_states[case.id]
    assert captured["error"].startswith("CriticOutputError(")
    assert {"drafts_stride", "reviewed_stride"} <= captured["state"].keys()
    assert STATE_INPUT_TEXT not in captured["state"]

    out = tmp_path / "sweep.json"
    write_failures(str(out), run.failed_states)
    written = reports_dir(out) / f"{case.id}.failure.json"
    assert json.loads(written.read_text("utf-8")) == json.loads(json.dumps(captured))
    assert kind_of(written.name) == "failure"


class TestCasesInFlight:
    """A sweep may run several cases at once, and the artifact may not know.

    The extraction mode is what asks for this: it has one node, so its wall
    clock is its model time, where an analysis sweep already fans six lanes out
    per case. Thirteen cases took 6.7 minutes of pure waiting, and a five-run
    spread — the unit that measures an ``extract.md`` edit honestly — took 33
    (#876).
    """

    def payload_of(self, run) -> str:
        """One sweep's measured output as bytes, which is the acceptance test.

        A byte-identical dump is what a risky refactor is held to here (#763),
        and the accumulators this change touches are the ones a concurrent loop
        could reorder: the payloads, the grounds, the coverage rows and the
        per-node execution counts.
        """
        return json.dumps(
            {
                "payloads": run.payloads,
                "failures": run.failures,
                "grounds": [entry.case_id for entry in run.grounds],
                "coverage": [list(row) for row in run.coverage],
                "executions": {
                    node: usage.executions for node, usage in run.latency.items()
                },
                "stopped_before": list(run.stopped_before),
            },
            sort_keys=True,
            default=str,
        )

    def test_a_batched_sweep_writes_what_a_sequential_one_writes(
        self, monkeypatch, case
    ):
        one = sweep(monkeypatch, case, None, trailing=2)
        many = sweep(monkeypatch, case, None, trailing=2, in_flight=4)

        assert self.payload_of(many) == self.payload_of(one)

    def test_the_cases_are_folded_in_corpus_order_whatever_order_they_finish(
        self, monkeypatch, case
    ):
        """The point of folding after the batch rather than as each case lands."""
        run = sweep(monkeypatch, case, None, trailing=2, in_flight=4)

        assert [payload["case"] for payload in run.payloads] == [
            case.id,
            "case-second",
            "case-3",
            "case-4",
        ]

    def test_the_cases_of_one_batch_really_do_overlap(self, monkeypatch, case):
        """The point of the change, and the only thing the artifact cannot show.

        Every other test here proves the loop still writes what it wrote, which
        a sweep that batched nothing would also pass. This one counts how many
        cases are inside ``run_analysis`` at once.
        """
        peak = 0
        live = 0
        real = modes.run_analysis

        async def counted(*args, **kwargs):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            try:
                # A yield to the loop, so a sibling can start. Without one the
                # coroutine could run to completion before the next is created,
                # and this would count 1 on a correctly batched sweep.
                await asyncio.sleep(0)
                return await real(*args, **kwargs)
            finally:
                live -= 1

        monkeypatch.setattr(modes, "run_analysis", counted)

        sweep(monkeypatch, case, None, trailing=2, in_flight=4)

        assert peak == 4

    def test_one_in_flight_runs_one_case_at_a_time(self, monkeypatch, case):
        """The default, and what every published sweep and the spend hold assume."""
        peak = 0
        live = 0
        real = modes.run_analysis

        async def counted(*args, **kwargs):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            try:
                await asyncio.sleep(0)
                return await real(*args, **kwargs)
            finally:
                live -= 1

        monkeypatch.setattr(modes, "run_analysis", counted)

        sweep(monkeypatch, case, None, trailing=2)

        assert peak == 1

    def test_a_stopping_fault_keeps_every_case_of_its_own_batch(
        self, monkeypatch, case
    ):
        """A batch's cases all ran and the provider billed them all, so a fault
        in one may not discard the others. What stops is the batch after it."""
        _raise_on_second_report(
            monkeypatch, RuntimeError("This request requires more credits")
        )

        run = sweep(monkeypatch, case, None, trailing=2, in_flight=2)

        # Cases one and two are the first batch. One of them stopped the sweep
        # — which one is up to the order they finished in — and the other is
        # measured whole rather than discarded with it.
        measured = [entry.case_id for entry in run.grounds]
        assert len(measured) == 1
        assert set(measured) <= {case.id, "case-second"}
        assert any("more credits" in failure for failure in run.failures)
        # Three and four are the batch nobody attempted.
        assert run.stopped_before == ("case-3", "case-4")
