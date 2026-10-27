"""The source tree, read once for every lint that walks it.

Several lints list the repository's source files and parse them with ``ast``,
and the dead-code lint parses every file again on each of its fixed-point
passes. This module is the one reader: the file list is built the same way
for every lint, and a parse is cached for the session.

It also holds the named file sets: the one answer to "which files count" for
each kind of file a lint reads, such as the Python the project runs, the test
modules, the lint modules, the prompts and the guides. A lint reads a named set
and applies its own predicate to it. A lint that reads less than a whole set
names what it leaves out, with the reason, through :func:`without`. Nothing
here judges a file; each lint keeps its own predicates and declarations.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path

from analysis_service.frameworks import (
    CRITIC_DOC,
    LANE_CLOSING_DOC,
    LANE_EXEMPLARS_DOC,
    LANE_SKILL_DOC,
    OUTPUT_DOC,
    SEVERITY_RUBRIC_DOC,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Directory names the repository does not hold, wherever they sit inside it:
#: build and tool caches, git's own store, a coverage report, and a virtual
#: environment that may sit inside the tree. Matched against the path relative
#: to :data:`REPO_ROOT`, so a checkout that itself sits under a folder of one of
#: these names still reads its own source.
_SKIPPED = frozenset(
    {
        "__pycache__",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "htmlcov",
    }
)

#: Directory names the repository holds and no lint reads: frozen research
#: probes. A question about whether a file exists still sees them.
_FROZEN = frozenset({"research"})

#: Directories the repository does not hold, by their place rather than by name.
#: ``evals/runs`` holds run artifacts the repository does not track, and a live
#: run leaves its probe scripts beside them, so a lint that reads that directory
#: judges a file the tree does not hold (#942). ``.claude/worktrees`` holds whole
#: checkouts at other revisions, while ``.claude/skills`` beside it is tracked
#: prose. Anchored rather than added to :data:`_SKIPPED`, so a future module
#: directory that happens to be called ``runs`` stays under every lint.
_SKIPPED_PATHS = ("evals/runs", ".claude/worktrees")

#: Trees whose prose records a state the repository has left, so no prose
#: lint holds them to the live tree. ``docs/research/`` is frozen evidence,
#: and an audit report under ``evals/experiments/`` names the artifacts it read
#: on the day. A lint that
#: reads a kind of prose these trees do not hold adds its own trees to this one.
FROZEN_PROSE = ("docs/research", "evals/experiments")

#: The Python the project runs: the service, the web app, every eval tool, the
#: examples and the scripts CI runs. Every Python file the repository tracks
#: sits under one of these roots or under :data:`TESTS`, except the frozen
#: research probes.
PYTHON: tuple[str, ...] = ("src", "webapp", "evals", "examples", ".github/scripts")

#: The test modules and the helpers they share.
TESTS: tuple[str, ...] = ("tests",)

#: The shipped configuration the service reads.
CONFIG: tuple[str, ...] = ("config",)

#: The GitHub Actions workflows.
WORKFLOWS: tuple[str, ...] = (".github/workflows",)

#: Every file the repository holds.
WHOLE_TREE: tuple[str, ...] = (".",)

#: Every text a model reads: the shared prompt bodies, each framework
#: package's own text and the Domain Packs.
MODEL_TEXT: tuple[str, ...] = ("prompts", "frameworks", "domains")

#: Every prompt a model reads, as ``glob`` patterns from the repository root:
#: the shared bodies, each package's own parts, and each lane's files. A
#: package's ``disclaimer.md`` goes to the report, and its ``cases`` and
#: ``notes`` are retrieved material, so none of them states an outcome.
PROMPTS: tuple[str, ...] = (
    "prompts/*.md",
    *(
        f"frameworks/*/{doc}.md"
        for doc in (
            CRITIC_DOC,
            OUTPUT_DOC,
            SEVERITY_RUBRIC_DOC,
            *sorted({doc for doc in LANE_CLOSING_DOC.values() if doc}),
        )
    ),
    *(f"frameworks/*/lanes/*/{doc}.md" for doc in (LANE_SKILL_DOC, LANE_EXEMPLARS_DOC)),
)

#: The Markdown that is not a guide, by file or tree, with the reason. Every
#: other Markdown file the repository holds is a guide.
NOT_GUIDES: dict[str, str] = {
    "docs/adr": (
        "a decision record states the evidence and the commands of the day it"
        " was decided"
    ),
    "docs/history": "a record of each history rewrite and of the commit IDs it maps",
    **dict.fromkeys(
        FROZEN_PROSE, "frozen prose, which records a state the repository has left"
    ),
    "evals/corpus": (
        "the source text of each eval case and the records of its review sittings"
    ),
    "evals/adversarial/cases": "the source text of each adversarial eval case",
    "evals/calibration_labels": (
        "the records of the calibration review sittings, as each sitting found"
    ),
    "examples/orders.md": "an example system description that a job reads as input",
    **dict.fromkeys(MODEL_TEXT, "text a model reads, which MODEL_TEXT names as a set"),
}

#: The marker that declares a test module a lint: a module that asserts a
#: property of the repository's own text, configuration or syntax trees.
LINT_MARKER = "pytest.mark.lint"

#: A reason shorter than this says nothing a reader can check.
SHORTEST_REASON = 40


def source_files(*roots: str, suffixes: tuple[str, ...] = (".py",)) -> list[Path]:
    """Every file a lint reads under ``roots`` carrying one of ``suffixes``."""
    return [
        path
        for path in held_files(*roots)
        if path.suffix in suffixes
        and not _FROZEN & set(path.relative_to(REPO_ROOT).parts)
    ]


def held_files(*roots: str) -> list[Path]:
    """Every file the repository holds under ``roots``, in a stable order.

    The answer to "does this file exist" for a lint, because the disk also
    holds what a checkout accumulates. A skipped directory is pruned, never
    entered.
    """
    found: list[Path] = []
    for root in roots:
        for folder, folders, files in os.walk(REPO_ROOT / root):
            here = Path(folder)
            folders[:] = [name for name in folders if not _skipped(here / name)]
            found.extend(here / name for name in files if name not in _SKIPPED)
    return sorted(found)


def _skipped(directory: Path) -> bool:
    """Is ``directory`` one the repository does not hold, by name or by place?"""
    relative = directory.relative_to(REPO_ROOT)
    return bool(_SKIPPED & set(relative.parts)) or relative.as_posix() in _SKIPPED_PATHS


@cache
def parse(path: Path) -> ast.Module:
    """The parsed module at ``path``, read once per session. Walk it; never edit it."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _under(path: Path, places: Iterable[str]) -> str | None:
    """The first of ``places`` that is ``path`` or a tree holding it, or None."""
    relative = path.relative_to(REPO_ROOT).as_posix()
    return next(
        (
            place
            for place in places
            if relative == place or relative.startswith(f"{place}/")
        ),
        None,
    )


