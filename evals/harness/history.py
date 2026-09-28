"""Old commit IDs, resolved through the history rewrite maps.

A history rewrite gives commits new IDs, and ``docs/history/`` keeps one map
per rewrite from each old ID to the new one. A record that is signed or
append-only keeps the ID it was written with, such as a Baseline's identity or
an experiment's revision. So a reader that asks git about a recorded commit
resolves the ID here first, and follows every rewrite since the record.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from evals.harness.provenance import REPO_ROOT

__all__ = ["HISTORY_DIR", "current_commit"]

HISTORY_DIR = REPO_ROOT / "docs" / "history"


@cache
def _renames(directory: Path) -> dict[str, str]:
    """Every old ID to its new one, across every map in ``directory``."""
    renames: dict[str, str] = {}
    for path in sorted(directory.glob("*.tsv")):
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            old, new = line.split("\t")
            renames[old] = new
    return renames


def current_commit(commit: str, directory: Path = HISTORY_DIR) -> str:
    """The ID ``commit`` has now, or ``commit`` itself where no rewrite renamed it."""
    renames = _renames(directory)
    seen: set[str] = set()
    while commit in renames and commit not in seen:
        seen.add(commit)
        commit = renames[commit]
    return commit
