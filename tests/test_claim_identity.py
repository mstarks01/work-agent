"""How far the fields a claim already carries go towards identifying it.

[#201](https://github.com/mstarks01/work-agent/issues/201) proposes resolving a
**Claim** to the parts that decide its identity, so that two spellings of one
threat compare equal with no model call. Three of the four parts it names are
already on the record — the lane, the affected **Element** IDs and the
**Grounds** — and only ``mechanism`` would be new.

This measures what those existing parts are worth, in the one direction the
corpus can answer. A blessed reference set is a set of claims their author
recorded as **distinct**, so two of them sharing an identity key is the key merging findings
that are not the same finding. That is a false merge, and it is the failure that
would make mechanical identity unusable: a deduplicator that drops a real
threat is worse than one that reports a paraphrase twice.

The other direction — one claim spelled two ways landing on two keys — is the
one #201's title names, and it is measured next door in
``tests/test_evals_identity.py`` over the recorded calibration labels. Read the two
together: this file prices a false merge, that one prices a false split, and
neither number means anything alone. ``FRONTIER`` there carries both errors for
six comparison rules at once, and this file is the merge column of its first row.

So this says nothing on its own about whether ``mechanism`` is needed. What it
does is keep a running count of the claims the corpus holds that the existing
fields cannot tell apart, accumulated one corpus case at a time instead of
guessed at once.

Deterministic over the blessed reference sets and free of provider calls, which
is why it gates on every PR rather than waiting for a sweep.
"""

from __future__ import annotations

import itertools

import pytest

from analysis_service.critic import endpoint_targets
from analysis_service.system_model import TrustBoundary
from evals import verify_corpus
from evals.harness.fingerprint import key_claim
from evals.harness.identity import ClaimPair, SubsetVerbIdentity, endpoint_form
from evals.harness.reference import GoldenCase, ReferenceThreat, load_corpus

#: Corpus pairs the scorer's matcher credits as one claim and the ledger keys as
#: two. Both answers are right: the matcher takes a subset because two writers
#: name one place at different grain, and the ledger takes equality because a
#: vote must land on the finding it was cast on. The consequence is worth
#: seeing — a produced threat can match a reference without sharing its review
#: key — and it is #671 finding 14.
#:
#: **Not a second copy of ``FRONTIER``'s merge column.** That prices the matcher
#: against 287 labelled pairs and asks how often it is *wrong*. This asks a
#: different question over a different population: within one case and one lane,
#: how often do the two shipped readers of "one place" disagree, whoever is
#: right. Recorded exactly, for the reason ``FRONTIER`` records its rows.
MATCHER_WIDER_THAN_FINGERPRINT = 2

#: Reference claims the existing fields cannot tell apart, each with the reason
#: the corpus is right to carry both. An entry says two distinct findings share
#: a lane and an element set, so the thing that separates them is the attacker
#: action — which is exactly what #201's ``mechanism`` would hold. A collision
#: between claims that are *not* distinct does not belong here; it belongs
#: merged in the corpus.
UNSEPARATED: dict[str, str] = {
    "09-cookbook-sokify-retail | tampering |"
    " flow:catalogue-spreadsheet-to-web-api:sql-statements, process:web-api": (
        "Driving the macros to change prices through the API, and appending"
        " further SQL to the statements those macros send, are two attacker"
        " actions against one flow: the first uses the interface as built, the"
        " second escapes it. Both are must-find, and merging them would hide"
        " the injection finding behind the price-change one."
    ),
}


def identity_key(case: GoldenCase, claim: ReferenceThreat) -> str:
    """One claim's identity under the fields the record carries today.

    The lane and the element set, and nothing else. **Element order is not part
    of it**: ``affected_element_ids`` is a list whose order no rule reads, so two
    agents naming the same two elements in opposite orders would otherwise land
    on two keys for one reason that has nothing to do with the claim.

    Scoped to the case because the claims are: an element ID resolves in one
    blessed **System Model**, and the same ID in two cases names two things.

    The element sets must be **equal**, not merely overlap. Overlap is the
    tempting relaxation — a lane agent naming a flow and a reference naming the
    process at one end of it are describing one thing — and #201's comment
    records what it costs on this corpus: it merges an order of magnitude more
    reference claims than equality does, every one of them a pair a reviewer
    ruled distinct.
    """
    elements = ", ".join(sorted(claim.affected_element_ids))
    return f"{case.meta.id} | {claim.category} | {elements}"


