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

#: Directory names no lint reads: build caches, frozen research probes and a
#: virtual environment that may sit inside the tree.
_SKIPPED = frozenset({"__pycache__", "research", ".venv"})


def source_files(*roots: str, suffixes: tuple[str, ...] = (".py",)) -> list[Path]:
    """Every file under ``roots`` carrying one of ``suffixes``, in a stable order."""
    return [
        path
        for root in roots
        for suffix in suffixes
        for path in sorted((REPO_ROOT / root).rglob(f"*{suffix}"))
        if not _SKIPPED & set(path.parts)
    ]


@cache
def parse(path: Path) -> ast.Module:
    """The parsed module at ``path``, read once per session. Walk it; never edit it."""
    return ast.parse(path.read_text(encoding="utf-8"))
