"""The sweep artifact: one shape, written once and read through one loader.

## Why this is a module

``command_run`` used to build the artifact as a dict literal and
:mod:`evals.harness.stability` used to read it back with ``raw.get("scores")``.
Two hand-kept halves, agreeing only by habit — and a key the writer stopped
producing read as a sweep that measured nothing, which is a plausible number
rather than an error.

The keys are now declared: the envelope's here, and every instrument's on its
own entry in :data:`~evals.harness.instruments.INSTRUMENTS`. :func:`build`
writes exactly that set and :func:`load_artifact` refuses a file missing any of
it, so ``ARTIFACT_VERSION`` guards a described shape rather than a number.

## The envelope

The twelve keys below are the sweep's own facts — what ran, what it ran on,
what answered, and whether the result is trusted. They are not readings over
the claims, which is why no instrument owns them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evals.harness.instruments import INSTRUMENTS, Sweep, artifact_blocks
from evals.harness.provenance import ProvenanceError, RunProvenance
from stride_service.certification import CertifyResult
from stride_service.report import NodeLatency, TokenUsage

# The eval artifact's schema version. Version 1 is the first artifact that
# records served identities at all, and so the first one promotable: every
# earlier artifact is unversioned, and there is no shim that would let one be
# read as this shape. Bump it for any change to the ``provenance`` block, and
# promotion will reject the older files by name rather than half-understanding
# them.
ARTIFACT_VERSION = 1


@dataclass(frozen=True)
class EvalArtifact:
    """A loaded sweep artifact: its provenance, plus the run that produced it.

    The context fields are not promotion inputs — nothing here changes which
    fingerprints get blessed — but an operator approving a promotion should see
    that the sweep they are certifying reported structural failures or read as
    untrusted. Refusing on them would be the wrong call: whether to bless a
    sweep with one failed case is a judgement, and the tool's job is to make
    sure it is a *made* one.

    ``raw`` is the parsed document, kept whole. Promotion needs none of it, but
    the artifact is the only record a finished sweep leaves, and an offline
    instrument reading some other block of it should go through the loader that
    decides an artifact is admissible rather than opening the file a second time
    under its own rules. Read a block with :meth:`block` rather than off ``raw``:
    the accessor raises on a key this version declares and the file does not
    carry, where ``raw.get`` returned an empty measurement.
    """

    path: Path
    mode: str
    cases: tuple[str, ...]
    trusted: bool
    structural_failures: tuple[str, ...]
    provenance: RunProvenance
    raw: dict[str, Any]

    def block(self, key: str) -> Any:
        """One declared block of the artifact, by key.

        **Raises rather than defaulting.** ``raw.get("scores") or ()`` is what
        this replaces, and it read a sweep whose scores block went missing as a
        sweep that scored nothing — the same number, from opposite facts.

        Checked here rather than at load, because which blocks a reader needs is
        the reader's own business: ``promote`` works from ``provenance`` alone
        and is credential-free by design, so holding every artifact to every
        key would refuse the files promotion exists to read.
        """
        if key not in DECLARED_KEYS:
            raise ProvenanceError(
                f"{key!r} is not a key an artifact declares; the declared set is"
                f" {sorted(DECLARED_KEYS)}"
            )
        if key not in self.raw:
            raise ProvenanceError(
                f"{self.path}: carries no {key!r} block, though an"
                f" artifact_version {ARTIFACT_VERSION} sweep writes one"
            )
        return self.raw[key]


def load_artifact(path: Path | str) -> EvalArtifact:
    """Read a sweep artifact's provenance, or refuse it.

    The version is checked before the shape, as
    :func:`~stride_service.certification.load_manifest` does: an artifact from
    another schema should be named as such, not reported as a heap of stray
    keys. There is no best-effort read of an unversioned artifact — those
    predate served identities entirely, so anything recovered from one would be
    a guess at what answered.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ProvenanceError(f"{path}: cannot be read: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProvenanceError(f"{path}: not an eval artifact (expected a JSON object)")

    version = raw.get("artifact_version")
    if version != ARTIFACT_VERSION:
        raise ProvenanceError(
            f"{path}: unsupported artifact_version {version!r}; expected"
            f" {ARTIFACT_VERSION}. An artifact without one predates recorded"
            " served identities and cannot be promoted — re-run the sweep"
        )

    block = raw.get("provenance")
    if not isinstance(block, dict):
        raise ProvenanceError(
            f"{path}: no provenance block, so nothing records what actually"
            " served this run"
        )

    stored = block.get("generation_identities")
    record = {
        key: value for key, value in block.items() if key != "generation_identities"
    }
    try:
        provenance = RunProvenance(**record)
    except ValidationError as exc:
        raise ProvenanceError(f"{path}: malformed provenance: {exc}") from exc

    derived = {
        tier: identity.to_json()
        for tier, identity in provenance.tier_identities().items()
    }
    if stored != derived:
        raise ProvenanceError(
            f"{path}: generation_identities disagrees with the node_runs it is"
            " derived from; the artifact has been edited and will not be promoted"
        )
    provenance.verify()
    return EvalArtifact(
        path=path,
        mode=str(raw.get("mode", "")),
        cases=tuple(str(case) for case in raw.get("cases", ())),
        trusted=bool(raw.get("trusted", False)),
        structural_failures=tuple(
            str(failure) for failure in raw.get("structural_failures", ())
        ),
        provenance=provenance,
        raw=raw,
    )


#: The sweep's own facts, as opposed to a reading over its claims. One writer,
#: so they are a literal here rather than a table.
ENVELOPE_KEYS: tuple[str, ...] = (
    "artifact_version",
    "mode",
    "cases",
    "models",
    "gating",
    "certification",
    "provenance",
    "node_usage",
    "node_latency",
    "structural_failures",
    "mode_output",
    "trusted",
)

#: Every key an artifact of this version carries. The envelope's, plus each
#: instrument's own declaration — so an instrument added to the table is a key
#: the loader starts requiring, with no edit here.
DECLARED_KEYS: frozenset[str] = frozenset(ENVELOPE_KEYS).union(
    *(instrument.keys for instrument in INSTRUMENTS.values())
)


def build(
    *,
    mode: str,
    cases: Sequence[str],
    models: Mapping[str, Any],
    certification: CertifyResult,
    provenance: RunProvenance,
    usage: Mapping[str, TokenUsage],
    latency: Mapping[str, NodeLatency],
    structural_failures: Sequence[str],
    payloads: Sequence[Mapping[str, Any]],
    trusted: bool,
    sweep: Sweep,
) -> dict[str, Any]:
    """The whole artifact: the sweep's envelope, plus every instrument's keys.

    ``provenance`` is what actually generated, per node execution — the record
    ``promote`` reads back. It replaces the ``node_fingerprints`` map, which
    carried the hashes without the served builds they were computed from, so a
    promotion could not be driven from a finished sweep at all
    ([#117](https://github.com/mstarks01/work-agent/issues/117)).

    ``trusted`` rides beside the aggregates so nothing downstream folds an
    uncertified run into a trusted number unaware.
    """
    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "mode": mode,
        "cases": list(cases),
        "models": dict(models),
        "gating": "tier-1-structural-only",
        "certification": certification.to_json(),
        "provenance": provenance.to_json(),
        "node_usage": {node: entry.model_dump() for node, entry in usage.items()},
        "node_latency": {
            node: {**entry.model_dump(), "mean_ms": round(entry.mean_ms)}
            for node, entry in latency.items()
        },
        "structural_failures": list(structural_failures),
        "mode_output": list(payloads),
        "trusted": trusted,
        # Every instrument's own keys, from the table that also printed them.
        # One source for the printed line and the written number is what stops
        # the two disagreeing.
        **artifact_blocks(sweep),
    }
    surplus = sorted(set(artifact) - DECLARED_KEYS)
    if surplus:
        raise ProvenanceError(
            f"the sweep wrote keys this artifact version does not declare:"
            f" {surplus}. Declare them on the instrument that owns them."
        )
    return artifact
