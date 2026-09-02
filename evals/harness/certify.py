"""Promoting a sweep winner: re-pin the sampling file and bless its fingerprints.

This is the write half of certification. The pure check lives in the service, at
:mod:`analysis_service.certification`, which certifies each job it completes.
This module is what only a sanctioned sweep does: it re-pins this deployment's
sampling file in place, and records the fingerprints that pinning implies.

It is this deployment's file rather than the repository's. Both files are
located through :class:`~analysis_service.deployment.Deployment`, so a sweep run
against a redirected ``ANALYSIS_SAMPLING`` promotes into the file it measured.

The write path is single-sourced. One ``SamplingConfig`` both re-pins the file's
values and derives the fingerprints recorded in the manifest, so the two cannot
drift, and a blessed fingerprint always describes the params the file holds.

Promotion is keyed by tier, following the manifest. ``promote`` recomputes the
fingerprints from the identity rather than accepting them from the caller, and
that redundancy is the no-drift invariant.

A tier's identity is more than one served build (#504). The execution identity
binds the requested route and the built graph's instruction digest beside the
served build, so a promotion resolves all three.

The three are not resolved alike, and the difference is which of them an
operator can reasonably choose between. Two served builds on one tier is a
provider rotation mid-sweep: both produced numbers in the same aggregate, so the
tool refuses and ``--served`` picks. Two requested routes is a configuration
that changed mid-sweep, which is a re-run rather than a choice, so there is no
flag. Two instruction digests is a sweep whose corpus declared two framework
selections and therefore built two graphs. Both are legitimate, and both are
blessed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from analysis_service.certification import (
    MANIFEST_VERSION,
    BlessedManifest,
    CertificationError,
    load_manifest,
)
from analysis_service.deployment import ConfigPaths, Deployment
from analysis_service.identity import execution_fingerprint
from analysis_service.model_tiers import TIER_NAMES, TierName
from analysis_service.sampling import SamplingConfig, TierSampling
from evals.harness.provenance import ProvenanceError, RunProvenance, TierIdentity


@dataclass(frozen=True)
class TierIdentityKey:
    """Everything but the sampling and the build that keys one tier's identity.

    Both routes, because a fingerprint binds both: ``served`` is what the
    provider said answered and ``requested`` is what this deployment asked for.
    A promotion carrying only the served half would bless a hash it could not
    recompute, and one carrying only the served half is also exactly the shape a
    compromised translator gets to choose.

    ``instruction_sha256`` is here rather than beside the build map because a
    sweep builds one graph per framework selection its corpus declares, so one
    tier can legitimately present several. A tier's promotion blesses one key
    per digest observed.
    """

    requested: str
    served: str
    instruction_sha256: str


def promotion_paths(deployment: Deployment | None = None) -> ConfigPaths:
    """Which ``sampling.toml`` and manifest a promotion re-pins.

    The deployment's, not the repo's. A sweep measures whatever
    ``ANALYSIS_SAMPLING`` names, so promoting its winner has to re-pin that same
    file — re-pinning the checked-in copy instead would bless a fingerprint
    describing params the measured configuration never held. The manifest is
    deployment-local by design and follows for the same reason.

    Both are resolved through the deployment rather than from ``REPO_ROOT``, so
    this module is not a second opinion on where config lives.
    """
    return (deployment or Deployment.from_env()).paths


# The file keys a sweep may re-pin in place, and how each serializes back to
# TOML. ``top_k`` is absent from every set: it is not part of the sampling
# config surface, because the build-time gate provably cannot cover it.
_FLOAT_PARAMS = frozenset(
    {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
)
_INT_PARAMS = frozenset({"seed", "max_output_tokens", "candidate_count"})
# ``thinking`` is a low/medium/high enum, not a resolved integer budget, so it
# serializes as a quoted string like any other TOML literal.
_STR_PARAMS = frozenset({"thinking"})

# On the sampling surface but deliberately outside promotion. ``constrain_output``
# is not a decoding value a sweep can tune towards — it records whether the
# provider serving this tier will accept the graph's schema at all, which is a
# property of the deployment rather than of the tuning. Promoting it would let a
# sweep rewrite a *deployment's* answer to that question with its own.
#
# It still enters the execution identity, which is the part that matters:
# constrained and unconstrained generation are different generation behaviour,
# so a sweep measured one way does not certify a run made the other way. Skipped
# here, compared there.
_NOT_PROMOTABLE = frozenset({"constrain_output"})


def promote(
    sampling: SamplingConfig,
    keys: Mapping[str, Sequence[TierIdentityKey]],
    *,
    build: Mapping[str, str],
    sampling_path: Path | str | None = None,
    manifest_path: Path | str | None = None,
) -> BlessedManifest:
    """Promote a sweep winner: re-pin ``sampling.toml`` and bless its fingerprints.

    ``keys`` maps each **tier** to every identity key it presented. There is no
    node -> tier walk: a fingerprint's payload carries no node name, so blessing
    is per tier in substance as well as in storage.

    ``build`` is the sweep's, not this process's. It completes the identity, and
    reading it from the promoting machine instead would bless a hash describing
    an install that never ran the sweep.

    Re-pinning is a value update **in place**, preserving the file's comments
    (the "why-absent" record is the point of the file). Promoting a param the
    file leaves *unset* raises: turning an unverified param into a pinned one is
    a human decision that owes a rationale, not a silent sweep write.

    Both paths default to this deployment's — see :func:`promotion_paths` — and
    stay explicit parameters so a caller holding a deployment, or a test, can
    name them directly.
    """
    unknown = sorted(set(keys) - set(TIER_NAMES))
    if unknown:
        raise CertificationError(f"unknown tier(s) in served builds: {unknown}")

    if sampling_path is None or manifest_path is None:
        paths = promotion_paths()
        sampling_path = sampling_path or paths.sampling
        manifest_path = manifest_path or paths.blessed_fingerprints

    sampling_path = Path(sampling_path)
    rewritten = _rewrite_sampling_values(
        sampling_path.read_text(encoding="utf-8"), sampling
    )

    base = load_manifest(manifest_path) if Path(manifest_path).exists() else None
    merged: dict[TierName, frozenset[str]] = dict(base.tiers) if base else {}
    # Walked in the vocabulary's order rather than the caller's, so the written
    # manifest does not vary with the order a sweep happened to report tiers in.
    for tier in TIER_NAMES:
        blessing = keys.get(tier)
        if not blessing:
            continue
        fingerprints = {
            execution_fingerprint(
                requested_route=key.requested,
                served_route=key.served,
                sampling=sampling.for_tier(tier).model_dump(),
                instruction_sha256=key.instruction_sha256,
                build=build,
            )
            for key in blessing
        }
        merged[tier] = frozenset({*merged.get(tier, frozenset()), *fingerprints})
    manifest = BlessedManifest(version=MANIFEST_VERSION, tiers=merged)

    # Both writes happen only once the rewrite has succeeded, so a rejected
    # promotion leaves neither file touched.
    sampling_path.write_text(rewritten, encoding="utf-8")
    Path(manifest_path).write_text(_dump_manifest(manifest), encoding="utf-8")
    return manifest


@dataclass(frozen=True)
class TierPromotion:
    """One tier's half of a promotion, resolved down to a single served build."""

    tier: str
    requested_models: tuple[str, ...]
    requested_model: str
    served_model: str
    sampling: TierSampling
    #: One key per instruction digest this tier ran under, and the fingerprint
    #: each produces. Plural because a sweep whose corpus declares two framework
    #: selections builds two graphs, and both are identities the numbers came
    #: from.
    keys: tuple[TierIdentityKey, ...]
    fingerprints: tuple[str, ...]
    nodes: tuple[str, ...]
    # Every build this tier was answered by, kept even once one is selected:
    # the operator approving a promotion should see what they are *not*
    # blessing, not only what they are.
    observed_served_models: tuple[str, ...]


