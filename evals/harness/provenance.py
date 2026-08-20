"""What a sweep's generation identities were, serialized so promotion can reuse them.

A blessed fingerprint is ``sha256(vendor-prefixed served build, resolved tier
sampling)``. Both halves are observations: the served build is read back off
each response, and the sampling is what the deployment resolved after env
overrides. Neither is recoverable from the configured tier strings, so an
artifact recording only what the run *asked* for cannot support a promotion —
the operator would have to rediscover the served builds by hand, and blessing
the requested model instead would certify a route rather than a build
([#117](https://github.com/mstarks01/work-agent/issues/117)).

**One record, one derived view.** :attr:`RunProvenance.node_runs` is the record:
one entry per node *execution*, carrying the tier it ran on, what was requested,
what answered, and the hash that pair produced. Everything else here is derived
from it — the per-tier summary a reader greps for, and the node -> fingerprint
observation map :func:`~stride_service.certification.certify` rules on. The
summary is written into the artifact for legibility and **re-derived** rather
than read back on load; a stored summary that disagrees with the per-node record
is a corrupted artifact, not a second opinion (OWASP A08).

The resolved sampling is stored **once per tier**, not per execution, mirroring
the clear block a :class:`~stride_service.report.Report` already carries:
a fingerprint is recomputable from a node's served build plus its tier's block,
and repeating the block on every execution would be the same fact twelve times
over, free to drift.

Loading fails closed (OWASP A02/A10): an unreadable file, invalid JSON, a
missing or unsupported ``artifact_version``, a malformed record, a tier with no
sampling block, or a fingerprint that does not recompute all raise rather than
yielding a partial record that promotion would bless.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from stride_service.certification import (
    CertificationError,
    Fingerprint,
    TierResolver,
)
from stride_service.model_tiers import TIER_NAMES, TierName
from stride_service.report import NodeRun
from stride_service.sampling import SamplingConfig, TierSampling, sampling_fingerprint

# How an unset param reads in the operator-facing display. A param this
# deployment leaves unset is not the same claim as one pinned to zero, and the
# two must not render alike in the block someone approves a promotion from.
UNSET = "unset"


class ProvenanceError(CertificationError):
    """An eval artifact cannot supply the identities a promotion needs.

    A subclass so one ``except CertificationError`` in the CLI covers reading
    the artifact and writing the manifest — from the operator's side they are
    one refusal to promote, and the message says which half failed.
    """


class NodeExecution(BaseModel):
    """One LLM node execution's generation identity, as observed.

    ``requested_model`` and ``served_model`` are both recorded and neither is
    computed from the other: their disagreement is the drift signal, and
    substituting the requested route where the served build is unknown is
    exactly the silent weakening certification exists to prevent. A node
    execution without a served build carries no fingerprint either (see
    :class:`~stride_service.report.NodeRun`), so it is absent from this record
    rather than present with a hole in it.

    ``tier`` is resolved once, here, from the deployment's node -> tier walk.
    Promotion keys by tier and must not re-derive it: a reader of the artifact
    holds no graph, and a second walk written against a later tier map would
    silently re-attribute a historical hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: str = Field(min_length=1, max_length=100)
    tier: TierName
    requested_model: str = Field(min_length=1, max_length=200)
    served_model: str = Field(min_length=1, max_length=200)
    generation_fingerprint: Fingerprint

    def to_json(self) -> dict[str, str]:
        """One entry, self-describing.

        ``node`` repeats the key it is filed under, so an entry lifted out of
        the map — by ``jq``, or into an issue — still names what ran. The map
        key stays authoritative: :class:`RunProvenance` refuses a record where
        the two disagree, so the repetition cannot become a second answer.
        """
        return {
            "node": self.node,
            "tier": self.tier,
            "requested_model": self.requested_model,
            "served_model": self.served_model,
            "generation_fingerprint": self.generation_fingerprint,
        }


