"""Catch every archived report up to two schema changes, so the archive reads again.

Two merged changes moved the record under the archive, and both of them make
``Report.model_validate`` fail on every archived report:

``Assumption.attribute``
    `#469 <https://github.com/mstarks01/work-agent/issues/469>`_ made an
    assumption name the attribute it inferred, as a required field. Every
    archived report predates it.

``NodeExecution.sampling_fingerprint``
    `#521 <https://github.com/mstarks01/work-agent/pull/521>`_ replaced the
    narrow ``sha256(served route, tier sampling)`` with the whole **Execution
    Identity**, and the record forbids an extra key. Every archived node
    carries the old one.

So ``run.py score`` and ``run.py review`` are dead on both live sweeps: the
2026-08-23 STRIDE sweep and the 2026-08-30 ASVS sweep.

**Where the attribute comes from, and why it is not a guess.** An ``analysis``
run injects the case's blessed model at ``prepare``, so an archived report's
``system_model`` is a copy of ``evals/corpus/<case>/model.json``. That file
carries the attribute today. This script matches an archived assumption to a
blessed one on all three fields it does carry — the assumption, the element ID
and the basis — and copies the attribute across. Nothing is inferred from the
prose, and a match that is not unique is refused rather than resolved.

**What this destroys, stated plainly.** The ``sampling_fingerprint`` on every
archived node execution. It is the narrower predecessor of the fingerprint
``analysis_service.identity`` spells now, no code reads it, and the record
refuses a report that carries it. The value is not recoverable from what is
left, and it never fed a score.

**What it leaves alone.** An assumption whose triple names no blessed one. Those
are the ``spread/`` runs, which analyse an ad-hoc description of this service
rather than a corpus case, so no blessed model holds their assumptions. Those
files stay unreadable, which costs nothing: ``run.py score`` takes corpus cases
and refuses an artifact holding none, so a spread run is read for its own
numbers and never re-scored. The script names how many it left.

Idempotent, and safe to re-run: a report already carrying the attribute and no
fingerprint is not a change. Run from the repository root:

    uv run python evals/migrations/2026-09-06-assumption-attribute-and-node-fingerprint.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: The key the node record no longer holds. Not a path: an archived artifact
#: carries node executions in more than one place, and the rule is a property
#: of the key rather than of where it sits.
DROPPED_KEY = "sampling_fingerprint"

#: The three fields an archived assumption carries, and the one it does not.
MATCH_FIELDS = ("assumption", "element_id", "basis")
FILLED_FIELD = "attribute"

HERE = Path(__file__).resolve().parents[1]
RUNS = HERE / "runs"
CORPUS = HERE / "corpus"


def blessed_attributes(corpus: Path) -> dict[tuple[str, ...], set[str]]:
    """Every blessed assumption's attribute, keyed by the three fields it shares.

    A set rather than one value, so a triple two cases spell alike is refused
    at the call site instead of resolving to whichever was read last.
    """
    found: dict[tuple[str, ...], set[str]] = {}
    for model in sorted(corpus.glob("*/model.json")):
        blessed = json.loads(model.read_text(encoding="utf-8"))
        for assumption in blessed.get("assumptions") or []:
            key = tuple(assumption[field] for field in MATCH_FIELDS)
            found.setdefault(key, set()).add(assumption[FILLED_FIELD])
    return found


class Counts:
    """What one walk changed: attributes filled, keys dropped, assumptions left."""

    def __init__(self) -> None:
        self.filled = 0
        self.dropped = 0
        self.left = 0

    def __bool__(self) -> bool:
        return bool(self.filled or self.dropped or self.left)

    def add(self, other: Counts) -> None:
        self.filled += other.filled
        self.dropped += other.dropped
        self.left += other.left


def _walk(node: Any, attributes: dict[tuple[str, ...], set[str]]) -> Counts:
    """Apply both changes below ``node``, and count what each one reached.

    An assumption is recognised by carrying every field in :data:`MATCH_FIELDS`
    and not :data:`FILLED_FIELD`, and a node execution by carrying
    :data:`DROPPED_KEY` — both read off the record rather than off a path, so a
    copy of either anywhere in an artifact is reached.
    """
    counts = Counts()
    if isinstance(node, dict):
        if DROPPED_KEY in node:
            del node[DROPPED_KEY]
            counts.dropped += 1
        is_assumption = all(field in node for field in MATCH_FIELDS)
        if is_assumption and FILLED_FIELD not in node:
            candidates = attributes.get(tuple(node[field] for field in MATCH_FIELDS))
            if candidates and len(candidates) == 1:
                node[FILLED_FIELD] = next(iter(candidates))
                counts.filled += 1
            else:
                counts.left += 1
        for value in node.values():
            counts.add(_walk(value, attributes))
    elif isinstance(node, list):
        for item in node:
            counts.add(_walk(item, attributes))
    return counts


def migrate(root: Path, corpus: Path, write: bool) -> Counts:
    """Report, and optionally apply, both changes to every artifact under ``root``."""
    attributes = blessed_attributes(corpus)
    totals = Counts()
    for path in sorted(root.rglob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        counts = _walk(raw, attributes)
        if not counts:
            continue
        totals.add(counts)
        print(
            f"{counts.filled:>4} {counts.dropped:>5} {counts.left:>5} "
            f" {path.relative_to(root.parent)}"
        )
        if write:
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    verb = "filled" if write else "would fill"
    print(
        f"\n{verb} {totals.filled} attribute(s)"
        f" and dropped {totals.dropped} {DROPPED_KEY}(s)"
    )
    if totals.left:
        print(
            f"left {totals.left} assumption(s) with no blessed match;"
            " those reports still refuse"
        )
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="apply the changes; otherwise report only"
    )
    parser.add_argument("--runs", default=str(RUNS), help="the archived runs directory")
    parser.add_argument("--corpus", default=str(CORPUS), help="the blessed corpus")
    args = parser.parse_args()
    migrate(Path(args.runs), Path(args.corpus), args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
