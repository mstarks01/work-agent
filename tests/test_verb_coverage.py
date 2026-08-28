"""Every reference claim carries an action verb, and a new one must too.

This started as a list of what was missing. Nothing is missing now: all 243
claims across all 13 cases carry a verb, so what is left is the guard that stops it coming back. A
case that arrives without verbs fails here rather than quietly weakening
:class:`~evals.harness.identity.SubsetVerbIdentity` on the case nobody checked.

**Why the verb belongs on the reference claim rather than being derived.** A
claim's action is what its author decided the finding *is* — ``evals/BLESSING.md``
step 4b is where that decision gets made, beside the element citation it sits
with. Deriving it from the claim text later would re-run that decision with less
context, and get a different answer on exactly the claims where it matters.

Deterministic over the corpus and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

from evals import verify_corpus
from evals.harness.reference import ReferenceThreat, load_corpus
from evals.harness.verbs import ACTION_VERBS, unknown_verbs

#: How many STRIDE reference claims each case carries, all of which must have a
#: verb. Spelled per case rather than as one total so that a case losing claims
#: and another gaining them cannot cancel out.
CLAIMS_PER_CASE: dict[str, int] = {
    "01-payments-checkout": 21,
    "02-iot-fleet-telemetry": 18,
    "03-batch-data-pipeline": 17,
    "04-ml-inference-service": 18,
    "05-cookbook-queue-webapp": 17,
    "06-cookbook-online-game": 18,
    "07-cicd-store-deploy": 24,
    "08-sso-identity-broker": 23,
    "09-cookbook-sokify-retail": 20,
    "10-cookbook-generic-cms": 17,
    "11-sparse-shift-scheduling": 16,
    "12-overclaiming-supplier-portal": 15,
    "13-dispatch-control-plane": 19,
}


def _stride_claims(case):
    return [
        claim
        for claim in case.references.get("stride", ())
        if isinstance(claim, ReferenceThreat)
    ]


def test_every_reference_claim_carries_a_verb():
    """The end state of the gap this file used to count.

    A case with an unassigned claim is named, with the claim, because "12 of 13"
    tells nobody which sentence to go and read.
    """
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    missing = [
        f"{case.meta.id}: {claim.claim}"
        for case in corpus
        for claim in _stride_claims(case)
        if claim.verb is None
    ]
    assert not missing, (
        "these reference claims carry no action verb; assign one from"
        " evals.harness.verbs as BLESSING.md step 4b asks:\n  " + "\n  ".join(missing)
    )


def test_the_claim_counts_are_what_is_recorded():
    """A new case must add itself here, so it cannot arrive uncounted."""
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    measured = {case.meta.id: len(_stride_claims(case)) for case in corpus}
    assert measured == CLAIMS_PER_CASE, (
        f"the corpus now holds {measured}. Update CLAIMS_PER_CASE, and check"
        " that VERB_MEASURED in tests/test_evals_identity.py still holds — the"
        " frontier is counted over these same claims."
    )


def test_every_assigned_verb_is_in_the_vocabulary():
    """A verb outside the set matches nothing, so it fails where it is written."""
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    for case in corpus:
        bad = unknown_verbs(
            claim.verb for claim in _stride_claims(case) if claim.verb is not None
        )
        assert not bad, (
            f"{case.meta.id} assigns {', '.join(bad)}, which"
            f" evals.harness.verbs does not carry ({len(ACTION_VERBS)} verbs)"
        )


#: Verbs no reference claim uses, each with why it stays. An exemption with a
#: reason, in the shape ``test_rule_coverage.py`` already uses here — unlike a
#: list of work nobody has done, an entry here says the omission is understood.
UNUSED_BUT_KEPT: dict[str, str] = {
    "guess-credential": (
        "No reference claim describes guessing a credential, and five"
        " no-match candidates in build_pairs.py do — 'brute-forces the fleet"
        " pre-shared key', 'guesses the build token because it is short'. Those"
        " are hard negatives against claims about *holding* or *recovering* a"
        " credential, which is exactly where a matcher must not merge. The verb"
        " earns its place on the half of the fixtures that is not assigned yet."
    ),
}


def test_every_verb_is_used_or_exempted_with_a_reason():
    """A verb no claim uses is a distinction the corpus cannot show is real.

    Not a failure of the corpus — a question for the vocabulary. A verb that
    earns no place in 243 claims is one to justify or to remove, and this is
    where that gets noticed rather than in a review two years from now.
    """
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    used = {
        claim.verb for case in corpus for claim in _stride_claims(case) if claim.verb
    }
    unused = sorted(ACTION_VERBS - used)

    assert unused == sorted(UNUSED_BUT_KEPT), (
        f"the unused verbs are now {unused}. A verb that has become used should"
        " leave UNUSED_BUT_KEPT; a newly unused one needs a reason there, or"
        " removal from evals.harness.verbs."
    )
    for verb, reason in UNUSED_BUT_KEPT.items():
        assert len(reason) > 100, f"{verb} needs a reason, not a label"
