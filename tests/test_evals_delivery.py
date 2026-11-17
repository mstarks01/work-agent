"""The delivery trace of a missed must-find's lane (#2271)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from analysis_service.candidates import CandidateSet, generate_candidates
from analysis_service.frameworks import package_for
from analysis_service.sources import render_fenced
from evals.harness import delivery, losses, run
from evals.harness.artifact import load_artifact
from evals.harness.bundle import read_report, stride_block
from evals.harness.reference import development_cases, flows_by_case, load_corpus
from evals.harness.scorecard import charged_misses

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


def test_the_captured_candidates_read_back_as_the_lane_was_sent(tampering):
    material = {
        "lanes": {
            "stride": {
                "tampering": {
                    "candidates": render_fenced(tampering.model_dump(mode="json"))
                }
            }
        }
    }

    assert delivery.sent_candidates(material, "tampering") == tampering


def test_a_capture_the_rebuild_reproduces_agrees(report, tampering, flows, options):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, tampering, flows, options
    )

    assert traced.capture == "agrees"
    assert traced.delivered == traced.rebuilt == "draft-contains"


def test_a_capture_without_a_rebuilt_candidate_differs(
    report, tampering, flows, options
):
    sent = CandidateSet(lane="tampering", candidates=tampering.candidates[:1])

    traced = delivery.trace_lane(
        ("process:background-worker", "store:database"),
        "tampering",
        report,
        sent,
        flows,
        options,
    )

    assert traced.capture == "differs"
    assert traced.delivered == "absent"
    assert traced.rebuilt == "equal"


def test_an_uncaptured_lane_reads_only_the_rebuild(report, flows, options):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, None, flows, options
    )

    assert traced.capture == "uncaptured"
    assert traced.delivered is None
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
        run.scorecard_of(artifact, corpus),
        load_artifact(artifact),
        development_cases(load_corpus(corpus)),
    )


def test_each_charged_miss_is_a_row_and_only_place_and_unled_are_traced():
    card = run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus")

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
    assert {lane.capture for lane in lanes} == {"uncaptured"}
    assert {lane.delivered for lane in lanes} == {None}
    assert set(traced.uncaptured) == {
        row.reference.case for row in traced.rows if row.lane is not None
    }


def test_a_captured_lane_is_read_as_the_sweep_sent_it(tmp_path):
    shutil.copy(BASELINE, tmp_path / BASELINE.name)
    reports = BASELINE.with_suffix(".reports")
    shutil.copytree(reports, tmp_path / reports.name)
    case = "02-iot-fleet-telemetry"
    report = read_report(reports / f"{case}.report.json")
    package = package_for("stride")
    catalog = report.assertions.catalog if report.assertions else None
    sent = generate_candidates(
        report.system_model, package.lanes, package.rules, catalog
    )
    material = {
        "prepared": {"user": "the input prepare handed the lanes"},
        "lanes": {
            "stride": {
                lane: {"candidates": render_fenced(held.model_dump(mode="json"))}
                for lane, held in sent.items()
            }
        },
    }
    (tmp_path / reports.name / f"{case}.lanes.json").write_text(json.dumps(material))

    traced = _trace(tmp_path / BASELINE.name)

    (row,) = [row for row in traced.rows if row.reference.case == case and row.lane]
    assert row.lane is not None
    assert row.lane.capture == "agrees"
    assert row.lane.delivered == row.lane.rebuilt == "equal"
    assert case not in traced.uncaptured


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
