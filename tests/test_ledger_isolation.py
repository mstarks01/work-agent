"""The ledger isolation check, over the file lists of past pull requests.

The passing cases are the shapes of record pull requests #1703 (a test
declaration beside the rows) and #1489 (a report-quality guide beside the
rows). The failing case is the shape of fix pull request #1713, which carried
the row of QA-2026-10-14-03-E2 beside the fix.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from evals.harness.audit import EXPERIMENTS_REL
from evals.harness.ledger_isolation import LEDGER_DIRS, code_beside_ledger
from evals.harness.submit import run_command
from tests.source_tree import REPO_ROOT

SCRIPT = REPO_ROOT / "evals" / "harness" / "ledger_isolation.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ledger.yml"

RECORD_WITH_A_TEST = (
    "evals/experiments/QA-2026-10-14-01.jsonl",
    "evals/experiments/QA-2026-10-14-01.md",
    "evals/experiments/QA-2026-10-14-01/count_on_reference_models.py",
    "tests/test_framework_neutrality.py",
)
RECORD_WITH_A_GUIDE = (
    "evals/experiments/grants/QA-2026-10-14-03.jsonl",
    "evals/experiments/QA-2026-10-14-03.jsonl",
    "docs/agents/report-quality.md",
)
FIX_WITH_A_ROW = (
    "evals/experiments/QA-2026-10-14-03.jsonl",
    "evals/experiments/QA-2026-10-14-03.md",
    "src/analysis_service/capabilities.py",
    "tests/test_capabilities.py",
)


def run(paths: tuple[str, ...]) -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="".join(f"{path}\0" for path in paths),
        capture_output=True,
        text=True,
        check=False,
    ).returncode


@pytest.mark.parametrize("paths", [RECORD_WITH_A_TEST, RECORD_WITH_A_GUIDE])
def test_a_record_pull_request_passes(paths: tuple[str, ...]) -> None:
    assert code_beside_ledger(paths) == ()
    assert run(paths) == 0


def test_a_fix_that_carries_a_ledger_row_fails() -> None:
    assert code_beside_ledger(FIX_WITH_A_ROW) == (
        "src/analysis_service/capabilities.py",
    )
    assert run(FIX_WITH_A_ROW) == 1


@pytest.mark.parametrize(
    "path",
    [
        "prompts/capabilities.md",
        "frameworks/asvs/critic.md",
        "config/model_tiers.toml",
        "evals/harness/audit.py",
        "evals/verify_corpus.py",
        "webapp/static/first_run.js",
        ".github/workflows/ci.yml",
        "uv.lock",
    ],
)
def test_each_code_path_beside_a_grant_fails(path: str) -> None:
    paths = ("evals/experiments/grants/QA-2026-10-14-03.jsonl", path)
    assert code_beside_ledger(paths) == (path,)


def test_a_path_that_git_quotes_in_a_list_of_lines_is_still_read(
    tmp_path: Path,
) -> None:
    """``git diff`` quotes a non-ASCII path in a list of lines, never under ``-z``."""
    for path in ("evals/experiments/QA-2026-10-14-03.jsonl", "src/café.py"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("x\n", encoding="utf-8")
    run_command(["git", "init", "-b", "main", str(tmp_path)], tmp_path)
    run_command(["git", "add", "."], tmp_path)
    names = subprocess.run(
        ["git", "diff", "-z", "--cached", "--name-only", "--no-renames"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout.decode()
    assert run(tuple(name for name in names.split("\0") if name)) == 1


def test_code_without_a_ledger_change_passes() -> None:
    assert code_beside_ledger(FIX_WITH_A_ROW[2:]) == ()


def test_the_ledger_folders_are_the_ledger_readers_folders() -> None:
    assert LEDGER_DIRS == (
        EXPERIMENTS_REL.as_posix(),
        (EXPERIMENTS_REL / "grants").as_posix(),
    )


def test_the_workflow_runs_the_base_copy_of_the_script() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in text
    assert "git show" in text
