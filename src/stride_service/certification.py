"""Certifying a run's generation identities against a deployment's manifest.

This lives in the **service**, not the eval harness (#17 decision 1). The
decisive fact was packaging rather than philosophy: ``evals/`` does not ship —
``pyproject.toml`` builds only ``src/stride_service`` — so the production image
could not import the eval gate even if it wanted to, and nothing in production
certified anything. That left #7 decision 4 unredeemed: it declined a
served-vs-configured comparison because drift "falls out of certification for
free", which is true only where something certifies.

So the pure check moved here and the **write** path stayed in evals:
``promote()`` and the certification-bar verification import *this* module. The
dependency inverts in the direction it already ran.

**The manifest keys by tier, not by node** (#14 decision 4). A fingerprint's
payload contains no node name — it is ``(vendor-prefixed served build, tier
sampling)`` — and the tier map puts ``critic`` and ``recritic`` both on
``strong``, ``extract`` and ``repair`` both on ``base``. So ``recritic``
presents a *byte-identical* hash to ``critic``, and per-node keying would call
that same hash blessed under one key and unblessed under the other, reporting
the first production revise path uncertified on a technicality. Node keying was
a leftover from build-time fingerprints; the binding is per tier and so is the
hash.

**Three states, and the third is a separate field.** ``certified`` keeps its
narrow meaning — every observed fingerprint is blessed — because callers consume
it as "trust these aggregates", and a tier that contributed to no number has not
made them untrustworthy. Folding "unexercised" into the boolean would mark an
ordinary run untrusted, which is how a gate teaches people to bypass it.

A node that never ran is **absent**, not present-with-an-empty-set: absence is
the sole encoding, never one of two drifting synonyms. An explicitly empty
observation set is therefore illegal.

Loading fails closed (OWASP A02/A10): a malformed manifest, an unknown key, a
non-hex fingerprint, or an unsupported version raises rather than certifying
against a silently-empty set.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stride_service.model_tiers import TIER_NAMES, TierName
from stride_service.report import NodeRun, StrideReport

# Version 2 re-keys the manifest from nodes to tiers (#14 decision 4). A hard
# cutover with no shim, and a free one: the file ships with empty sets, so
# nothing stored can be invalidated by the change.
MANIFEST_VERSION = 2

# A fingerprint is a lowercase sha256 hex digest. Validating the shape on write
# is defence in depth against TOML injection through a crafted key (OWASP A05).
_HEX64 = r"^[0-9a-f]{64}$"

Fingerprint = Annotated[str, Field(pattern=_HEX64)]

# Node key -> the tier it runs on. The caller supplies this because only it
# knows which naming the observations use.
TierResolver = Callable[[str], TierName]


class CertificationError(ValueError):
    """The blessed-fingerprint manifest is invalid or unusable."""


class BlessedManifest(BaseModel):
    """The per-tier blessed fingerprints a sanctioned sweep recorded.

    A tier keeps a *set* because it accumulates several blessed served-builds
    over time. Duplicates collapse — membership is all that matters.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    tiers: dict[TierName, frozenset[Fingerprint]] = Field(default_factory=dict)

    def blessed_for(self, tier: str) -> frozenset[str]:
        """The fingerprints blessed for one tier — empty if the tier is unknown."""
        return self.tiers.get(tier, frozenset())


