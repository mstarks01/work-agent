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


class TestTheDeltaCache:
    """The delta is read once per checklist pass, and never held past one."""

    def test_a_pass_reads_the_tree_once(self, repo, monkeypatch):
        reads = []
        real = submit.run_command

        def counted(args, cwd):
            if args[:3] == ["git", "diff", "--name-only"]:
                reads.append(args)
            return real(args, cwd)

        monkeypatch.setattr(submit, "run_command", counted)
        prepare_vote(repo)
        git(repo, "fetch", "origin")
        with submit._delta_cache(repo):
            submit._vote_preflight(repo, "ada")
        assert len(reads) == 1

    def test_the_cache_does_not_outlive_its_pass(self, repo):
        """A write between passes is seen, which is why the scope is narrow."""
        git(repo, "fetch", "origin")
        with submit._delta_cache(repo):
            before = submit.changed_paths(repo)
        (repo / "stray.txt").write_text("written between passes\n", encoding="utf-8")
        with submit._delta_cache(repo):
            after = submit.changed_paths(repo)

        assert "stray.txt" not in before
        assert "stray.txt" in after
        assert submit._DELTA == {}


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

    def test_a_vote_file_that_is_not_utf8_is_reported_not_raised(self, repo):
        """A contributor's own bytes, committed by the same pull request."""
        votes = prepare_vote(repo)
        (votes / "ada.jsonl").write_bytes(b'{"voter": "ada", "note": "\xff\xfe"}\n')
        git(repo, "fetch", "origin")

        check = submit._check_your_file_appends(repo, "ada")

        assert not check.passed
        assert "not UTF-8" in check.problems[0]

    def test_a_roster_that_is_not_utf8_does_not_raise(self, repo, tmp_path):
        live = tmp_path / "voters.toml"
        live.write_bytes(b'version = 1\n[voters.ada]\nstanding = "\xff\xfe"\n')

        assert submit._roster_delta(ROSTER_WITH_ADA, live) == []
