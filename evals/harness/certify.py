"""The offline eval gate: certify a run's per-node fingerprints (ticket 08).

The spec is [ticket 03](.wayfinder/model-tuning/tickets/03-provenance-and-override-policy.md)
§4. Every LLM node stamps a generation-identity fingerprint (ticket 07):
``sha256(served model, resolved tier sampling)``. This module holds the
checked-in blessed set and the pure verdict computed against it — it turns
"green suite, drifting prod" from an invisible risk into a visible, gateable
fact.

The verdict lives in the **eval/CI layer, never on the product report**: a
production report carries raw fingerprints only (ticket 07) and must describe
itself without the eval manifest. "Never silently trust" means the eval path
**always** runs :func:`certify` and surfaces the verdict, refusing to fold an
uncertified run into trusted aggregates; whether an uncertified verdict
hard-fails is a CI policy knob, not this module's call.

Loading fails closed (OWASP A02/A10): a malformed manifest, an unknown key, a
non-hex fingerprint, or an unsupported version raises rather than certifying
against a silently-empty set. The live gate *run* stays out of scope (no
Vertex); everything here is exercised offline against scripted fingerprints.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stride_service.model_tiers import TierName
from stride_service.report import StrideReport
from stride_service.sampling import SamplingConfig, sampling_fingerprint

EVALS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_ROOT.parent
DEFAULT_MANIFEST_PATH = EVALS_ROOT / "blessed-fingerprints.toml"
DEFAULT_SAMPLING_PATH = REPO_ROOT / "config" / "sampling.toml"

# The file keys a sweep may re-pin in place, and how each serializes back to
# TOML. ``top_k`` is absent from every set: sampling version 3 removed it from
# the config surface entirely, because the build-time gate provably cannot cover
# it, so there is no longer a line for a promotion to re-pin.
_FLOAT_PARAMS = frozenset(
    {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
)
_INT_PARAMS = frozenset({"seed", "max_output_tokens", "candidate_count"})
# ``thinking`` is a low/medium/high enum in version 3, not a resolved integer
# budget, so it serializes as a quoted string like any other TOML literal —
# the mixed-scalar reversal the previous schema needed is gone with it.
_STR_PARAMS = frozenset({"thinking"})

# Hard cutover, like the sampling loader (ticket 02): only this manifest version
# is accepted, everything else fails closed.
MANIFEST_VERSION = 1

# A fingerprint is a lowercase sha256 hex digest (ticket 07); a node key is a
# graph node identifier. Validating the node shape on write is defence in depth
# against TOML injection through a crafted key (OWASP A05).
_HEX64 = r"^[0-9a-f]{64}$"
_NODE_KEY = r"^[a-z][a-z0-9_]*$"

Fingerprint = Annotated[str, Field(pattern=_HEX64)]


class CertificationError(ValueError):
    """The blessed-fingerprint manifest is invalid or unusable."""


class BlessedManifest(BaseModel):
    """The per-node blessed fingerprints a baseline sweep recorded.

    ``extra="forbid"`` rejects a stray top-level key; the node table maps a
    graph node name to the *set* of fingerprints blessed for it (a node may
    accumulate several blessed served-builds). Duplicate fingerprints within a
    node collapse — the membership test is all that matters.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    nodes: dict[str, frozenset[Fingerprint]] = Field(default_factory=dict)

    def blessed_for(self, node: str) -> frozenset[str]:
        """The fingerprints blessed for one node — empty if the node is unknown."""
        return self.nodes.get(node, frozenset())


