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

**A review must cover every framework the case carries.** Step 6 asks the reader
to sign off on the reference sets *together*, because the property being
established — that the set is exhaustive against that model — is not
framework-local: one shared **System Model** feeds N reference sets, and a
session that read STRIDE's 21 claims says nothing about the 17 ASVS records
beside them. So the ``read`` list is checked against the case's declared
frameworks rather than merely being present, and a case reviewed for one
framework stays debt for the other.

Deterministic over ``case.json`` and free of provider calls, which is why it
gates on every PR.
"""

from __future__ import annotations

import pytest

from evals import verify_corpus
from evals.harness.reference import CLAIMS_DIR, load_corpus

#: Cases nobody has read, each with what that leaves unchecked. Every entry is
#: debt rather than an exemption: unlike the lists in ``test_rule_coverage.py``
#: and ``test_vocabulary_coverage.py``, no reason here says the omission is
#: acceptable. They are the cases that shipped before step 6 was enforced, and
#: the list is meant to shrink to nothing.
UNREVIEWED: dict[str, str] = {
    "02-iot-fleet-telemetry": (
        "18 STRIDE claims and 8 ASVS records, unread. The ASVS records feed the "
        "applicability matrix, which scores whether a requirement applies and "
        "never whether the set is complete."
    ),
    "03-batch-data-pipeline": "17 STRIDE claims, unread. Declares STRIDE only.",
    "04-ml-inference-service": (
        "18 STRIDE claims and 10 ASVS records. One STRIDE claim asserted the "
        "model emits training data in a case with no training pipeline; review "
        "sitting 01 found it through a calibration pair rather than by "
        "reading the case, so the rest of both sets is still unread."
    ),
    "05-cookbook-queue-webapp": (
        "17 STRIDE claims and 7 ASVS records, unread. The ASVS records feed the "
        "applicability matrix, which scores whether a requirement applies and "
        "never whether the set is complete."
    ),
    "06-cookbook-online-game": (
        "18 STRIDE claims and 6 ASVS records. Review sitting 01 relabelled a "
        "pair against this case's fabricated-progression claim, which is the "
        "nearest anybody has come to reading it. The ASVS records cover the "
        "moderation website alone, which is the only part ASVS scopes itself to."
    ),
    "07-cicd-store-deploy": "24 STRIDE claims, unread. Declares STRIDE only.",
    "08-sso-identity-broker": (
        "23 STRIDE claims and 12 ASVS records, unread. The only case scored at "
        "ASVS level 2, and the records were written by an agent in #236 against "
        "a source nobody has read against its model."
    ),
    "09-cookbook-sokify-retail": (
        "20 STRIDE claims and 7 ASVS records, unread. The ASVS records feed the "
        "applicability matrix, which scores whether a requirement applies and "
        "never whether the set is complete."
    ),
    "10-cookbook-generic-cms": (
        "17 STRIDE claims and 8 ASVS records, unread. The ASVS records feed the "
        "applicability matrix, which scores whether a requirement applies and "
        "never whether the set is complete."
    ),
    "11-sparse-shift-scheduling": (
        "16 STRIDE claims and 8 ASVS records, unread. The sparsest source in the "
        "corpus, so its ASVS records rest on the least stated material of any."
    ),
    "12-overclaiming-supplier-portal": (
        "15 STRIDE claims and 10 ASVS records, unread. The vendor datasheet in "
        "its source asserts controls the input never states, so a reader has to "
        "separate the claim from the fact on every record."
    ),
    "13-dispatch-control-plane": (
        "19 STRIDE claims and 6 ASVS records, unread. The ASVS records feed the "
        "applicability matrix, which scores whether a requirement applies and "
        "never whether the set is complete."
    ),
}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def reviewed_by_case(corpus):
    """Whether each case carries a step 6 sign-off covering all of its frameworks."""
    return {
        case.meta.id: case.meta.review is not None
        and not required_reading(case) - set(case.meta.review.read)
        for case in corpus
    }


def required_reading(case) -> set[str]:
    """What a complete step 6 session reads for this case.

    The shared artefacts, plus one reference set per framework the case declares.
    Derived from the declaration rather than listed, so a case that gains a third
    framework's reference set re-opens its review by construction.
    """
    return {"source.md", "model.json"} | {
        f"{CLAIMS_DIR}/{declared.name}.json" for declared in case.meta.frameworks
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


def test_a_review_covers_every_framework_the_case_carries(corpus):
    """A session that read one framework's set has not reviewed the case.

    The failure this exists for: case 01 declares both frameworks, so a `read`
    list naming ``claims/stride.json`` alone leaves 17 ASVS records unread while
    the case reads as signed off.
    """
    partial = {
        case.meta.id: sorted(required_reading(case) - set(case.meta.review.read))
        for case in corpus
        if case.meta.review is not None
    }
    incomplete = {case_id: gap for case_id, gap in partial.items() if gap}
    assert not incomplete, (
        f"these cases carry a review that did not cover everything: {incomplete}."
        " Step 6 signs off on the model and every framework's reference set"
        " together; read what is missing and extend the `read` list, or the case"
        " belongs back in UNREVIEWED."
    )
