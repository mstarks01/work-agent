"""What a certification decision is made against: one node execution's identity.

An **Execution Identity** is everything that decided what a node's answer could
be. It is versioned, it is canonical, and its sha256 is the fingerprint a
deployment's manifest blesses.

The payload carries seven parts. Six of them decide what a node's answer could
be: the requested route, the served build, how far that served build is trusted,
the resolved decoding params, the digest of every instruction the built graph
carries, and the installed versions of the three distributions that sit between
a node and its provider — this service, the agent runtime and the model
translator. The seventh is the payload's own schema version, so a fingerprint
says which shape it hashed. A hash over fewer of them certifies a run whose
behaviour a blessed run never had.

Both model identities are bound, rather than the served one alone. The served
build is what the provider said answered, read off its own event stream, and
nothing here verifies it: a compromised translator can return any string. With
the served build alone in the payload, such a translator picks a build the
manifest already blesses and certifies whatever it likes — the deployment asked
for a cheap model, the translator claimed an approved one, and the fingerprint
matched. Binding the requested route as well makes the manifest bless a pair,
and the requested half comes from the deployment's own configuration, where the
translator has no say. The provider's claim can no longer select an approved
entry by itself. It still cannot be verified by itself either, which is why
``served_trust`` is in the payload rather than in a comment.

**No endpoint or region is in the payload, on any vendor.** That is a ruling
rather than an omission: a region names where a request went, not what decided
the answer, and two regions serving one set of weights would give one run two
fingerprints for no difference a reader could act on.

Widening the identity re-baselines every blessed fingerprint. A prompt edit, a
``litellm`` bump or a service release now moves every hash, so a deployment's
manifest goes stale and its runs report uncertified until a sanctioned sweep
blesses the new ones. That is the cost, and it is the point: a run on edited
prompts is not the run that was sanctioned, and reporting it as certified was
the defect. It is also why the manifest is versioned. A file blessed against the
old two-part hash fails closed, rather than certifying against a payload it
never saw.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Final

from analysis_service.vendors import ServedTrust, vendor_for_route

#: The identity schema. Bumped whenever the payload gains, loses or re-spells a
#: field, because every fingerprint moves when it does. Certification refuses a
#: manifest written for a different one rather than comparing across them.
#:
#: Version 2 makes ``served_trust`` vary by vendor. It was the constant
#: ``"provider_reported"``, which was false for ``vertex``: litellm fills the
#: served identifier from the request there, so every vertex fingerprint stated
#: that a provider named the build and no provider did.
IDENTITY_VERSION: Final = 2

#: Every distribution whose code sits between a node's request and the
#: provider's response, so a version change here can change an answer. A table
#: rather than three named fields: adding the next one is an entry, and the
#: payload spells the distribution names it read rather than a shape that has to
#: be kept in step with them.
#:
#: ``google-genai`` sits there too, and was missing: it is what ADK hands a
#: request to, four shipped modules import it, and ``google-adk==2.5.0`` permits
#: any ``2.x`` -- so it moved while an identity that did not name it hashed the
#: same before and after, which is the exact drift this table exists to catch.
BUILD_DISTRIBUTIONS: Final = (
    "analysis-service",
    "google-adk",
    "google-genai",
    "litellm",
)


class BuildIdentityError(RuntimeError):
    """A distribution whose version the identity needs is not installed."""


@cache
def build_identity() -> Mapping[str, str]:
    """The installed version of every distribution in :data:`BUILD_DISTRIBUTIONS`.

    Fails closed. A missing distribution raises rather than yielding an identity
    with a hole in it: a payload that silently omits the translator's version
    hashes the same before and after a ``litellm`` bump, which is the drift this
    field exists to catch. All three are hard dependencies, so an install that
    cannot answer is broken in a way certification must not paper over.

    Cached because it reads installed metadata and cannot change inside a
    process — an in-place upgrade is a new process.
    """
    versions = {}
    for distribution in BUILD_DISTRIBUTIONS:
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError as exc:
            raise BuildIdentityError(
                f"{distribution} is not installed, so this run has no execution"
                " identity to certify against"
            ) from exc
    return versions


def served_trust_for(requested_route: str) -> ServedTrust:
    """How much the served build is worth, for the vendor a route names.

    Read off the *requested* route because that is the only key the three call
    sites hold. The producer has a route and no ``Vendor``; the two verifiers
    recompute from an artifact's recorded ``requested_model``, which is a
    string. :func:`~analysis_service.vendors.vendor_for_route` is the one
    reader of the rule that turns it into a vendor.

    Raising on a route that names no vendor is safe, and is the point.
    ``node_models`` is built from the tier config through ``Vendor.prefix``, so
    the producer always holds a registry prefix, and a node that never reached a
    model carries no served build and is never fingerprinted at all.
    """
    return vendor_for_route(requested_route).served_trust


def execution_identity(
    *,
    requested_route: str,
    served_route: str,
    sampling: Mapping[str, Any],
    instruction_sha256: str,
    build: Mapping[str, str],
) -> dict[str, Any]:
    """One node execution's identity, as the mapping the fingerprint hashes.

    Returned rather than hashed straight away because a reader has to be able to
    see what was hashed. Every value here is recorded in the clear somewhere in
    the report — the two routes on the node, the sampling in the per-tier block,
    the instruction digest and the build versions in the execution block — so a
    fingerprint is recomputable from the artifact rather than taken on trust.

    ``sampling`` is a plain mapping and not a ``TierSampling``, so this module
    stays out of the sampling and tier imports the report schema also avoids.

    ``build`` is passed rather than read here, and that is what separates
    producing an identity from checking one. A producer hands in
    :func:`build_identity` — the install it is running on. A verifier hands in
    the build map the artifact *recorded*, so a fingerprint keeps recomputing
    after the verifying machine upgrades ``litellm``. Reading the live install
    on both paths would make every stored artifact fail the moment a dependency
    moved, which is not drift in the run but drift in the reader.
    """
    return {
        "version": IDENTITY_VERSION,
        "requested": requested_route,
        "served": served_route,
        "served_trust": served_trust_for(requested_route),
        "sampling": dict(sampling),
        "instructions": instruction_sha256,
        "build": dict(build),
    }


def fingerprint(identity: Mapping[str, Any]) -> str:
    """The sha256 of one execution identity, canonically serialized.

    Sorted keys and no whitespace, so two processes that agree on the identity
    agree on the hash. Nested rather than flattened, because a flat payload lets
    a sampling param named ``served`` collide with the field above it — and a
    collision here reads as a matching identity.
    """
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def execution_fingerprint(
    *,
    requested_route: str,
    served_route: str,
    sampling: Mapping[str, Any],
    instruction_sha256: str,
    build: Mapping[str, str],
) -> str:
    """One node execution's fingerprint: :func:`fingerprint` of its identity."""
    return fingerprint(
        execution_identity(
            requested_route=requested_route,
            served_route=served_route,
            sampling=sampling,
            instruction_sha256=instruction_sha256,
            build=build,
        )
    )