class UncertifiedNode(BaseModel):
    """One node execution whose fingerprint no blessed baseline covered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: str
    fingerprint: str

    def to_json(self) -> dict[str, str]:
        return {"node": self.node, "fingerprint": self.fingerprint}


class CertifyResult(BaseModel):
    """The three-state verdict over a run's generation identities.

    ``uncertified`` is reported **by node** even though blessing is by tier: the
    operator needs to know which node presented the offending hash, and the hash
    itself, to tell an override-drifted run from a served-build-drifted one.
    They are indistinguishable to the check — both are simply absent from the
    set — and reported identically, which is the point.

    ``unexercised`` names **tiers**, not nodes: "``recritic`` never ran" is a
    routing fact, not a certification fact, and every tier has a node that
    always runs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    certified: bool
    uncertified: tuple[UncertifiedNode, ...] = ()
    unexercised: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every tier the graph declares actually presented an identity."""
        return not self.unexercised

    def to_json(self) -> dict[str, object]:
        return {
            "certified": self.certified,
            "uncertified_nodes": [node.to_json() for node in self.uncertified],
            "unexercised_tiers": list(self.unexercised),
        }


def certify(
    observations: Mapping[str, frozenset[str]],
    manifest: BlessedManifest,
    tier_of: TierResolver,
    expected_nodes: Iterable[str],
) -> CertifyResult:
    """Certify one run's observed fingerprints against a deployment's manifest.

    ``observations`` maps a node to **every** fingerprint that node presented:
    one per execution, so twelve eval cases give one node twelve, and a build
    that moves mid-run gives it two. An empty set is illegal — a node that never
    ran is simply absent.

    ``expected_nodes`` is the built graph's LLM nodes, which is where the
    expectation of what *should* have run comes from (#14 decision 2). It is
    deliberately **not** the manifest: the manifest is deployment-local and
    ships empty, so a manifest-derived expectation would be empty exactly on day
    one, and would conflate a *stale* manifest with an *unexercised* one.

    Pure and order-independent: results are sorted so the verdict does not
    depend on mapping iteration order.
    """
    empty = sorted(node for node, prints in observations.items() if not prints)
    if empty:
        raise CertificationError(
            f"nodes with an empty fingerprint set: {empty};"
            " a node that never ran must be absent, not present and empty"
        )

    uncertified = tuple(
        UncertifiedNode(node=node, fingerprint=fingerprint)
        for node, prints in sorted(observations.items())
        for fingerprint in sorted(prints)
        if fingerprint not in manifest.blessed_for(tier_of(node))
    )
    expected_tiers = {tier_of(node) for node in expected_nodes}
    exercised_tiers = {tier_of(node) for node in observations}
    unexercised = tuple(
        tier for tier in TIER_NAMES if tier in expected_tiers - exercised_tiers
    )
    return CertifyResult(
        certified=not uncertified,
        uncertified=uncertified,
        unexercised=unexercised,
    )


def fingerprints_of(nodes: Iterable[NodeRun]) -> dict[str, frozenset[str]]:
    """The node -> fingerprint sets a run of node executions presents.

    Only LLM nodes carry a fingerprint: a deterministic FunctionNode has none
    and is skipped rather than certified against an empty set. A node appearing
    more than once — the critic on a revise path — contributes every hash it
    presented, which is what makes a mid-run build move visible.

    Takes the node runs rather than a report because the two callers hold
    different things: the service certifies a finished report, while a sweep
    certifies runs that may produce no report at all — the extraction mode
    scores an emission, not a :class:`StrideReport`, and its ``extract``
    execution is no less an observed generation identity for that.
    """
    observations: dict[str, set[str]] = {}
    for node in nodes:
        if node.sampling_fingerprint is None:
            continue
        observations.setdefault(node.node, set()).add(node.sampling_fingerprint)
    return {node: frozenset(prints) for node, prints in observations.items()}


def report_fingerprints(report: StrideReport) -> dict[str, frozenset[str]]:
    """The node -> fingerprint sets a finished report presents, ready to certify."""
    return fingerprints_of(report.nodes)


def load_manifest(path: Path | str) -> BlessedManifest:
    """Load and validate the blessed-fingerprint manifest, fail-closed.

    Every failure path raises :class:`CertificationError`: an unreadable file,
    invalid TOML, an unsupported version, an unknown tier, or a non-hex
    fingerprint — never a silently-empty manifest that would certify every run.
    """
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CertificationError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise CertificationError(f"{path}: cannot be read: {exc}") from exc

    # The version check fires before shape validation: a version-1 file's
    # ``[nodes]`` table would otherwise be rejected as a stray key, which is
    # true but says nothing about why.
    version = raw.get("version")
    if version != MANIFEST_VERSION:
        raise CertificationError(
            f"{path}: unsupported version {version!r};"
            f" expected {MANIFEST_VERSION} (hard cutover: version 1 keyed by"
            " node, this keys by tier)"
        )

    try:
        return BlessedManifest(**raw)
    except ValidationError as exc:
        raise CertificationError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class CertificationGate:
    """One deployment's manifest plus the policy for acting on its verdict.

    Assembled once at startup and reused, so two jobs in one process can never
    be certified against different manifests — which would be #10's silent
    override displaced from space into time.

    The two withholding rules deliberately differ (#17 decisions 3 and 4):

    * **uncertified** withholds only under ``require_certified``. It is a
      *measurement* gate, and the manifest ships empty, so on by default it
      would fail every run on day one and train people to switch it off.
    * **unexercised** withholds **unconditionally**. It is an *assertion*, not a
      measurement: it is unreachable on any run that produces a report, because
      every tier has a node that always runs and a rejected model returns no
      report at all. Its cost is therefore zero, and putting it behind the same
      knob would make the free half inert for every default deployment.

    Withholding means refusing the *report*, never failing the job: a failed job
    has no report at all, and the fingerprints that prove the drift live in it.
    The report is the evidence; the envelope is the claim.
    """

    manifest: BlessedManifest
    tier_of: TierResolver
    require_certified: bool = False

    def check(
        self, report: StrideReport, expected_nodes: Iterable[str]
    ) -> CertifyResult:
        """Certify a finished report. Runs once, after every node has run."""
        return certify(
            report_fingerprints(report),
            self.manifest,
            self.tier_of,
            expected_nodes,
        )

    def withholds(self, result: CertifyResult) -> bool:
        """Whether this verdict means the report must not be served."""
        return not result.complete or (
            self.require_certified and not result.certified
        )
