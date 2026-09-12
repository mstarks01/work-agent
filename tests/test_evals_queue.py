"""The queue spends a reviewer's attention, so what it spends it on is tested.

Three properties carry the design: it never asks twice, it asks the most
informative question first, and it cannot leak which configuration produced a
finding. The last one is why :class:`~evals.harness.queue.QueueItem` exists at
all rather than a report claim being passed straight through.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from evals.harness.fingerprint import components_for, version_for
from evals.harness.ledger import Ledger
from evals.harness.queue import (
    PRIORITIES,
    Finding,
    build,
    merge_runs,
    priority_of,
    summarise,
)
from tests.eval_factories import SAMPLE_CONTENT, SAMPLE_PROSE, cast, other_content

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
    content=SAMPLE_CONTENT,
    prose=SAMPLE_PROSE,
):
    """One produced finding as the queue holds it.

    ``content`` is the structural digest a vote on it records. Passing another
    value is how a test says the claim has been re-argued since.
    """
    return Finding(
        case=case,
        framework="stride",
        lane="spoofing",
        title=title,
        description="An attacker does the thing.",
        element_ids=(target,),
        content=content,
        prose=prose,
        quotes=("the source says so",),
        verb=verb,
        seen_in=seen_in,
        runs=runs,
    )


def value_of(item):
    """The components the queue would build for this finding, at its own version."""
    return components_for(
        "stride",
        "spoofing",
        item.element_ids,
        FLOWS["01"],
        verb=item.verb,
        scope=item.case,
    )


def test_an_answered_finding_never_comes_back():
    """The economic argument for the fingerprint, as one assertion."""
    known = finding()
    ledger = Ledger(
        votes=[cast(value_of(known), "01", "up", "sam", content=known.content)]
    )

    assert build([known], FLOWS, ledger) == []


def test_naming_a_voter_asks_only_what_that_voter_has_not_answered():
    """What makes a second, independent opinion possible."""
    known = finding()
    ledger = Ledger(
        votes=[cast(value_of(known), "01", "up", "sam", content=known.content)]
    )

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
        content=SAMPLE_CONTENT,
        prose=SAMPLE_PROSE,
    )
    as_ends = Finding(
        case="01",
        framework="stride",
        lane="spoofing",
        title="Cited as endpoints",
        description="",
        element_ids=("process:a", "process:b"),
        verb="intercept",
        content=SAMPLE_CONTENT,
        prose=SAMPLE_PROSE,
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


def test_a_second_voter_is_not_told_how_the_first_one_voted():
    """The queue does not rank by the reference pool, which is built from votes.

    A finding this voter has not answered can only be in that pool because
    somebody else put it there, so the weight, the position and the printed
    reason all carried the earlier voter's verdict into the second opinion --
    the one pass that has to be independent.
    """
    pooled = finding(title="Ada said yes", target="process:p")
    untouched = finding(title="Nobody voted", target="process:n")
    ledger = Ledger()
    # The finding's own case, so ada's vote really does key onto it. It read
    # "01-payments-checkout" against a finding filed under "01", which the key
    # ignored before the scope entered it and which would now quietly make this
    # a test about two unpooled findings.
    ledger.votes.append(
        cast(value_of(pooled), pooled.case, "up", "ada", content=pooled.content)
    )

    items = build([pooled, untouched], FLOWS, ledger, voter="bob")

    assert {item.finding.title for item in items} == {"Ada said yes", "Nobody voted"}
    assert len({item.priority for item in items}) == 1, (
        "one of them was ranked by how ada voted"
    )
    assert len({item.why for item in items}) == 1, (
        "and the reason printed beside it said so"
    )


def test_the_first_reason_wins_rather_than_the_sum():
    """Summing would rank new-and-unmatched above volatile, which is wrong."""
    volatile = finding(seen_in=1, runs=3)
    weight, why = priority_of(volatile)
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
    elsewhere = finding(target="process:z")
    ledger = Ledger(
        votes=[
            cast(
                value_of(elsewhere),
                "01",
                "up",
                "sam",
                content=elsewhere.content,
            )
        ]
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

    ASVS composes no verb, so keying it under STRIDE's rule would read a field
    it never has; STRIDE names no requirement, so ASVS's rule would read one it
    never has.
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
        content=SAMPLE_CONTENT,
        prose=SAMPLE_PROSE,
    )
    items = {
        item.finding.framework: item for item in build([stride, asvs], FLOWS, Ledger())
    }

    assert items["stride"].fingerprint.startswith(f"v{version_for('stride')}:")
    assert items["asvs"].fingerprint.startswith(f"v{version_for('asvs')}:")


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
            content=SAMPLE_CONTENT,
            prose=SAMPLE_PROSE,
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


class TestNeedsEvidenceIsNotAnAnswer:
    """The button says "Needs more evidence". It has to mean that.

    Every other verdict answers the question. This one says the reviewer could
    not answer it from what they were shown -- so counting it as answered took
    the finding out of their queue for good, and the one input asking to see
    more was the one that guaranteed they never would.
    """

    def _ledger(self, item, *, sitting):
        led = Ledger()
        led.votes.append(
            cast(
                value_of(item),
                # The finding's own case. The key reads the case, so a vote
                # cast under "01-payments-checkout" against a finding that says
                # "01" is mis-keyed.
                item.case,
                "needs-evidence",
                "ada",
                content=item.content,
                sitting=sitting,
            )
        )
        return led

    def test_it_does_not_come_back_in_the_sitting_it_was_cast_in(self):
        """Otherwise the reviewer is handed it again on the next click."""
        unanswerable = finding(title="Cannot judge this", target="process:a")
        led = self._ledger(unanswerable, sitting="web-1")

        queue = build([unanswerable], FLOWS, led, voter="ada", sitting="web-1")

        assert queue == []

    def test_it_comes_back_in_a_later_sitting(self):
        """Which is what the reviewer asked for: ask again, over whatever
        evidence exists by then."""
        unanswerable = finding(title="Cannot judge this", target="process:a")
        led = self._ledger(unanswerable, sitting="web-1")

        queue = build([unanswerable], FLOWS, led, voter="ada", sitting="web-2")

        assert [item.finding.title for item in queue] == ["Cannot judge this"]

    def test_a_real_answer_still_never_comes_back(self):
        """The economics the fingerprint buys: a vote is spent once and kept."""
        answered = finding(title="Judged", target="process:b")
        led = Ledger()
        led.votes.append(
            cast(
                value_of(answered),
                "01",
                "up",
                "ada",
                content=answered.content,
                sitting="web-1",
            )
        )

        assert build([answered], FLOWS, led, voter="ada", sitting="web-2") == []

    def test_another_voter_is_unaffected_by_it(self):
        """It records what one reviewer could not judge, not a fact about the
        finding."""
        unanswerable = finding(title="Cannot judge this", target="process:a")
        led = self._ledger(unanswerable, sitting="web-1")

        queue = build([unanswerable], FLOWS, led, voter="bob", sitting="web-1")

        assert [item.finding.title for item in queue] == ["Cannot judge this"]


class TestARewrittenFindingIsAskedAgain:
    """A vote answers words, and the words move between runs.

    The fingerprint is blind to prose on purpose, so an answer on an earlier
    wording outlives a rewrite of the whole finding. Here the same topic comes
    back to the person who answered it, and to nobody else.
    """

    def _ledger(self, item, voter="sam", **kwargs):
        led = Ledger()
        led.votes.append(
            cast(
                value_of(item),
                item.case,
                kwargs.pop("verdict", "up"),
                voter,
                content=item.content,
                prose=item.prose,
                **kwargs,
            )
        )
        return led

    def test_the_same_argument_stays_answered(self):
        known = finding()
        led = self._ledger(known)

        assert build([known], FLOWS, led, voter="sam") == []

    def test_a_re_argued_finding_comes_back_to_its_voter(self):
        answered_before = finding()
        led = self._ledger(answered_before)
        reargued = finding(content=other_content())

        queue = build([reargued], FLOWS, led, voter="sam")

        assert len(queue) == 1
        assert "re-argued" in queue[0].why

    def test_a_rewording_alone_does_not_come_back(self):
        """A model writes new prose every run, and re-asking on that would
        spend a whole sitting on paraphrases."""
        answered_before = finding()
        led = self._ledger(answered_before)
        reworded = finding(title="Said another way", prose="p1:1111111111111111")

        assert build([reworded], FLOWS, led, voter="sam") == []

    def test_the_reader_sees_their_own_earlier_answer(self):
        """What makes the second look a re-read rather than a fresh question."""
        answered_before = finding()
        led = self._ledger(answered_before, verdict="down", reason="too-vague")
        reargued = finding(content=other_content())

        queue = build([reargued], FLOWS, led, voter="sam")

        assert queue[0].previously == "down (too-vague)"

    def test_a_re_argument_ranks_below_a_finding_nobody_has_answered(self):
        """The topic already has an answer, which buys less than one nobody
        has answered at all."""
        answered_before = finding()
        led = self._ledger(answered_before)
        reargued = finding(title="Re-argued since", content=other_content())
        fresh = finding(target="process:b", title="Never asked")

        queue = build([reargued, fresh], FLOWS, led, voter="sam")

        assert [item.finding.title for item in queue] == [
            "Never asked",
            "Re-argued since",
        ]

    def test_an_unnamed_queue_stays_blind(self):
        """Re-offering here would tell this reviewer that somebody else had
        answered the earlier version. That is the leak the deleted `unmatched`
        row was deleted for."""
        answered_before = finding()
        led = self._ledger(answered_before)
        reargued = finding(content=other_content())

        assert build([reargued], FLOWS, led) == []
        assert not build([reargued], FLOWS, led, voter="ada")[0].previously

    def test_a_needs_evidence_answer_is_not_a_rewrite(self):
        """It comes back because the reviewer asked it to, and the reason has
        to say that rather than blaming the prose."""
        unanswerable = finding(title="Cannot judge this")
        led = self._ledger(unanswerable, verdict="needs-evidence", sitting="web-1")

        queue = build([unanswerable], FLOWS, led, voter="sam", sitting="web-2")

        assert len(queue) == 1
        assert "re-argued" not in queue[0].why

    def test_the_item_carries_the_digests_a_vote_will_record(self):
        item = build([finding()], FLOWS, Ledger())[0]

        assert (item.content, item.prose) == (SAMPLE_CONTENT, SAMPLE_PROSE)