@pytest.fixture(scope="module")
def corpus() -> tuple[GoldenCase, ...]:
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def collisions() -> dict[str, str]:
    """Every identity key more than one reference claim in a case lands on.

    Maps the key to the claims that share it, so a failure names the findings a
    reader has to look at rather than a key they would have to go and resolve.
    """
    found = {}
    for case in load_corpus(verify_corpus.CORPUS_DIR):
        keyed: dict[str, list[str]] = {}
        for claim in case.references.get("stride", ()):
            # The registry maps "stride" to this record, so the narrowing is a
            # fail-closed restatement rather than a branch with a live else.
            assert isinstance(claim, ReferenceThreat), claim
            keyed.setdefault(identity_key(case, claim), []).append(claim.claim)
        found.update(
            {key: "; ".join(claims) for key, claims in keyed.items() if len(claims) > 1}
        )
    return found


def test_no_two_reference_claims_collide_without_a_stated_reason(collisions):
    undeclared = sorted(set(collisions) - set(UNSEPARATED))
    assert not undeclared, (
        "these reference claims share a lane and an element set, and nothing"
        " says why they are still two claims:"
        f" {[(key, collisions[key]) for key in undeclared]}. Either they are one"
        " finding written twice — merge them in the corpus — or the attacker"
        " action is what separates them, which is the case #201 exists for: add"
        " the key to UNSEPARATED with the reason."
    )


def test_the_exemption_list_does_not_rot(collisions):
    """A key that stops colliding has to leave the list, or it excuses nothing."""
    stale = sorted(set(UNSEPARATED) - set(collisions))
    assert not stale, (
        f"these keys no longer collide and are still exempted: {stale}. A"
        " reworded or re-elemented claim moves its key, so check the corpus"
        " still carries both findings before removing them from UNSEPARATED."
    )


