"""The delivery trace of a missed must-find's lane (#2271)."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.candidates import CandidateSet, generate_candidates
from analysis_service.frameworks import package_for
from analysis_service.sources import render_fenced
from evals.harness import delivery
from evals.harness.bundle import read_report, stride_block
from evals.harness.reference import flows_by_case, load_corpus

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
def flows(report):
    (case,) = [
        case for case in load_corpus(REPO_ROOT / "evals/corpus") if case.id == CASE
    ]
    return flows_by_case([case], {CASE: report.system_model})[CASE]


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
    assert delivery.lead(reference, tampering, flows) == expected


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

    assert delivery.captured(material, "tampering") == tampering


def test_a_capture_the_rebuild_reproduces_agrees(report, tampering, flows):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, tampering, flows
    )

    assert traced.capture == "agrees"
    assert traced.delivered == traced.rebuilt == "draft-contains"


def test_a_capture_without_a_rebuilt_candidate_differs(report, tampering, flows):
    sent = CandidateSet(lane="tampering", candidates=tampering.candidates[:1])

    traced = delivery.trace_lane(
        ("process:background-worker", "store:database"),
        "tampering",
        report,
        sent,
        flows,
    )

    assert traced.capture == "differs"
    assert traced.delivered == "absent"
    assert traced.rebuilt == "equal"


def test_an_uncaptured_lane_reads_only_the_rebuild(report, flows):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, None, flows
    )

    assert traced.capture == "uncaptured"
    assert traced.delivered is None
    assert traced.rebuilt == "draft-contains"


def test_an_archived_lead_rule_is_one_the_archived_block_fired(report, flows):
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

    before = delivery.trace_lane(references, "tampering", report, None, flows)
    after = delivery.trace_lane(references, "tampering", trimmed, None, flows)

    assert before.archived_lead_rules == (
        "tampering-unprotected-transit-crossing",
        "tampering-unverified-write-to-store",
    )
    assert after.archived_lead_rules == ("tampering-unverified-write-to-store",)
