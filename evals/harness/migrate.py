"""Lift an older sweep artifact to the version the loader reads.

## Why this exists

``load_artifact`` takes one version and refuses every other, on purpose: an
artifact from another schema should be named as such rather than
half-understood. That is right for the loader and leaves a real problem behind
it. Every repeat set this repository has paid for sits under ``evals/runs/`` at
``artifact_version`` 6, and the band instrument is calibrated on repeat sets —
so the data that makes the instrument work is the data the loader refuses
(#916).

Re-running them costs money and buys nothing the files already hold. This is
the other answer: a one-way lift, into a copy.

## What it is not

**Not a compatibility shim.** Nothing here is called during a load, a sweep or
a promotion. The loader stays strict and takes exactly one version, so an old
artifact still refuses everywhere until somebody deliberately lifts it and says
where to put the result.

**Not the archive migrations #855 deleted.** Those walked the merged Baselines
and rewrote them in place, which a digest seal makes wrong; this refuses to
write inside ``evals/baselines`` at all.

## The table

:data:`STEPS` is keyed by the version each step lifts **from**, so a chain is
composed by lookup rather than by an ``if``. A version added to
``ARTIFACT_VERSION`` with no step keyed to the one before it is refused by
name, and :func:`missing_steps` is what a test asks so the gap is found before
an operator meets it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.harness.artifact import ARTIFACT_VERSION
from evals.harness.provenance import ProvenanceError

#: The oldest version a step exists for. Below it an artifact predates recorded
#: served identities, and nothing recovered from one would be an observation.
OLDEST = 6

#: What version 7 renamed inside each ``scores`` row's ``metrics``. The old
#: names read as detection quality and the numbers are not that: the matcher
#: compares lane, action and endpoint-resolved targets and reads no prose. No
#: value moves.
_COVERAGE_RENAMES = {
    "recall": "reference_coverage",
    "must_find_recall": "must_find_coverage",
    "expected_recall": "expected_coverage",
    "element_accuracy": "element_agreement",
}


def _six_to_seven(raw: dict[str, Any]) -> dict[str, Any]:
    """Rename the claim scorer's four coverage metrics, in place of nothing else.

    Only a row's ``metrics`` is touched. A key already carrying the new name is
    left alone rather than overwritten, so a half-migrated file cannot lose the
    value it already has.
    """
    for score in raw.get("scores") or ():
        metrics = score.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for old, new in _COVERAGE_RENAMES.items():
            if old in metrics and new not in metrics:
                metrics[new] = metrics.pop(old)
    return raw


#: One step per version, keyed by the version it lifts **from**.
STEPS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    6: _six_to_seven,
}


def missing_steps() -> list[int]:
    """Versions between :data:`OLDEST` and the current one with no step.

    The table checked against the thing it is a table of. A version bump that
    forgets a step leaves an artifact this module claims to lift and cannot, so
    a test asks this rather than a reader remembering.
    """
    return [
        version for version in range(OLDEST, ARTIFACT_VERSION) if version not in STEPS
    ]


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """One artifact document, lifted to :data:`ARTIFACT_VERSION`.

    Refuses rather than guessing, on every version it has no chain for: one
    older than :data:`OLDEST`, one newer than this build reads, and one whose
    chain has a hole. The document is returned unchanged where it is already
    current, so lifting a directory twice is not an error.
    """
    version = raw.get("artifact_version")
    if version == ARTIFACT_VERSION:
        return raw
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProvenanceError(
            f"artifact_version is {version!r}, so this file does not say which"
            " schema it was written under and nothing can lift it"
        )
    if version > ARTIFACT_VERSION:
        raise ProvenanceError(
            f"artifact_version {version} is newer than this build reads"
            f" ({ARTIFACT_VERSION}); update the tree rather than the artifact"
        )
    if version < OLDEST:
        raise ProvenanceError(
            f"artifact_version {version} is older than {OLDEST}, the oldest this"
            " lifts. Those predate recorded served identities, so what answered"
            " cannot be recovered — re-run the sweep"
        )
    for step in range(version, ARTIFACT_VERSION):
        if step not in STEPS:
            raise ProvenanceError(
                f"no step lifts artifact_version {step} to {step + 1}, so"
                f" {version} cannot reach {ARTIFACT_VERSION}"
            )
        raw = STEPS[step](raw)
    raw["artifact_version"] = ARTIFACT_VERSION
    return raw


def migrate_file(path: Path, out: Path) -> int:
    """Lift the artifact at ``path`` into ``out``. Returns the version it came from.

    Reads with :func:`json.loads` rather than through ``load_artifact``, which
    is the whole point: the loader refuses the shape this exists to repair.
    Every other check the loader makes still applies afterwards, to the copy.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ProvenanceError(f"{path}: cannot be read: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProvenanceError(f"{path}: not an eval artifact (expected a JSON object)")
    was = raw.get("artifact_version")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(migrate(raw), indent=2) + "\n", encoding="utf-8")
    return was if isinstance(was, int) else 0
