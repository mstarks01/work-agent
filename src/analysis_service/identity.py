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
:data:`SERVED_TRUST` is in the payload rather than in a comment.

No endpoint or region is here yet, and that is deliberate rather than
overlooked. Whether a region scope belongs in an execution identity is
[#496](https://github.com/mstarks01/work-agent/issues/496)'s question. Bedrock
gives one set of weights two spellings, and Vertex's location has never been in
the payload. Answering it here would decide that ticket by accident. It becomes
a version 2 field when #496 rules, and :data:`IDENTITY_VERSION` is what makes
adding it a re-key rather than a silent widening.

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

#: The identity schema. Bumped whenever the payload gains, loses or re-spells a
#: field, because every fingerprint moves when it does. Certification refuses a
#: manifest written for a different one rather than comparing across them.
IDENTITY_VERSION: Final = 1

#: How much the served build is worth. It is read off the provider's own event
#: stream, so it attests to nothing on its own — and it rides *inside* the
#: payload so that a future identity which really can verify a served build
#: produces a different hash rather than a same-looking one with a better story.
SERVED_TRUST: Final = "provider_reported"

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
        "served_trust": SERVED_TRUST,
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
