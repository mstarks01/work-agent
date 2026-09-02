"""Signing a report so a reader can tell who produced it, and that it is intact.

A report already carries hashes, and they are not this. ``source_sha256``,
``instruction_sha256`` and every ``execution_fingerprint`` are unkeyed digests.
They let a reader recompute a value from the artifact and notice that the two
disagree. Anyone who edits the report can recompute them too. They establish
internal consistency, and nothing about origin.

An attestation is a signature over the whole report by a key a deployment holds.
It answers two questions the digests cannot: which deployment produced this, and
whether any covered byte has moved since. It answers no others, and the one it
is most likely to be misread as answering is the one it must not.

A valid signature says a report is authentic. It does not say the findings are
correct, and it does not say the run was certified. Certification is a separate,
deployment-local verdict about execution identity; see
:mod:`analysis_service.certification`. A signed uncertified report is an
ordinary thing, and anyone who reads a green signature as an endorsement of the
analysis has read it backwards.

The canonical form is versioned, and versioning it is the whole design. A
signature is over bytes, so "the report" has to mean exactly one byte string.
The report is a pydantic model whose JSON serialization is stable but not
guaranteed across schema changes, so :func:`canonicalize` pins the rules — JSON,
sorted keys, no insignificant whitespace, UTF-8 — and :data:`CANONICAL_VERSION`
names them. A verifier that meets an envelope on a version it does not implement
fails. It does not guess, because a guess that produced the same bytes by luck
would validate a report nobody signed.

The signature is detached rather than enveloped. It lives beside the report
rather than inside it, because a field inside the report would have to be
excluded from its own coverage, and "everything except this one field" is a rule
that grows exceptions. Detached keeps the covered set equal to the whole
artifact.

The scheme is Ed25519, because it needs no parameter choices: no curve to pick,
no hash to pair, no padding mode. The scheme with fewest ways to be configured
wrongly is the right one for a signature nobody will tune.

Key material never enters this module's return values, never reaches a log, and
never reaches a report. Only key identifiers do.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tomllib
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from analysis_service.errors import ConfigError

#: The canonicalization rules a signature is over. Bumped when any of them
#: changes, because every existing signature stops verifying when they do — which
#: is correct, and is why the version is in the signed payload rather than beside
#: it.
CANONICAL_VERSION = 1

#: What is being signed. A type tag in the signed bytes is what stops a signature
#: over one kind of document being replayed as one over another.
PAYLOAD_TYPE = "application/vnd.work-agent.report+json"

_SIGNATURE_LENGTH = 64


class KeyringError(ConfigError):
    """The verification keyring is missing or unusable."""


class DuplicateKeyError(ValueError):
    """A JSON object naming one key twice, which two parsers may read apart."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that refuses an object naming a key twice.

    A signature covers the canonical form of the *parsed* document, which is
    what lets a verifier check a report without this project's models. The cost
    is that parsing is where two readers can disagree: ``json`` keeps the last
    of a repeated key, other parsers keep the first, and a file carrying both
    ``"summary": "critical"`` and ``"summary": "none"`` verifies here while
    reading the other way somewhere else.

    Nothing this service produces has a repeated key, so refusing one costs a
    real report nothing and takes the disagreement away.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise DuplicateKeyError(f"{key!r} appears twice in one object")
        seen[key] = value
    return seen


def load_report(text: str) -> Mapping[str, Any]:
    """One report from JSON text, refusing what two parsers could read apart.

    The reader a verifier should use. ``json.loads`` on its own is not enough:
    see :func:`_no_duplicate_keys`.
    """
    return json.loads(text, object_pairs_hook=_no_duplicate_keys)


def canonicalize(report: Mapping[str, Any]) -> bytes:
    """The exact bytes a signature covers.

    Sorted keys and no insignificant whitespace, so two processes holding the
    same report agree on the byte string. UTF-8 with ``ensure_ascii`` off,
    because a report carries submitted prose: escaping it would make the bytes
    depend on a serializer setting rather than on the content.

    Takes the dumped mapping rather than a :class:`~analysis_service.report.Report`
    so a verifier can work from the JSON file alone — it must not need this
    project's models to check a signature, which is most of what "operates
    independently of the producing service" means.

    **What is covered is the canonical form of the parsed document, not the
    file's bytes.** Two files that parse alike verify alike, which is the point:
    a re-serialized report is the same report. It also means parsing is where a
    forgery would live, so :func:`load_report` is the reader that closes the one
    way two parsers can disagree about what a file says.
    """
    return json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(report: Mapping[str, Any]) -> str:
    """The sha256 of the canonical form, hex."""
    return hashlib.sha256(canonicalize(report)).hexdigest()