def without(paths: Iterable[Path], left_out: Mapping[str, str]) -> list[Path]:
    """``paths`` less each file under a file or tree that ``left_out`` names.

    The form of a declared subset: a lint that reads less than a named set keys
    each part it leaves out, relative to :data:`REPO_ROOT`, to the reason. A key
    that leaves out no file is stale, and a short reason says nothing, so either
    raises. A file added later under a named set is read unless a key names it.
    """
    thin = [key for key, reason in left_out.items() if len(reason) < SHORTEST_REASON]
    if thin:
        raise ValueError(f"state a reason for leaving out {thin}")
    kept: list[Path] = []
    used: set[str] = set()
    for path in paths:
        place = _under(path, left_out)
        if place is None:
            kept.append(path)
        else:
            used.add(place)
    stale = sorted(set(left_out) - used)
    if stale:
        raise ValueError(f"{stale} leave out no file of the set, so drop them")
    return kept


def prompt_files() -> list[Path]:
    """Every prompt a model reads: the files :data:`PROMPTS` matches."""
    return sorted({path for pattern in PROMPTS for path in REPO_ROOT.glob(pattern)})


def guide_files() -> list[Path]:
    """Every guide: each Markdown file the repository holds, less
    :data:`NOT_GUIDES`."""
    return [
        path
        for path in source_files(*WHOLE_TREE, suffixes=(".md",))
        if _under(path, NOT_GUIDES) is None
    ]


def lint_modules() -> list[Path]:
    """Every test module that declares itself a lint with :data:`LINT_MARKER`."""
    return [path for path in source_files(*TESTS) if _declares_a_lint(parse(path))]


def _declares_a_lint(tree: ast.Module) -> bool:
    """Whether a module-level ``pytestmark`` names :data:`LINT_MARKER`."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            marks = (
                node.value.elts
                if isinstance(node.value, ast.List | ast.Tuple)
                else [node.value]
            )
            return any(ast.unparse(mark) == LINT_MARKER for mark in marks)
    return False