class TierIdentity(BaseModel):
    """Every identity one tier presented across a whole sweep — a derived view.

    Plural throughout, and deliberately so. A tier configured with one GA
    string can be answered by more than one build in a single sweep: providers
    rotate, and a sweep is hours long. Collapsing that to "the" served build
    would bless one of two builds the numbers actually came from, chosen by
    iteration order, so every observation is kept and the choice is pushed onto
    the operator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_models: tuple[str, ...]
    served_models: tuple[str, ...]
    fingerprints: tuple[Fingerprint, ...]
    nodes: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        """Whether this tier was answered by more than one served build."""
        return len(self.served_models) > 1

    def to_json(self) -> dict[str, list[str]]:
        return {
            "requested_models": list(self.requested_models),
            "served_models": list(self.served_models),
            "fingerprints": list(self.fingerprints),
            "nodes": list(self.nodes),
        }


class RunProvenance(BaseModel):
    """Everything a sweep observed about *how* it generated, ready to promote from.

    Carries the two config versions it was measured under. They are not
    decoration: promotion re-pins a sampling file, and re-pinning values
    measured under one schema into a file on another is how a blessed
    fingerprint comes to describe parameters no run ever carried. The versions
    are what let that be refused by name instead of half-applied.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sampling_config_version: int
    tiers_config_version: int
    sampling: dict[TierName, TierSampling]
    node_runs: dict[str, tuple[NodeExecution, ...]]

    @model_validator(mode="after")
    def _entries_match_their_key(self) -> Self:
        """The map key is the node; an entry naming another one is corruption."""
        mismatched = sorted(
            f"{node} -> {execution.node}"
            for node, executions in self.node_runs.items()
            for execution in executions
            if execution.node != node
        )
        if mismatched:
            raise ValueError(
                f"node_runs entries filed under another node: {mismatched}"
            )
        return self

    @property
    def executions(self) -> tuple[NodeExecution, ...]:
        """Every execution, node order then arrival order within a node."""
        return tuple(
            execution
            for node in sorted(self.node_runs)
            for execution in self.node_runs[node]
        )

    def observations(self) -> dict[str, frozenset[str]]:
        """The node -> fingerprint sets :func:`certify` rules on.

        Derived here rather than folded alongside during the sweep, so the
        verdict and the artifact cannot be computed from two different walks of
        the same executions. A node that never ran is absent, which is the
        encoding ``certify`` requires.
        """
        observed: dict[str, set[str]] = {}
        for node, executions in self.node_runs.items():
            for execution in executions:
                observed.setdefault(node, set()).add(execution.generation_fingerprint)
        return {node: frozenset(prints) for node, prints in observed.items()}

    def tier_identities(self) -> dict[TierName, TierIdentity]:
        """The per-tier summary, in the fixed tier order rather than a run's."""
        by_tier: dict[TierName, list[NodeExecution]] = {}
        for execution in self.executions:
            by_tier.setdefault(execution.tier, []).append(execution)
        return {
            tier: TierIdentity(
                requested_models=_unique(e.requested_model for e in executions),
                served_models=_unique(e.served_model for e in executions),
                fingerprints=_unique(e.generation_fingerprint for e in executions),
                nodes=_unique(e.node for e in executions),
            )
            for tier in TIER_NAMES
            if (executions := by_tier.get(tier))
        }

    def sampling_config(self) -> SamplingConfig:
        """The sampling this sweep resolved, as a config a promotion can re-pin.

        Every tier is required, including one the sweep never exercised: a
        :class:`SamplingConfig` describes a whole deployment, and inventing a
        default for a missing tier would re-pin that tier's file values to
        something no run measured.
        """
        try:
            return SamplingConfig(
                version=self.sampling_config_version, tiers=dict(self.sampling)
            )
        except ValidationError as exc:
            raise ProvenanceError(f"the recorded sampling is unusable: {exc}") from exc

    def verify(self) -> None:
        """Recompute every recorded fingerprint; raise on the first that differs.

        The serialized hash is never the input to a promotion — the canonical
        :func:`~stride_service.sampling.sampling_fingerprint` recomputes it from
        the served build and the tier's sampling block. This check is what makes
        the stored value evidence rather than an assertion: an artifact whose
        hash does not follow from the identity beside it is refused, so a
        hand-edited fingerprint cannot reach the manifest (OWASP A08).
        """
        for execution in self.executions:
            sampling = self.sampling.get(execution.tier)
            if sampling is None:
                raise ProvenanceError(
                    f"{execution.node} ran on tier {execution.tier!r}, which has no"
                    " recorded sampling block: the fingerprint cannot be recomputed"
                )
            recomputed = sampling_fingerprint(execution.served_model, sampling)
            if recomputed != execution.generation_fingerprint:
                raise ProvenanceError(
                    f"{execution.node}: the recorded fingerprint"
                    f" {execution.generation_fingerprint} does not follow from"
                    f" served build {execution.served_model!r} and tier"
                    f" {execution.tier!r}'s sampling (recomputes to {recomputed});"
                    " the artifact is inconsistent and will not be promoted"
                )

    def to_json(self) -> dict[str, Any]:
        """The artifact block: the record, plus the summary derived from it.

        ``generation_identities`` is written for the reader who greps rather
        than walks — "what answered for ``strong``" should not require folding
        ``node_runs`` by hand. It is a rendering, not a second record:
        :func:`load_artifact` derives it again and refuses an artifact whose
        stored copy disagrees.
        """
        return {
            "sampling_config_version": self.sampling_config_version,
            "tiers_config_version": self.tiers_config_version,
            "sampling": {
                tier: sampling.model_dump() for tier, sampling in self.sampling.items()
            },
            "node_runs": {
                node: [execution.to_json() for execution in executions]
                for node, executions in sorted(self.node_runs.items())
            },
            "generation_identities": {
                tier: identity.to_json()
                for tier, identity in self.tier_identities().items()
            },
        }


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """Sorted distinct values — a stable summary, independent of arrival order."""
    return tuple(sorted(set(values)))