class TestTheFourReadersOfOnePlace:
    """Four pieces of code fold a claim's citations into "one place in the graph".

    ``critic.endpoint_targets`` folds a draft's citations for the runtime
    duplicate step; ``identity.endpoint_form`` folds them for the scorer and for
    ``fingerprint.components_for``. They are two implementations of one rule in
    two modules, and until this ran nothing compared them — ``endpoint_targets``
    says it is "kept in step by ``tests/test_evals_identity.py``" and that file
    never mentions it. They agree today only because a literal ``"boundary:"``
    in one happens to match ``TrustBoundary.id_prefix`` read by the other.

    On top of that fold sit three *relations*, and they are deliberately
    different, which is why this asserts the implications between them rather
    than equality:

    - ``duplicate_groups`` — exact folded targets and one verb, **across** lanes.
    - ``SubsetVerbIdentity`` — folded targets by **subset** and one action,
      within a lane.
    - ``fingerprint`` — exact folded targets, one verb, one lane, one case.

    Each has its own test elsewhere, against its own expectation, which is
    exactly how two readers of one rule drift while both suites stay green.
    """

    def _flows(self, case):
        return {
            flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
        }

    def test_the_two_folds_answer_alike_on_every_corpus_citation(self, corpus):
        """The pair with no relation between them: they must simply agree."""
        disagreements = []
        for case in corpus:
            flows = self._flows(case)
            for claim in case.stride_claims():
                runtime = endpoint_targets(claim.affected_element_ids, flows)
                harness = endpoint_form(claim.affected_element_ids, flows)
                if runtime != harness:
                    disagreements.append(
                        (case.id, claim.claim, sorted(runtime), sorted(harness))
                    )

        assert disagreements == [], (
            "analysis_service.critic.endpoint_targets and"
            " evals.harness.identity.endpoint_form fold one claim's citations"
            f" into two different places: {disagreements}. They are one rule"
            " with two readers, so the runtime would call two drafts duplicates"
            " that the scorer counts as two findings, or the reverse."
        )

    def test_the_two_folds_drop_a_zone_by_the_same_definition(self):
        """A literal against a schema constant, which is how they could diverge.

        ``endpoint_targets`` reads ``TrustBoundary.id_prefix``;
        ``comparable_elements`` spells ``"boundary:"``. Renaming the prefix
        would leave one reader dropping zones and the other keeping them, with
        no corpus citation needed to notice.
        """
        zone = f"{TrustBoundary.id_prefix}:public-internet"

        assert endpoint_targets([zone, "process:a"], {}) == frozenset({"process:a"})
        assert endpoint_form([zone, "process:a"], {}) == frozenset({"process:a"})

    def test_one_fingerprint_implies_the_runtime_calls_them_duplicates(self, corpus):
        """Fingerprint equality is strictly the stronger key, so it must imply.

        It reads everything the duplicate key reads and the lane besides. A pair
        the ledger answers with one vote that the runtime would have kept apart
        is a finding counted once and reported twice.
        """
        for case in corpus:
            flows = self._flows(case)
            for left, right in itertools.combinations(case.stride_claims(), 2):
                same_key = _key(case, left, flows) == _key(case, right, flows)
                same_place = endpoint_targets(
                    left.affected_element_ids, flows
                ) == endpoint_targets(right.affected_element_ids, flows)
                if same_key:
                    assert same_place and left.verb == right.verb, (
                        f"{case.id}: {left.claim!r} and {right.claim!r} share a"
                        " fingerprint and the runtime duplicate key would not"
                        " pair them"
                    )

    def test_one_fingerprint_implies_the_matcher_calls_them_one_claim(self, corpus):
        """The matcher is the loosest of the three, so it must implicate too.

        Exact folded targets are a subset of themselves in both directions, and
        the lane and verb are shared, so a fingerprint collision the matcher
        splits would mean a vote landing on a finding the scorer never matched.
        """
        for case in corpus:
            flows = self._flows(case)
            matcher = SubsetVerbIdentity({case.id: flows})
            for left, right in itertools.combinations(case.stride_claims(), 2):
                if _key(case, left, flows) != _key(case, right, flows):
                    continue
                pair = ClaimPair(
                    case=case.id,
                    category=left.category,
                    reference_claim=left.claim,
                    candidate_claim=right.claim,
                    reference_element_ids=left.affected_element_ids,
                    candidate_element_ids=right.affected_element_ids,
                    reference_verb=left.verb,
                    candidate_verb=right.verb,
                )
                assert matcher.equivalent(pair).match, (
                    f"{case.id}: one fingerprint, two claims to the matcher:"
                    f" {left.claim!r} and {right.claim!r}"
                )

    def test_the_matcher_merges_strictly_more_than_the_fingerprint(self, corpus):
        """The gap between subset and equality, counted rather than argued.

        Both readers are right and they still disagree, which is why this is a
        recorded number and not a failure. Case 08 supplies both pairs today:
        one claim names a flow and a store, the other names only the store, and
        the matcher folds the narrower into the wider while the ledger keys them
        apart. Moving this number means the two relations moved apart, and
        whoever moved them has to say by how much.
        """
        wider = 0
        for case in corpus:
            flows = self._flows(case)
            matcher = SubsetVerbIdentity({case.id: flows})
            for left, right in itertools.combinations(case.stride_claims(), 2):
                if left.category != right.category:
                    continue
                pair = ClaimPair(
                    case=case.id,
                    category=left.category,
                    reference_claim=left.claim,
                    candidate_claim=right.claim,
                    reference_element_ids=left.affected_element_ids,
                    candidate_element_ids=right.affected_element_ids,
                    reference_verb=left.verb,
                    candidate_verb=right.verb,
                )
                if matcher.equivalent(pair).match and _key(case, left, flows) != _key(
                    case, right, flows
                ):
                    wider += 1

        assert wider == MATCHER_WIDER_THAN_FINGERPRINT, (
            f"the matcher now merges {wider} corpus pairs the fingerprint keys"
            f" apart, where {MATCHER_WIDER_THAN_FINGERPRINT} was recorded."
            " Update the constant and say what moved the two relations apart."
        )


def _key(case: GoldenCase, claim: ReferenceThreat, flows) -> str:
    value, _ = key_claim(
        "stride",
        case.id,
        claim.category,
        claim.affected_element_ids,
        flows,
        verb=claim.verb,
    )
    return value