class Attestation(BaseModel):
    """A detached signature over one report, with what a verifier needs to check it.

    ``signed_at`` is **when this service signed**, not when the run happened, and
    it is not evidence of either: a machine's clock is not a trusted timestamp,
    and nothing here countersigns it. It is recorded because a rotation policy is
    stated in time and an operator reading a revoked key needs to know whether
    the signature predates the revocation. A deployment that needs a trustworthy
    time needs a timestamping authority, which this is not.

    ``key_id`` names the key, never the key. Verification resolves it through a
    keyring the *verifier* holds, so a report cannot carry the public key that
    validates it — an attestation that shipped its own key would verify against
    itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_version: int = Field(ge=1)
    payload_type: str
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_id: str = Field(min_length=1, max_length=200)
    #: base64, over the canonical bytes.
    signature: str = Field(min_length=1)
    #: Aware, always. It is compared against a key's retirement, and Python
    #: refuses to order a naive datetime against an aware one -- so a naive
    #: value here raised ``TypeError`` out of :func:`verify`, past a handler
    #: that catches neither, and exited on a code the table does not name. This
    #: field arrives in the file a caller supplies, so the shape is theirs to
    #: get wrong and this is where it is refused.
    signed_at: AwareDatetime

    def signed_payload(self) -> bytes:
        """The bytes the signature is actually over. See :func:`signed_payload`."""
        return signed_payload(
            canonical_version=self.canonical_version,
            key_id=self.key_id,
            payload_type=self.payload_type,
            report_sha256=self.report_sha256,
            signed_at=self.signed_at,
        )


def signed_payload(
    *,
    canonical_version: int,
    key_id: str,
    payload_type: str,
    report_sha256: str,
    signed_at: datetime,
) -> bytes:
    """The bytes a signature is over.

    **Not the report's own bytes.** It is the report digest bound to the
    canonicalization version, the payload type, the key id and the signing
    time — so a signature cannot be lifted onto a different document, replayed
    under a different canonicalization, re-attributed to another key, or moved
    in time to predate a retirement. Signing the report bytes alone would leave
    all four open. The signing time stays a claim by whoever held the key, but
    it is now that signer's claim and nobody else's.

    A free function as well as a method, because :func:`sign` needs it before an
    :class:`Attestation` exists: the signature is one of that model's required
    fields, so building a placeholder with an empty one would make the illegal
    state representable to get at the payload.
    """
    return canonicalize(
        {
            "canonical_version": canonical_version,
            "key_id": key_id,
            "payload_type": payload_type,
            "report_sha256": report_sha256,
            "signed_at": signed_at.isoformat(),
        }
    )


def sign(
    report: Mapping[str, Any], private_key: Ed25519PrivateKey, *, key_id: str
) -> Attestation:
    """Sign one report's canonical form with this deployment's key."""
    report_sha256 = digest(report)
    signed_at = datetime.now(UTC)
    raw = private_key.sign(
        signed_payload(
            canonical_version=CANONICAL_VERSION,
            key_id=key_id,
            payload_type=PAYLOAD_TYPE,
            report_sha256=report_sha256,
            signed_at=signed_at,
        )
    )
    return Attestation(
        canonical_version=CANONICAL_VERSION,
        payload_type=PAYLOAD_TYPE,
        report_sha256=report_sha256,
        key_id=key_id,
        signature=base64.b64encode(raw).decode("ascii"),
        signed_at=signed_at,
    )


KeyStatus = Literal["active", "retired", "revoked"]


class VerificationKey(BaseModel):
    """One public key a verifier will accept, and the policy attached to it.

    Three statuses rather than two, because retirement and revocation are
    different events with different answers. A **retired** key stopped signing;
    what it signed before then is still good, and refusing it would invalidate
    every historical report on the day a deployment rotated. A **revoked** key
    was compromised; nothing it signed can be trusted, including what it signed
    before anyone noticed, because an attacker holding it could backdate.

    Collapsing them into one flag forces a deployment to choose between keeping
    history verifiable and being able to disown a leaked key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: str = Field(min_length=1)
    #: base64, raw Ed25519 public key (32 bytes).
    public_key: str = Field(min_length=1)
    status: KeyStatus
    #: When this key stopped signing. Required for a retired key and meaningless
    #: for an active one, so it is checked rather than merely typed.
    #: Aware, for the reason :attr:`Attestation.signed_at` is: the two are
    #: compared, and TOML's local date-time is naive and perfectly valid TOML.
    retired_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _retirement_carries_its_date(self) -> Self:
        if self.status == "retired" and self.retired_at is None:
            raise ValueError(
                f"{self.key_id}: a retired key needs retired_at, or a verifier"
                " cannot tell which signatures predate the rotation"
            )
        if self.status != "retired" and self.retired_at is not None:
            raise ValueError(f"{self.key_id}: retired_at is only for a retired key")
        return self

    def loaded(self) -> Ed25519PublicKey:
        try:
            return Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key))
        except (ValueError, TypeError) as exc:
            raise KeyringError(f"{self.key_id}: unusable public key") from exc


