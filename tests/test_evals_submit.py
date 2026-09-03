"""The submit command's spine, proven against a real git origin and a fake gh.

The checks are #320's and #322's, run where the contributor is: the delta
names only the login, the vote file only appends, nobody raises their own
standing, and nothing outside the allowlist rides along. The end-to-end tests
use a throwaway origin and a `gh` shim on PATH, because the one thing this
module must never do in a test is reach GitHub.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.harness import submit
from evals.harness.fingerprint import Components
from evals.harness.ledger import cast
from evals.harness.run import main
from evals.harness.sitting import unreviewed_cases

ROSTER_BASE = """version = 1

[voters.mstarks01]
standing = "maintainer"
"""

ROSTER_WITH_ADA = (
    ROSTER_BASE
    + """
[voters.ada]
standing = "contributor"
"""
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def vote_line(target: str = "process:a", voter: str = "ada") -> str:
    vote = cast(
        Components("stride", "information-disclosure", (target,), verb="read"),
        "01-payments-checkout",
        "up",
        voter,
    )
    return json.dumps(vote.to_json(), ensure_ascii=False, sort_keys=True) + "\n"


CASE = "99-test-case"
OTHER = "98-other-case"


def unreviewed_line(case: str) -> str:
    return f'    "{case}": "unread",\n'


def digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_case(clone: Path, case: str) -> Path:
    """One unread corpus case: its sources, one claim file and its metadata."""
    case_dir = clone / "evals" / "corpus" / case
    (case_dir / "claims").mkdir(parents=True)
    (case_dir / "source.md").write_text("a system\n", encoding="utf-8")
    (case_dir / "model.json").write_text("{}\n", encoding="utf-8")
    (case_dir / "claims" / "stride.json").write_text("[]\n", encoding="utf-8")
    (case_dir / "case.json").write_text(
        json.dumps(
            {
                "id": case,
                "sources": [{"kind": "description", "file": "source.md"}],
                "frameworks": [{"name": "stride"}],
                "reviews": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return case_dir


@pytest.fixture
def repo(tmp_path):
    """A clone with an origin whose main holds the roster, two unread cases
    under evals/corpus/, the unreviewed-list stub, and no votes."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        capture_output=True,
        check=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin), str(clone)], capture_output=True, check=True
    )
    git(clone, "config", "user.email", "test@example.test")
    git(clone, "config", "user.name", "A Test")
    (clone / ".gitignore").write_text("evals/runs/\n", encoding="utf-8")
    review = clone / "evals" / "review"
    review.mkdir(parents=True)
    (review / "voters.toml").write_text(ROSTER_BASE, encoding="utf-8")
    for case in (CASE, OTHER):
        seed_case(clone, case)
    (clone / "tests").mkdir()
    (clone / "tests" / "test_case_review.py").write_text(
        "UNREVIEWED = {\n"
        + "".join(unreviewed_line(case) for case in (CASE, OTHER))
        + "}\n",
        encoding="utf-8",
    )
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "seed")
    git(clone, "push", "-u", "origin", "main")
    return clone


