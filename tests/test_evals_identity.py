"""Mechanical claim identity, scored against the same labels as the judge.

The measurement [#201](https://github.com/mstarks01/work-agent/issues/201) asks
for. `tests/test_claim_identity.py` answered the false-merge direction over the
blessed reference sets — the lane and the element set separate 242 of 243 claims
— and could not answer the direction the issue's title names, because the
calibration set's candidate side was a bare string. It now carries element IDs,
so this runs :class:`~evals.harness.identity.MechanicalIdentity` through
:func:`~evals.harness.calibration.measure_agreement`, against the same hand
labels and the same 90% bar as the pinned judge.

**Recorded, not thresholded.** The figures below are asserted exactly rather
than against a floor. A corpus case, a relabelled pair or a re-cut element set
moves them, and the author who moves them has to say what the new number is —
which is how the running count #201 needs stays honest. A floor would let the
number rot downwards inside the slack.

Free of provider calls: the rule is arithmetic over two sorted lists.
"""

from __future__ import annotations

import random

import pytest

from evals.harness.calibration import AGREEMENT_BAR, load_pairs, measure_agreement
from evals.harness.identity import IdentityError, MechanicalIdentity
from evals.harness.judge import ClaimPair, UnmatchedThreat, claim_payload
from tests.factories import valid_model

#: What element agreement alone is worth on the hand labels, measured
#: 2026-08-18 over the 201 ``match`` pairs that carry candidate element IDs.
#: Every number here is quoted in #201, so moving one means updating the issue.
MEASURED = {
    "assigned_pairs": 201,
    # The rule the record can express today: the two element sets are equal.
    "equality_agreements": 110,
    # It never merges two claims a human called different, and splits nearly
    # half of the ones a human called the same.
    "false_matches": 0,
    "false_non_matches": 91,
    # Two relaxations, measured for the trade-off rather than adopted. Both buy
    # paraphrases back on this half and cost false merges on the other, which
    # ``tests/test_claim_identity.py`` prices at 1 collision for equality
    # against 34 for overlap over the blessed reference sets.
    "either_subset_agreements": 159,
    "any_overlap_agreements": 196,
}


@pytest.fixture(scope="module")
def assigned():
    """The labelled pairs whose candidate claim carries element IDs."""
    return [pair for pair in load_pairs() if pair.candidate_element_ids is not None]


def test_every_assigned_pair_is_a_match_label(assigned):
    """The assignment pass covered the ``match`` half and only that half.

    The false-split question lives entirely in the pairs a human called equal,
    so those were assigned first. A ``no-match`` pair that acquired element IDs
    without the figures below moving would mean the two sets stopped describing
    the same population.
    """
    assert {pair.label for pair in assigned} == {"match"}
    assert len(assigned) == MEASURED["assigned_pairs"]


def test_element_agreement_alone_does_not_reach_the_judge_s_bar(assigned):
    result = measure_agreement(MechanicalIdentity(), assigned)

    assert result.total == MEASURED["assigned_pairs"]
    assert result.agreements == MEASURED["equality_agreements"], (
        f"element agreement now scores {result.agreements}/{result.total} where"
        f" {MEASURED['equality_agreements']} was recorded. Update MEASURED and"
        " re-quote the figure on #201; the issue's argument rests on it."
    )
    assert len(result.false_matches) == MEASURED["false_matches"]
    assert len(result.false_non_matches) == MEASURED["false_non_matches"]
    assert not result.meets_bar
    assert result.agreement < AGREEMENT_BAR


def test_the_two_relaxations_are_priced(assigned):
    """What loosening the element comparison recovers on this half.

    Neither is adopted. The point of measuring both is that the gap between them
    is where ``mechanism`` would have to work: overlap recovers almost every
    paraphrase, and the blessed reference sets say it merges 34 pairs a reviewer
    ruled distinct, so nothing in the middle is free.
    """
    subset = 0
    overlap = 0
    for pair in assigned:
        reference = set(pair.reference_element_ids)
        candidate = set(pair.candidate_element_ids or ())
        if reference <= candidate or candidate <= reference:
            subset += 1
        if reference & candidate:
            overlap += 1

    assert subset == MEASURED["either_subset_agreements"]
    assert overlap == MEASURED["any_overlap_agreements"]


def test_a_candidate_with_no_assigned_elements_is_refused():
    """Unassigned is not "no elements", so the rule refuses rather than misses.

    Scoring an unassigned pair would count every one of the 138 ``no-match``
    candidates as a false split and quietly halve the agreement figure.
    """
    unassigned = next(
        pair for pair in load_pairs() if pair.candidate_element_ids is None
    )

    with pytest.raises(IdentityError, match="no element IDs are assigned"):
        MechanicalIdentity().equivalent(unassigned.to_claim_pair())


def test_bucketing_an_unmatched_threat_is_refused():
    """The rule answers one of the judge's two calls and says so on the other."""
    threat = UnmatchedThreat(
        threat_id="S-01",
        category="spoofing",
        claim="An attacker replays a stolen session cookie.",
        description="d",
        affected_element_ids=("process:storefront-api",),
    )

    with pytest.raises(IdentityError, match="cannot bucket"):
        MechanicalIdentity().adjudicate(threat, valid_model(), ())


def test_the_judge_never_sees_the_element_ids():
    """The pinned judge's payload is unchanged, so no calibration re-baselines.

    ``ClaimPair`` gained two fields for the rule above. Passing them to the judge
    would change what it is asked on every pair and silently invalidate every
    agreement number this repo holds, so the payload is pinned here rather than
    left to whoever next edits :func:`claim_payload`.
    """
    pair = ClaimPair(
        case="01-payments-checkout",
        category="spoofing",
        reference_claim="An attacker replays a stolen session cookie.",
        candidate_claim="An attacker reuses a stolen cookie to order as the shopper.",
        reference_element_ids=("flow:shopper-to-storefront-api:place-order",),
        candidate_element_ids=("entity:shopper",),
    )

    payload = claim_payload(pair, rng=random.Random(0))

    assert set(payload) == {"stride_category", "claim_a", "claim_b"}
    assert "shopper-to-storefront-api" not in repr(payload)