class Keyring(BaseModel):
    """The keys a verifier accepts, and nothing else.

    Held by the **verifier**, never shipped beside a report. A report that
    carried the key validating it would verify against itself, which is the
    whole failure signing exists to prevent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    keys: tuple[VerificationKey, ...]

    @model_validator(mode="after")
    def _each_key_is_named_once(self) -> Self:
        """No key id appears twice, because :meth:`get` would have to pick one.

        **This is the revocation path's fail-open.** An operator who revokes a
        key by *appending* a ``revoked`` entry rather than editing the existing
        one leaves two entries under one id; a first-match lookup then returns
        whichever the file happens to list first, and a revoked key verifies.
        The keyring cannot decide which entry an operator meant, so it refuses
        the file rather than resolving the ambiguity in the direction that
        grants trust.
        """
        seen = Counter(key.key_id for key in self.keys)
        repeated = sorted(key_id for key_id, count in seen.items() if count > 1)
        if repeated:
            raise ValueError(
                f"these key ids are named more than once: {', '.join(repeated)}."
                " One id is one key: to revoke a key, set status on the entry it"
                " already has rather than adding a second one."
            )
        return self

    def get(self, key_id: str) -> VerificationKey | None:
        return next((key for key in self.keys if key.key_id == key_id), None)


Verdict = Literal[
    # The signature is good and the key's policy allows it.
    "verified",
    # No attestation was supplied. Not a failure, and emphatically not trust.
    "unsigned",
    # The signature does not check out, or a covered byte moved.
    "invalid",
    # The signature is good, and the key was revoked. Deliberately distinct.
    "revoked",
    # This verifier does not implement the envelope's canonicalization.
    "unsupported",
    # The key id is not in this verifier's keyring.
    "unknown-key",
]


class Verification(BaseModel):
    """What a verifier concluded, and why.

    ``verdict`` is one of six and never a boolean, because the states a caller
    must act on differently are more than two: an **unsigned** report is an
    ordinary artifact from a deployment that does not sign, an **unknown-key**
    one may be perfectly good to somebody with a fuller keyring, and a
    **revoked** one is a signature that checks out and must still be refused.
    A boolean would collapse all three into "no" and teach a reader to treat
    them alike.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Verdict
    key_id: str | None = None
    signer_status: KeyStatus | None = None
    detail: str

    @property
    def trustworthy(self) -> bool:
        """Whether this report's **origin** is established. Nothing more.

        Not whether its findings are right, and not whether the run was
        certified. See the module docstring.
        """
        return self.verdict == "verified"


