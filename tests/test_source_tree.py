"""The one enumerator every lint walks the tree with, held to the other gates.

Three tools decide which files in this checkout get judged, and they decide it
separately: ``ruff`` respects ``.gitignore``, ``mypy`` reads its own ``exclude``,
and the lints in this suite read :mod:`tests.source_tree`. One rule, three
readers, so the tests below check them against each other rather than each
against its own expectation.

The cost of a disagreement is concrete: ``evals/runs`` holds run artifacts the
repository does not track, a live run leaves its probe scripts beside them, and
a reader that judges that directory fails on a file the tree does not hold
(#942).

The named file sets are held the same way: each one against a source that no
lint reads, such as ``git ls-files`` and the code paths of
``.github/scripts/five_class_read.py``, or against the rule its comment states.
"""

from __future__ import annotations

import ast
import subprocess
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from tests import source_tree
from tests.source_tree import (
    _SKIPPED,
    _SKIPPED_PATHS,
    CONFIG,
    LINT_MARKER,
    MODEL_TEXT,
    NOT_GUIDES,
    PROMPTS,
    PYTHON,
    REPO_ROOT,
    TESTS,
    WORKFLOWS,
    guide_files,
    lint_modules,
    parse,
    prompt_files,
    source_files,
    without,
)
from tests.test_five_class_read import check

pytestmark = pytest.mark.lint


def test_no_skipped_path_is_one_git_tracks():
    """A skipped directory holds nothing the repository keeps.

    The other direction of the rule: skipping is for what the checkout
    accumulates, never for source the tree owns. Committing a file under one of
    these paths takes it out of every lint in silence, so this is what refuses
    it.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z", *_SKIPPED_PATHS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = [name for name in tracked.split("\0") if name]

    assert not found, (
        f"these tracked files sit under a path no lint reads: {found}. Either the"
        f" files belong somewhere the lints reach, or {_SKIPPED_PATHS} is wrong."
    )


def test_mypy_excludes_every_path_the_lints_skip():
    """``mypy`` walks the same directories, so it needs the same answer.

    It is the push gate, and its own ``files`` names ``evals``, so a path this
    module skips and ``mypy`` does not is a file that fails a push while no lint
    reads it.
    """
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mypy = manifest["tool"]["mypy"]

    missing = [
        path
        for path in _SKIPPED_PATHS
        if path.split("/")[0] in mypy["files"] and f"^{path}/" not in mypy["exclude"]
    ]

    assert not missing, (
        f"tests/source_tree.py skips {missing} and pyproject.toml's [tool.mypy]"
        " exclude does not, so the push gate judges a file no lint reads."
    )


def test_the_enumerator_reads_the_source_it_is_asked_for():
    """A positive control: the skip rules do not empty the scan."""
    found = {path.name for path in source_files("src")}

    assert {"fan_in.py", "claims.py", "graph.py"} <= found


def _tree(root: Path, *names: str) -> None:
    """Write an empty file at each repository-relative name under ``root``."""
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).touch()


def test_a_checkout_under_a_skipped_name_still_reads_its_source(tmp_path, monkeypatch):
    """A skip name is matched inside the repository, never in the folders above it."""
    root = tmp_path / "research" / ".venv" / "checkout"
    _tree(root, "src/kept.py")
    monkeypatch.setattr(source_tree, "REPO_ROOT", root)

    assert source_files("src") == [root / "src/kept.py"]


def test_no_walk_reads_the_run_archive_or_a_nested_worktree(tmp_path, monkeypatch):
    """A worktree under ``.claude/worktrees`` is a whole checkout at another
    revision, and ``evals/runs`` is untracked output. The skills beside them are
    tracked prose and stay in."""
    _tree(
        tmp_path,
        "src/kept.py",
        ".claude/skills/audit/SKILL.md",
        ".claude/worktrees/other/src/kept.py",
        ".claude/worktrees/other/README.md",
        "evals/runs/job/probe.py",
        "evals/runs/job/notes.md",
        "evals/harness/run.py",
    )
    monkeypatch.setattr(source_tree, "REPO_ROOT", tmp_path)

    found = {
        path.relative_to(tmp_path).as_posix()
        for path in source_files(".", suffixes=(".py", ".md"))
    }

    assert found == {
        "src/kept.py",
        ".claude/skills/audit/SKILL.md",
        "evals/harness/run.py",
    }


def test_every_directory_gitignore_names_is_skipped():
    """``.gitignore`` lists what the checkout accumulates, so every walk skips it.

    A directory there and not here is a directory a lint reads in one checkout
    and not in CI's: a nested worktree, the run archive, a coverage report.
    """
    patterns = [
        line.strip().strip("/")
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip().endswith("/") and not line.startswith("#")
    ]

    missing = [
        pattern
        for pattern in patterns
        if pattern not in (_SKIPPED_PATHS if "/" in pattern else _SKIPPED)
    ]

    assert not missing, (
        f".gitignore names {missing} and tests/source_tree.py walks into it."
    )


def _tracked(*patterns: str) -> list[Path]:
    """Every file git tracks that matches one of ``patterns``."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", *patterns],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / name for name in listed.split("\0") if name]


