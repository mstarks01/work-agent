"""The queue spends a reviewer's attention, so what it spends it on is tested.

Three properties carry the design: it never asks twice, it asks the most
informative question first, and it cannot leak which configuration produced a
finding. The last one is why :class:`~evals.harness.queue.QueueItem` exists at
all rather than a report claim being passed straight through.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from evals.harness.fingerprint import components_for, fingerprint
from evals.harness.ledger import Ledger, cast
from evals.harness.queue import (
    PRIORITIES,
    Finding,
    build,
    merge_runs,
    priority_of,
    summarise,
)

FLOWS = {
    "01": {"flow:a-to-b:call": ("process:a", "process:b")},
}


def finding(
    title="A threat",
    target="process:a",
    case="01",
    seen_in=1,
    runs=1,
    verb="impersonate",
):
    return Finding(
        case=case,
        framework="stride",
        lane="spoofing",
        title=title,
        description="An attacker does the thing.",
        element_ids=(target,),
        quotes=("the source says so",),
        verb=verb,
        seen_in=seen_in,
        runs=runs,
    )


def value_of(item):
    """The components the queue would build for this finding, at its own version."""
    return components_for(
        "stride", "spoofing", item.element_ids, FLOWS["01"], verb=item.verb
    )


def test_an_answered_finding_never_comes_back():
    """The economic argument for the fingerprint, as one assertion."""
    known = finding()
    ledger = Ledger(votes=[cast(value_of(known), "01", "up", "sam")])

    assert build([known], FLOWS, ledger) == []


def test_naming_a_voter_asks_only_what_that_voter_has_not_answered():
    """What makes a second, independent opinion possible."""
    known = finding()
    ledger = Ledger(votes=[cast(value_of(known), "01", "up", "sam")])

    assert build([known], FLOWS, ledger, voter="sam") == []
    assert len(build([known], FLOWS, ledger, voter="ada")) == 1


def test_one_finding_in_two_runs_is_one_question():
    """Deduplicated by fingerprint; two runs agreeing is not two questions."""
    items = build([finding(), finding()], FLOWS, Ledger())
    assert len(items) == 1


def test_a_flow_and_its_endpoints_are_one_question():
    """The endpoint resolution that stops one finding being asked twice."""
    as_flow = Finding(
        case="01",
        framework="stride",
        lane="spoofing",
        title="Cited as a flow",
        description="",
        element_ids=("flow:a-to-b:call",),
        verb="intercept",
    )
    as_ends = Finding(
        case="01",
        framework="stride",
        lane="spoofing",
        title="Cited as endpoints",
        description="",
        element_ids=("process:a", "process:b"),
        verb="intercept",
    )
    assert len(build([as_flow, as_ends], FLOWS, Ledger())) == 1


def test_a_volatile_finding_outranks_an_unmatched_one():
    """Volatility settles a recall number two ways; it is worth the click."""
    volatile = finding(title="Sometimes found", target="process:v", seen_in=2, runs=5)
    unmatched = finding(title="Always found", target="process:u")

    items = build([unmatched, volatile], FLOWS, Ledger())
    assert [item.finding.title for item in items] == [
        "Sometimes found",
        "Always found",
    ]
    assert items[0].volatile
    assert not items[1].volatile


def test_a_finding_already_in_the_pool_ranks_below_an_unmatched_one():
    pooled = finding(title="Known good", target="process:p")
    unmatched = finding(title="Never seen", target="process:n")
    pool = frozenset({fingerprint(value_of(pooled))})

    items = build([pooled, unmatched], FLOWS, Ledger(), reference_pool=pool)
    assert [item.finding.title for item in items] == ["Never seen", "Known good"]


def test_the_first_reason_wins_rather_than_the_sum():
    """Summing would rank new-and-unmatched above volatile, which is wrong."""
    volatile = finding(seen_in=1, runs=3)
    weight, why = priority_of(volatile, in_reference_set=True)
    assert weight == 30
    assert "some runs and not others" in why


def test_every_priority_row_carries_a_reason_a_reviewer_can_read():
    for name, weight, why in PRIORITIES:
        assert name and weight > 0
        assert len(why) > 40, f"{name} needs a reason, not a label"


def test_the_order_is_stable_across_rebuilds():
    """A reviewer who steps away comes back to the same queue."""
    findings = [finding(title=f"Threat {n}", target=f"process:{n}") for n in range(8)]
    first = [item.fingerprint for item in build(findings, FLOWS, Ledger())]
    second = [item.fingerprint for item in build(reversed(findings), FLOWS, Ledger())]
    assert first == second


def test_a_queue_item_carries_no_configuration():
    """Blind by construction: there is no field for a model name to sit in."""
    item = build([finding()], FLOWS, Ledger())[0]
    payload = item.to_json()

    forbidden = {"config", "model", "vendor", "tier", "temperature", "run"}
    assert not forbidden & set(payload)
    assert "config" not in repr(item.finding)


def test_a_case_with_no_flow_map_still_queues():
    """A case the caller did not resolve flows for is not a crash."""
    items = build([finding(case="99")], {}, Ledger())
    assert len(items) == 1


def test_the_summary_counts_what_a_reviewer_decides_from(tmp_path):
    ledger = Ledger(
        votes=[cast(value_of(finding(target="process:z")), "01", "up", "sam")]
    )
    items = build(
        [finding(target="process:a"), finding(case="02", target="process:b")],
        FLOWS,
        ledger,
    )
    summary = summarise(items, ledger)

    assert summary["waiting"] == 2
    assert summary["by_case"] == {"01": 1, "02": 1}
    assert summary["voters"] == ["sam"]
    assert summary["pool"] == 1


def test_a_stride_finding_with_no_verb_fails_closed():
    """Never a silent fall back to the weaker rule for a package that has one."""
    with pytest.raises(Exception, match="verb"):
        build([finding(verb=None)], FLOWS, Ledger())


def test_each_framework_is_keyed_by_its_own_rule():
    """A sweep carries both packages, and they do not identify claims alike.

    ASVS composes no verb, so keying it at version 2 would read a field it never
    has; STRIDE names no requirement, so version 3 would read one it never has.
    The version rides in the value, so the two cannot be compared by accident
    either.
    """
    stride = finding(target="process:a")
    asvs = Finding(
        case="01",
        framework="asvs",
        lane="authentication",
        title="A requirement ruling",
        description="",
        element_ids=("process:a",),
        identifier="V6.2.1",
    )
    items = {
        item.finding.framework: item for item in build([stride, asvs], FLOWS, Ledger())
    }

    assert items["stride"].fingerprint.startswith("v2:")
    assert items["asvs"].fingerprint.startswith("v3:")


def test_two_requirements_in_one_chapter_are_two_questions():
    """One vote must not answer for a requirement nobody read."""

    def ruling(identifier):
        return Finding(
            case="01",
            framework="asvs",
            lane="authentication",
            title=f"Ruling on {identifier}",
            description="",
            element_ids=("process:a",),
            identifier=identifier,
        )

    items = build([ruling("V6.2.1"), ruling("V6.2.2")], FLOWS, Ledger())

    assert len({item.fingerprint for item in items}) == 2


class TestTheRunCountsComeFromTheRuns:
    """``merge_runs`` is where ``volatile`` stops being a field nobody sets.

    The counts are read off several sweeps of one configuration rather than
    declared on a finding, so a sitting held over one artifact reports 1 of 1
    and asks its questions in the other two priorities' order.
    """

    def test_a_finding_two_sweeps_of_five_produced_is_volatile(self):
        steady = finding(title="Every time", target="process:a")
        sometimes = finding(title="Sometimes", target="process:b")
        runs = [[steady, sometimes], [steady, sometimes], [steady], [steady], [steady]]

        merged = {row.title: row for row in merge_runs(runs, FLOWS)}

        assert (merged["Sometimes"].seen_in, merged["Sometimes"].runs) == (2, 5)
        assert (merged["Every time"].seen_in, merged["Every time"].runs) == (5, 5)

    def test_one_artifact_makes_nothing_volatile(self):
        merged = merge_runs([[finding()]], FLOWS)

        assert (merged[0].seen_in, merged[0].runs) == (1, 1)
        assert build(merged, FLOWS, Ledger())[0].volatile is False

    def test_a_run_naming_one_identity_twice_counts_once_for_that_run(self):
        """The denominator is runs, so a repeated lane is a different question."""
        twice = [finding(title="First spelling"), finding(title="Second spelling")]

        merged = merge_runs([twice, [finding(title="First spelling")]], FLOWS)

        assert len(merged) == 1
        assert (merged[0].seen_in, merged[0].runs) == (2, 2)

    def test_the_merged_finding_is_the_one_the_queue_asks_about(self):
        """Merging and building key alike, or a run count rides a stranger."""
        sometimes = finding(title="Sometimes", target="process:b")
        merged = merge_runs([[sometimes], [finding()]], FLOWS)

        items = {item.finding.title: item for item in build(merged, FLOWS, Ledger())}

        assert items["Sometimes"].volatile is True
        assert "some runs and not others" in items["Sometimes"].why
