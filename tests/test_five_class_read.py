"""The five-class read check, driven over real commits and pull request bodies."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from evals.harness.submit import run_command
from tests.source_tree import REPO_ROOT
from tests.test_workflow_lints import FILTERED_WORKFLOWS, _filter_paths

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


def _guide_code_paths() -> list[str]:
    """Each code path that the pre-merge section of ``docs/agents/code-review.md``
    lists, in order."""
    text = REVIEW_GUIDE.read_text(encoding="utf-8")
    paragraph = text.partition("changes a code path:")[2].partition("The check")[0]
    return re.findall(r"`([^`]+)`", paragraph)


def test_the_code_paths_are_the_guides():
    assert list(check.CODE_PATHS) == _guide_code_paths()


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
        "config/model_tiers.toml",
        "domains/http-api.md",
        "evals/verify_corpus.py",
        "evals/critic_review/score.py",
        "pyproject.toml",
    ],
)
def test_a_code_path_needs_the_read(path):
    assert check.changes_code(["docs/Web-App.md", path])


@pytest.mark.parametrize("workflow", FILTERED_WORKFLOWS, ids=lambda path: path.name)
def test_every_path_that_changes_the_analysis_is_a_code_path(workflow):
    swept = [
        entry.replace("**", "module.py")
        for entry in _filter_paths(workflow)
        if not entry.startswith("!")
    ]
    assert swept
    assert [path for path in swept if not check.changes_code([path])] == []


@pytest.mark.parametrize(
    "path",
    [
        "docs/agents/code-review.md",
        "AGENTS.md",
        "GLOSSARY.md",
        "evals/corpus/x.json",
        "evals/README.md",
        "docs/research/probe.py",
    ],
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


@pytest.mark.parametrize(
    "hide",
    [
        lambda text: f"<!--\n{text}\n-->",
        lambda text: f"```\n{text}\n```",
        lambda text: f"~~~markdown\n{text}\n~~~",
    ],
    ids=["comment", "backtick-fence", "tilde-fence"],
)
def test_a_section_that_does_not_render_does_not_count(hide):
    assert check.missing(f"Intro.\n\n{hide(FULL_SECTION)}") == [check.HEADING]


def test_a_class_inside_a_comment_does_not_count():
    body = FULL_SECTION.replace(LAST, f"<!-- {LAST} -->")
    assert check.missing(body) == [check.CLASSES[-1]]


def test_an_empty_entry_is_named():
    body = FULL_SECTION.replace(
        f"**{check.CLASSES[1]}**: none.", f"**{check.CLASSES[1]}**:"
    )
    assert check.missing(body) == [f"text after {check.CLASSES[1]}"]


def test_the_names_on_one_line_are_not_a_read():
    body = f"{check.HEADING}\n\n" + ", ".join(check.CLASSES) + ": none."
    assert check.missing(body) == [f"text after {name}" for name in check.CLASSES[:-1]]


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


def test_a_code_file_moved_out_of_a_code_path_needs_the_read(repo):
    _commit(repo, "src/module.py")
    base = run_command(["git", "rev-parse", "HEAD"], repo).strip()
    (repo / "scripts").mkdir()
    run_command(["git", "mv", "src/module.py", "scripts/module.py"], repo)
    head = _commit(repo, "docs/guide.md")
    assert _run(repo, base, head, "") == 1


def test_a_docs_change_needs_no_section(repo):
    base = run_command(["git", "rev-parse", "HEAD"], repo).strip()
    head = _commit(repo, "docs/guide.md")
    assert _run(repo, base, head, "") == 0


def test_the_check_reruns_when_the_body_is_edited():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "types: [opened, edited, reopened, synchronize]" in text
    assert ".github/scripts/five_class_read.py" in text
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in text


#: Every prompt a model reads, as ``glob`` patterns from the repository root:
#: the shared bodies, each package's own parts, and each lane's files. A
#: package's ``disclaimer.md`` goes to the report, and its ``cases`` and
#: ``notes`` are retrieved material, so none of them states an outcome.
PROMPT_FILES = (
    "prompts/*.md",
    "frameworks/*/critic.md",
    "frameworks/*/output.md",
    "frameworks/*/severity_rubric.md",
    "frameworks/*/lane_closing.md",
    "frameworks/*/lanes/*/*.md",
)


def _tracked() -> list[str]:
    return run_command(["git", "ls-files"], REPO_ROOT).split()


@pytest.mark.parametrize("pattern", sorted(check.PROMPT_READERS))
def test_each_reader_is_a_module(pattern):
    assert any(fnmatchcase(path, pattern) for path in _tracked())


@pytest.mark.parametrize(
    "prompt", sorted({p for ps in check.PROMPT_READERS.values() for p in ps})
)
def test_each_prompt_a_reader_names_is_a_file(prompt):
    assert list(REPO_ROOT.glob(prompt))


@pytest.mark.parametrize(
    "prompt",
    sorted(
        str(path.relative_to(REPO_ROOT))
        for pattern in PROMPT_FILES
        for path in REPO_ROOT.glob(pattern)
    ),
)
def test_each_prompt_has_a_module_that_decides_its_outcomes(prompt):
    named = {p for ps in check.PROMPT_READERS.values() for p in ps}
    assert any(fnmatchcase(prompt, pattern) for pattern in named), (
        f"{prompt} is in no row of PROMPT_READERS, so a change to the code it"
        " describes asks for no read of it."
    )


def test_a_module_change_names_its_prompts():
    assert check.prompts_to_read(
        ["docs/guide.md", "src/analysis_service/compact.py"]
    ) == ["prompts/extract-compact.md"]
    assert check.prompts_to_read(["src/analysis_service/report.py"]) == []


def test_a_prompt_read_names_each_prompt_with_text_after_it():
    prompts = ["frameworks/*/output.md", "prompts/analyze.md"]
    body = (
        f"{check.PROMPT_HEADING}\n\n- `frameworks/*/output.md`: true.\n"
        "- `prompts/analyze.md`: true."
    )
    assert check.missing(body, check.PROMPT_HEADING, prompts) == []
    assert check.missing("", check.PROMPT_HEADING, prompts) == [check.PROMPT_HEADING]
    assert check.missing(
        f"{check.PROMPT_HEADING}\n\n- `prompts/analyze.md`: true.",
        check.PROMPT_HEADING,
        prompts,
    ) == ["frameworks/*/output.md"]


def test_a_module_change_with_no_prompt_read_is_refused(repo):
    base = run_command(["git", "rev-parse", "HEAD"], repo).strip()
    head = _commit(repo, "src/analysis_service/compact.py")
    read = f"{check.PROMPT_HEADING}\n\n- prompts/extract-compact.md: still true."
    assert _run(repo, base, head, FULL_SECTION) == 1
    assert _run(repo, base, head, f"{FULL_SECTION}\n\n{read}") == 0