def verify(
    report: Mapping[str, Any],
    attestation: Attestation | None,
    keyring: Keyring,
) -> Verification:
    """Check one report against one attestation, under one keyring.

    Fails closed at every step, and each failure has its own verdict rather than
    a shared one: an unsupported canonicalization is not the same event as a bad
    signature, and an operator debugging the first should not be reading the
    message for the second.
    """
    if attestation is None:
        return Verification(
            verdict="unsigned",
            detail=(
                "no attestation was supplied. This report may be genuine; nothing"
                " here establishes that it is."
            ),
        )
    if attestation.canonical_version != CANONICAL_VERSION:
        return Verification(
            verdict="unsupported",
            key_id=attestation.key_id,
            detail=(
                f"the attestation uses canonicalization version"
                f" {attestation.canonical_version}; this verifier implements"
                f" {CANONICAL_VERSION} and will not guess at the difference"
            ),
        )
    if attestation.payload_type != PAYLOAD_TYPE:
        return Verification(
            verdict="unsupported",
            key_id=attestation.key_id,
            detail=f"unexpected payload type {attestation.payload_type!r}",
        )

    key = keyring.get(attestation.key_id)
    if key is None:
        return Verification(
            verdict="unknown-key",
            key_id=attestation.key_id,
            detail=(
                f"key {attestation.key_id!r} is not in this keyring. The"
                " signature may be good to somebody who holds it."
            ),
        )

    actual = digest(report)
    if actual != attestation.report_sha256:
        return Verification(
            verdict="invalid",
            key_id=key.key_id,
            signer_status=key.status,
            detail=(
                f"the report canonicalizes to {actual}, and the attestation"
                f" covers {attestation.report_sha256}: a covered field moved"
            ),
        )

    try:
        signature = base64.b64decode(attestation.signature, validate=True)
    except (ValueError, TypeError):
        return Verification(
            verdict="invalid",
            key_id=key.key_id,
            signer_status=key.status,
            detail="the signature is not valid base64",
        )
    if len(signature) != _SIGNATURE_LENGTH:
        return Verification(
            verdict="invalid",
            key_id=key.key_id,
            signer_status=key.status,
            detail=f"an Ed25519 signature is {_SIGNATURE_LENGTH} bytes,"
            f" not {len(signature)}",
        )
    try:
        key.loaded().verify(signature, attestation.signed_payload())
    except InvalidSignature:
        return Verification(
            verdict="invalid",
            key_id=key.key_id,
            signer_status=key.status,
            detail="the signature does not check out against this key",
        )

    if key.status == "revoked":
        # After the cryptographic check, not before: an operator needs to know
        # whether a revoked key really signed this, or whether somebody is
        # replaying its id over a forged document. Those are different incidents.
        return Verification(
            verdict="revoked",
            key_id=key.key_id,
            signer_status=key.status,
            detail=(
                f"the signature is cryptographically valid and key"
                f" {key.key_id!r} is revoked. Nothing it signed can be trusted,"
                " including signatures that predate the revocation."
            ),
        )
    retired_before = (
        key.status == "retired"
        and key.retired_at is not None
        and attestation.signed_at > key.retired_at
    )
    if retired_before and key.retired_at is not None:
        return Verification(
            verdict="invalid",
            key_id=key.key_id,
            signer_status=key.status,
            detail=(
                f"key {key.key_id!r} retired at {key.retired_at.isoformat()}"
                f" and this attestation claims {attestation.signed_at.isoformat()}"
            ),
        )

    return Verification(
        verdict="verified",
        key_id=key.key_id,
        signer_status=key.status,
        detail=(
            f"signed by {key.key_id!r} ({key.status}). This establishes origin"
            " and integrity only: it says nothing about whether the findings are"
            " correct, and nothing about whether the run was certified."
        ),
    )


def load_keyring(path: Path | str) -> Keyring:
    """Load and validate a verification keyring, fail closed.

    Every failure path raises: an unreadable file, invalid TOML, an unsupported
    version, a malformed key, or a retired key with no date. Never a silently
    empty keyring, which would answer ``unknown-key`` to everything and read as
    a configuration problem rather than as the absence of every key.
    """
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise KeyringError(f"{path}: cannot be read") from exc
    except tomllib.TOMLDecodeError as exc:
        raise KeyringError(f"{path}: invalid TOML: {exc}") from exc
    try:
        keyring = Keyring.model_validate(raw)
    except ValidationError as exc:
        raise KeyringError(f"{path}: {exc}") from exc
    if not keyring.keys:
        raise KeyringError(
            f"{path}: the keyring names no keys, so every attestation would read"
            " as unknown-key. Remove the file or add a key."
        )
    return keyring
