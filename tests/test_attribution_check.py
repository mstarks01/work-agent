"""The attribution check, driven over real commits and texts.

Each refused case is one that run 11 of the security audit measured passing
the check (#1294): an AI as the author with an address outside the listed
domains, a folded trailer, a Cyrillic look-alike letter, and "Generated using".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from evals.harness.submit import run_command

SCRIPT = Path(__file__).resolve().parent.parent / ".githooks" / "check-attribution"
WORKFLOW = SCRIPT.parent.parent / ".github" / "workflows" / "attribution.yml"
HUMAN = ("Ada Lovelace", "ada@example.test")


def check(cwd: Path, *args: str, text: str | None = None) -> int:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd,
        input=text,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def commit(repo: Path, message: str, author: tuple[str, str] = HUMAN) -> None:
    name, email = author
    (repo / "f").write_text(message, encoding="utf-8")
    run_command(["git", "add", "f"], repo)
    run_command(
        [
            "git",
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "-m",
            message,
            f"--author={name} <{email}>",
        ],
        repo,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    run_command(["git", "init", "-b", "main", str(tmp_path)], tmp_path)
    commit(tmp_path, "Seed")
    return tmp_path


REFUSED_MESSAGES = {
    "folded-trailer": "Add a file\n\nCo-Authored-By:\n Claude <noreply@example.test>",
    "look-alike": "Add a file\n\nСlaude-Session: x",
    "generated-using": "Add a file\n\nGenerated using Claude.",
}


@pytest.mark.parametrize("message", REFUSED_MESSAGES.values(), ids=REFUSED_MESSAGES)
def test_a_message_that_names_an_ai_is_refused(repo, message):
    commit(repo, message)
    assert check(repo, "HEAD~1..HEAD") == 1


@pytest.mark.parametrize("message", REFUSED_MESSAGES.values(), ids=REFUSED_MESSAGES)
def test_a_body_that_names_an_ai_is_refused(repo, message):
    assert check(repo, "--text", text=message.partition("\n\n")[2]) == 1


def test_an_ai_author_with_an_unlisted_address_is_refused(repo):
    commit(repo, "Add a file", author=("Claude", "bot@example.test"))
    assert check(repo, "HEAD~1..HEAD") == 1


@pytest.mark.parametrize(
    "message",
    ["Add a file", "Move the cursor to the end\n\nDevin reviewed it."],
)
def test_a_human_commit_passes(repo, message):
    """A control: the ordinary words among the names do not refuse a commit."""
    commit(repo, message, author=("Devin Smith", "devin@example.test"))
    assert check(repo, "HEAD~1..HEAD") == 0


def test_a_pull_request_runs_the_base_branch_s_copy():
    """A pull request that edited the script passed its own check."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'git show "$BASE_SHA:.githooks/check-attribution"' in workflow
    assert 'git show "$BEFORE:.githooks/check-attribution"' in workflow
