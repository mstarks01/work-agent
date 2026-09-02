"""Null the ``verb`` on every archived ASVS claim, so the archive reads again.

#424 ruled that ASVS composes no action verb. A framework whose claims name a
catalog requirement is keyed by that requirement, so a verb on one of its claims
is a field nothing reads. ``DraftRequirementRuling.verb`` became
``SkipJsonSchema[None]`` in ``6f9e74f``, and the first ASVS sweep had already
run that morning. 123 claims in ``evals/runs/`` therefore carry a verb the
record now refuses, and ``Report.model_validate`` fails on the reports that hold
them. The sweep's own scorer, ``run.py score`` and ``run.py review`` are all
dead on it.

State plainly what this destroys: the verb an ASVS lane agent wrote, on 123
claims across 15 files. Nothing reads it. ``applicability.py`` matches ASVS
claims by requirement identifier, ``VERSION_FOR`` keys this package's identity
by requirement rather than by action, and #426 records that a framework whose
claims carry a catalog identifier composes no verb at all. The value was never
part of a score, and cannot become one.

What it leaves alone is every STRIDE claim, and there are 774 of them with a
verb in ``evals/runs/``. A verb is half of STRIDE's claim identity, so dropping
one there would silently re-key the ledger. The rule below reads each claim's
own ``framework`` field rather than the file it sits in, and every one of the
123 carries it.

It is idempotent and safe to re-run, because a claim already carrying ``null``
is not a change. Run it from the repository root::

    uv run python evals/migrations/2026-08-30-asvs-verb.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: The package whose claims may not carry a verb. Not a list of files: the rule
#: is a property of the framework, and it is read off each claim.
FRAMEWORK = "asvs"

RUNS = Path(__file__).resolve().parents[1] / "runs"


def _null_asvs_verbs(node: Any) -> int:
    """Null every ASVS claim's ``verb`` below ``node``; return how many changed.

    A claim is recognised by carrying both ``framework`` and ``verb``, which is
    what every record in an archived artifact does — the report, the drafts
    beside it, and the per-run copies under ``spread/``.
    """
    changed = 0
    if isinstance(node, dict):
        if node.get("framework") == FRAMEWORK and node.get("verb") is not None:
            node["verb"] = None
            changed += 1
        for value in node.values():
            changed += _null_asvs_verbs(value)
    elif isinstance(node, list):
        for item in node:
            changed += _null_asvs_verbs(item)
    return changed


def migrate(root: Path, write: bool) -> int:
    """Report, and optionally apply, the change to every artifact under ``root``."""
    total = 0
    for path in sorted(root.rglob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        changed = _null_asvs_verbs(raw)
        if not changed:
            continue
        total += changed
        print(f"{changed:>4}  {path.relative_to(root.parent)}")
        if write:
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    verb = "nulled" if write else "would null"
    print(f"\n{verb} {total} ASVS verb(s)")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="apply the change; otherwise report only"
    )
    parser.add_argument("--runs", default=str(RUNS), help="the archived runs directory")
    args = parser.parse_args()
    migrate(Path(args.runs), args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
