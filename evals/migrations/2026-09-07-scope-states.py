"""Respell the archived scope states, so the archive reads under the four-state vocabulary.

#659 changed what a **Scope Entry** may say. ``applicable`` became
``not-raised``: the old word read as a verdict that the unit applies, where
the fact is only that no lane filed a claim on it. ``undecidable`` is new, for
a framework whose **Precondition** could not tell whether it applies at all;
that case was written as ``not-applicable`` with a reason saying the input
never said, which told a reader the unit was ruled out.

``ScopeEntry.state`` is a closed literal, so every archived report carrying
the old spelling fails ``Report.model_validate`` and ``run.py score`` is dead
on it. This script rewrites both:

* every scope entry whose ``state`` is ``applicable`` becomes ``not-raised``;
* every scope entry whose ``state`` is ``not-applicable`` and whose reason is
  the graph's own undecidable sentence — it begins ``the input never says
  whether`` — becomes ``undecidable``. The sentence is the graph's template
  rather than an agent's prose, so matching it is reading a code-written fact.

A scope entry is recognised by carrying ``unit`` and ``state`` together, read
off the record rather than off a path, so a copy anywhere in an artifact is
reached. Nothing else in the file is touched.

Idempotent, and safe to re-run: a report already respelled is not a change.
Run from the repository root:

    uv run python evals/migrations/2026-09-07-scope-states.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RENAMED = {"applicable": "not-raised"}
UNDECIDABLE_PREFIX = "the input never says whether"


@dataclass
class Counts:
    renamed: int = 0
    undecidable: int = 0

    def __bool__(self) -> bool:
        return bool(self.renamed or self.undecidable)

    def add(self, other: Counts) -> None:
        self.renamed += other.renamed
        self.undecidable += other.undecidable


def _walk(node: Any) -> Counts:
    counts = Counts()
    if isinstance(node, dict):
        if "unit" in node and "state" in node:
            state = node["state"]
            if state in RENAMED:
                node["state"] = RENAMED[state]
                counts.renamed += 1
            elif state == "not-applicable" and str(node.get("reason", "")).startswith(
                UNDECIDABLE_PREFIX
            ):
                node["state"] = "undecidable"
                counts.undecidable += 1
        for value in node.values():
            counts.add(_walk(value))
    elif isinstance(node, list):
        for item in node:
            counts.add(_walk(item))
    return counts


def migrate(root: Path, write: bool) -> Counts:
    """Report, and optionally apply, the respelling to every artifact under ``root``."""
    totals = Counts()
    for path in sorted(root.rglob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        counts = _walk(raw)
        if not counts:
            continue
        print(
            f"{path}: {counts.renamed} applicable -> not-raised,"
            f" {counts.undecidable} not-applicable -> undecidable"
        )
        if write:
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        totals.add(counts)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("evals/runs"))
    parser.add_argument("--write", action="store_true", help="apply the changes")
    args = parser.parse_args()
    totals = migrate(args.root, args.write)
    verb = "rewrote" if args.write else "would rewrite"
    print(
        f"{verb} {totals.renamed} applicable entries and"
        f" {totals.undecidable} undecidable entries under {args.root}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