def prepare_sitting(
    clone: Path,
    case: str = CASE,
    reviewer: str = "ada",
    read_for: str | None = None,
    read: list[str] | None = None,
    document: str = "REVIEW-ada.md",
    write_document: bool = True,
    clear_unreviewed: bool = True,
) -> Path:
    """Ada's working state after a sitting: entry, evidence, the line gone."""
    case_dir = clone / "evals" / "corpus" / case
    files = (
        read if read is not None else ["source.md", "model.json", "claims/stride.json"]
    )
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    meta["reviews"].append(
        {
            "submitted_by": reviewer,
            "submitted_for": read_for or reviewer,
            "date": "2026-08-26",
            "read": [
                {"file": name, "sha256": digest(case_dir / name)} for name in files
            ],
            "document": document,
            "notes": "",
        }
    )
    (case_dir / "case.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    if write_document:
        # The shape `sitting.document()` writes. The fixture used to put
        # fifteen bytes of prose here, which is why no test noticed that the
        # evidence gate never checked what a reading document is.
        (case_dir / document).write_text(
            f"# Case Sitting — `{case}`\n\n**A case.**\n\n"
            "Read by ada, submitted by ada.\n\n---\n\n## My own list\n\n"
            "- a spoofed device\n",
            encoding="utf-8",
        )
    if clear_unreviewed:
        listing = clone / "tests" / "test_case_review.py"
        listing.write_text(
            listing.read_text(encoding="utf-8").replace(unreviewed_line(case), ""),
            encoding="utf-8",
        )
    (clone / "evals" / "review" / "voters.toml").write_text(
        ROSTER_WITH_ADA, encoding="utf-8"
    )
    return case_dir


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A gh on PATH that answers as user ada, owner ada, and logs every call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    log.touch()
    shim = bin_dir / "gh"
    shim.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$*" in\n'
        '  *"api user"*) echo ada ;;\n'
        '  *".owner.login"*) echo ada ;;\n'
        '  *"--json name"*) echo repo ;;\n'
        '  *"pr create"*) echo "https://example.test/pr/1" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def prepare_vote(clone: Path, roster: str = ROSTER_WITH_ADA) -> Path:
    """Ada's working state: her roster line added, one vote appended."""
    (clone / "evals" / "review" / "voters.toml").write_text(roster, encoding="utf-8")
    votes = clone / "evals" / "review" / "votes"
    votes.mkdir(exist_ok=True)
    (votes / "ada.jsonl").write_text(vote_line(), encoding="utf-8")
    return votes


class TestTheVoteChecks:
    def test_a_clean_submission_passes_every_check(self, repo):
        prepare_vote(repo)
        git(repo, "fetch", "origin")
        checks = submit._vote_preflight(repo, "ada")
        failed = [check.name for check in checks if not check.passed]
        assert not failed

    def test_a_stray_change_is_refused_by_name(self, repo):
        prepare_vote(repo)
        (repo / "README.md").write_text("drift\n", encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_scope(repo, "ada", kind_name="vote")
        assert not check.passed
        assert "README.md" in check.problems[0]

    def test_another_voters_file_is_refused(self, repo):
        votes = prepare_vote(repo)
        (votes / "sam.jsonl").write_text(vote_line(voter="sam"), encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_the_delta_is_yours(repo, "ada")
        assert not check.passed
        assert "sam.jsonl" in check.problems[0]

    def test_a_scalar_roster_entry_is_noted_and_does_not_raise(self, repo, tmp_path):
        """`ada = "contributor"` where `[voters.ada]` was meant.

        The line a first-timer writes, and the roster loader's to refuse. This
        note runs after that check's own handler and is the only check a
        roster-only PR runs, so it has to survive the shape rather than raise
        `AttributeError` through the whole preflight.
        """
        live = tmp_path / "voters.toml"
        live.write_text(
            'version = 1\n\n[voters]\nada = "contributor"\n\n'
            '[voters.mstarks01]\nstanding = "maintainer"\n',
            encoding="utf-8",
        )

        notes = submit._roster_delta(ROSTER_WITH_ADA, live)

        assert notes == ["ada: changed, and its entry is not a table"]

    def test_a_vote_keyed_under_a_version_stride_left_is_refused(self, repo):
        """The row the loader cannot refuse, caught where it arrives.

        Self-consistent at version 1, because version 1 reads no verb, so it
        loads and scores under a rule STRIDE has moved past -- and then `rekey`
        refuses the whole ledger over it. The loader has to keep accepting it,
        because a ledger written before a rule change is what `rekey` reads.
        An added row is the one row a pull request can insist is current.
        """
        votes = prepare_vote(repo)
        stale = cast(
            Components("stride", "information-disclosure", ("process:a",)),
            "01-payments-checkout",
            "up",
            "ada",
            version=1,
        )
        (votes / "ada.jsonl").write_text(
            json.dumps(stale.to_json(), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        git(repo, "fetch", "origin")

        check = submit._check_your_file_appends(repo, "ada")

        assert not check.passed
        assert "keyed at version 1" in check.problems[0]
        assert "stride keys at" in check.problems[0]

    def test_a_rewrite_of_history_is_refused(self, repo):
        """#322's append shape: the base content must be a byte prefix."""
        votes = prepare_vote(repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "first vote")
        git(repo, "push", "origin", "main")
        (votes / "ada.jsonl").write_text(vote_line(target="store:b"), encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_your_file_appends(repo, "ada")
        assert not check.passed
        assert "rewrites history" in check.problems[0]

    def test_an_empty_delta_has_nothing_to_submit(self, repo):
        prepare_vote(repo)
        (repo / "evals" / "review" / "votes" / "ada.jsonl").write_text(
            "", encoding="utf-8"
        )
        git(repo, "fetch", "origin")
        check = submit._check_your_file_appends(repo, "ada")
        assert not check.passed
        assert "nothing to submit" in check.problems[0]

    def test_a_self_raised_standing_is_refused(self, repo):
        """#320's one dangerous edit, failed at the contributor's machine."""
        prepare_vote(
            repo,
            roster=ROSTER_BASE + '\n[voters.ada]\nstanding = "maintainer"\n',
        )
        git(repo, "fetch", "origin")
        check = submit._check_no_self_raise(repo, "ada")
        assert not check.passed

    def test_the_title_counts_votes_and_cases(self, repo):
        votes = prepare_vote(repo)
        with (votes / "ada.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(vote_line(target="store:b"))
        git(repo, "fetch", "origin")
        assert submit._vote_title(repo, "ada") == "Vote: ada, 2 votes over 1 cases"


class TestTheSittingChecks:
    def test_a_clean_sitting_passes_every_check(self, repo):
        prepare_sitting(repo)
        git(repo, "fetch", "origin")
        checks = submit._sitting_preflight(repo, "ada")
        failed = [check.name for check in checks if not check.passed]
        assert not failed

    def test_an_entry_naming_someone_else_is_refused(self, repo):
        prepare_sitting(repo, reviewer="sam")
        git(repo, "fetch", "origin")
        check = submit._check_sitting_is_yours(repo, "ada")
        assert not check.passed
        assert "'sam'" in check.problems[0]

    def test_a_drifted_digest_is_refused_by_filename(self, repo):
        case_dir = prepare_sitting(repo)
        (case_dir / "source.md").write_text("edited after the read\n", encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_sitting_evidence(repo, "ada")
        assert not check.passed
        assert "source.md" in check.problems[0]

    def test_a_missing_document_is_refused(self, repo):
        prepare_sitting(repo, write_document=False)
        git(repo, "fetch", "origin")
        check = submit._check_sitting_evidence(repo, "ada")
        assert not check.passed
        assert "REVIEW-ada.md" in check.problems[0]

    def test_a_rewritten_history_is_refused(self, repo):
        """`reviews` is append-only; the base entries must survive as a prefix."""
        case_dir = repo / "evals" / "corpus" / CASE
        meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        meta["reviews"] = [
            {
                "submitted_by": "mstarks01",
                "submitted_for": "mstarks01",
                "date": "2026-08-20",
                "read": [{"file": "source.md", "sha256": "0" * 64}],
                "document": "REVIEW-mstarks01.md",
                "notes": "",
            }
        ]
        (case_dir / "case.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "an earlier sitting")
        git(repo, "push", "origin", "main")

        meta["reviews"] = []
        (case_dir / "case.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        prepare_sitting(repo)
        git(repo, "fetch", "origin")
        check = submit._check_sitting_is_yours(repo, "ada")
        assert not check.passed
        assert "append-only" in check.problems[0]

    def test_a_partial_read_is_refused(self, repo):
        prepare_sitting(repo, read=["source.md", "model.json"])
        git(repo, "fetch", "origin")
        check = submit._check_sitting_covers(repo, "ada")
        assert not check.passed
        assert "claims/stride.json" in check.problems[0]

    def test_a_surviving_unreviewed_line_is_refused(self, repo):
        prepare_sitting(repo, clear_unreviewed=False)
        git(repo, "fetch", "origin")
        check = submit._check_sitting_clears_unreviewed(repo, "ada")
        assert not check.passed
        assert "UNREVIEWED" in check.problems[0]

    def test_a_respelled_key_does_not_pass_for_a_cleared_line(self, repo):
        """The two checks over this file used to key an entry two ways: one on
        the literal text ``"<case>":`` and one on what :mod:`ast` reads.

        A key whose first character is written as an escape is absent from the
        text and present to the parser, so the clears check called the line gone
        while the edits-only check removed the entry's whole span from both
        sides -- and whatever the entry carried rode in unread. Both read the
        parsed table now, so there is no second opinion to play off.
        """
        prepare_sitting(repo, clear_unreviewed=False)
        listing = repo / "tests" / "test_case_review.py"
        source = listing.read_text("utf-8")
        case = submit._sitting_cases(repo)[0]
        respelled = f'"\\x{ord(case[0]):02x}{case[1:]}":'
        listing.write_text(source.replace(f'"{case}":', respelled), "utf-8")
        git(repo, "fetch", "origin")
        assert f'"{case}":' not in listing.read_text("utf-8"), (
            "the premise: a text reader finds no line to complain about"
        )
        assert case in unreviewed_cases(repo), "and the parser still reads it"

        check = submit._check_sitting_clears_unreviewed(repo, "ada")

        assert not check.passed, (
            "the entry is still there under a different spelling, and the check"
            " read the text rather than the table"
        )


class TestTheRosterDeltaIsShownToTheReviewer:
    """`_check_no_self_raise` reads one line, the author's, which is the
    contract `contribution.yml` states this job proves.

    Two audits observed that every other roster line goes past unremarked, and
    both concluded the merge is the control rather than the check. This is the
    note both of them asked for: a control that is a person reading a diff works
    better when it is shown the diff. It never fails a submission -- what a
    roster edit is entitled to do is a judgement about people, and the check
    cannot make it.
    """

    def _roster(self, repo, text):
        (repo / "evals" / "review" / "voters.toml").write_text(text, "utf-8")

    def test_a_standing_raised_for_somebody_else_is_reported(self, repo):
        prepare_vote(repo)
        self._roster(
            repo, ROSTER_WITH_ADA + '\n[voters.sam]\nstanding = "maintainer"\n'
        )
        git(repo, "fetch", "origin")

        check = submit._check_no_self_raise(repo, "ada")

        assert check.passed, "it reports, it does not refuse"
        assert any("sam" in note for note in check.notes)

    def test_an_alias_fold_is_reported(self, repo):
        """The shape a reviewer greps for `maintainer` would not find: the
        added line says `aliases`, and the standing it grants is on another."""
        prepare_vote(repo)
        self._roster(
            repo,
            ROSTER_WITH_ADA.replace(
                'standing = "maintainer"',
                'standing = "maintainer"\naliases = ["sam"]',
                1,
            ),
        )
        git(repo, "fetch", "origin")

        check = submit._check_no_self_raise(repo, "ada")

        assert check.passed
        assert any("aliases" in note for note in check.notes)

    def test_an_ordinary_submission_reports_only_its_own_line(self, repo):
        """A first-timer registers in the same PR as their first vote, so the
        note is never empty in practice -- what matters is that it holds one
        line, the author's, and that a reviewer can see when it does not."""
        prepare_vote(repo)
        git(repo, "fetch", "origin")

        notes = submit._check_no_self_raise(repo, "ada").notes

        assert notes == ("ada: added as 'contributor'",)


class TestTheDocumentIsCheckedForWhatItClaims:
    """The gate was one `.is_file()` probe on whatever name the entry gave.

    A name is not a path this repository resolves, but it is a claim, and the
    probe let the claim be any file that happens to exist -- `"source.md"`
    satisfied it in every case directory, so an entry could clear a case while
    committing no reading document at all.

    Naming it is only half. `MIN_OWN_LIST` is the floor that tells a filled copy
    from an empty one, and it was enforced in the app and in the offline
    envelope and on neither side of a pull request -- so `touch
    REVIEW-<login>.md` passed every gate under the correct name, and
    constraining the name alone would have moved the same hole one step.
    """

    def test_an_entry_naming_another_file_is_refused(self, repo):
        prepare_sitting(repo)
        case = submit._sitting_cases(repo)[0]
        path = repo / "evals" / "corpus" / case / "case.json"
        meta = json.loads(path.read_text("utf-8"))
        meta["reviews"][-1]["document"] = "source.md"
        path.write_text(json.dumps(meta, indent=2), "utf-8")
        git(repo, "fetch", "origin")

        check = submit._check_sitting_evidence(repo, "ada")

        assert not check.passed
        assert "another submission's" in check.problems[0]

    @pytest.mark.parametrize(
        "content",
        [
            "\n\n  \n",
            "nnnnnnnnnnnn",
            "some prose a contributor typed instead of holding a sitting",
        ],
    )
    def test_a_file_that_is_not_a_reading_document_is_refused(self, repo, content):
        """The first version of this gate compared the whole file against
        `MIN_OWN_LIST`, which is the floor the app applies to *the own list* --
        a different thing measured against the same number, so twelve bytes of
        anything passed and no length could ever have seen an empty own list.
        """
        prepare_sitting(repo)
        case = submit._sitting_cases(repo)[0]
        (repo / "evals" / "corpus" / case / "REVIEW-ada.md").write_text(
            content, "utf-8"
        )
        git(repo, "fetch", "origin")

        check = submit._check_sitting_evidence(repo, "ada")

        assert not check.passed
        assert "not a reading document" in check.problems[0]

    def test_an_honest_sitting_still_passes(self, repo):
        prepare_sitting(repo)
        git(repo, "fetch", "origin")

        assert submit._check_sitting_evidence(repo, "ada").passed


class TestASittingCarriesNCases:
    """ADR 0020: one reader's session is one pull request, whatever N is."""

    def test_two_cases_pass_every_check(self, repo):
        prepare_sitting(repo)
        prepare_sitting(repo, case=OTHER)
        git(repo, "fetch", "origin")
        checks = submit._sitting_preflight(repo, "ada")
        failed = [check.name for check in checks if not check.passed]
        assert not failed

    def test_no_case_directory_is_refused(self, repo):
        (repo / "evals" / "review" / "voters.toml").write_text(
            ROSTER_WITH_ADA, encoding="utf-8"
        )
        git(repo, "fetch", "origin")
        check = submit._check_subject_count(repo, "ada", kind_name="sitting")
        assert not check.passed
        assert "at least one case directory" in check.problems[0]

    def test_two_baseline_directories_are_still_refused(self, repo):
        """The count is a field, so the baseline kind keeps today's rule."""
        for name in ("first", "second"):
            directory = repo / "evals" / "baselines" / name
            directory.mkdir(parents=True)
            (directory / "baseline.json").write_text("{}\n", encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_subject_count(repo, "ada", kind_name="baseline")
        assert not check.passed
        assert "exactly one Baseline directory" in check.problems[0]

    def test_every_kind_names_a_cardinality_the_table_holds(self):
        """The table is checked against its registry, as CLAUDE.md asks."""
        unknown = {
            name
            for name, kind in submit.KINDS.items()
            if kind.subjects not in submit._SUBJECTS
        }
        assert not unknown

    def test_the_allowlist_carries_every_touched_case(self, repo):
        prepare_sitting(repo)
        prepare_sitting(repo, case=OTHER)
        git(repo, "fetch", "origin")
        allowed = submit._sitting_allowlist(repo, "ada")
        assert f"evals/corpus/{CASE}/case.json" in allowed
        assert f"evals/corpus/{OTHER}/case.json" in allowed

    def test_one_bad_case_refuses_the_whole_submission(self, repo):
        prepare_sitting(repo)
        prepare_sitting(repo, case=OTHER, write_document=False)
        git(repo, "fetch", "origin")
        checks = submit._sitting_preflight(repo, "ada")
        assert not all(check.passed for check in checks)

    def test_every_failure_is_reported_in_one_pass(self, repo):
        for case in (CASE, OTHER):
            case_dir = prepare_sitting(repo, case=case)
            (case_dir / "source.md").write_text("edited after\n", encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_sitting_evidence(repo, "ada")
        assert not check.passed
        assert {problem.split("/")[0] for problem in check.problems} == {CASE, OTHER}

    def test_a_case_that_appends_no_entry_is_refused(self, repo):
        """The gate that replaces the old cardinality check."""
        prepare_sitting(repo)
        claims = repo / "evals" / "corpus" / OTHER / "claims" / "stride.json"
        claims.write_text('[{"id": "x"}]\n', encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_sitting_is_yours(repo, "ada")
        assert not check.passed
        assert check.problems == (f"{OTHER}/case.json appends no sitting entry",)

    def test_malformed_metadata_refuses_only_its_own_case(self, repo):
        """A refusal, not a traceback: the other cases still report."""
        prepare_sitting(repo)
        broken = repo / "evals" / "corpus" / OTHER / "case.json"
        broken.write_text("[]\n", encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_sitting_is_yours(repo, "ada")
        assert not check.passed
        refusal = f"{OTHER}/case.json: no `reviews` list to append to"
        assert check.problems == (refusal,)

    def test_every_problem_starts_with_its_case_id(self, repo):
        prepare_sitting(repo, reviewer="sam", read=["source.md"])
        prepare_sitting(repo, case=OTHER, write_document=False, clear_unreviewed=False)
        git(repo, "fetch", "origin")
        problems = [
            problem
            for check in submit._sitting_preflight(repo, "ada")
            for problem in check.problems
        ]
        assert problems
        assert all(
            problem.startswith((f"{CASE}/", f"{CASE}:", f"{OTHER}/", f"{OTHER}:"))
            for problem in problems
        ), problems

    def test_the_checklist_length_does_not_move_with_n(self, repo):
        prepare_sitting(repo)
        git(repo, "fetch", "origin")
        one = [check.name for check in submit._sitting_preflight(repo, "ada")]
        prepare_sitting(repo, case=OTHER)
        git(repo, "fetch", "origin")
        two = [check.name for check in submit._sitting_preflight(repo, "ada")]
        assert one == two
        assert len(two) == 9

    def test_a_foreign_edit_to_the_unreviewed_list_is_refused(self, repo):
        """The one file outside a case directory a sitting may change is a
        module `pytest` imports, so the allowlist admitting it by path is not
        the whole check. A line the sitting did not clear is a line nobody
        asked for, whatever it says."""
        prepare_sitting(repo)
        listing = repo / "tests" / "test_case_review.py"
        listing.write_text(
            listing.read_text(encoding="utf-8") + 'import os\nos.environ["X"] = "1"\n',
            encoding="utf-8",
        )
        git(repo, "fetch", "origin")
        check = submit._check_sitting_edits_only_the_list(repo, "ada")
        assert not check.passed
        assert "test_case_review.py" in check.problems[0]

    def test_an_edit_to_another_case_s_entry_is_refused(self, repo):
        """Every case this submission does not carry is on the far side of
        the same rule."""
        prepare_sitting(repo)
        listing = repo / "tests" / "test_case_review.py"
        listing.write_text(
            listing.read_text(encoding="utf-8").replace(
                unreviewed_line(OTHER), f'    "{OTHER}": "rewritten",\n'
            ),
            encoding="utf-8",
        )
        git(repo, "fetch", "origin")
        assert not submit._check_sitting_edits_only_the_list(repo, "ada").passed

    def test_a_list_that_will_not_parse_is_a_checklist_line(self, repo):
        """A10. The check reads a file somebody edited, so a source that no
        longer parses answers with a line a contributor can act on."""
        prepare_sitting(repo)
        listing = repo / "tests" / "test_case_review.py"
        listing.write_text(
            listing.read_text(encoding="utf-8") + "def broken(\n", encoding="utf-8"
        )
        git(repo, "fetch", "origin")
        check = submit._check_sitting_edits_only_the_list(repo, "ada")
        assert not check.passed
        assert "will not parse" in check.problems[0]

    def test_a_line_the_reader_has_not_cleared_yet_is_the_other_check(self, repo):
        """One condition, one message. A forgotten clear is named by the
        check that asks for it, and never twice."""
        prepare_sitting(repo, clear_unreviewed=False)
        git(repo, "fetch", "origin")
        assert not submit._check_sitting_clears_unreviewed(repo, "ada").passed
        assert submit._check_sitting_edits_only_the_list(repo, "ada").passed

    def test_the_title_counts_the_cases(self, repo):
        prepare_sitting(repo)
        prepare_sitting(repo, case=OTHER)
        git(repo, "fetch", "origin")
        assert submit._sitting_title(repo, "ada") == "Sitting: ada, 2 cases"

    def test_the_closing_lists_the_case_ids(self, repo):
        prepare_sitting(repo)
        prepare_sitting(repo, case=OTHER)
        git(repo, "fetch", "origin")
        closing = submit._sitting_closing(repo, "ada")
        assert f"- {CASE}" in closing
        assert f"- {OTHER}" in closing
        assert closing.count("standing") == 1


class TestSelfRegistration:
    """A first-timer's roster line writes itself (#320's self-registration)."""

    @pytest.fixture(autouse=True)
    def rooted(self, repo, monkeypatch):
        monkeypatch.setattr(submit, "REPO_ROOT", repo)
        self.repo = repo

    def roster(self):
        return (self.repo / "evals" / "review" / "voters.toml").read_text("utf-8")

    def test_a_first_timer_is_added_as_a_contributor(self, fake_gh, capsys):
        """The command knows the login and knows the line is missing."""
        votes = self.repo / "evals" / "review" / "votes"
        votes.mkdir()
        (votes / "ada.jsonl").write_text(vote_line(), encoding="utf-8")

        assert main(["submit", "vote", "--dry-run"]) == 0
        assert 'standing = "contributor"' in self.roster()
        assert "[voters.ada]" in self.roster()
        assert "added your roster line" in capsys.readouterr().out

    def test_somebody_already_rostered_is_left_alone(self, fake_gh, capsys):
        prepare_vote(self.repo)
        before = self.roster()
        assert main(["submit", "vote", "--dry-run"]) == 0
        assert self.roster() == before
        assert "added your roster line" not in capsys.readouterr().out

    def test_it_never_raises_a_standing(self, fake_gh):
        """Self-registration only ever appends a contributor line."""
        votes = self.repo / "evals" / "review" / "votes"
        votes.mkdir()
        (votes / "ada.jsonl").write_text(vote_line(), encoding="utf-8")
        main(["submit", "vote", "--dry-run"])
        assert self.roster().count("maintainer") == 1, (
            "only the pre-existing maintainer line may say maintainer"
        )


class TestWhatCIRuns:
    """``verify-contribution``: the same checks, against the PR's author."""

    @pytest.fixture(autouse=True)
    def rooted(self, repo, monkeypatch):
        monkeypatch.setattr(submit, "REPO_ROOT", repo)
        self.repo = repo

    def verify(self, author):
        git(self.repo, "fetch", "origin")
        return main(["verify-contribution", "--author", author])

    def test_a_clean_vote_pr_passes_as_its_author(self, capsys):
        prepare_vote(self.repo)
        assert self.verify("ada") == 0
        assert "checking this vote PR as ada" in capsys.readouterr().out

    def test_the_same_pr_fails_under_another_login(self, capsys):
        """The binding: a submission enters only through its own author's PR."""
        prepare_vote(self.repo)
        assert self.verify("sam") == 1
        assert "FAIL" in capsys.readouterr().out

    def test_a_clean_sitting_pr_passes_as_its_reviewer(self, capsys):
        prepare_sitting(self.repo)
        assert self.verify("ada") == 0
        assert "checking this sitting PR as ada" in capsys.readouterr().out

    def test_a_sitting_naming_someone_else_fails(self):
        prepare_sitting(self.repo, reviewer="sam")
        assert self.verify("ada") == 1

    def test_a_two_case_sitting_pr_passes_as_its_reviewer(self, capsys):
        """CI needs no edit: the same preflight serves both surfaces."""
        prepare_sitting(self.repo)
        prepare_sitting(self.repo, case=OTHER)
        assert self.verify("ada") == 0
        assert "checking this sitting PR as ada" in capsys.readouterr().out

    def test_one_bad_case_fails_the_whole_two_case_pr(self, capsys):
        prepare_sitting(self.repo)
        prepare_sitting(self.repo, case=OTHER, reviewer="sam")
        assert self.verify("ada") == 1
        assert OTHER in capsys.readouterr().out

    def test_two_kinds_in_one_pr_are_refused(self, capsys):
        prepare_vote(self.repo)
        prepare_sitting(self.repo)
        assert self.verify("ada") == 1
        assert "one kind per PR" in capsys.readouterr().out

    def test_an_ordinary_code_pr_has_nothing_to_check(self, capsys):
        (self.repo / "README.md").write_text("a docs fix\n", encoding="utf-8")
        assert self.verify("ada") == 0
        assert "nothing to check" in capsys.readouterr().out

    def test_a_roster_line_alone_still_may_not_raise_its_own_standing(self, capsys):
        """The one edit that is dangerous with no submission behind it."""
        (self.repo / "evals" / "review" / "voters.toml").write_text(
            ROSTER_BASE + '\n[voters.ada]\nstanding = "maintainer"\n', encoding="utf-8"
        )
        assert self.verify("ada") == 1
        out = capsys.readouterr().out
        assert "roster PR as ada" in out
        assert "FAIL" in out

    def test_a_first_contributor_line_alone_is_fine(self):
        (self.repo / "evals" / "review" / "voters.toml").write_text(
            ROSTER_WITH_ADA, encoding="utf-8"
        )
        assert self.verify("ada") == 0

    def test_an_empty_author_is_refused_rather_than_passed(self, capsys):
        prepare_vote(self.repo)
        assert self.verify("   ") == 1
        assert "the binding cannot be checked" in capsys.readouterr().out


class TestTheCommand:
    @pytest.fixture(autouse=True)
    def rooted(self, repo, monkeypatch):
        monkeypatch.setattr(submit, "REPO_ROOT", repo)
        self.repo = repo

    def test_dry_run_prints_the_checklist_and_stages_nothing(self, fake_gh, capsys):
        prepare_vote(self.repo)
        assert main(["submit", "vote", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "submitting as ada" in out
        assert "FAIL" not in out
        assert "dry run" in out
        assert not git(self.repo, "ls-remote", "origin", "refs/heads/submit/*")

    def test_a_failing_checklist_opens_nothing(self, fake_gh, capsys):
        prepare_vote(self.repo)
        (self.repo / "stray.txt").write_text("x\n", encoding="utf-8")
        assert main(["submit", "vote"]) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "nothing opened" in out
        assert "pr create" not in fake_gh.read_text(encoding="utf-8")

    def test_the_full_run_pushes_a_branch_and_opens_the_pr(self, fake_gh, capsys):
        prepare_vote(self.repo)
        assert main(["submit", "vote"]) == 0
        out = capsys.readouterr().out
        assert "https://example.test/pr/1" in out
        assert "maintainer reviews every line" in out

        date = datetime.now(UTC).date().isoformat()
        branch = f"submit/vote/ada-{date}"
        assert branch in git(self.repo, "ls-remote", "origin", "refs/heads/submit/*")
        staged = git(self.repo, "show", f"origin/{branch}:evals/review/votes/ada.jsonl")
        assert json.loads(staged)["voter"] == "ada"
        log = fake_gh.read_text(encoding="utf-8")
        assert "pr create" in log
        assert f"--head {branch}" in log

    def test_a_second_pr_on_one_day_gets_a_suffix(self, fake_gh):
        prepare_vote(self.repo)
        assert main(["submit", "vote"]) == 0
        assert main(["submit", "vote"]) == 0
        date = datetime.now(UTC).date().isoformat()
        remote = git(self.repo, "ls-remote", "origin", "refs/heads/submit/*")
        assert f"submit/vote/ada-{date}\n" in remote.replace("\t", "\n")
        assert f"submit/vote/ada-{date}-2" in remote

    def test_the_working_tree_is_never_switched(self, fake_gh):
        prepare_vote(self.repo)
        head_before = git(self.repo, "rev-parse", "HEAD")
        assert main(["submit", "vote"]) == 0
        assert git(self.repo, "rev-parse", "HEAD") == head_before
        assert (self.repo / "evals" / "review" / "votes" / "ada.jsonl").exists()

    def test_a_baseline_runs_end_to_end(self, fake_gh, capsys, monkeypatch):
        from evals.harness import prices
        from evals.harness.prices import UnitPrices
        from tests.test_evals_baseline import payload, write_sweep

        monkeypatch.setattr(
            prices,
            "unit_prices",
            lambda model: UnitPrices(model, 1e-6, 4e-6, 1e-7),
        )
        runs = self.repo / "evals" / "runs"
        runs.mkdir(parents=True)
        source = write_sweep(runs, payload(), "art")
        (self.repo / "evals" / "review" / "voters.toml").write_text(
            ROSTER_WITH_ADA, encoding="utf-8"
        )

        assert main(["submit", "baseline", "--artifact", str(source)]) == 0
        out = capsys.readouterr().out
        assert "https://example.test/pr/1" in out
        assert "standings behind its Baseline" in out

        assembled = [
            path
            for path in (self.repo / "evals" / "baselines").iterdir()
            if path.is_dir()
        ]
        assert len(assembled) == 1
        name = assembled[0].name
        date = datetime.now(UTC).date().isoformat()
        branch = f"submit/baseline/ada-{date}"
        manifest = json.loads(
            git(
                self.repo,
                "show",
                f"origin/{branch}:evals/baselines/{name}/baseline.json",
            )
        )
        assert manifest["name"] == name
        assert manifest["sweeps"][0]["submitted_by"] == "ada"
        assert f"Baseline: {name}" in fake_gh.read_text(encoding="utf-8")

    def test_a_sitting_over_two_cases_runs_end_to_end(self, fake_gh, capsys):
        prepare_sitting(self.repo)
        prepare_sitting(self.repo, case=OTHER)
        assert main(["submit", "sitting"]) == 0
        out = capsys.readouterr().out
        assert "https://example.test/pr/1" in out
        assert "clears its case from UNREVIEWED" in out
        assert f"- {CASE}" in out

        date = datetime.now(UTC).date().isoformat()
        branch = f"submit/sitting/ada-{date}"
        assert branch in git(self.repo, "ls-remote", "origin", "refs/heads/submit/*")
        for case in (CASE, OTHER):
            staged = json.loads(
                git(self.repo, "show", f"origin/{branch}:evals/corpus/{case}/case.json")
            )
            assert staged["reviews"][0]["submitted_by"] == "ada"
        listing = git(self.repo, "show", f"origin/{branch}:tests/test_case_review.py")
        assert CASE not in listing
        assert OTHER not in listing
        log = fake_gh.read_text(encoding="utf-8")
        assert "Sitting: ada, 2 cases" in log


class TestTheDeltaCache:
    """The delta is read once per checklist pass, and never held past one."""

    def test_a_pass_reads_the_tree_once(self, repo, monkeypatch):
        reads = []
        real = submit._run

        def counted(args, cwd):
            if args[:3] == ["git", "diff", "--name-only"]:
                reads.append(args)
            return real(args, cwd)

        monkeypatch.setattr(submit, "_run", counted)
        prepare_vote(repo)
        git(repo, "fetch", "origin")
        with submit._delta_cache(repo):
            submit._vote_preflight(repo, "ada")
        assert len(reads) == 1

    def test_the_cache_does_not_outlive_its_pass(self, repo):
        """A write between passes is seen, which is why the scope is narrow."""
        git(repo, "fetch", "origin")
        with submit._delta_cache(repo):
            before = submit._changed_paths(repo)
        (repo / "stray.txt").write_text("written between passes\n", encoding="utf-8")
        with submit._delta_cache(repo):
            after = submit._changed_paths(repo)

        assert "stray.txt" not in before
        assert "stray.txt" in after
        assert submit._DELTA == {}


class TestASittingChangesAnAnswerNeverAQuestion:
    """#388: the sources and the model are the question the reader answers."""

    def test_a_claim_correction_passes_every_check(self, repo):
        """#327's rule survives: the edit and the record travel together."""
        claims = repo / "evals" / "corpus" / CASE / "claims" / "stride.json"
        claims.write_text('[{"claim": "corrected"}]\n', encoding="utf-8")
        prepare_sitting(repo)
        git(repo, "fetch", "origin")
        checks = submit._sitting_preflight(repo, "ada")
        assert not [check.name for check in checks if not check.passed]

    def test_editing_the_blessed_model_fails_scope(self, repo):
        prepare_sitting(repo)
        (repo / "evals" / "corpus" / CASE / "model.json").write_text(
            '{"edited": true}\n', encoding="utf-8"
        )
        git(repo, "fetch", "origin")
        check = submit._check_scope(repo, "ada", kind_name="sitting")
        assert not check.passed
        assert any(f"evals/corpus/{CASE}/model.json" in p for p in check.problems)

    def test_editing_a_declared_source_fails_scope(self, repo):
        prepare_sitting(repo)
        (repo / "evals" / "corpus" / CASE / "source.md").write_text(
            "a different system\n", encoding="utf-8"
        )
        git(repo, "fetch", "origin")
        check = submit._check_scope(repo, "ada", kind_name="sitting")
        assert not check.passed
        assert any(f"evals/corpus/{CASE}/source.md" in p for p in check.problems)

    def test_another_readers_document_fails_scope(self, repo):
        prepare_sitting(repo)
        (repo / "evals" / "corpus" / CASE / "REVIEW-sam.md").write_text(
            "someone else's copy\n", encoding="utf-8"
        )
        git(repo, "fetch", "origin")
        check = submit._check_scope(repo, "ada", kind_name="sitting")
        assert not check.passed
        assert any(f"evals/corpus/{CASE}/REVIEW-sam.md" in p for p in check.problems)

    def test_the_claim_files_come_from_the_declaration(self, repo):
        """A package nobody wrote is covered the moment a case declares it."""
        case_dir = repo / "evals" / "corpus" / CASE
        meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        meta["frameworks"].append({"name": "newthing"})
        (case_dir / "case.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        (case_dir / "claims" / "newthing.json").write_text(
            '[{"claim": "the new package says so"}]\n', encoding="utf-8"
        )
        prepare_sitting(
            repo,
            read=[
                "source.md",
                "model.json",
                "claims/stride.json",
                "claims/newthing.json",
            ],
        )
        git(repo, "fetch", "origin")
        assert f"evals/corpus/{CASE}/claims/newthing.json" in submit._sitting_allowlist(
            repo, "ada"
        )
        checks = submit._sitting_preflight(repo, "ada")
        assert not [check.name for check in checks if not check.passed]

    def test_the_allowlist_keeps_the_shared_files(self, repo):
        prepare_sitting(repo)
        git(repo, "fetch", "origin")
        allowed = submit._sitting_allowlist(repo, "ada")
        assert submit.CASE_REVIEW_TEST in allowed
        assert submit.ROSTER_FILE in allowed

    def test_a_document_named_in_the_entry_admits_nothing(self, repo):
        """The name is the author's own; an entry cannot widen the list."""
        prepare_sitting(repo, document="model.json", write_document=False)
        git(repo, "fetch", "origin")
        assert f"evals/corpus/{CASE}/model.json" not in submit._sitting_allowlist(
            repo, "ada"
        )

    def test_a_declared_name_never_reaches_outside_the_case(self, repo):
        """The name reaches a path, so a separator in one names nothing."""
        case_dir = repo / "evals" / "corpus" / CASE
        meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        meta["frameworks"].append({"name": "../../../src/analysis_service/report"})
        (case_dir / "case.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        prepare_sitting(repo)
        git(repo, "fetch", "origin")
        allowed = submit._sitting_allowlist(repo, "ada")
        assert all(rel.startswith(("evals/", "tests/")) for rel in allowed)
        assert not [rel for rel in allowed if ".." in rel]


class TestTheChecklistSurvivesAShapeNobodyListed:
    """Both crashes reported by run-6, both fail-closed and both a traceback."""

    def test_a_vote_naming_an_unknown_framework_is_reported_not_raised(self, repo):
        """`version_for` raises `FingerprintError`, which is neither of the two
        classes the handler names. The row loads, because `fingerprint()`
        consults no table, and then aborted the whole preflight."""
        votes = prepare_vote(repo)
        # Self-consistent, so `__post_init__` accepts it and `_stale_keys` is
        # what meets the unknown name. A mismatched fingerprint would be caught
        # one step earlier and would not exercise this path at all.
        stray = cast(
            Components("not-a-package", "information-disclosure", ("process:a",)),
            "01-payments-checkout",
            "up",
            "ada",
            version=1,
        )
        (votes / "ada.jsonl").write_text(
            json.dumps(stray.to_json(), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        git(repo, "fetch", "origin")

        check = submit._check_your_file_appends(repo, "ada")

        assert not check.passed
        assert "no known rule" in check.problems[0]

    def test_a_roster_whose_voters_table_is_a_scalar_is_noted(self, repo, tmp_path):
        """The same defect one level up from the entry guard: `voters = "abc"`
        is legal TOML, `set(was)` would iterate its characters, and `was.get`
        does not exist."""
        live = tmp_path / "voters.toml"
        live.write_text('version = 1\nvoters = "abc"\n', encoding="utf-8")

        notes = submit._roster_delta(ROSTER_WITH_ADA, live)

        assert notes == ["voters: the roster's own table is not a table"]