@dataclass(frozen=True)
class PromotionPlan:
    """What a promotion would write, assembled from an artifact and nothing else.

    Built before anything is written so the operator approves a concrete list of
    identities rather than a command. Every fingerprint here is **recomputed**
    by :func:`~analysis_service.identity.execution_fingerprint` from the
    artifact's recorded identity — the artifact's stored hashes are verified
    against the same function and never copied forward, so an edited artifact
    cannot smuggle a fingerprint into the manifest.
    """

    sampling: SamplingConfig
    tiers: tuple[TierPromotion, ...]
    build: dict[str, str]

    @property
    def keys(self) -> dict[str, tuple[TierIdentityKey, ...]]:
        """The ``tier -> identity keys`` mapping :func:`promote` blesses."""
        return {entry.tier: entry.keys for entry in self.tiers}


def plan_promotion(
    provenance: RunProvenance, chosen: Mapping[str, str] | None = None
) -> PromotionPlan:
    """Resolve an artifact into the exact set of identities a promotion blesses.

    ``chosen`` names one served build per tier and is required only where the
    sweep observed more than one — a tier answered by two builds is not
    resolvable by this code, because both produced numbers that went into the
    same aggregate and picking either would be the tool deciding what the
    operator certified. It **selects among observations**; a build the sweep
    never saw is rejected, so the choice can narrow what is blessed and never
    introduce it.

    Raises :class:`ProvenanceError` on anything unresolvable, so a caller that
    is already handling a bad artifact handles an ambiguous one the same way.
    """
    provenance.verify()
    identities = provenance.tier_identities()
    if not identities:
        raise ProvenanceError(
            "the artifact recorded no execution identities: this sweep"
            " observed no served build, so there is nothing to bless"
        )

    chosen = dict(chosen or {})
    unknown = sorted(set(chosen) - set(identities))
    if unknown:
        raise ProvenanceError(
            f"--served names tier(s) {unknown} that this sweep did not exercise;"
            f" it exercised {sorted(identities)}"
        )

    sampling = provenance.sampling_config()
    return PromotionPlan(
        sampling=sampling,
        build=dict(provenance.build),
        tiers=tuple(
            _tier_promotion(
                tier, identity, chosen.get(tier), sampling, build=provenance.build
            )
            for tier, identity in identities.items()
        ),
    )


