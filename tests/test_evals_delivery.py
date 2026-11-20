"""The delivery trace of a missed must-find's lane (#2271)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from analysis_service.candidates import CandidateSet, generate_candidates
from analysis_service.frameworks import package_for
from analysis_service.graph import unfence
from analysis_service.sources import render_fenced
from evals.harness import delivery, losses, panels, run
from evals.harness.artifact import load_artifact
from evals.harness.bundle import read_report, stride_block
from evals.harness.lane_replay import captured_lane
from evals.harness.ledger import Ledger
from evals.harness.modes import EvalRunError
from evals.harness.reference import development_cases, flows_by_case, load_corpus
from evals.harness.scorecard import charged_misses
from evals.harness.triggers import rebuilt_lane_inputs

REPO_ROOT = Path(__file__).resolve().parent.parent
CASE = "05-cookbook-queue-webapp"
REPORT = (
    REPO_ROOT
    / "evals/baselines/d4edd64-gpt-5.6-terra-fd1c49af/mstarks01-ab5d72fb.reports"
    / f"{CASE}.report.json"
)


@pytest.fixture(scope="module")
def report():
    return read_report(REPORT)


@pytest.fixture(scope="module")
def case():
    (held,) = [
        case for case in load_corpus(REPO_ROOT / "evals/corpus") if case.id == CASE
    ]
    return held


@pytest.fixture(scope="module")
def flows(report, case):
    return flows_by_case([case], {CASE: report.system_model})[CASE]


@pytest.fixture(scope="module")
def options(case):
    return case.declaration("stride").options


@pytest.fixture(scope="module")
def tampering(report):
    package = package_for("stride")
    catalog = report.assertions.catalog if report.assertions else None
    return generate_candidates(
        report.system_model, package.lanes, package.rules, catalog
    )["tampering"]


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (("process:background-worker", "store:database"), "equal"),
        (("store:message-queue",), "draft-contains"),
        (("process:web-application", "store:database"), "overlap"),
        (("entity:nobody",), "absent"),
    ],
)
def test_the_lead_is_the_strongest_relation_of_a_candidate(
    tampering, flows, reference, expected
):
    assert delivery.closest_relation(reference, tampering, flows) == expected


@pytest.fixture(scope="module")
def sent(report, options):
    """The tampering lane's artifacts, as a capture of today's rebuild."""
    return dict(rebuilt_lane_inputs("stride", report, options).artifacts["tampering"])


def _rendered(candidates: CandidateSet) -> str:
    return render_fenced(candidates.model_dump(mode="json"))


def _candidates(sent) -> CandidateSet:
    return CandidateSet.model_validate_json(unfence(sent["candidates"]))


def _captured(traced: delivery.LaneTrace) -> delivery.LaneComparison:
    assert traced.captured is not None
    return traced.captured


def test_the_captured_candidates_read_back_as_the_lane_was_sent(tampering):
    material = {
        "lanes": {"stride": {"tampering": {"candidates": _rendered(tampering)}}}
    }

    sent = captured_lane(material, "stride", "tampering")

    assert _candidates(sent) == tampering


@pytest.mark.parametrize(
    ("lane", "held"),
    [
        ("tampering", {"scope": "a scope"}),
        ("tampering", {"candidates": "", "drafts": "a stray key"}),
        ("spoofing", {"candidates": ""}),
    ],
)
def test_a_capture_with_no_candidates_a_stray_key_or_no_lane_is_refused(lane, held):
    material = {"lanes": {"stride": {"tampering": held}}}

    with pytest.raises(EvalRunError, match="capture holds"):
        captured_lane(material, "stride", lane)


def test_a_capture_the_rebuild_reproduces_agrees(report, sent, flows, options):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, sent, flows, options
    )

    assert _captured(traced).same_leads == "agrees"
    assert _captured(traced).same_lane_artifacts == "agrees"
    assert _captured(traced).delivered == traced.rebuilt == "draft-contains"


def test_a_capture_without_a_rebuilt_candidate_differs(
    report, tampering, sent, flows, options
):
    trimmed = CandidateSet(lane="tampering", candidates=tampering.candidates[:1])

    traced = delivery.trace_lane(
        ("process:background-worker", "store:database"),
        "tampering",
        report,
        {**sent, "candidates": _rendered(trimmed)},
        flows,
        options,
    )

    assert _captured(traced).same_leads == "differs"
    assert _captured(traced).same_lane_artifacts == "differs"
    assert _captured(traced).delivered == "absent"
    assert traced.rebuilt == "equal"


def _no_facts(candidates: CandidateSet) -> CandidateSet:
    return candidates.model_copy(
        update={
            "candidates": tuple(
                candidate.model_copy(update={"facts": {}})
                for candidate in candidates.candidates
            )
        }
    )


def _inverted_questions(candidates: CandidateSet) -> CandidateSet:
    return candidates.model_copy(
        update={"questions": dict.fromkeys(candidates.questions, "INVERTED QUESTION")}
    )


@pytest.mark.parametrize("change", [_no_facts, _inverted_questions])
def test_a_changed_candidate_payload_with_the_same_leads_is_not_equivalent(
    report, sent, flows, options, change
):
    """The #2314 reproduction: the same IDs, with other facts or questions."""
    changed = change(_candidates(sent))
    assert changed != _candidates(sent)

    traced = delivery.trace_lane(
        ("store:message-queue",),
        "tampering",
        report,
        {**sent, "candidates": _rendered(changed)},
        flows,
        options,
    )
    path = panels.preparation_panel(_one_row(traced)).path

    assert _captured(traced).same_leads == "agrees"
    assert _captured(traced).same_lane_artifacts == "differs"
    assert path is not None and path.fidelity == "known-different"


