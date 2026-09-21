"""Which golden cases a person has read, and which are still waiting.

``evals/BLESSING.md`` step 6 is the only thing in this repo that would catch a
reference claim asserting a fact its own model does not hold. Review sitting 01
proved that: it found one in case 04, and a mechanical check for the same defect
fires on 231 of 243 claims because a claim is *supposed* to describe an attack in
words the system description never uses. So the reading session is not
belt-and-braces on top of the lints — it is the only instrument for a whole class
of defect, and the corpus shipped 13 cases without it.

This makes the gap countable and stops it growing. A case a merged
submission under ``evals/review/submissions`` currently clears has been read.
:func:`~evals.review_submission.current_reviews` is the one reader of that
question, and it is fail-closed: a submission stops clearing its case the
moment any file it read changes. Every case no submission clears is named in
:data:`UNREVIEWED` with what it is still exposed to, and a **new** case that
arrives without a review fails.

:data:`UNREVIEWED` says what each unread case leaves unchecked. It is not the
count of unread cases — :func:`~evals.review_submission.unreviewed_cases`
derives that from the corpus and the submissions, so no list can disagree with
it. An entry for a case somebody has since read is spent and can be deleted.

**A sitting must cover every framework the case carries.** Step 6 asks the reader
to sign off on the reference sets *together*, because the property being
established — that the set is exhaustive against that model — is not
framework-local: one shared **System Model** feeds N reference sets, and a
session that read STRIDE's 21 claims says nothing about the 17 ASVS records
beside them. So the ``read`` list is checked against the case's declared
frameworks rather than merely being present, and a case reviewed for one
framework stays unread for the other.

Deterministic over the corpus and the merged submissions, and free of
provider calls, which is why it gates on every PR.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from evals import verify_corpus
from evals.harness.reference import ReadRecord, load_corpus
from evals.harness.sitting import moved
from evals.review_submission import REPO_ROOT, unreviewed_cases

#: Cases nobody has read, each with what that leaves unchecked. Every entry is
#: a case nobody read rather than an exemption: unlike the lists in
#: ``test_rule_coverage.py``
#: and ``test_vocabulary_coverage.py``, no reason here says the omission is
#: acceptable.
#:
#: **Empty for one day.** The sitting of 2026-09-21 read all 13 cases and
#: marked all 344 recorded claims; acting on those marks then changed a claim
#: file in every case, which breaks each signature. Every entry below is the
#: same situation and says so: the reader read the very sets they ruled out, so
#: nothing they judged is hidden, and a second sitting over the current files
#: clears it.
UNREVIEWED: dict[str, str] = {
    "01-payments-checkout": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: 5 STRIDE claims dropped; one elevation-of-privilege claim added at `expected`, written by the reader in their own list and promoted after every elevation claim this case carried was ruled out. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "02-iot-fleet-telemetry": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; 5 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "03-batch-data-pipeline": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 2 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "04-ml-inference-service": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 2 STRIDE claims dropped; 1 ASVS record dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "05-cookbook-queue-webapp": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: 5 STRIDE claims dropped; one elevation-of-privilege claim added at `expected`, written by the reader in their own list and promoted after every elevation claim this case carried was ruled out. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "06-cookbook-online-game": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 3 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "07-cicd-store-deploy": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 4 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "08-sso-identity-broker": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; 3 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "09-cookbook-sokify-retail": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 2 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "10-cookbook-generic-cms": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 1 STRIDE claim dropped; 1 ASVS record dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "11-sparse-shift-scheduling": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 1 STRIDE claim dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "12-overclaiming-supplier-portal": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: the `unsure` must-find claims reworded to state the premise they rest on, because the reader marked them `unsure` and their notes name a local fact the input never carried; one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 4 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
    "13-dispatch-control-plane": (
        "Read on 2026-09-21 and un-read by that same sitting's own "
        "rulings: one repudiation claim retiered to `expected`, because the reader marked it `unsure` and their note says a description cannot settle it; 2 STRIDE claims dropped. The reader read the sets they then ruled "
        "out, so the signature cannot match and nothing they judged "
        "has been hidden. A second sitting over the current files "
        "clears it."
    ),
}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def reviewed_by_case(corpus):
    """Whether merged submissions currently clear each case.

    One reader, and the one the app and ``--list`` read too:
    :func:`~evals.review_submission.unreviewed_cases`. A case is read when
    every framework it declares is covered, and a submission stops covering
    a framework the moment a file it read changes — so a PR that edits a read
    file puts the case back on the list fail-closed. It carries a fresh
    review, or a person names the case in ``UNREVIEWED``, in the PR that
    caused it.
    """
    unread = set(unreviewed_cases(REPO_ROOT))
    return {case.meta.id: case.meta.id not in unread for case in corpus}


def test_a_new_case_carries_a_sitting(reviewed_by_case):
    undeclared = sorted(
        case_id
        for case_id, reviewed in reviewed_by_case.items()
        if not reviewed and case_id not in UNREVIEWED
    )
    assert not undeclared, (
        f"these cases have no Case Sitting that clears them: {undeclared}."
        " Either no merged submission under evals/review/submissions covers"
        " every framework the case declares, or a read file changed under a"
        " submission's digests. Hold a sitting (evals/BLESSING.md step 6) and"
        " contribute it, or name the case as unread by adding its line to"
        " UNREVIEWED. A case merged unread cannot be caught later by any lint"
        " — that is what this module's docstring is about."
    )


def test_every_listed_case_exists(reviewed_by_case):
    """The list names cases, not ghosts — a renamed case must be re-entered."""
    missing = sorted(set(UNREVIEWED) - set(reviewed_by_case))
    assert not missing, f"UNREVIEWED names cases that do not exist: {missing}"


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
