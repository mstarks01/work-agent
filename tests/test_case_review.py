"""Which golden cases a person has read, and which are still waiting.

``evals/BLESSING.md`` step 6 is the only thing in this repo that would catch a
reference claim asserting a fact its own model does not hold. Review sitting 01
proved that: it found one in case 04, and a mechanical check for the same defect
fires on 231 of 243 claims because a claim is *supposed* to describe an attack in
words the system description never uses. So the reading session is not
belt-and-braces on top of the lints — it is the only instrument for a whole class
of defect, and the corpus shipped 13 cases without it.

This makes the gap countable and stops it growing. A case whose ``reviews``
list holds a clearing **Case Sitting** — a rostered submitting account, every
required file read, every recorded digest still matching — has been read. Every case
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
from pydantic import ValidationError

from evals import verify_corpus
from evals.harness.reference import ReadRecord, load_corpus
from evals.harness.roster import DEFAULT_ROSTER_PATH
from evals.harness.roster import load as load_roster
from evals.harness.sitting import clears, document_name, drifted, moved
from evals.harness.submit import reads_as_a_reading_document

#: Cases nobody has read, each with what that leaves unchecked. Every entry is
#: a case nobody read rather than an exemption: unlike the lists in
#: ``test_rule_coverage.py``
#: and ``test_vocabulary_coverage.py``, no reason here says the omission is
#: acceptable. They are the cases that shipped before step 6 was enforced, and
#: the list is meant to shrink to nothing.
UNREVIEWED: dict[str, str] = {
    "01-payments-checkout": (
        "21 STRIDE claims and 17 ASVS records. The 2026-08-23 sitting read "
        "them and its finding stands, but the model.json it signed predates "
        "the assumption `attribute` field (#465), so the digest no longer "
        "matches the bytes anybody read. The case needs a fresh sitting."
    ),
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
            clears(case, sitting, roster, verify_corpus.CORPUS_DIR)
            for sitting in case.meta.reviews
        )
        for case in corpus
    }


def test_a_new_case_carries_a_sitting(reviewed_by_case):
    undeclared = sorted(
        case_id
        for case_id, reviewed in reviewed_by_case.items()
        if not reviewed and case_id not in UNREVIEWED
    )
    assert not undeclared, (
        f"these cases have no Case Sitting that clears them: {undeclared}."
        " Either no `reviews` entry covers every required file, its"
        " `submitted_by` has no roster line, or a read file changed under its"
        " digests. Hold"
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
    stale_files = {
        f"{case.meta.id}[{index}]": stale
        for case in corpus
        for index, sitting in enumerate(case.meta.reviews)
        if (stale := drifted(case, sitting, verify_corpus.CORPUS_DIR))
    }
    assert not stale_files, (
        f"these sittings' read files changed under their digests: {stale_files}."
        " Hold a fresh sitting over the changed files and append its entry,"
        " or put the case's line back in UNREVIEWED — and either way, a"
        " person names the unread case in this PR."
    )


def _document_problems(case, sitting) -> list[str]:
    """What is wrong with one sitting's evidence document, if anything."""
    expected = document_name(sitting.submitted_by)
    if sitting.document != expected:
        return [f"names {sitting.document!r}, and its submitter writes {expected!r}"]
    path = verify_corpus.CORPUS_DIR / case.meta.id / expected
    if not path.is_file():
        return [f"{expected} is not committed beside the case"]
    if not reads_as_a_reading_document(path):
        return [f"{expected} is not a reading document, so it evidences nothing"]
    return []


def test_every_sitting_names_an_existing_document(corpus):
    """Only the filled ``REVIEW-<login>.md`` shows the method ran (#327).

    All three words are checked. The name has to be the one
    :func:`~evals.harness.sitting.document_name` derives from the submitting
    login, because a document under another name is another submission's; the
    file has to be there; and it has to say something, because an empty file is
    not a filled one and this docstring is the only place that ever said so.

    It used to be one ``.is_file()`` probe on whatever name the entry gave, so
    ``"source.md"`` satisfied it in every case directory.
    """
    missing = [
        f"{case.meta.id}: {problem}"
        for case in corpus
        for sitting in case.meta.reviews
        for problem in _document_problems(case, sitting)
    ]
    assert not missing, (
        f"these sittings name evidence documents that do not exist: {missing}."
        " Commit the filled copy beside the case; the entry's `document`"
        " field is what makes the sitting auditable."
    )