@pytest.mark.parametrize("artifact", ["scope", "reference_notes", "prior_cases"])
def test_a_changed_lane_artifact_differs(report, sent, flows, options, artifact):
    traced = delivery.trace_lane(
        ("store:message-queue",),
        "tampering",
        report,
        {**sent, artifact: sent[artifact] + "\nchanged"},
        flows,
        options,
    )

    assert _captured(traced).same_leads == "agrees"
    assert _captured(traced).same_lane_artifacts == "differs"


def test_a_lane_artifact_the_capture_lacks_differs(report, sent, flows, options):
    held = {name: text for name, text in sent.items() if name != "prior_cases"}

    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, held, flows, options
    )

    assert _captured(traced).same_lane_artifacts == "differs"


def test_an_agreeing_lane_leaves_the_evidence_payload_unverified(
    report, sent, flows, options
):
    """The evidence catalog is a shared key, which no rebuild reproduces."""
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, sent, flows, options
    )
    path = panels.preparation_panel(_one_row(traced)).path

    assert path is not None and path.fidelity == "unverified"
    assert "not compared: the shared key evidence_catalog" in path.evidence
    assert "not compared: the prepared user turn" in path.evidence


def _one_row(traced: delivery.LaneTrace) -> delivery.Delivery:
    card = run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus", Ledger())
    row = delivery.DeliveryRow(reference=card.rows[0], lane=traced)
    return delivery.Delivery(run=card.run, rows=(row,), re_keyed=0, uncaptured={})


def test_an_uncaptured_lane_reads_only_the_rebuild(report, flows, options):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, None, flows, options
    )

    assert traced.captured is None
    assert traced.rebuilt == "draft-contains"


def test_an_archived_lead_rule_is_one_the_archived_block_fired(report, flows, options):
    references = ("store:message-queue",)
    block = stride_block(report)
    kept = [
        rule
        for rule in block.fired_rules
        if rule != "tampering-unprotected-transit-crossing"
    ]
    trimmed = report.model_copy(
        update={
            "analyses": [
                block.model_copy(update={"fired_rules": kept})
                if each is block
                else each
                for each in report.analyses
            ]
        }
    )

    before = delivery.trace_lane(references, "tampering", report, None, flows, options)
    after = delivery.trace_lane(references, "tampering", trimmed, None, flows, options)

    assert before.archived_lead_rules == (
        "tampering-unprotected-transit-crossing",
        "tampering-unverified-write-to-store",
    )
    assert after.archived_lead_rules == ("tampering-unverified-write-to-store",)


BASELINE = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)


def _trace(artifact: Path) -> delivery.Delivery:
    corpus = REPO_ROOT / "evals/corpus"
    return delivery.trace(
        run.scorecard_of(artifact, corpus, Ledger()),
        load_artifact(artifact),
        development_cases(load_corpus(corpus)),
    )