def _relative(paths) -> set[str]:
    return {path.relative_to(REPO_ROOT).as_posix() for path in paths}


def test_every_tracked_python_file_is_in_a_named_set():
    """The Python the project runs and the tests hold every tracked Python file.

    The frozen research probes are the one exception, and the walk skips them by
    name. A tracked file outside both sets is a file every Python lint misses.
    """
    named = _relative(source_files(*PYTHON, *TESTS))
    missing = sorted(
        name
        for name in _relative(_tracked("*.py"))
        if name not in named and "research" not in name.split("/")
    )

    assert not missing, (
        f"no named set holds {missing}. Add the root to PYTHON or TESTS in"
        " tests/source_tree.py."
    )


def test_the_python_set_holds_every_python_code_path():
    """The CI check's code paths and the PYTHON set name the same Python.

    ``five_class_read.CODE_PATHS`` decides which change needs a five-class
    read, and nothing in the lints reads it. A Python file on a code path
    that PYTHON leaves out is code a reviewer must read and no lint reads.
    """
    named = _relative(source_files(*PYTHON))
    on_a_code_path = {
        name
        for name in _relative(_tracked("*.py"))
        if check.changes_code([name]) and not fnmatchcase(name, "tests/*")
    }

    assert on_a_code_path, "no tracked Python file is on a code path"
    assert sorted(on_a_code_path - named) == []


def test_each_named_root_holds_a_tracked_file():
    """A root that holds nothing is a set that reads less than its name says."""
    empty = [
        root
        for root in (*PYTHON, *TESTS, *CONFIG, *WORKFLOWS, *MODEL_TEXT)
        if not _tracked(root)
    ]

    assert not empty


def test_the_python_set_holds_the_eval_scripts_examples_and_ci_script():
    """A positive control: the scripts at the top of ``evals/``, the examples and
    the CI script are in the set the one-reader lints walk."""
    named = _relative(source_files(*PYTHON))

    assert {
        "evals/verify_corpus.py",
        "examples/embed.py",
        ".github/scripts/five_class_read.py",
    } <= named


def test_every_tracked_markdown_file_is_a_guide_or_says_why_not():
    """A Markdown file is a guide unless :data:`NOT_GUIDES` names it."""
    guides = _relative(guide_files())
    missing = sorted(
        name
        for name in _relative(_tracked("*.md"))
        if name not in guides
        and source_tree._under(REPO_ROOT / name, NOT_GUIDES) is None
    )

    assert not missing, f"these are neither guides nor in NOT_GUIDES: {missing}"


@pytest.mark.parametrize("place", sorted(NOT_GUIDES))
def test_each_entry_that_is_not_a_guide_is_live_and_says_why(place):
    assert _tracked(place), f"{place} holds no tracked file, so drop it"
    assert len(NOT_GUIDES[place]) >= 40


