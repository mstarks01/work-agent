"""Write the archive back in the encoding its producer uses.

Two earlier migrations dumped every report they touched with
``json.dumps(..., indent=2)`` and the default ``ensure_ascii=True``. The
producer does not: ``evals/harness/bundle.py`` writes a report through
``Report.model_dump_json``, which emits UTF-8. So 78 archived reports had every
em-dash, curly quote and en-dash rewritten to a ``\\uXXXX`` escape, and the one
merged **Baseline**'s file digests were re-stamped over that.

**Nothing about the content changes.** This re-serializes each file and asserts
that the parsed value is identical, so the only difference is which characters
are escaped. ``json.dumps(value, ensure_ascii=False, indent=2) + "\\n"``
reproduces ``model_dump_json(indent=2) + "\\n"`` byte for byte, which is why the
archive lands back on the producer's spelling rather than on a third one.

**What it touches, and why the rule is that shape.** A file is rewritten only
where it carries a ``\\u`` escape now and carried none at
``reviewed/2026-09-15`` — the tag before the two migrations ran. That excludes a
file whose own producer escapes: ``bundle`` writes ``*.proposals.json`` through
plain ``json.dumps``, and those have always held escapes. So the rule undoes an
edit rather than imposing an encoding, and a file nobody re-escaped is left
alone.

It leaves alone anything the tag does not hold, which is every sweep under the
gitignored ``evals/runs/``. Those are one machine's working output rather than
the archive, and nothing here can say what they looked like before.

The Baseline manifests are refreshed through the same reader ``verify`` calls,
for the reason the crossing migration gives: the digests exist to make a silent
edit loud, and this edit is a script in a pull request.

Idempotent, and safe to re-run: a file already in the producer's encoding is not
a change. Run from the repository root:

    uv run python evals/migrations/2026-09-11-restore-archive-encoding.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# A migration runs as a script, so ``sys.path[0]`` is this directory and the
# repository root is not on the path. The editable install puts
# ``analysis_service`` there; ``evals`` is a namespace package in the tree and
# has to be pointed at.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evals.harness.archive import archive_bytes
from evals.harness.baseline import refresh_manifests
from evals.harness.bundle import kind_of_path

#: The tag whose trees predate the two migrations that escaped the archive. A
#: file that already held an escape there holds one because its producer writes
#: one, and this leaves it alone.
BEFORE_REF = "reviewed/2026-09-15"


def _escaped_at(ref: str, rel: str) -> bool | None:
    """Whether ``rel`` carried a ``\\u`` escape at ``ref``, or None if absent there."""
    found = subprocess.run(
        ["git", "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0:
        return None
    return "\\u" in found.stdout


def _restored(text: str, kind: str) -> str | None:
    """``text`` in its producer's encoding, or None where it is already there.

    ``kind`` rather than one spelling: this walk reaches every kind a Baseline
    holds, and the drafts written three lines after a report in ``bundle`` are
    escaped ASCII on purpose. Restoring them to the report's spelling would be
    the same defect this script repairs, pointed the other way.
    """
    value = json.loads(text)
    rewritten = archive_bytes(kind, value)
    if rewritten == text:
        return None
    # The content is the thing that must not move. A dump that parses to
    # something else is a defect in this script, not an encoding difference.
    assert json.loads(rewritten) == value
    return rewritten


def migrate(root: Path, write: bool) -> int:
    """Report, and optionally apply, the re-encoding under ``root``."""
    changed = 0
    for path in sorted(root.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        if "\\u" not in text:
            continue
        if _escaped_at(BEFORE_REF, str(path)) is not False:
            continue
        kind = kind_of_path(path)
        if kind is None:
            print(f"  {path}: no declared producer for this file, left alone")
            continue
        rewritten = _restored(text, kind)
        if rewritten is None:
            continue
        print(f"{path}: re-encoded in the producer's spelling")
        if write:
            path.write_text(rewritten, encoding="utf-8")
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("evals"))
    parser.add_argument("--write", action="store_true", help="apply the changes")
    args = parser.parse_args()
    changed = migrate(args.root, args.write)
    manifests = refresh_manifests(args.root, args.write)
    verb = "rewrote" if args.write else "would rewrite"
    print(
        f"{verb} {changed} file(s) and {manifests} Baseline manifest(s) under"
        f" {args.root}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
