"""Type the archived ``unreconciled_rulings``, so the archive reads under the record.

#710 changed the field from a list of sentences to a list of
:class:`~analysis_service.claims.UnreconciledRuling` records — a claim ID, a
closed ``kind``, and the sentence. Every archived report carrying the old
spelling fails ``Report.model_validate``, and ``run.py score`` is dead on it.

This script rewrites each sentence into the record it describes. **The regular
expression this uses is the very thing #710 removed**, and it is right here and
nowhere else: the archive holds prose, so the kind can only be recovered by
reading it, and doing that once in a migration is what stops every future
consumer from doing it. The service itself never parses a message again.

Four patterns cover the 303 archived entries — every one of them written by
``review_issues``. The ``unbriefed-change`` kind that ``merge_retry`` records
appears in no archived run.

An entry is recognised by sitting in an ``unreconciled_rulings`` list of
strings, read off the record rather than off a path, so a copy anywhere in an
artifact is reached. A list already holding records is left alone. A sentence
no pattern matches is reported and **not** rewritten: it would need a kind
this script cannot name, and a wrong kind is worse than a failed load.

Idempotent, and safe to re-run: a report already typed is not a change.
Run from the repository root:

    uv run python evals/migrations/2026-09-10-unreconciled-ruling-kinds.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Each pattern against the kind it names and the group holding the claim ID.
# Ordered most specific first: a ``duplicate`` rejection and a missing
# ``rejected_because`` both open "claim 'X' is ...".
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^critic dropped draft '(?P<id>.*)'$"), "dropped"),
    (
        re.compile(r"^critic returned claim '(?P<id>.*)', which no lane agent"),
        "invented",
    ),
    (re.compile(r"^claim ID '(?P<id>.*)' is used by \d+ drafts$"), "duplicate-id"),
    (
        re.compile(r"^re-ask changed ruling '(?P<id>.*)', which no problem"),
        "unbriefed-change",
    ),
    (
        re.compile(r"^claim '(?P<id>.*)' is ruled confirmed but its own grounds"),
        "confirmed-on-unknown",
    ),
    (
        re.compile(r"^claim '(?P<id>.*)' rules on '.*', and a duplicate of a"),
        "duplicate-on-unit",
    ),
    (
        re.compile(r"^claim '(?P<id>.*)' hangs its needs-info verdict on"),
        "unresolved-unknown",
    ),
    (
        re.compile(r"^claim '(?P<id>.*)' is ruled needs-info and its related_unknowns"),
        "unresolved-unknown",
    ),
    # Everything else ``_verdict_shape_issues`` writes. Last, so the two
    # openings above win.
    (re.compile(r"^claim '(?P<id>.*)' is (ruled|rejected)"), "verdict-shape"),
)

# The bound the field carries, so a migrated sentence validates.
MESSAGE_MAX_CHARS = 1500
CLAIM_ID_MAX_CHARS = 300


@dataclass
class Counts:
    typed: int = 0
    unmatched: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.typed or self.unmatched)

    def add(self, other: Counts) -> None:
        self.typed += other.typed
        self.unmatched += other.unmatched


def _typed(message: str) -> dict[str, str] | None:
    """One archived sentence as the record it describes, or ``None``."""
    for pattern, kind in PATTERNS:
        match = pattern.match(message)
        if match:
            return {
                "claim_id": match.group("id")[:CLAIM_ID_MAX_CHARS] or "(unnamed)",
                "kind": kind,
                "message": message[:MESSAGE_MAX_CHARS],
            }
    return None


def _walk(node: Any) -> Counts:
    counts = Counts()
    if isinstance(node, dict):
        rulings = node.get("unreconciled_rulings")
        if isinstance(rulings, list):
            rewritten = []
            for entry in rulings:
                if not isinstance(entry, str):
                    rewritten.append(entry)
                    continue
                record = _typed(entry)
                if record is None:
                    counts.unmatched.append(entry)
                    rewritten.append(entry)
                    continue
                rewritten.append(record)
                counts.typed += 1
            node["unreconciled_rulings"] = rewritten
        for value in node.values():
            counts.add(_walk(value))
    elif isinstance(node, list):
        for item in node:
            counts.add(_walk(item))
    return counts


def migrate(root: Path, write: bool) -> Counts:
    """Report, and optionally apply, the typing to every artifact under ``root``."""
    totals = Counts()
    for path in sorted(root.rglob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        counts = _walk(raw)
        if not counts:
            continue
        print(f"{path}: {counts.typed} typed, {len(counts.unmatched)} unmatched")
        for message in counts.unmatched:
            print(f"  no pattern: {message[:120]}")
        if write and counts.typed:
            # ``ensure_ascii`` off, for the reason every writer in this tree
            # leaves it off: ``bundle`` writes a report through
            # ``model_dump_json``, which emits UTF-8. The default escapes every
            # non-ASCII character in the file and makes an archived report no
            # longer the bytes its producer writes.
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        totals.add(counts)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("evals"))
    parser.add_argument("--write", action="store_true", help="apply the changes")
    args = parser.parse_args()
    totals = migrate(args.root, args.write)
    verb = "rewrote" if args.write else "would rewrite"
    print(
        f"{verb} {totals.typed} entries under {args.root};"
        f" {len(totals.unmatched)} matched no pattern"
    )
    return 1 if totals.unmatched else 0


if __name__ == "__main__":
    sys.exit(main())