@pytest.mark.parametrize("pattern", PROMPTS)
def test_each_prompt_pattern_matches_a_text_a_model_reads(pattern):
    """A pattern that matches nothing is a prompt the set lost in silence."""
    matched = _relative(REPO_ROOT.glob(pattern))
    model_text = _relative(source_files(*MODEL_TEXT, suffixes=(".md",)))

    assert matched
    assert matched <= model_text
    assert matched <= _relative(prompt_files())


def _defined(tree: ast.Module) -> set[str]:
    """Every public name a module defines at its top level."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {target.id for target in targets if isinstance(target, ast.Name)}
    return {name for name in names if not name.startswith("_")}


#: Every name :mod:`tests.source_tree` defines but ``REPO_ROOT``: each one reads
#: the tree or names a part of it.
_TREE_READERS = frozenset(_defined(parse(Path(source_tree.__file__)))) - {"REPO_ROOT"}


def _reads_the_tree(tree: ast.Module) -> bool:
    """Whether a module imports a file set or the walk from the source tree."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "tests.source_tree"
        and any(alias.name in _TREE_READERS for alias in node.names)
        or isinstance(node, ast.ImportFrom)
        and node.module == "tests"
        and any(alias.name == "source_tree" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_every_module_that_reads_the_tree_declares_itself_a_lint():
    """The lint set is declared by a marker, and held to what a module reads.

    A test module that takes a file set or the walk from
    :mod:`tests.source_tree` asserts a property of the repository's own files,
    so it is a lint. The marker, not the file name, puts it in
    :func:`~tests.source_tree.lint_modules`.
    """
    lints = set(lint_modules())
    undeclared = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in source_files(*TESTS)
        if _reads_the_tree(parse(path)) and path not in lints
    )

    assert not undeclared, (
        f"{undeclared} read the tree and do not declare `pytestmark = {LINT_MARKER}`."
    )


#: The named sets of roots a lint may pass to the walk.
_ROOT_SETS = frozenset(
    {"PYTHON", "TESTS", "CONFIG", "WORKFLOWS", "MODEL_TEXT", "WHOLE_TREE"}
)

#: The modules that may pass the walk a root of their own: the walk itself, and
#: this module, whose tests build a tree of their own to walk.
_THE_WALK = frozenset({"tests/source_tree.py", "tests/test_source_tree.py"})


def _own_roots(tree: ast.Module) -> list[int]:
    """Each line that calls the walk with a root other than a named set."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        in {"source_files", "held_files"}
        and not all(
            isinstance(argument, ast.Starred)
            and isinstance(argument.value, ast.Name)
            and argument.value.id in _ROOT_SETS
            for argument in node.args
        )
    )


def test_every_walk_reads_a_named_set():
    """No lint states its own roots: each one names the set it reads.

    A lint that passes the walk its own roots is a second reader of "which
    files count", and the narrowest copy wins with no warning. A lint that
    reads less than a set says so through :func:`~tests.source_tree.without`.
    """
    found = sorted(
        f"{relative}:{line}"
        for path in source_files(*TESTS)
        if (relative := path.relative_to(REPO_ROOT).as_posix()) not in _THE_WALK
        for line in _own_roots(parse(path))
    )

    assert not found, (
        f"these walk roots of their own: {found}. Read a named set from"
        " tests/source_tree.py, and leave out a part of it with `without`."
    )


def test_the_walk_lint_sees_a_root_of_its_own():
    """A positive control on the lint above."""
    tree = ast.parse('source_files("src")\nsource_files(*PYTHON, *TESTS)\n')

    assert _own_roots(tree) == [1]


def test_a_subset_names_what_it_leaves_out_and_why():
    files = source_files(*PYTHON)
    reason = "a reason long enough to say what a reader can check here"

    kept = without(files, {"examples": reason})

    assert kept and not any(
        path.is_relative_to(REPO_ROOT / "examples") for path in kept
    )
    with pytest.raises(ValueError, match="no file"):
        without(files, {"no-such-tree": reason})
    with pytest.raises(ValueError, match="reason"):
        without(files, {"examples": "short"})
