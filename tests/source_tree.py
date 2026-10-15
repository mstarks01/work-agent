"""The source tree, read once for every lint that walks it.

Several lints list the repository's source files and parse them with ``ast``,
and the dead-code lint parses every file again on each of its fixed-point
passes. This module is the one reader: the file list is built the same way
for every lint, and a parse is cached for the session. Nothing here judges a
file; each lint keeps its own predicates, scopes and declarations.
"""

from __future__ import annotations

import ast
import os
from functools import cache
from pathlib import Path

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
