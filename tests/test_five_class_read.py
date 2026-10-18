"""The five-class read check, driven over real commits and pull request bodies."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from evals.harness.submit import run_command
from tests.source_tree import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "five_class_read.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "five-class.yml"
REVIEW_GUIDE = REPO_ROOT / "docs" / "agents" / "code-review.md"

_spec = importlib.util.spec_from_file_location("five_class_read", SCRIPT)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

FULL_SECTION = "\n".join(
    [
        check.HEADING,
        *(f"{n}. **{name}**: none." for n, name in enumerate(check.CLASSES, 1)),
    ]
)


def _guide_classes() -> list[str]:
    """Each class the class table of ``docs/agents/code-review.md`` names, in order."""
    text = REVIEW_GUIDE.read_text(encoding="utf-8")
    table = text.partition("## The five defect classes")[2]
    return re.findall(r"^\| \d+ \| \*\*(.+?)\*\* \|", table, re.MULTILINE)


def test_the_class_names_are_the_guides():
    assert list(check.CLASSES) == _guide_classes()


@pytest.mark.parametrize(
    "path",
    [
        "src/analysis_service/graph.py",
        "webapp/main.py",
        "evals/harness/run.py",
        "prompts/extract.md",
        "frameworks/stride/output.md",
        "tests/test_graph.py",
        ".githooks/check-attribution",
        ".github/workflows/ci.yml",
        ".github/scripts/five_class_read.py",
    ],
)
def test_a_code_path_needs_the_read(path):
    assert check.changes_code(["docs/Web-App.md", path])


@pytest.mark.parametrize(
    "path",
    ["docs/agents/code-review.md", "AGENTS.md", "GLOSSARY.md", "evals/corpus/x.json"],
)
def test_a_path_that_holds_no_code_needs_no_read(path):
    assert not check.changes_code([path])


def test_a_body_with_every_class_passes():
    assert check.missing(f"Intro.\n\n{FULL_SECTION}\n\n## Checks\n\nAll pass.") == []


def test_a_body_with_no_heading_names_the_heading():
    body = FULL_SECTION.replace(check.HEADING, "## Review")
    assert check.missing(body) == [check.HEADING]


def test_a_body_that_leaves_out_a_class_names_it():
    body = FULL_SECTION.replace("A shape not listed", "A shape")
    assert check.missing(body) == ["A shape not listed"]


LAST = f"5. **{check.CLASSES[-1]}**: none."


@pytest.mark.parametrize("heading", ["## Docs", "# Docs"])
def test_a_class_under_the_next_heading_does_not_count(heading):
    body = FULL_SECTION.replace(LAST, f"{heading}\n\n{LAST}")
    assert check.missing(body) == [check.CLASSES[-1]]


def test_a_subheading_inside_the_section_keeps_the_section_open():
    body = FULL_SECTION.replace(LAST, f"### Last\n\n{LAST}")
    assert check.missing(body) == []


def test_a_name_wrapped_over_two_lines_counts():
    body = FULL_SECTION.replace("A bound predicted, not", "A bound predicted,\r\nnot")
    assert check.missing(body) == []


def _commit(repo: Path, path: str) -> str:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(path, encoding="utf-8")
    run_command(["git", "add", path], repo)
    run_command(
        [
            "git",
            "-c",
            "user.name=Ada Lovelace",
            "-c",
            "user.email=ada@example.test",
            "commit",
            "-m",
            f"Add {path}",
        ],
        repo,
    )
    return run_command(["git", "rev-parse", "HEAD"], repo).strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    run_command(["git", "init", "-b", "main", str(tmp_path)], tmp_path)
    _commit(tmp_path, "README.md")
    return tmp_path


def _run(repo: Path, base: str, head: str, body: str) -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPT), base, head],
        cwd=repo,
        env={**os.environ, "PR_BODY": body},
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def test_a_code_change_with_no_section_is_refused(repo):
    base = run_command(["git", "rev-parse", "HEAD"], repo).strip()
    head = _commit(repo, "src/module.py")
    assert _run(repo, base, head, "A body with no section.") == 1
    assert _run(repo, base, head, FULL_SECTION) == 0


def test_a_docs_change_needs_no_section(repo):
    base = run_command(["git", "rev-parse", "HEAD"], repo).strip()
    head = _commit(repo, "docs/guide.md")
    assert _run(repo, base, head, "") == 0


def test_the_check_reruns_when_the_body_is_edited():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "types: [opened, edited, reopened, synchronize]" in text
    assert ".github/scripts/five_class_read.py" in text
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in text
