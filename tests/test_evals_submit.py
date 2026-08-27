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
DEBT_LINE = f'    "{CASE}": "unread",\n'


def digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo(tmp_path):
    """A clone with an origin whose main holds the roster, one unread case
    under evals/corpus/, the debt-list stub, and no votes."""
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
    case_dir = clone / "evals" / "corpus" / CASE
    (case_dir / "claims").mkdir(parents=True)
    (case_dir / "source.md").write_text("a system\n", encoding="utf-8")
    (case_dir / "model.json").write_text("{}\n", encoding="utf-8")
    (case_dir / "claims" / "stride.json").write_text("[]\n", encoding="utf-8")
    (case_dir / "case.json").write_text(
        json.dumps(
            {"id": CASE, "frameworks": [{"name": "stride"}], "reviews": []}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    (clone / "tests").mkdir()
    (clone / "tests" / "test_case_review.py").write_text(
        "UNREVIEWED = {\n" + DEBT_LINE + "}\n", encoding="utf-8"
    )
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "seed")
    git(clone, "push", "-u", "origin", "main")
    return clone


def prepare_sitting(
    clone: Path,
    reviewer: str = "ada",
    read: list[str] | None = None,
    document: str = "REVIEW-ada.md",
    write_document: bool = True,
    clear_debt: bool = True,
) -> Path:
    """Ada's working state after a sitting: entry, evidence, debt line gone."""
    case_dir = clone / "evals" / "corpus" / CASE
    files = (
        read if read is not None else ["source.md", "model.json", "claims/stride.json"]
    )
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    meta["reviews"].append(
        {
            "reviewer": reviewer,
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
        (case_dir / document).write_text("the filled copy\n", encoding="utf-8")
    if clear_debt:
        (clone / "tests" / "test_case_review.py").write_text(
            "UNREVIEWED = {\n}\n", encoding="utf-8"
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
        check = submit._check_only_the_allowlist_changed(repo, "ada")
        assert not check.passed
        assert "README.md" in check.problems[0]

    def test_another_voters_file_is_refused(self, repo):
        votes = prepare_vote(repo)
        (votes / "sam.jsonl").write_text(vote_line(voter="sam"), encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_the_delta_is_yours(repo, "ada")
        assert not check.passed
        assert "sam.jsonl" in check.problems[0]

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
                "reviewer": "mstarks01",
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

    def test_a_surviving_debt_line_is_refused(self, repo):
        prepare_sitting(repo, clear_debt=False)
        git(repo, "fetch", "origin")
        check = submit._check_sitting_clears_debt(repo, "ada")
        assert not check.passed
        assert "UNREVIEWED" in check.problems[0]

    def test_two_case_directories_are_refused(self, repo):
        prepare_sitting(repo)
        other = repo / "evals" / "corpus" / "98-other-case"
        other.mkdir(parents=True)
        (other / "source.md").write_text("another\n", encoding="utf-8")
        git(repo, "fetch", "origin")
        check = submit._check_one_case(repo, "ada")
        assert not check.passed


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

        assembled = list((self.repo / "evals" / "baselines").iterdir())
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

    def test_a_sitting_runs_end_to_end(self, fake_gh, capsys):
        prepare_sitting(self.repo)
        assert main(["submit", "sitting"]) == 0
        out = capsys.readouterr().out
        assert "https://example.test/pr/1" in out
        assert "clears the case from UNREVIEWED" in out

        date = datetime.now(UTC).date().isoformat()
        branch = f"submit/sitting/ada-{date}"
        assert branch in git(self.repo, "ls-remote", "origin", "refs/heads/submit/*")
        staged = json.loads(
            git(self.repo, "show", f"origin/{branch}:evals/corpus/{CASE}/case.json")
        )
        assert staged["reviews"][0]["reviewer"] == "ada"
        debt = git(self.repo, "show", f"origin/{branch}:tests/test_case_review.py")
        assert CASE not in debt
        log = fake_gh.read_text(encoding="utf-8")
        assert f"Sitting: {CASE} by ada" in log
