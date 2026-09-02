"""Certifying a run's execution identities against a deployment's manifest.

The pure check lives in the service rather than in the eval harness, because
``evals/`` does not ship: ``pyproject.toml`` builds only ``src/analysis_service``,
so the production image could not import an eval-side gate. The write path stays
in evals, and ``promote()`` and the certification-bar verification import this
module.

The manifest keys by tier rather than by node. A fingerprint's payload contains
no node name, because it is the versioned execution identity in
:mod:`analysis_service.identity`. The tier map puts ``critic/<framework>`` and
``recritic/<framework>`` both on ``strong``, and the tier loader requires that
pairing. It puts ``extract`` and ``repair`` both on ``base``, and every one of a
framework's lane agents on its single ``analyze/<framework>`` key. A framework's
recritic therefore presents a byte-identical hash to its critic, and its lane
agents present one hash between them, however many the framework declares.
Keying per node would call the same hash blessed under one key and unblessed
under another, and report the production revise path uncertified on a
technicality.

Keying by tier is also what makes the map survive N frameworks. Graph node names
carry their framework, so they multiply with the selection while the tiers do
not. A manifest keyed by node would need a new blessing for every framework
added, over execution identities that had not changed.

A provider's word cannot select a blessed entry on its own (#504). The identity
binds the requested route beside the served one, so a blessing is over a pair. A
translator that returns an approved build string while the deployment asked for
something cheaper produces a fingerprint no manifest holds, and the run reports
uncertified. That is the manipulation this gate can refuse. What it still cannot
do is verify a served build, which is why the report labels it
``provider_reported`` rather than leaving a reader to assume better.

There are three states, and the third is a separate field. ``certified`` keeps
its narrow meaning, that every observed fingerprint is blessed, because callers
consume it as "trust these aggregates" and a tier that contributed to no number
has not made them untrustworthy. Folding "unexercised" into the boolean would
mark an ordinary run untrusted, which is how a gate teaches people to bypass it.

A node that never ran is absent, rather than present with an empty set. Absence
is the sole encoding, and never one of two synonyms that could drift apart. An
explicitly empty observation set is therefore illegal.

Loading fails closed (OWASP A02 and A10). A malformed manifest, an unknown key,
a non-hex fingerprint, or an unsupported version raises, rather than certifying
against a silently empty set.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analysis_service.model_tiers import TIER_NAMES, TierName
from analysis_service.report import NodeRun, Report

# The manifest schema version. Keyed by tier, with no compatibility shim for
# older files.
#
# Version 3 is the execution-identity cutover (#504). Every fingerprint a
# version-2 manifest holds was computed over ``(served route, sampling)`` and
# means nothing against a payload that now also binds the requested route, the
# instruction digest and the build versions. A stale file is refused rather than
# compared across schemas, because the failure of comparing is a run reported
# certified against a hash of a different thing.
MANIFEST_VERSION = 3

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

    def blessed_for(self, tier: TierName) -> frozenset[str]:
        """The fingerprints blessed for one tier — empty if the manifest omits it."""
        return self.tiers.get(tier, frozenset())


class UncertifiedNode(BaseModel):
    """One node execution whose fingerprint no blessed baseline covered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: str
    fingerprint: str

    def to_json(self) -> dict[str, str]:
        return {"node": self.node, "fingerprint": self.fingerprint}


class CertifyResult(BaseModel):
    """The three-state verdict over a run's execution identities.

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
    that moves mid-sweep gives it two. An empty set is illegal — a node that
    never ran is simply absent.

    ``expected_nodes`` is the built graph's LLM nodes, which is where the
    expectation of what *should* have run comes from. It is deliberately
    **not** the manifest: the manifest is deployment-local and ships empty, so
    a manifest-derived expectation would be empty exactly on day one, and would
    conflate a *stale* manifest with an *unexercised* one.

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
    and is skipped rather than certified against an empty set. A node maps to
    a *set* because one node can present several hashes: a sweep runs each node
    once per case, so twelve cases give one node twelve, and a build that moves
    partway through gives it two distinct ones. A single service run cannot
    loop, so there it is one hash per node.

    Takes the node runs rather than a report because the two callers hold
    different things: the service certifies a finished report, while a sweep
    certifies runs that may produce no report at all — the extraction mode
    scores an emission, not a :class:`Report`, and its ``extract``
    execution is no less an observed execution identity for that.
    """
    observations: dict[str, set[str]] = {}
    for node in nodes:
        if node.execution_fingerprint is None:
            continue
        observations.setdefault(node.node, set()).add(node.execution_fingerprint)
    return {node: frozenset(prints) for node, prints in observations.items()}


def report_fingerprints(report: Report) -> dict[str, frozenset[str]]:
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

    # The version check fires before shape validation, so a file on another
    # schema is named as such rather than reported as a set of stray keys.
    version = raw.get("version")
    if version != MANIFEST_VERSION:
        raise CertificationError(
            f"{path}: unsupported version {version!r};"
            f" expected {MANIFEST_VERSION}, which keys blessed fingerprints"
            " by tier"
        )

    try:
        return BlessedManifest(**raw)
    except ValidationError as exc:
        raise CertificationError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class CertificationGate:
    """One deployment's manifest plus the policy for acting on its verdict.

    Assembled once at startup and reused, so two jobs in one process can never
    be certified against different manifests.

    The two withholding rules deliberately differ:

    * **uncertified** withholds only under ``require_certified``. It is a
      *measurement* gate, and the manifest ships empty, so on by default it
      would fail every run on day one and train people to switch it off.
    * **unexercised** withholds **unconditionally**. It is an *assertion*, not
      a measurement: it is unreachable on any run that produces a report,
      because every tier has a node that always runs and a rejected model
      returns no report at all. Its cost is therefore zero, and putting it
      behind the same knob would make the free half inert for every default
      deployment.

    Withholding means refusing the *report*, never failing the job: a failed job
    has no report at all, and the fingerprints that prove the drift live in it.
    The report is the evidence; the envelope is the claim.
    """

    manifest: BlessedManifest
    tier_of: TierResolver
    require_certified: bool = False

    def check(self, report: Report, expected_nodes: Iterable[str]) -> CertifyResult:
        """Certify a finished report. Runs once, after every node has run."""
        return certify(
            report_fingerprints(report),
            self.manifest,
            self.tier_of,
            expected_nodes,
        )

    def withholds(self, result: CertifyResult) -> bool:
        """Whether this verdict means the report must not be served."""
        return not result.complete or (self.require_certified and not result.certified)
