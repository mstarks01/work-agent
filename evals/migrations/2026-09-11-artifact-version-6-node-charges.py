"""Move the archive to artifact_version 6, which declares ``node_charges``.

`evals/harness/artifact.py` now declares one more top-level key: what each
node's providers said they charged, summed across the sweep (#822). A declared
key is a version event, so `load_artifact` refuses every version-5 file until
this runs — and the one merged **Baseline** cost $5.68 and cannot be re-run to
produce a version-6 copy of itself.

**Nothing about any number changes.** Every sweep in the archive ran on a
vendor that reports token counts and nothing else, so each one gains
``"node_charges": {}`` — the honest record that no provider stated a charge —
and its ``artifact_version`` becomes 6. The costs the manifest carries are
arithmetic over the same tokens at the same rates, so they recompute to what
they already say.

**A sweep is keyed by its own bytes, so the file is renamed.** The stem of an
artifact in a Baseline is ``sha256`` of its contents, and editing the contents
moves it. `verify` checks the digests listed in ``baseline.json`` and does not
recompute the stem, so leaving the name in place would pass CI while the filename
claimed a digest the file no longer has — which is the one thing the seal
exists to prevent. The manifest entry, its recorded digests and its cost are
re-stamped through the harness's own readers rather than edited by hand.

The artifact is written the way its producer writes it:
``json.dumps(document, indent=2) + "\\n"``, which for the committed Baseline
reproduces the current file byte for byte before the edit. ``node_charges`` is
inserted directly after ``node_usage``, so a migrated file matches what a
version-6 sweep would have written.

``evals/runs/`` is gitignored working output rather than the archive, and it is
migrated too: those artifacts are one machine's record of what it spent, and a
reader that refuses them helps nobody. Seven of them sit at version 2, which
predates this reader entirely; they are left alone rather than guessed at.

Idempotent, and safe to re-run: a file already at version 6 is not a change.
Run from the repository root:

    uv run python evals/migrations/2026-09-11-artifact-version-6-node-charges.py --write

Without ``--write`` it reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evals.harness import baseline
from evals.harness.archive import archive_bytes
from evals.harness.artifact import ARTIFACT_VERSION, load_artifact

#: The version this migration reads. A file on any other version is not this
#: migration's business: version 6 is already done, and the version-2 sweeps
#: predate the served identities `load_artifact` requires.
FROM_VERSION = 5


def _artifact_bytes(document: dict[str, Any]) -> bytes:
    """The producer's own spelling, so a migrated file is a produced file."""
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _migrated(document: dict[str, Any]) -> dict[str, Any]:
    """``document`` at version 6, with the new key where ``build`` writes it."""
    out: dict[str, Any] = {}
    for key, value in document.items():
        out[key] = ARTIFACT_VERSION if key == "artifact_version" else value
        if key == "node_usage":
            out["node_charges"] = {}
    return out


def _sweep_artifacts(root: Path) -> list[Path]:
    """Every artifact file in the archive, committed or local.

    A Baseline directory holds its artifacts beside ``baseline.json``; a local
    run directory holds one per mode. Neither holds an artifact inside a
    ``.reports`` directory, which is where the per-case reports live.
    """
    found = []
    for directory in ("baselines", "runs"):
        base = root / "evals" / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            if path.name == "baseline.json" or ".reports" in path.parts:
                continue
            if any(part.endswith(".reports") for part in path.parts):
                continue
            found.append(path)
    return found


def _version_of(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("artifact_version")
    except (OSError, json.JSONDecodeError):
        return None


def _reseal(directory: Path, write: bool, planned: dict[Path, bytes]) -> list[str]:
    """Re-stamp one Baseline's manifest against the files now on disk.

    Every value is recomputed through the harness's own readers — the digests
    through :func:`baseline._file_digests` and the cost through
    :func:`baseline.price_sweep` — so this states nothing the verifier will not
    recompute for itself.
    """
    manifest_path = directory / "baseline.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    notes = []
    entries = []
    for entry in manifest.get("sweeps", []):
        stem = str(entry.get("artifact", "")).removesuffix(".json")
        path = directory / f"{stem}.json"
        if not path.is_file():
            notes.append(f"{directory.name}: {stem}.json is named but absent")
            continue
        # The bytes this file will hold, which in a dry run are the bytes it
        # would hold. A rename reported only after it happened is a rename
        # nobody can check first.
        payload = planned.get(path, path.read_bytes())
        digest = hashlib.sha256(payload).hexdigest()[:8]
        new_stem = f"{stem.split('-')[0]}-{digest}"
        if new_stem != stem:
            notes.append(f"{directory.name}: {stem} -> {new_stem}")
            if write:
                path.rename(directory / f"{new_stem}.json")
                reports = directory / f"{stem}.reports"
                if reports.is_dir():
                    reports.rename(directory / f"{new_stem}.reports")
        artifact = load_artifact(directory / f"{new_stem}.json") if write else None
        entries.append(
            {
                **entry,
                "artifact": f"{new_stem}.json",
                "files": baseline._file_digests(directory, new_stem),
                "cost": (
                    baseline.price_sweep(artifact).to_json()
                    if artifact is not None
                    else entry.get("cost")
                ),
            }
            if write
            else entry
        )
    if write:
        manifest["sweeps"] = entries
        manifest_path.write_text(archive_bytes("manifest", manifest), encoding="utf-8")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the changes")
    args = parser.parse_args()

    root = REPO_ROOT
    changed = []
    skipped = []
    planned: dict[Path, bytes] = {}
    for path in _sweep_artifacts(root):
        version = _version_of(path)
        if version == ARTIFACT_VERSION:
            continue
        if version != FROM_VERSION:
            skipped.append(f"{path.relative_to(root)} (version {version!r})")
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = _artifact_bytes(_migrated(document))
        planned[path] = payload
        if args.write:
            path.write_bytes(payload)
        changed.append(str(path.relative_to(root)))

    for line in changed:
        print(f"migrated: {line}" if args.write else f"would migrate: {line}")
    for line in skipped:
        print(f"left alone: {line}")

    baselines = root / "evals" / "baselines"
    for directory in sorted(p for p in baselines.glob("*") if p.is_dir()):
        for note in _reseal(directory, args.write, planned):
            print(f"resealed: {note}" if args.write else f"would reseal: {note}")
        if args.write:
            problems = baseline.verify(directory, root=root)
            for problem in problems:
                print(f"PROBLEM {problem}")
            if problems:
                return 1
            print(f"verified: {directory.name}")

    print(f"\n{len(changed)} artifact(s), {len(skipped)} left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
