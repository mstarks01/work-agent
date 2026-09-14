"""The source tree, read once for every lint that walks it.

Several lints list the repository's source files and parse them with ``ast``,
and the dead-code lint parses every file again on each of its fixed-point
passes. This module is the one reader: the file list is built the same way
for every lint, and a parse is cached for the session. Nothing here judges a
file; each lint keeps its own predicates, scopes and declarations.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Directory names no lint reads, wherever they sit: build caches, frozen
#: research probes, and a virtual environment that may sit inside the tree.
_SKIPPED = frozenset({"__pycache__", "research", ".venv"})

#: Directories no lint reads, by their place in the tree rather than by name.
#: ``evals/runs`` holds run artifacts the repository does not track, and a live
#: run leaves its probe scripts beside them, so a lint that reads that directory
#: judges a file the tree does not hold (#942). Anchored rather than added to
#: :data:`_SKIPPED`, so a future module directory that happens to be called
#: ``runs`` stays under every lint.
_SKIPPED_PATHS = ("evals/runs",)


def source_files(*roots: str, suffixes: tuple[str, ...] = (".py",)) -> list[Path]:
    """Every file under ``roots`` carrying one of ``suffixes``, in a stable order."""
    return [
        path
        for root in roots
        for suffix in suffixes
        for path in sorted((REPO_ROOT / root).rglob(f"*{suffix}"))
        if not _SKIPPED & set(path.parts) and not _under_skipped_path(path)
    ]


def _under_skipped_path(path: Path) -> bool:
    """Does ``path`` sit under one of :data:`_SKIPPED_PATHS`?"""
    relative = path.relative_to(REPO_ROOT).as_posix()
    return any(relative.startswith(f"{skipped}/") for skipped in _SKIPPED_PATHS)


@cache
def parse(path: Path) -> ast.Module:
    """The parsed module at ``path``, read once per session. Walk it; never edit it."""
    return ast.parse(path.read_text(encoding="utf-8"))