def test_each_charged_miss_is_a_row_and_only_place_and_unled_are_traced():
    card = run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus", Ledger())

    traced = _trace(BASELINE)

    assert [row.reference for row in traced.rows] == list(charged_misses(card.rows))
    assert {row.reference.cause for row in traced.rows if row.lane is None}.isdisjoint(
        losses.SILENT
    )
    assert all(
        row.lane is not None
        for row in traced.rows
        if row.reference.cause in losses.SILENT
    )


def test_a_sweep_with_no_lane_capture_reads_only_the_rebuild():
    traced = _trace(BASELINE)

    lanes = [row.lane for row in traced.rows if row.lane is not None]
    assert lanes
    assert {lane.captured for lane in lanes} == {None}
    assert set(traced.uncaptured) == {
        row.reference.case for row in traced.rows if row.lane is not None
    }


CAPTURED_CASE = "02-iot-fleet-telemetry"


def _captured_sweep(tmp_path: Path, extra: dict[str, str]) -> Path:
    """The Baseline with a lane capture of today's rebuild for one case.

    ``extra`` is added to each lane's artifacts.
    """
    shutil.copy(BASELINE, tmp_path / BASELINE.name)
    reports = BASELINE.with_suffix(".reports")
    shutil.copytree(reports, tmp_path / reports.name)
    report = read_report(reports / f"{CAPTURED_CASE}.report.json")
    (declared,) = [
        each
        for each in load_corpus(REPO_ROOT / "evals/corpus")
        if each.id == CAPTURED_CASE
    ]
    sent = rebuilt_lane_inputs(
        "stride", report, declared.declaration("stride").options
    ).artifacts
    material = {
        "prepared": {"user": "the input prepare handed the lanes"},
        "lanes": {"stride": {lane: {**held, **extra} for lane, held in sent.items()}},
    }
    lanes = tmp_path / reports.name / f"{CAPTURED_CASE}.lanes.json"
    lanes.write_text(json.dumps(material))
    return tmp_path / BASELINE.name


def test_a_captured_lane_is_read_as_the_sweep_sent_it(tmp_path):
    traced = _trace(_captured_sweep(tmp_path, {}))

    (row,) = [
        row for row in traced.rows if row.reference.case == CAPTURED_CASE and row.lane
    ]
    assert row.lane is not None
    captured = _captured(row.lane)
    assert captured.same_leads == captured.same_lane_artifacts == "agrees"
    assert captured.delivered == row.lane.rebuilt == "equal"
    assert CAPTURED_CASE not in traced.uncaptured


def test_a_refused_lane_capture_reads_as_uncaptured_with_its_reason(tmp_path):
    traced = _trace(_captured_sweep(tmp_path, {"drafts": "a stray key"}))

    (row,) = [
        row for row in traced.rows if row.reference.case == CAPTURED_CASE and row.lane
    ]
    assert row.lane is not None and row.lane.captured is None
    assert "drafts" in traced.uncaptured[CAPTURED_CASE]


def test_the_command_writes_the_trace_with_its_record(tmp_path):
    out = tmp_path / "delivery.json"

    assert run.main(["delivery", str(BASELINE), "--out", str(out)]) == 0

    written = json.loads(out.read_text())
    assert written["record"]["command"] == "delivery"
    assert len(written["rows"]) == len(_trace(BASELINE).rows)


def test_a_shared_endpoint_is_a_relation_and_no_lead_rule(report, flows, options):
    """The worker is an endpoint of a candidate's flow, and no candidate names it."""
    traced = delivery.trace_lane(
        ("process:background-worker",), "tampering", report, None, flows, options
    )

    assert traced.rebuilt == "draft-contains"
    assert traced.archived_lead_rules == ()


def test_the_re_keyed_must_finds_are_counted_beside_the_rows():
    """The 2198870 Baseline cites 12 must-finds by element IDs the corpus re-keyed."""
    artifact = (
        REPO_ROOT
        / "evals/baselines/2198870-gpt-5.6-terra-6cd39a95/mstarks01-e5d71171.json"
    )

    assert _trace(artifact).re_keyed == 12