def _tier_promotion(
    tier: TierName,
    identity: TierIdentity,
    chosen: str | None,
    sampling: SamplingConfig,
    *,
    build: Mapping[str, str],
) -> TierPromotion:
    """One tier's promotion, refusing rather than choosing among its identities."""
    served = _select_served(tier, identity, chosen)
    requested = _select_requested(tier, identity)
    tier_sampling = sampling.for_tier(tier)
    keys = tuple(
        TierIdentityKey(requested=requested, served=served, instruction_sha256=digest)
        for digest in identity.instruction_digests
    )
    return TierPromotion(
        tier=tier,
        requested_models=identity.requested_models,
        requested_model=requested,
        served_model=served,
        sampling=tier_sampling,
        keys=keys,
        fingerprints=tuple(
            execution_fingerprint(
                requested_route=key.requested,
                served_route=key.served,
                sampling=tier_sampling.model_dump(),
                instruction_sha256=key.instruction_sha256,
                build=build,
            )
            for key in keys
        ),
        nodes=identity.nodes,
        observed_served_models=identity.served_models,
    )


def _select_requested(tier: str, identity: TierIdentity) -> str:
    """The one requested route this tier ran on, or refuse.

    There is no ``--requested`` counterpart to ``--served``, and there should not
    be: a served build is the provider's choice and an operator can reasonably
    pick among several, while a requested route is this deployment's own
    configuration. A tier that presented two of them was misconfigured mid-sweep,
    and the fix is to re-run rather than to bless half of it.
    """
    if len(identity.requested_models) == 1:
        return identity.requested_models[0]
    raise ProvenanceError(
        f"tier {tier!r} was asked for under"
        f" {len(identity.requested_models)} different routes"
        f" ({', '.join(identity.requested_models)}), so its numbers came from a"
        " mix of configurations. Re-run the sweep against one route; there is no"
        " flag to pick among them, because a requested route is this"
        " deployment's own configuration rather than the provider's choice"
    )


def _select_served(tier: str, identity: TierIdentity, chosen: str | None) -> str:
    if chosen is not None:
        if chosen not in identity.served_models:
            raise ProvenanceError(
                f"--served {tier}={chosen}: this sweep never observed that build"
                f" on {tier}. Observed: {', '.join(identity.served_models)}"
            )
        return chosen
    if identity.ambiguous:
        raise ProvenanceError(
            f"tier {tier!r} was answered by {len(identity.served_models)} different"
            f" served builds ({', '.join(identity.served_models)}), so its numbers"
            " came from a mix. Name the one to bless with"
            f" --served {tier}=<build>, and repeat the promotion for the other if"
            " both should be certified"
        )
    return identity.served_models[0]


def _wanted_values(sampling: SamplingConfig) -> dict[tuple[str, str], str]:
    """The ``(tier, file key) -> serialized value`` a promotion means to write."""
    wanted: dict[tuple[str, str], str] = {}
    for tier_name, tier in sampling.tiers.items():
        for param, value in tier.model_dump().items():
            if value is not None and param not in _NOT_PROMOTABLE:
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
            " unset with a rationale; pinning one is a human decision"
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
    validated to ``[0-9a-f]{64}``. Tier keys are re-checked against the known
    tier names as defence in depth against a crafted key landing verbatim in a
    file the *service* parses (OWASP A05).
    """
    parts = [
        "# Blessed execution-identity fingerprints — a machine record.",
        "# Written by evals.harness.certify.promote, single-sourced with",
        "# config/sampling.toml. Keyed by TIER: a fingerprint is",
        "# (vendor-prefixed served build, tier sampling) and carries no node",
        "# name, so two nodes on one tier present the same hash by construction.",
        "#",
        "# Deployment-local: the repo ships this empty, and only a deployment",
        "# that has run a sanctioned sweep can fill it in.",
        "",
        f"version = {manifest.version}",
        "",
        "[tiers]",
    ]
    for tier in sorted(manifest.tiers):
        if tier not in TIER_NAMES:
            raise CertificationError(f"refusing to write unknown tier key {tier!r}")
        prints = sorted(manifest.tiers[tier])
        if not prints:
            parts.append(f"{tier} = []")
            continue
        parts.append(f"{tier} = [")
        parts.extend(f'  "{fingerprint}",' for fingerprint in prints)
        parts.append("]")
    return "\n".join(parts) + "\n"