def test_every_submitting_account_has_a_roster_line(corpus, roster):
    """Standing labels the read, and the one roster is where standing lives."""
    unrostered = sorted(
        {
            sitting.submitted_by
            for case in corpus
            for sitting in case.meta.reviews
            if sitting.submitted_by not in roster
        }
    )
    assert not unrostered, (
        f"these submitting accounts have no line in evals/review/voters.toml:"
        f" {unrostered}. A sitting no rostered account carries clears nothing,"
        " because no published number could state the standing behind it."
    )


class TestAReadRecordNamesAFileInsideItsCase:
    """The value is joined onto a case directory and the result is read."""

    @pytest.mark.parametrize(
        "path", ["source.md", "model.json", "claims/stride.json", "claims/asvs.json"]
    )
    def test_the_shapes_the_corpus_actually_holds_are_accepted(self, path):
        ReadRecord(file=path, sha256="0" * 64)

    @pytest.mark.parametrize(
        "path",
        [
            "../../../etc/passwd",
            "/etc/hostname",
            "a/../b",
            "..",
            ".hidden",
            "a\\b",
            "claims/../../x",
        ],
    )
    def test_a_path_that_leaves_the_case_directory_is_refused(self, path):
        """`Path("/case") / "/etc/hostname"` is `/etc/hostname`: an absolute
        right-hand side replaces the left. `moved()` then reads whatever the
        record names and compares the digest the same record supplied, which is
        a digest oracle, an unbounded read, and an uncaught `PermissionError`
        on a file the process may not open."""
        with pytest.raises(ValidationError):
            ReadRecord(file=path, sha256="0" * 64)


class TestAReadRecordCannotLeaveItsCaseBySymlink:
    """`CORPUS_RELATIVE_PATH` bounds the name; a symlink needs no bad name.

    Run-6 closed the string half of this and the docstring, the test and the
    fix all said it was closed. `source.md` matches the pattern perfectly and
    can point anywhere, so the digest oracle, the unbounded read and the
    uncaught `PermissionError` all came back.
    """

    def _case(self, tmp_path):
        case = tmp_path / "99-a-case"
        case.mkdir()
        (case / "real.md").write_text("genuine\n", encoding="utf-8")
        return case

    def test_a_symlink_out_of_the_case_is_stale_not_matched(self, tmp_path):
        case = self._case(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("the secret\n", encoding="utf-8")
        (case / "source.md").symlink_to(outside)
        correct = hashlib.sha256(outside.read_bytes()).hexdigest()

        # A correct guess must NOT report "not stale"; that is the oracle.
        assert moved(case, {"source.md": correct}) == ["source.md"]

    def test_an_unreadable_target_does_not_raise(self, tmp_path):
        """The lint runs over a stranger's PR tree in CI; it must not crash."""
        case = self._case(tmp_path)
        (case / "source.md").symlink_to("/proc/1/mem")

        assert moved(case, {"source.md": "0" * 64}) == ["source.md"]

    def test_a_symlink_loop_does_not_raise(self, tmp_path):
        """`resolve` raises `RuntimeError` on a loop under 3.12, which an
        `except OSError` let through as the traceback the lint promises not to
        leave on a stranger's pull request.
        """
        case = self._case(tmp_path)
        (case / "source.md").symlink_to(case / "source.md")

        assert moved(case, {"source.md": "0" * 64}) == ["source.md"]

    def test_an_ordinary_file_still_verifies(self, tmp_path):
        case = self._case(tmp_path)
        digest = hashlib.sha256(b"genuine\n").hexdigest()

        assert moved(case, {"real.md": digest}) == []
