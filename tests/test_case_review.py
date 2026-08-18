"""Which golden cases a person has read, and which are still waiting.

``evals/BLESSING.md`` step 6 is the only thing in this repo that would catch a
reference claim asserting a fact its own model does not hold. Review sitting 01
proved that: it found one in case 04, and a mechanical check for the same defect
fires on 231 of 243 claims because a claim is *supposed* to describe an attack in
words the system description never uses. So the reading session is not
belt-and-braces on top of the lints — it is the only instrument for a whole class
of defect, and the corpus shipped 13 cases without it.

This makes the debt countable and stops it growing. A case carrying a
``review`` block in ``case.json`` has been read. Every case that has not is named
in :data:`UNREVIEWED` with what it is still exposed to, and a **new** case that
arrives without a block fails.

Deterministic over ``case.json`` and free of provider calls, which is why it
gates on every PR.
"""

from __future__ import annotations

import pytest

from evals import verify_corpus
from evals.harness.reference import load_corpus

#: Cases nobody has read, each with what that leaves unchecked. Every entry is
#: debt rather than an exemption: unlike the lists in ``test_rule_coverage.py``
#: and ``test_vocabulary_coverage.py``, no reason here says the omission is
#: acceptable. They are the cases that shipped before step 6 was enforced, and
#: the list is meant to shrink to nothing.
UNREVIEWED: dict[str, str] = {
    "01-payments-checkout": (
        "The control case. Every far-domain recall number in the suite is a delta"
        " against this one, so an error in its 21 reference claims moves every"
        " comparison the corpus exists to make. Reviewed first: REVIEW-02."
    ),
    "02-iot-fleet-telemetry": "18 reference claims, unread.",
    "03-batch-data-pipeline": "17 reference claims, unread.",
    "04-ml-inference-service": (
        "18 reference claims. One of them asserted the model emits training data"
        " in a case with no training pipeline; review sitting 01 found it through"
        " a judge-calibration pair rather than by reading the case, so the rest of"
        " this set is still unread."
    ),
    "05-cookbook-queue-webapp": "17 reference claims, unread.",
    "06-cookbook-online-game": (
        "18 reference claims. Review sitting 01 relabelled a pair against this"
        " case's fabricated-progression claim, which is the nearest anybody has"
        " come to reading it."
    ),
    "07-cicd-store-deploy": "24 reference claims, unread.",
    "08-sso-identity-broker": "23 reference claims, unread.",
    "09-cookbook-sokify-retail": "20 reference claims, unread.",
    "10-cookbook-generic-cms": "17 reference claims, unread.",
    "11-sparse-shift-scheduling": "16 reference claims, unread.",
    "12-overclaiming-supplier-portal": "15 reference claims, unread.",
    "13-dispatch-control-plane": "19 reference claims, unread.",
}


@pytest.fixture(scope="module")
def reviewed_by_case():
    """Whether each case carries a step 6 sign-off."""
    return {
        case.meta.id: case.meta.review is not None
        for case in load_corpus(verify_corpus.CORPUS_DIR)
    }


def test_a_new_case_carries_a_review(reviewed_by_case):
    undeclared = sorted(
        case_id
        for case_id, reviewed in reviewed_by_case.items()
        if not reviewed and case_id not in UNREVIEWED
    )
    assert not undeclared, (
        f"these cases carry no `review` block in case.json: {undeclared}. Run"
        " evals/BLESSING.md step 6 and record who read it, when, and what they"
        " read. A case merged unread cannot be caught later by any lint — that"
        " is what this module's docstring is about."
    )


def test_the_debt_list_does_not_rot(reviewed_by_case):
    """A case that gets read has to leave the list, or the count is a lie."""
    stale = sorted(
        case_id for case_id in UNREVIEWED if reviewed_by_case.get(case_id, False)
    )
    assert not stale, (
        f"these cases now carry a review and are still listed as debt: {stale}."
        " Remove them from UNREVIEWED."
    )


def test_every_listed_case_exists(reviewed_by_case):
    """The list names cases, not ghosts — a renamed case must be re-entered."""
    missing = sorted(set(UNREVIEWED) - set(reviewed_by_case))
    assert not missing, f"UNREVIEWED names cases that do not exist: {missing}"
