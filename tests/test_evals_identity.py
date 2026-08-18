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

import itertools
import random

import pytest

from evals import verify_corpus
from evals.harness.calibration import AGREEMENT_BAR, load_pairs, measure_agreement
from evals.harness.identity import (
    IdentityError,
    MechanicalIdentity,
    comparable_elements,
    endpoint_form,
)
from evals.harness.judge import ClaimPair, UnmatchedThreat, claim_payload
from evals.harness.reference import ReferenceThreat, load_corpus
from tests.factories import valid_model

#: What element agreement alone is worth on the recorded labels, measured
#: 2026-08-18 over the 200 ``match`` pairs that carry candidate element IDs.
#: Review sitting 01 relabelled one pair out of the set, which is why the count
#: is 200 and not the 201 #204 reported.
#: Every number here is quoted in #201, so moving one means updating the issue.
MEASURED = {
    "assigned_pairs": 200,
    # The rule the record can express today: the two element sets are equal,
    # with zones dropped.
    "equality_agreements": 111,
    # It never merges two claims a human called different, and splits nearly
    # half of the ones a human called the same.
    "false_matches": 0,
    "false_non_matches": 89,
}

#: The frontier, both errors at once. ``splits`` counts the ``match`` pairs a
#: rule calls different; ``merges`` counts the reference-claim pairs a rule calls
#: the same, over the 287 within-lane pairs the corpus holds — and every one of
#: those is a pair the corpus records as a distinct claim, so every merge is an error.
#:
#: Read down the table: no rule here is usable. The tightest loses 89 of 200
#: paraphrases; the loosest destroys 126 findings. **The interesting row is
#: endpoint subset**, which clears the 90% bar on splits and is the only row
#: whose merges are few enough to enumerate and design against — which is what
#: #201's ``mechanism`` has to separate.
FRONTIER = {
    "equality": {"splits": 89, "merges": 1},
    "endpoint equality": {"splits": 60, "merges": 6},
    "subset": {"splits": 41, "merges": 7},
    "endpoint subset": {"splits": 14, "merges": 23},
    "overlap": {"splits": 4, "merges": 34},
    "endpoint overlap": {"splits": 1, "merges": 126},
}


def _rules(flows_by_case):
    """Each frontier rule as ``(case, elements, elements) -> same claim?``."""

    def bare(case, ids):
        return comparable_elements(ids)

    def ends(case, ids):
        return endpoint_form(ids, flows_by_case[case])

    def equal(shape):
        return lambda case, a, b: shape(case, a) == shape(case, b)

    def subset(shape):
        return lambda case, a, b: (
            shape(case, a) <= shape(case, b) or shape(case, b) <= shape(case, a)
        )

    def overlap(shape):
        return lambda case, a, b: bool(shape(case, a) & shape(case, b))

    return {
        "equality": equal(bare),
        "endpoint equality": equal(ends),
        "subset": subset(bare),
        "endpoint subset": subset(ends),
        "overlap": overlap(bare),
        "endpoint overlap": overlap(ends),
    }


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def flows_by_case(corpus):
    """Each case's ``flow id -> (source, destination)``, for the endpoint rules."""
    return {
        case.meta.id: {
            flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
        }
        for case in corpus
    }


@pytest.fixture(scope="module")
def assigned():
    """The labelled pairs whose candidate claim carries element IDs."""
    return [pair for pair in load_pairs() if pair.candidate_element_ids is not None]


def test_every_assigned_pair_is_a_match_label(assigned):
    """The assignment pass covered the ``match`` half and only that half.

    The false-split question lives entirely in the pairs the labels call equal,
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


def test_the_frontier_is_priced_on_both_errors(assigned, corpus, flows_by_case):
    """Every rule in :data:`FRONTIER`, against both ways of being wrong.

    None of them is adopted. Measuring them together is the point: a rule read on
    one axis always looks good, and the pair of numbers is what shows that no
    comparison of **Element**s alone is usable.
    """
    rules = _rules(flows_by_case)
    measured = {}
    for name, rule in rules.items():
        splits = sum(
            1
            for pair in assigned
            if not rule(
                pair.case, pair.reference_element_ids, pair.candidate_element_ids
            )
        )
        merges = 0
        for case in corpus:
            claims = [
                claim
                for claim in case.references.get("stride", ())
                if isinstance(claim, ReferenceThreat)
            ]
            for left, right in itertools.combinations(claims, 2):
                if left.category != right.category:
                    continue
                if rule(
                    case.meta.id,
                    left.affected_element_ids,
                    right.affected_element_ids,
                ):
                    merges += 1
        measured[name] = {"splits": splits, "merges": merges}

    assert measured == FRONTIER, (
        f"the frontier moved to {measured}. Update FRONTIER and re-quote it on"
        " #201; the design of `mechanism` is argued from these six pairs of"
        " numbers."
    )


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
