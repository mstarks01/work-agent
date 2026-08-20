"""Mechanical claim identity, scored against the recorded labels.

The measurement [#201](https://github.com/mstarks01/work-agent/issues/201) asks
for. `tests/test_claim_identity.py` answered the false-merge direction over the
blessed reference sets — the lane and the element set separate 242 of 243 claims
— and could not answer the direction the issue's title names, because the
calibration set's candidate side was a bare string. It now carries element IDs,
so this runs :class:`~evals.harness.identity.MechanicalIdentity` through
:func:`~evals.harness.calibration.measure_agreement`, against the same hand
labels and the same 90% bar every matcher is held to.

**Recorded, not thresholded.** The figures below are asserted exactly rather
than against a floor. A corpus case, a relabelled pair or a re-cut element set
moves them, and the author who moves them has to say what the new number is —
which is how the running count #201 needs stays honest. A floor would let the
number rot downwards inside the slack.

Free of provider calls: the rule is arithmetic over two sorted lists.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from evals import verify_corpus
from evals.harness.calibration import AGREEMENT_BAR, load_pairs, measure_agreement
from evals.harness.identity import (
    IdentityError,
    MechanicalIdentity,
    SubsetVerbIdentity,
    comparable_elements,
    endpoint_form,
    endpoint_subset,
)
from evals.harness.reference import ReferenceThreat, load_corpus
from evals.harness.verbs import UNSEPARATED, same_action

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
    #: The rule #201 argues for, and the only row here that is usable. Both
    #: columns move the right way against ``endpoint subset``: one more split,
    #: twenty fewer merges. It is not an element rule, so it is measured by
    #: :func:`_rules`'s verb-aware entry rather than by a shape function.
    "endpoint subset + verb": {"splits": 15, "merges": 3},
    "endpoint equality": {"splits": 60, "merges": 6},
    "subset": {"splits": 41, "merges": 7},
    "endpoint subset": {"splits": 14, "merges": 23},
    "overlap": {"splits": 4, "merges": 34},
    "endpoint overlap": {"splits": 1, "merges": 126},
}


def _rules(flows_by_case):
    """Each frontier rule as ``(case, ids, ids, verb, verb) -> same claim?``.

    The verbs are in the signature so that one table can hold an element-only
    rule and a rule that reads the action, and the two columns stay comparable.
    Every element rule ignores them, which is the point: the difference between
    the rows is exactly what the verb buys.
    """

    def bare(case, ids):
        return comparable_elements(ids)

    def ends(case, ids):
        return endpoint_form(ids, flows_by_case[case])

    def equal(shape):
        return lambda case, a, b, va, vb: shape(case, a) == shape(case, b)

    def subset(shape):
        return lambda case, a, b, va, vb: (
            shape(case, a) <= shape(case, b) or shape(case, b) <= shape(case, a)
        )

    def overlap(shape):
        return lambda case, a, b, va, vb: bool(shape(case, a) & shape(case, b))

    def subset_and_verb(case, a, b, va, vb):
        elements = ends(case, a) <= ends(case, b) or ends(case, b) <= ends(case, a)
        return elements and same_action(va, vb)

    return {
        "equality": equal(bare),
        "endpoint equality": equal(ends),
        "subset": subset(bare),
        "endpoint subset": subset(ends),
        "endpoint subset + verb": subset_and_verb,
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


def test_element_agreement_alone_does_not_reach_the_bar(assigned):
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
                pair.case,
                pair.reference_element_ids,
                pair.candidate_element_ids,
                pair.reference_verb,
                pair.candidate_verb,
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
                    left.verb,
                    right.verb,
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


#: The verb half, measured over the cases that carry verbs — which is case 01
#: today, and grows as ``tests/test_verb_coverage.py``'s debt shrinks. Both
#: columns, for the reason ``FRONTIER`` carries both: the merge count alone
#: would make the tightest possible rule look best.
#:
#: ``subset`` is what ``endpoint subset`` merges on these cases; ``subset_verb``
#: is what survives the verb. The gap between them is what the verb buys.
VERB_MEASURED = {
    "cases": 13,
    "within_lane_pairs": 287,
    "subset": 23,
    "subset_verb": 3,
}

#: What :class:`~evals.harness.identity.SubsetVerbIdentity` scores against the
#: recorded labels, on the shared bar. The first mechanical rule in this
#: repository to clear it — ``MechanicalIdentity`` sits at 111/200 — and the
#: number the judge's retirement rests on.
#:
#: Read it as ``evals/README.md`` reads every other agreement figure: the labels
#: are agent-authored, so this measures reproduction and not correctness.
SUBSET_VERB_AGREEMENT = {"agreements": 185, "total": 200}


def test_the_verb_separates_what_elements_alone_merge(corpus, flows_by_case):
    """The measurement #201 rests on, over real reference sets and real verbs.

    Not a re-run of the 23-pair analysis that argued for the vocabulary: this
    reads the corpus through the shipped loader, so it moves when a blessing
    pass assigns verbs to another case, and it fails when it moves.
    """
    assigned = [
        case
        for case in corpus
        if all(
            claim.verb
            for claim in case.references.get("stride", ())
            if isinstance(claim, ReferenceThreat)
        )
    ]

    pairs = subset = subset_verb = 0
    for case in assigned:
        flows = flows_by_case[case.meta.id]
        claims = [
            claim
            for claim in case.references["stride"]
            if isinstance(claim, ReferenceThreat)
        ]
        for left, right in itertools.combinations(claims, 2):
            if left.category != right.category:
                continue
            pairs += 1
            if not endpoint_subset(
                left.affected_element_ids, right.affected_element_ids, flows
            ):
                continue
            subset += 1
            if same_action(left.verb, right.verb):
                subset_verb += 1

    measured = {
        "cases": len(assigned),
        "within_lane_pairs": pairs,
        "subset": subset,
        "subset_verb": subset_verb,
    }
    assert measured == VERB_MEASURED, (
        f"the verb measurement moved to {measured}. Update VERB_MEASURED and"
        " re-quote it in docs/agents/claim-identity.md, which cites these"
        " numbers as the argument for the verb."
    )
    assert subset_verb < subset, "the verb bought nothing on this corpus"


def test_every_surviving_merge_is_a_recorded_one(corpus, flows_by_case):
    """A merge the verb does not break must be named in ``UNSEPARATED``.

    Otherwise the three recorded exceptions become a floor nobody notices
    rising: a fourth would sit in the count and nowhere else.
    """
    recorded = {(case, lane) for case, lane, _ in UNSEPARATED}
    for case in corpus:
        claims = [
            claim
            for claim in case.references.get("stride", ())
            if isinstance(claim, ReferenceThreat)
        ]
        if not claims or not all(claim.verb for claim in claims):
            continue
        flows = flows_by_case[case.meta.id]
        for left, right in itertools.combinations(claims, 2):
            same_lane = left.category == right.category
            merged = same_lane and endpoint_subset(
                left.affected_element_ids, right.affected_element_ids, flows
            )
            if merged and same_action(left.verb, right.verb):
                assert (case.meta.id, left.category) in recorded, (
                    f"{case.meta.id}/{left.category} survives the verb rule and"
                    " is not in verbs.UNSEPARATED. Add it with the reason, or"
                    f" separate it:\n  A: {left.claim}\n  B: {right.claim}"
                )


def test_the_rule_clears_the_bar(assigned, flows_by_case):
    """``SubsetVerbIdentity`` through the shared scoreboard.

    One scoreboard, two answers to one question — which is what building the
    rule to the ``Judge`` protocol was for. The number is judge-relative in the
    same way every other agreement figure here is: the labels are
    agent-authored, so this measures reproduction rather than correctness.
    """
    result = measure_agreement(SubsetVerbIdentity(flows_by_case), assigned)

    assert {
        "agreements": result.agreements,
        "total": result.total,
    } == SUBSET_VERB_AGREEMENT, (
        f"the rule now scores {result.agreements}/{result.total}. Update"
        " SUBSET_VERB_AGREEMENT and re-quote it in"
        " docs/agents/claim-identity.md."
    )
    assert result.meets_bar, "the rule fell below the bar"
    assert not result.false_matches, (
        "this set is match-labelled throughout, so a false match is impossible"
        " and its appearance means the fixtures changed shape"
    )


def test_the_rule_refuses_a_pair_with_no_verb(assigned, flows_by_case):
    """Never a silent fall back to grading the element half alone.

    Built from an assigned pair with the verb removed, because in the fixtures
    the two fields are assigned together — so a pair carrying no verb also
    carries no elements, and the element check would answer first. This
    isolates the verb refusal from that one.
    """
    stripped = dataclasses.replace(assigned[0].to_claim_pair(), candidate_verb=None)
    with pytest.raises(IdentityError, match="no action verb"):
        SubsetVerbIdentity(flows_by_case).equivalent(stripped)
