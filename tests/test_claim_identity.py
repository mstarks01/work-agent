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

import pytest

from evals import verify_corpus
from evals.harness.reference import GoldenCase, ReferenceThreat, load_corpus

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
