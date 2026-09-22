"""A corpus addition that lands on an identity a sitting already marked.

A reference claim's identity outlives the claim holding it: a dropped claim
frees its fingerprint, and a later addition landing there takes the mark the
reader made about the claim at that identity.

**The base revision is the discriminator, and that is why this is a preflight.**
Where every case is unread, a claim legitimately carrying its own mark reads
identically to one that inherited another's, so a rule over the corpus and the
merged submissions alone answers about every marked claim rather than about the
added ones.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.harness import preflight, run
from evals.harness.reference import load_case
from evals.harness.sitting import mark_targets

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"
CASE = "05-cookbook-queue-webapp"


class TestWhatCountsAsAnAddition:
    def test_a_finding_the_base_also_carried_is_not_one(self):
        """A mark on an identity that did not move is the reader's own."""
        standing = {"v6:aaaa": "a claim."}

        found = preflight.inherited_rulings(
            CASE, standing, standing, {"v6:aaaa": "agree"}
        )

        assert found == []

    def test_a_reword_is_not_one(self):
        """A reword holds the identity by design, so its mark is still earned."""
        before = {"v6:aaaa": "the old wording."}
        after = {"v6:aaaa": "the new wording, same lane and place."}

        found = preflight.inherited_rulings(CASE, after, before, {"v6:aaaa": "agree"})

        assert found == []


class TestWhatItNames:
    def test_an_addition_that_inherits_a_mark(self):
        found = preflight.inherited_rulings(
            CASE,
            {"v6:aaaa": "different text at the same identity."},
            {},
            {"v6:aaaa": "duplicate"},
        )

        assert len(found) == 1
        assert found[0].mark == "duplicate"
        assert found[0].fingerprint == "v6:aaaa"
        assert found[0].claim == "different text at the same identity."

    def test_an_addition_on_a_free_identity_is_quiet(self):
        found = preflight.inherited_rulings(
            CASE, {"v6:bbbb": "somewhere nobody ruled."}, {}, {"v6:aaaa": "duplicate"}
        )

        assert found == []

    def test_it_names_every_one_rather_than_the_first(self):
        added = {"v6:aaaa": "one.", "v6:bbbb": "two.", "v6:cccc": "three."}

        found = preflight.inherited_rulings(
            CASE, added, {}, dict.fromkeys(added, "reject")
        )

        assert len(found) == 3


class TestTheIdentitiesComeFromTheSharedReader:
    """One rule, one reader: the keys are the sitting's, not a second spelling."""

    def test_keys_of_agrees_with_mark_targets(self):
        targets = mark_targets(load_case(CORPUS / CASE))

        keys = preflight.keys_of(CORPUS / CASE)

        assert set(keys) == {target.fingerprint for target in targets}

    def test_it_reads_every_framework_the_case_declares(self):
        targets = mark_targets(load_case(CORPUS / CASE))
        declared = {target.framework for target in targets}

        assert len(declared) > 1, "this case is meant to carry more than one package"
        assert len(preflight.keys_of(CORPUS / CASE)) == len(targets)


def test_the_command_is_reachable():
    assert run.COMMANDS["corpus-preflight"].run is preflight.command_preflight


class TestABaseItCannotRead:
    """A revision this clone does not carry is not a case the change adds.

    Both answers reach the same place — a case with no base has every claim
    counted as an addition — so a mistyped `--base` would report the whole
    corpus as new and name every mark on it. `git ls-tree` tells the two apart
    in its exit code, and this drives the revision that proved it rather than a
    simpler one written afterwards.
    """

    UNREADABLE = "deadbeefdeadbeef"

    def _args(self, base):
        return argparse.Namespace(base=base, root=preflight.REPO_ROOT)

    def test_an_unreadable_revision_is_refused_by_name(self, capsys):
        code = preflight.command_preflight(self._args(self.UNREADABLE))

        assert code == 1
        assert self.UNREADABLE in capsys.readouterr().err

    def test_it_never_reports_the_whole_corpus_as_added(self, capsys):
        """The shape of the defect, not only its exit code.

        Reporting `added` at all is what made the failure readable as an
        answer: the command printed a count over every claim in the corpus and
        listed a ruling for each one.
        """
        preflight.command_preflight(self._args(self.UNREADABLE))

        assert "finding(s) added" not in capsys.readouterr().out

    def test_a_base_it_can_read_still_answers(self, capsys):
        """``HEAD``, never ``HEAD~1``.

        CI checks out one commit, so a clone here may carry no parent — and
        this test first named ``HEAD~1``, which the refusal above then caught
        for exactly the right reason and failed the suite. The base a readable
        case needs is a revision that exists, and ``HEAD`` is the one every
        checkout has. What separates the two paths is the count line, which the
        refusal never prints, so that is what this reads rather than an exit
        code a dirty tree can move.
        """
        preflight.command_preflight(self._args("HEAD"))

        assert "finding(s) added since HEAD" in capsys.readouterr().out

    def test_the_error_type_is_exported(self):
        """A caller that drives this in-process needs the type by name."""
        assert "PreflightError" in preflight.__all__
        assert issubclass(preflight.PreflightError, RuntimeError)


def test_the_repo_root_default_is_a_checkout():
    """`--root` defaults to this clone, which the tests above lean on."""
    assert (preflight.REPO_ROOT / ".git").exists()