@dataclass(frozen=True)
class UncertifiedNode:
    """One node whose run fingerprint no blessed baseline covered."""

    node: str
    fingerprint: str

    def to_json(self) -> dict[str, str]:
        return {"node": self.node, "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class CertifyResult:
    """The gate verdict: whether every node's fingerprint is blessed.

    ``uncertified`` names each offending node and the fingerprint it presented,
    so an override-drifted run and a served-build-drifted run are both reported
    with the exact hash that failed — the two are indistinguishable to the gate
    (both absent from the set) and reported identically, which is the point.
    """

    certified: bool
    uncertified: tuple[UncertifiedNode, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "certified": self.certified,
            "uncertified_nodes": [node.to_json() for node in self.uncertified],
        }


def certify(
    node_fingerprints: Mapping[str, str], manifest: BlessedManifest
) -> CertifyResult:
    """Certify a run's per-node fingerprints against the blessed manifest.

    A pure function (ticket 03 §4): a node is uncertified when its fingerprint is
    absent from that node's blessed set, so an empty set — a node no sweep has
    blessed yet — fails every fingerprint closed. Sorted so the verdict is
    deterministic regardless of the mapping's iteration order.
    """
    uncertified = tuple(
        UncertifiedNode(node=node, fingerprint=fingerprint)
        for node, fingerprint in sorted(node_fingerprints.items())
        if fingerprint not in manifest.blessed_for(node)
    )
    return CertifyResult(certified=not uncertified, uncertified=uncertified)


def report_fingerprints(report: StrideReport) -> dict[str, str]:
    """The node -> fingerprint map a CI job certifies a stored report against.

    Only LLM nodes carry a fingerprint (ticket 07): a deterministic FunctionNode
    has none and is skipped, never certified against an empty set.
    """
    return {
        node.node: node.sampling_fingerprint
        for node in report.nodes
        if node.sampling_fingerprint is not None
    }


def load_manifest(path: Path | str = DEFAULT_MANIFEST_PATH) -> BlessedManifest:
    """Load and validate the blessed-fingerprint manifest, fail-closed.

    Every failure path raises :class:`CertificationError`: an unreadable file,
    invalid TOML, an unsupported version, a bad node key, or a non-hex
    fingerprint — never a silently-empty manifest that would certify every run.
    """
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CertificationError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise CertificationError(f"{path}: cannot be read: {exc}") from exc

    try:
        manifest = BlessedManifest(**raw)
    except ValidationError as exc:
        raise CertificationError(f"{path}: {exc}") from exc

    if manifest.version != MANIFEST_VERSION:
        raise CertificationError(
            f"{path}: unsupported version {manifest.version};"
            f" expected {MANIFEST_VERSION}"
        )
    return manifest


def promote(
    sampling: SamplingConfig,
    served_models: Mapping[str, str],
    resolve_tier: Callable[[str], TierName],
    *,
    sampling_path: Path | str = DEFAULT_SAMPLING_PATH,
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
) -> BlessedManifest:
    """Promote a sweep winner: re-pin ``sampling.toml`` and bless its fingerprints.

    The single-sourced write path (ticket 03 §4): one ``SamplingConfig`` both
    re-pins the file's values *and* derives the fingerprints recorded in the
    manifest, so the two cannot drift — a blessed fingerprint always describes
    the params the file actually holds. ``served_models`` maps each graph node to
    the served model half of its fingerprint; the new fingerprints merge into
    each node's set (a node accumulates several blessed served-builds).

    Re-pinning is a value update **in place**, preserving the file's comments
    (the "why-absent" record is the point of the file). Promoting a param the
    file leaves *unset* raises: turning an UNVERIFIED param into a pinned one is
    a human decision that owes a rationale (ticket 04), not a silent sweep write.
    """
    sampling_path = Path(sampling_path)
    rewritten = _rewrite_sampling_values(
        sampling_path.read_text(encoding="utf-8"), sampling
    )

    fingerprints = {
        node: sampling_fingerprint(served, sampling.for_tier(resolve_tier(node)))
        for node, served in served_models.items()
    }
    base = (
        load_manifest(manifest_path) if Path(manifest_path).exists() else None
    )
    merged: dict[str, frozenset[str]] = {
        node: set(prints) for node, prints in (base.nodes.items() if base else ())
    }
    for node, fingerprint in fingerprints.items():
        merged[node] = frozenset({*merged.get(node, frozenset()), fingerprint})
    manifest = BlessedManifest(version=MANIFEST_VERSION, nodes=merged)

    # Both writes happen only once the rewrite has succeeded, so a rejected
    # promotion leaves neither file touched.
    sampling_path.write_text(rewritten, encoding="utf-8")
    Path(manifest_path).write_text(_dump_manifest(manifest), encoding="utf-8")
    return manifest


def _wanted_values(sampling: SamplingConfig) -> dict[tuple[str, str], str]:
    """The ``(tier, file key) -> serialized value`` a promotion means to write."""
    wanted: dict[tuple[str, str], str] = {}
    for tier_name, tier in sampling.tiers.items():
        for param, value in tier.model_dump().items():
            if value is not None:
                wanted[(tier_name, param)] = _file_value(param, value)
    return wanted


def _file_value(param: str, value: float | str) -> str:
    """Serialize one param's value as the TOML literal the file would hold."""
    if param in _FLOAT_PARAMS:
        return repr(float(value))
    if param in _INT_PARAMS:
        return str(int(value))
    if param in _STR_PARAMS:
        return json.dumps(str(value))
    raise CertificationError(f"{param} is not a promotable sampling param")


def _rewrite_sampling_values(text: str, sampling: SamplingConfig) -> str:
    """Re-pin present param lines from ``sampling``; raise on an unset promotion.

    Walks the file line by line, tracking the current ``[tiers.<tier>]`` section,
    and rewrites the value on each ``<param> = <value>`` line the config sets,
    keeping any trailing comment. A param the config sets but the file does not
    already pin has nowhere to land without inventing an uncommented line, so it
    raises rather than guessing where the human rationale would go.
    """
    wanted = _wanted_values(sampling)
    written: set[tuple[str, str]] = set()
    tier: str | None = None
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        section = _tier_section(line)
        if section is not None:
            tier = section
            continue
        if tier is None:
            continue
        param, updated = _rewrite_line(line, tier, wanted)
        if param is not None:
            lines[index] = updated
            written.add((tier, param))

    unwritten = sorted(key for key in wanted if key not in written)
    if unwritten:
        detail = ", ".join(f"tiers.{t}.{p}" for t, p in unwritten)
        raise CertificationError(
            f"cannot promote unset param(s) {detail}: the file leaves them"
            " unset with a rationale; pinning one is a human decision (ticket 04)"
        )
    return "".join(lines)


def _tier_section(line: str) -> str | None:
    """The tier named by a ``[tiers.<tier>]`` header line, or ``None``."""
    stripped = line.strip()
    if stripped.startswith("[tiers.") and stripped.endswith("]"):
        return stripped[len("[tiers.") : -1]
    return None


def _rewrite_line(
    line: str, tier: str, wanted: Mapping[tuple[str, str], str]
) -> tuple[str | None, str]:
    """Rewrite one ``<param> = <value>`` line if the config re-pins it.

    Returns the param name and the new line when a rewrite happened, else
    ``(None, line)``. A commented-out line (``# top_p ...``) is left untouched —
    an unset param is promoted through the raise in the caller, never by
    uncommenting here.
    """
    code, _, comment = line.partition("#")
    key, sep, _ = code.partition("=")
    if not sep:
        return None, line
    param = key.strip()
    value = wanted.get((tier, param))
    if value is None:
        return None, line
    rebuilt = f"{key}= {value}"
    if comment:
        rebuilt = f"{rebuilt}  #{comment}"
    return param, f"{rebuilt.rstrip()}\n"


def _dump_manifest(manifest: BlessedManifest) -> str:
    """Serialize the manifest to TOML, sorted for a stable, reviewable diff.

    Hand-rolled rather than via a TOML writer (not a dependency here): the shape
    is a version plus a table of hex-string arrays, and every value is already
    validated to ``[0-9a-f]{64}`` or a node-key slug, so there is nothing to
    escape. Node keys are re-checked against the slug shape as defence in depth
    against a crafted key landing verbatim in the file (OWASP A05).
    """
    parts = [
        "# Blessed generation-identity fingerprints (wayfinder ticket 08).",
        "# Written by evals.harness.certify.promote — a machine record; the",
        "# fingerprints are single-sourced with config/sampling.toml.",
        "",
        f"version = {manifest.version}",
        "",
        "[nodes]",
    ]
    for node in sorted(manifest.nodes):
        if not re.match(_NODE_KEY, node):
            raise CertificationError(f"refusing to write malformed node key {node!r}")
        prints = sorted(manifest.nodes[node])
        if not prints:
            parts.append(f"{node} = []")
            continue
        parts.append(f"{node} = [")
        parts.extend(f'  "{fingerprint}",' for fingerprint in prints)
        parts.append("]")
    return "\n".join(parts) + "\n"

