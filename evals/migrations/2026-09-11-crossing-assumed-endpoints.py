"""Recompute the archived boundary crossings, so the archive reads again.

`#468 <https://github.com/mstarks01/work-agent/issues/468>`_ gave
:class:`~analysis_service.system_model.BoundaryCrossing` an
``assumed_endpoints`` field: the endpoints whose ``trust_zone`` extraction
inferred. ``Report`` checks its embedded crossings against the ones it derives
from its embedded model, so an archived report whose model carries a
``trust_zone`` assumption now fails ``Report.model_validate``, and ``run.py
score`` is dead on it. 24 of the 78 archived reports fail that way.

**Nothing is inferred here, because the value is derived.** A crossing is a
pure function of the embedded **Valid System Model**, and so is the new field.
This script rewrites each report's ``boundary_crossings`` to what
:meth:`~analysis_service.system_model.SystemModel.boundary_crossings` computes
from the model in the same file. That is the one reader of the rule, so the
archive is caught up by the code rather than beside it.

**What it destroys.** Nothing. A stored crossing that disagreed with its own
model would be overwritten, but no such report can exist: the same check that
now fails refused one at the time it was written.

It rewrites all 78 reports rather than the 24 that fail, so the archive holds
one shape: the other 54 gain an empty ``assumed_endpoints`` on each crossing,
which is what a crossing with no inferred zone says.

**A merged Baseline's manifest records a digest per report file, and this
refreshes it.** Those digests exist to make a silent edit loud, and they do
their job here: without the refresh, ``verify`` fails on the one merged
Baseline. The edit is not silent — it is this script, in a pull request, in the
history — and the bytes it rewrites are derived from the model in the same
file, so a reader can recompute them without trusting anything. The refresh
calls the same digest reader ``verify`` calls, so the two cannot disagree.

Idempotent, and safe to re-run: a report already carrying the derived crossings
is not a change. Run from the repository root:

    uv run python evals/migrations/2026-09-11-crossing-assumed-endpoints.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# A migration runs as a script, so ``sys.path[0]`` is this directory and the
# repository root is not on the path. The editable install puts
# ``analysis_service`` there; ``evals`` is a namespace package in the tree and
# has to be pointed at.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from analysis_service.system_model import SystemModel
from evals.harness.archive import archive_bytes
from evals.harness.baseline import _file_digests


def _derived(report: dict) -> list[dict] | None:
    """The crossings this report's own model yields, or None if it has no model.

    A file under the archive that is not a report, or a report whose model will
    not parse, is left alone: this script fixes one field and is not the place
    a second defect is discovered.
    """
    raw = report.get("system_model")
    if not isinstance(raw, dict):
        return None
    try:
        model = SystemModel.model_validate(raw)
    except ValueError:
        return None
    try:
        crossings = model.boundary_crossings()
    except ValueError:
        return None
    return [crossing.model_dump(mode="json") for crossing in crossings]


def migrate(root: Path, write: bool) -> tuple[int, int]:
    """Report, and optionally apply, the recomputation under ``root``."""
    changed = 0
    skipped = 0
    for path in sorted(root.rglob("*.report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        derived = _derived(report)
        if derived is None:
            print(f"{path}: no readable system model, left alone")
            skipped += 1
            continue
        if report.get("boundary_crossings") == derived:
            continue
        marked = sum(1 for crossing in derived if crossing["assumed_endpoints"])
        print(f"{path}: {len(derived)} crossings, {marked} with an inferred zone")
        report["boundary_crossings"] = derived
        if write:
            # The producer's own spelling, from the one table that holds it.
            # A report is UTF-8 and the drafts written beside it are not, so a
            # migration that picks an encoding rather than asking for one
            # re-seals the Baseline over bytes it never meant to touch.
            path.write_text(archive_bytes("report", report), encoding="utf-8")
        changed += 1
    return changed, skipped


def refresh_manifests(root: Path, write: bool) -> int:
    """Recompute the file digests every Baseline manifest under ``root`` records."""
    changed = 0
    for manifest_path in sorted(root.rglob("baseline.json")):
        directory = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        moved = False
        for entry in manifest.get("sweeps", []):
            stem = str(entry.get("artifact", "")).removesuffix(".json")
            recomputed = _file_digests(directory, stem)
            if entry.get("files") != recomputed:
                entry["files"] = recomputed
                moved = True
        if not moved:
            continue
        print(f"{manifest_path}: file digests recomputed")
        if write:
            # The spelling ``assemble`` writes, so a refreshed manifest and a
            # freshly assembled one are the same bytes.
            manifest_path.write_text(
                archive_bytes("manifest", manifest), encoding="utf-8"
            )
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("evals"))
    parser.add_argument("--write", action="store_true", help="apply the changes")
    args = parser.parse_args()
    changed, skipped = migrate(args.root, args.write)
    manifests = refresh_manifests(args.root, args.write)
    verb = "rewrote" if args.write else "would rewrite"
    print(
        f"{verb} {changed} report(s) and {manifests} Baseline manifest(s) under"
        f" {args.root}; {skipped} report(s) left alone"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
