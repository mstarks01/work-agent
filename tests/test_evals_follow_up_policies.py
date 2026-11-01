"""The follow-up orders the comparison reads, on the #1289 review's cases."""

from __future__ import annotations

import argparse
import json

import pytest

from analysis_service.bands import Band
from analysis_service.claims import UnknownRef
from analysis_service.questions import FollowUpNeeds
from evals.harness.follow_up_policies import (
    EFFORTS,
    POLICIES,
    command_follow_up_policies,
    readings,
)
from tests.factories import asking_threat, sample_analysis, sample_report

CRITICAL, LOW = 0, -3


def key(name: str) -> tuple[str, ...]:
    return ("", "", "", name, "", "")


def needs(evidence, named=None, waiting=None, bands=None) -> FollowUpNeeds:
    named = named or {}
    waiting = waiting if waiting is not None else {**evidence, **named}
    return FollowUpNeeds(
        evidence=evidence,
        named=named,
        waiting=waiting,
        bands={f: Band(order=o, label=str(o)) for f, o in bands.items()},
        refs={},
    )


def order(policy: str, of: FollowUpNeeds) -> list[str]:
    return [k[3] for _, k in POLICIES[policy](of)]


A, B, C = key("A"), key("B"), key("C")
SIZE = UnknownRef(
    element_id="store:orders-db", question="capacity-limits", facets=["size"]
)
PAIR = needs(
    {("stride", "crit"): frozenset({A, B}), ("stride", "low"): frozenset({C})},
    bands={("stride", "crit"): CRITICAL, ("stride", "low"): LOW},
)


def test_the_old_order_asked_a_low_singleton_before_a_critical_pair():
    """The review's counterexample, as ADR 0055 shipped it: C, then A and B."""
    assert order("completion-first", PAIR) == ["C", "A", "B"]


def test_the_shipped_order_asks_the_critical_pair_first():
    assert order("shipped", PAIR) == ["A", "B", "C"]


def test_a_critic_only_critical_fact_now_comes_first():
    critic_only = needs(
        {("stride", "low"): frozenset({C})},
        named={("stride", "crit"): frozenset({A})},
        bands={("stride", "crit"): CRITICAL, ("stride", "low"): LOW},
    )
    assert order("completion-first", critic_only) == ["C", "A"]
    assert order("shipped", critic_only) == ["A", "C"]


@pytest.mark.parametrize("policy", list(POLICIES))
def test_every_order_asks_each_open_fact_once(policy):
    keys = [k for _, k in POLICIES[policy](PAIR)]
    assert sorted(keys) == sorted({A, B, C})


def test_the_asked_effort_charges_only_the_facets_a_finding_needs():
    """A size-only finding costs one choice asked and four whole, and both
    counts complete it (#2029 package 1, ADR 0112)."""
    report = sample_report(analyses=[sample_analysis([asking_threat(SIZE)])])
    whole = {
        one.effort: one
        for one in readings("r", report)
        if one.policy == "shipped" and one.budget is None
    }
    assert (whole["asked"].choices, whole["whole"].choices) == (1, 4)
    assert whole["asked"].completed == whole["whole"].completed == 1
    at_one = {
        one.effort: one.completed
        for one in readings("r", report)
        if one.policy == "shipped" and one.budget == 1
    }
    assert at_one == {"asked": 1, "whole": 0}


def test_the_out_file_starts_with_the_record_of_each_report_read(tmp_path):
    """The ADR 0112 figures came from this file, so it names its inputs (#2029)."""
    report = sample_report(analyses=[sample_analysis([asking_threat(SIZE)])])
    path = tmp_path / "01.report.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out.json"
    args = argparse.Namespace(root=tmp_path, samples=None, out=out)

    assert command_follow_up_policies(args) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert list(written) == ["record", "summary"]
    assert written["record"]["command"] == "follow-up-policies"
    assert written["record"]["table"]["efforts"] == list(EFFORTS)
    assert [one["path"] for one in written["record"]["inputs"]] == [str(path)]
