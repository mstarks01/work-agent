"""Which reference claims carry an action verb, and which are still waiting.

Fingerprint version 2 and :class:`~evals.harness.identity.SubsetVerbIdentity`
both read a verb, and no reference claim carries one. That is debt, not a design
gap: the vocabulary and both readers ship measured, and the corpus fills in one
blessing pass at a time.

This is the same shape as ``tests/test_case_review.py``. A count that shrinks is
a debt somebody is paying; a comment saying "to do" is one nobody is. So the
number is asserted rather than described, and it can only go down.

**Why the verb belongs on the reference claim rather than being derived.** A
claim's action is what its author decided the finding *is* — ``evals/BLESSING.md``
step 4 is where that decision gets made, beside the element citation it sits
with. Deriving it from the claim text later would re-run that decision with less
context, and get a different answer on exactly the claims where it matters.

Deterministic over the corpus and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

from evals import verify_corpus
from evals.harness.reference import ReferenceThreat, load_corpus
from evals.harness.verbs import ACTION_VERBS, unknown_verbs

#: Reference claims with no ``verb``, per case. Every entry is debt: unlike the
#: lists in ``test_rule_coverage.py``, no reason here says the omission is
#: acceptable. It is meant to shrink to nothing, at which point
#: ``DEFAULT_VERSION`` in :mod:`evals.harness.fingerprint` moves to 2 and
#: ``SubsetVerbIdentity`` can be scored over the corpus.
#:
#: Case 01 is at zero: it is the control, so it is the one every far-domain
#: recall figure is a delta against, and the one worth assigning first.
UNASSIGNED: dict[str, int] = {
    "01-payments-checkout": 0,
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


def test_the_unassigned_count_is_what_is_recorded():
    """Exact, in the shape ``MEASURED`` and ``FRONTIER`` already use here.

    Both directions fail: the debt growing is a new case that skipped the field,
    and the debt shrinking is progress somebody must re-quote. Either way the
    number in this file stays the number in the corpus.
    """
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    measured = {
        case.meta.id: sum(
            1 for claim in _stride_claims(case) if getattr(claim, "verb", None) is None
        )
        for case in corpus
    }
    assert measured == UNASSIGNED, (
        f"claims with no action verb moved to {measured}. Update UNASSIGNED."
        " When a case reaches 0, its reference set can be scored by"
        " SubsetVerbIdentity; when every case does, DEFAULT_VERSION in"
        " evals.harness.fingerprint moves to 2."
    )


def test_every_assigned_verb_is_in_the_vocabulary():
    """A verb outside the set matches nothing, so it fails where it is written."""
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    for case in corpus:
        assigned = [
            claim.verb
            for claim in _stride_claims(case)
            if getattr(claim, "verb", None) is not None
        ]
        bad = unknown_verbs(assigned)
        assert not bad, (
            f"{case.meta.id} assigns {', '.join(bad)}, which"
            f" evals.harness.verbs does not carry ({len(ACTION_VERBS)} verbs)"
        )
