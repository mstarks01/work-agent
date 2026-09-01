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

import collections
import dataclasses
import itertools

import pytest

from evals import verify_corpus
from evals.harness.calibration import (
    AGREEMENT_BAR,
    load_pairs,
    measure_agreement,
    measure_merges,
)
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

#: What element agreement alone is worth on the recorded labels, over the 315
#: scored pairs that carry candidate element IDs and a verb.
#:
#: **Both halves are assigned now.** Until #511 only the 200 ``match``
#: candidates carried the fields, so this rule was priced on the split
#: direction alone and its ``false_matches`` was structurally zero. The 115
#: scored negatives now say what element equality really costs: it merges 26 of
#: them. Every number here is quoted in #201, so moving one means updating the
#: issue.
MEASURED = {
    "assigned_pairs": 315,
    "match_pairs": 200,
    "no_match_pairs": 115,
    # The rule the record can express today: the two element sets are equal,
    # with zones dropped.
    "equality_agreements": 200,
    # It splits nearly half the pairs a label calls the same, and merges a
    # fifth of the ones it calls different.
    "false_matches": 26,
    "false_non_matches": 89,
}

#: The frontier, all three ways of being wrong at once.
#:
#: - ``splits`` counts the 200 ``match`` pairs a rule calls different.
#: - ``candidate_merges`` counts the 115 scored ``no-match`` pairs it calls the
#:   same. These are candidate paraphrases, which is the population a live run
#:   emits, and they were unmeasurable before #511 assigned them elements and
#:   verbs.
#: - ``reference_merges`` counts the 287 within-lane pairs of distinct corpus
#:   claims it calls the same. Every one is an error by construction.
#:
#: **The candidate column is why the verb is not optional.** Read on reference
#: pairs alone, ``endpoint subset`` merges 23 of 287 and looks survivable. Read
#: on the candidates a run actually produces, it merges **85 of 115** — it is
#: barely a rule at all. The verb takes that to 5 for one extra split.
FRONTIER = {
    "equality": {"splits": 89, "candidate_merges": 26, "reference_merges": 1},
    #: The rule #201 argues for, and the only row here that is usable.
    #: It is not an element rule, so it is measured by :func:`_rules`'s
    #: verb-aware entry rather than by a shape function.
    "endpoint subset + verb": {
        "splits": 15,
        "candidate_merges": 5,
        "reference_merges": 3,
    },
    "endpoint equality": {"splits": 60, "candidate_merges": 40, "reference_merges": 6},
    "subset": {"splits": 41, "candidate_merges": 68, "reference_merges": 7},
    "endpoint subset": {"splits": 14, "candidate_merges": 85, "reference_merges": 23},
    "overlap": {"splits": 4, "candidate_merges": 87, "reference_merges": 34},
    "endpoint overlap": {
        "splits": 1,
        "candidate_merges": 103,
        "reference_merges": 126,
    },
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
    """The scored pairs whose candidate claim carries element IDs and a verb."""
    return [
        pair
        for pair in load_pairs()
        if pair.is_scored and pair.candidate_element_ids is not None
    ]


def test_both_label_halves_are_assigned(assigned):
    """Both directions are measurable, which #511 is the work that made true.

    While only the ``match`` half carried the fields, every merge figure came
    from reference claims comparing with each other and no candidate pair could
    ever produce one. A drop here means a fixture lost its assignment and a
    merge count silently fell with it.
    """
    by_label = collections.Counter(pair.label for pair in assigned)

    assert by_label["match"] == MEASURED["match_pairs"]
    assert by_label["no-match"] == MEASURED["no_match_pairs"]
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
    """Every rule in :data:`FRONTIER`, against all three ways of being wrong.

    None of them is adopted. Measuring them together is the point: a rule read on
    one axis always looks good, and the columns together are what show that no
    comparison of **Element**s alone is usable.
    """
    rules = _rules(flows_by_case)
    measured = {}
    for name, rule in rules.items():
        splits = candidate_merges = 0
        for pair in assigned:
            ruled = rule(
                pair.case,
                pair.reference_element_ids,
                pair.candidate_element_ids,
                pair.reference_verb,
                pair.candidate_verb,
            )
            if pair.label_match and not ruled:
                splits += 1
            elif ruled and not pair.label_match:
                candidate_merges += 1
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
        measured[name] = {
            "splits": splits,
            "candidate_merges": candidate_merges,
            "reference_merges": merges,
        }

    assert measured == FRONTIER, (
        f"the frontier moved to {measured}. Update FRONTIER and re-quote it on"
        " #201 and in docs/agents/claim-identity.md; the design of `mechanism`"
        " is argued from these numbers."
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
#: today, and grows as ``tests/test_verb_coverage.py`` covers more. Both
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
#: repository to clear it — ``MechanicalIdentity`` sits at 200/315 — and the
#: number the judge's retirement rests on. It is the admission gate, not the
#: measurement: :data:`FRONTIER` carries that.
#:
#: Read it as ``evals/README.md`` reads every other agreement figure: the labels
#: are agent-authored, so this measures reproduction and not correctness.
SUBSET_VERB_AGREEMENT = {"agreements": 295, "total": 315}


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
    rule to the ``Matcher`` protocol was for. The number is rule-relative in the
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
    assert len(result.false_non_matches) == FRONTIER["endpoint subset + verb"]["splits"]
    assert (
        len(result.false_matches)
        == FRONTIER["endpoint subset + verb"]["candidate_merges"]
    ), (
        "the scoreboard and the frontier must agree on the candidate merge"
        " count; they read the same pairs through two code paths"
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


def test_the_shared_merge_measurement_matches_the_frontier(corpus, flows_by_case):
    """``measure_merges`` and ``FRONTIER`` must count the same three merges.

    The frontier prices seven rules with raw shape functions, because the point
    is to compare them. ``calibration.measure_merges`` prices one rule through
    the :class:`~evals.harness.identity.Matcher` protocol, because the CLI and
    the doc lint need the shipped rule's number without the other six. Two
    computations of one figure drift, so this pins them together.
    """
    merges = measure_merges(SubsetVerbIdentity(flows_by_case), corpus, "stride")

    assert merges.within_lane_pairs == VERB_MEASURED["within_lane_pairs"]
    assert len(merges.merges) == VERB_MEASURED["subset_verb"]
    assert len(merges.merges) == FRONTIER["endpoint subset + verb"]["reference_merges"]
    assert {(merge.case, merge.lane) for merge in merges.merges} == {
        (case, lane) for case, lane, _ in UNSEPARATED
    }
