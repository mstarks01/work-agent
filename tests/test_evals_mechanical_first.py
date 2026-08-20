"""The short-circuit only takes the direction the rule is trustworthy in.

Getting this backwards is the failure that matters: the rule's ``no-match`` is
wrong on 15 of 200 labelled matches, so a wrapper that believed it would take
7% of recall out silently. Every test here is about that asymmetry.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from evals.harness.identity import IdentityError, MechanicalFirstJudge
from evals.harness.judge import BucketRuling, ClaimPair, ClaimRuling, UnmatchedThreat
from tests.factories import valid_model

FLOWS = {"01": {"flow:a-to-b:call": ("process:a", "process:b")}}


class RecordingJudge:
    """A stand-in that says yes to everything and counts what reached it."""

    def __init__(self) -> None:
        self.pairs: list[ClaimPair] = []
        self.adjudications = 0

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        self.pairs.append(pair)
        return ClaimRuling(match=True, rationale="the judge was asked")

    def adjudicate(self, threat, system_model, sibling_claims) -> BucketRuling:
        self.adjudications += 1
        return BucketRuling(bucket="valid-unlisted", rationale="delegated")


def pair(
    *,
    reference_ids=("process:a",),
    candidate_ids=("process:a",),
    reference_verb="read",
    candidate_verb="read",
) -> ClaimPair:
    return ClaimPair(
        case="01",
        category="information-disclosure",
        reference_claim="An attacker reads the store.",
        candidate_claim="An attacker reads what the store holds.",
        reference_element_ids=reference_ids,
        candidate_element_ids=candidate_ids,
        reference_verb=reference_verb,
        candidate_verb=candidate_verb,
    )


def test_a_mechanical_match_is_the_answer_and_no_model_is_asked():
    """The direction the rule is right about 99% of the time."""
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    ruling = wrapper.equivalent(pair())

    assert ruling.match
    assert inner.pairs == [], "the judge was asked about a settled pair"
    assert (wrapper.settled, wrapper.delegated) == (1, 0)


def test_a_mechanical_no_match_goes_to_the_judge_anyway():
    """The direction the rule is wrong about 7% of the time.

    Believing it here would take those 15 labelled matches out of recall with
    nothing in the artifact to show it happened.
    """
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    ruling = wrapper.equivalent(pair(candidate_verb="alter"))

    assert ruling.match, "the judge's answer must win where the rule declines"
    assert len(inner.pairs) == 1
    assert (wrapper.settled, wrapper.delegated) == (0, 1)


def test_disjoint_elements_also_reach_the_judge():
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    wrapper.equivalent(pair(candidate_ids=("store:z",)))

    assert len(inner.pairs) == 1
    assert wrapper.settled == 0


def test_a_pair_the_rule_cannot_read_reaches_the_judge_rather_than_raising():
    """Every claim of a package that composes no verb takes this path."""
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    wrapper.equivalent(pair(candidate_verb=None))

    assert len(inner.pairs) == 1
    assert (wrapper.settled, wrapper.delegated) == (0, 1)


def test_the_rule_alone_would_have_raised_on_that_pair():
    """Pins why the wrapper catches: the rule refuses, it does not answer."""
    from evals.harness.identity import SubsetVerbIdentity

    with pytest.raises(IdentityError):
        SubsetVerbIdentity(FLOWS).equivalent(pair(candidate_verb=None))


def test_a_flow_cited_against_its_endpoints_is_settled_mechanically():
    """The endpoint resolution, through the wrapper the scorer uses."""
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    wrapper.equivalent(
        pair(
            reference_ids=("flow:a-to-b:call",),
            candidate_ids=("process:a", "process:b"),
            reference_verb="intercept",
            candidate_verb="intercept",
        )
    )
    assert wrapper.settled == 1
    assert inner.pairs == []


def test_adjudication_is_delegated_whole():
    """No comparison of fields answers whether the model supports a claim."""
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    wrapper.adjudicate(
        UnmatchedThreat(
            threat_id="T1",
            category="spoofing",
            claim="An attacker does a thing.",
            description="",
            affected_element_ids=("process:a",),
        ),
        valid_model(),
        (),
    )
    assert inner.adjudications == 1


def test_the_counters_are_what_the_artifact_reports():
    inner = RecordingJudge()
    wrapper = MechanicalFirstJudge(inner, FLOWS)

    wrapper.equivalent(pair())
    wrapper.equivalent(pair())
    wrapper.equivalent(pair(candidate_verb="alter"))

    assert (wrapper.settled, wrapper.delegated) == (2, 1)