def provenance_of(
    executions: Iterable[NodeRun],
    *,
    tier_of: TierResolver,
    sampling: SamplingConfig,
    tiers_config_version: int,
) -> RunProvenance:
    """Build the record from the node runs a sweep performed.

    ``executions`` is every node execution across every case — the same flat
    list the token totals are folded from, so the artifact's provenance and its
    costs describe one set of calls. Executions carrying no fingerprint are
    skipped: a deterministic FunctionNode has no generation identity, and an LLM
    node whose response named no build has nothing honest to record.

    ``tier_of`` is the deployment's node -> tier walk, passed in rather than
    re-derived, so this module never becomes a second opinion on which tier a
    node runs on.
    """
    node_runs: dict[str, list[NodeExecution]] = {}
    for run in executions:
        if run.sampling_fingerprint is None:
            continue
        # Both halves are present by construction — NodeRun's own validator
        # requires a served model beside a fingerprint, and the runner
        # fingerprints only a node it resolved a requested route for. Checked
        # anyway, because the alternative to raising here is a promotion
        # blessing an empty served identity.
        if not run.model or not run.requested_model:
            raise ProvenanceError(
                f"{run.node} carries a fingerprint without a"
                f" {'served' if not run.model else 'requested'} model;"
                " there is no generation identity to record"
            )
        node_runs.setdefault(run.node, []).append(
            NodeExecution(
                node=run.node,
                tier=tier_of(run.node),
                requested_model=run.requested_model,
                served_model=run.model,
                generation_fingerprint=run.sampling_fingerprint,
            )
        )
    return RunProvenance(
        sampling_config_version=sampling.version,
        tiers_config_version=tiers_config_version,
        sampling=dict(sampling.tiers),
        node_runs={node: tuple(runs) for node, runs in node_runs.items()},
    )
