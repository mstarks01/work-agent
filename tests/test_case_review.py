"""Which golden cases a person has read, and which are still waiting.

``evals/BLESSING.md`` step 6 is the only thing in this repo that would catch a
reference claim asserting a fact its own model does not hold. Review sitting 01
proved that: it found one in case 04, and a mechanical check for the same defect
fires on 231 of 243 claims because a claim is *supposed* to describe an attack in
words the system description never uses. So the reading session is not
belt-and-braces on top of the lints — it is the only instrument for a whole class
of defect, and the corpus shipped 13 cases without it.

This makes the gap countable and stops it growing. A case whose ``reviews``
list holds a clearing **Case Sitting** — a rostered reviewer, every required
file read, every recorded digest still matching — has been read. Every case
that has not is named in :data:`UNREVIEWED` with what it is still exposed to,
and a **new** case that arrives without one fails.

**A sitting must cover every framework the case carries.** Step 6 asks the reader
to sign off on the reference sets *together*, because the property being
established — that the set is exhaustive against that model — is not
framework-local: one shared **System Model** feeds N reference sets, and a
session that read STRIDE's 21 claims says nothing about the 17 ASVS records
beside them. So the ``read`` list is checked against the case's declared
frameworks rather than merely being present, and a case reviewed for one
framework stays unread for the other.

Deterministic over ``case.json`` and free of provider calls, which is why it
gates on every PR.
"""

from __future__ import annotations

import hashlib

import pytest

from evals import verify_corpus
from evals.harness.reference import CLAIMS_DIR, load_corpus
from evals.harness.roster import DEFAULT_ROSTER_PATH
from evals.harness.roster import load as load_roster

#: Cases nobody has read, each with what that leaves unchecked. Every entry is
#: a case nobody read rather than an exemption: unlike the lists in
#: ``test_rule_coverage.py``
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
def roster():
    return load_roster(DEFAULT_ROSTER_PATH)


@pytest.fixture(scope="module")
def reviewed_by_case(corpus, roster):
    """Whether any of each case's sittings takes it off the list.

    A sitting clears when a **rostered** person read every required file and
    the recorded digests still match the tree (#327). A drifted digest stops
    clearing, so a PR that edits a read file puts the case back on the list
    fail-closed: it carries a fresh sitting, or it puts the case's line back
    in ``UNREVIEWED`` — a person always names the unread case, in the PR that
    caused it.
    """
    return {
        case.meta.id: any(
            _clears(case, sitting, roster) for sitting in case.meta.reviews
        )
        for case in corpus
    }


def _clears(case, sitting, roster) -> bool:
    covered = not required_reading(case) - {record.file for record in sitting.read}
    return covered and sitting.reviewer in roster and not _drifted(case, sitting)


def _drifted(case, sitting) -> list[str]:
    """The read files whose bytes no longer match the sitting's digests."""
    case_dir = verify_corpus.CORPUS_DIR / case.meta.id
    stale = []
    for record in sitting.read:
        target = case_dir / record.file
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != record.sha256
        ):
            stale.append(record.file)
    return stale


def required_reading(case) -> set[str]:
    """What a complete Case Sitting reads for this case.

    The shared artefacts, plus one reference set per framework the case declares.
    Derived from the declaration rather than listed, so a case that gains a third
    framework's reference set re-opens its review by construction.
    """
    return {"source.md", "model.json"} | {
        f"{CLAIMS_DIR}/{declared.name}.json" for declared in case.meta.frameworks
    }


def test_a_new_case_carries_a_sitting(reviewed_by_case):
    undeclared = sorted(
        case_id
        for case_id, reviewed in reviewed_by_case.items()
        if not reviewed and case_id not in UNREVIEWED
    )
    assert not undeclared, (
        f"these cases have no Case Sitting that clears them: {undeclared}."
        " Either no `reviews` entry covers every required file, its reviewer"
        " has no roster line, or a read file changed under its digests. Hold"
        " a sitting (evals/BLESSING.md step 6) and append the entry, or name"
        " the case as unread by adding its line to UNREVIEWED. A case merged"
        " unread cannot be caught later by any lint — that is what this"
        " module's docstring is about."
    )


def test_the_unreviewed_list_does_not_rot(reviewed_by_case):
    """A case that gets read has to leave the list, or the count is a lie."""
    stale = sorted(
        case_id for case_id in UNREVIEWED if reviewed_by_case.get(case_id, False)
    )
    assert not stale, (
        f"these cases now carry a review and are still listed as unread:"
        f" {stale}."
        " Remove them from UNREVIEWED."
    )


def test_every_listed_case_exists(reviewed_by_case):
    """The list names cases, not ghosts — a renamed case must be re-entered."""
    missing = sorted(set(UNREVIEWED) - set(reviewed_by_case))
    assert not missing, f"UNREVIEWED names cases that do not exist: {missing}"


def test_no_recorded_digest_has_drifted(corpus):
    """A sitting signs specific bytes; a silent edit under it must be loud.

    The failure this exists for: a PR that improves a reviewed case's
    ``claims/stride.json`` would leave the sitting's sign-off pointing at
    words the reviewer never read. The digest names the drifted file here, in
    the PR that caused it, and the author answers with a fresh sitting or a
    re-opened UNREVIEWED line.
    """
    drifted = {
        f"{case.meta.id}[{index}]": stale
        for case in corpus
        for index, sitting in enumerate(case.meta.reviews)
        if (stale := _drifted(case, sitting))
    }
    assert not drifted, (
        f"these sittings' read files changed under their digests: {drifted}."
        " Hold a fresh sitting over the changed files and append its entry,"
        " or put the case's line back in UNREVIEWED — and either way, a"
        " person names the unread case in this PR."
    )


def test_every_sitting_names_an_existing_document(corpus):
    """Only the filled ``REVIEW-<login>.md`` shows the method ran (#327)."""
    missing = [
        f"{case.meta.id}: {sitting.document}"
        for case in corpus
        for sitting in case.meta.reviews
        if not (verify_corpus.CORPUS_DIR / case.meta.id / sitting.document).is_file()
    ]
    assert not missing, (
        f"these sittings name evidence documents that do not exist: {missing}."
        " Commit the filled copy beside the case; the entry's `document`"
        " field is what makes the sitting auditable."
    )


def test_every_reviewer_has_a_roster_line(corpus, roster):
    """Standing labels the read, and the one roster is where standing lives."""
    unrostered = sorted(
        {
            sitting.reviewer
            for case in corpus
            for sitting in case.meta.reviews
            if sitting.reviewer not in roster
        }
    )
    assert not unrostered, (
        f"these reviewers have no line in evals/review/voters.toml:"
        f" {unrostered}. A sitting by an unrostered person clears nothing,"
        " because no published number could state the standing behind it."
    )
