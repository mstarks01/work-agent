"""Fill ``moved`` on the archived repaired quotes, so the archive reads under schema 3.0.

#675 added ``moved`` to a **Repaired Quote**: what the substitution changed in
the claim's own terms, a negation or a number, as
:func:`analysis_service.grounding.meaning_moved` computes it from what the
agent wrote and the span the service put in its place. The field is required,
because the critic and the viewer route on it, and it is checked on load
against the two texts it is computed from.

Every archived report that carries a repair predates the field, so
``Report.model_validate`` fails on it. This script fills the field the way the
service would have: it finds the claim the mark names inside the same block,
reads the ground at the mark's index, and records what moved between the
mark's ``written`` and that ground's ``text``. ``scan_complete`` is left
unset — nothing recorded whether those scans finished, and ``None`` is the
schema's word for that.

A repaired quote is recognised by carrying ``written`` and ``similarity``
together inside a block's ``repaired_quotes`` list, read off the record. A
mark whose claim or index the block does not carry is reported and left
alone: the report would fail validation on that mark anyway, and guessing a
text for it would hide the fault.

Idempotent, and safe to re-run: a mark already carrying ``moved`` is checked
against the texts and rewritten only where it disagrees. Run from the
repository root:

    uv run python evals/migrations/2026-09-07-repaired-quote-moved.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis_service.grounding import meaning_moved


@dataclass
class Counts:
    filled: int = 0
    unresolved: int = 0

    def __bool__(self) -> bool:
        return bool(self.filled)

    def add(self, other: Counts) -> None:
        self.filled += other.filled
        self.unresolved += other.unresolved


def _fill_block(block: dict[str, Any], where: str) -> Counts:
    counts = Counts()
    claims = {
        claim["id"]: claim
        for key in ("claims", "rejected_claims")
        for claim in block.get(key, ())
        if isinstance(claim, dict) and "id" in claim
    }
    for mark in block.get("repaired_quotes", ()):
        if not isinstance(mark, dict) or "written" not in mark:
            continue
        claim = claims.get(mark.get("claim_id"))
        grounds = claim.get("grounds", ()) if claim else ()
        index = mark.get("index", -1)
        if not isinstance(index, int) or not 0 <= index < len(grounds):
            print(
                f"  {where}: repaired quote on {mark.get('claim_id')!r}#{index} names no ground"
            )
            counts.unresolved += 1
            continue
        moved = list(meaning_moved(mark["written"], grounds[index].get("text", "")))
        if mark.get("moved") != moved:
            mark["moved"] = moved
            counts.filled += 1
    return counts


def _walk(node: Any, where: str) -> Counts:
    counts = Counts()
    if isinstance(node, dict):
        if "repaired_quotes" in node and "claims" in node:
            counts.add(_fill_block(node, where))
        for value in node.values():
            counts.add(_walk(value, where))
    elif isinstance(node, list):
        for item in node:
            counts.add(_walk(item, where))
    return counts


def migrate(root: Path, write: bool) -> Counts:
    """Report, and optionally apply, the fill to every artifact under ``root``."""
    total = Counts()
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"  skipped {path}: {error}", file=sys.stderr)
            continue
        counts = _walk(payload, str(path))
        if counts:
            print(f"{path}: filled {counts.filled}")
            if write:
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        total.add(counts)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path("evals/runs"))
    parser.add_argument("--write", action="store_true", help="apply the fill")
    args = parser.parse_args(argv)
    total = migrate(args.root, args.write)
    verb = "filled" if args.write else "would fill"
    print(f"{verb} {total.filled} repaired quotes; {total.unresolved} name no ground")
    return 0


if __name__ == "__main__":
    sys.exit(main())
