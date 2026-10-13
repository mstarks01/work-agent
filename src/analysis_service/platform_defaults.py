"""What a named platform or protocol always does, written where a description is silent.

Some services have a security property by design that nobody can turn off:
DynamoDB encrypts every table at rest, and Stripe refuses a request that is not
over HTTPS or carries no API key. A description that names the service has
said the property, even where it says nothing more, so asking the owner about
it again only spends an answer (ADR 0082).

**Only a property its vendor documents as always on, and impossible to
disable, is in the table.** A default that can be turned off, such as RDS
encryption or an SQS queue's encryption, is a guess about this system and stays
a question. So does a property the vendor documents for one route but not
all, such as TLS on every AWS API.

**A stated value always wins.** A default fills only an attribute the model
leaves ``unknown``, so "the bucket is public read" or "no authentication"
stands. Each value the table writes carries an :class:`Assumption` whose basis
names the vendor's statement and where it is documented, so a reader can tell
a platform default from a fact the description stated.

A name that marks a look-alike ("S3-compatible", an emulator, a self-hosted
copy) is never matched: the property belongs to the vendor's service, not to
its interface.

**A protocol's own definition is a default too** (``flow-protocol``): every
flow that names SFTP is encrypted, and every flow that names WireGuard
authenticates its peers, because the standard defines no other way to speak
it. Only a protocol the flow's own ``protocol`` field states counts, never one
the model inferred, and a field that also names a plain sibling ("HTTP and
HTTPS") or negates ("HTTP without TLS") counts for nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from analysis_service.analysis import control_state
from analysis_service.system_model import (
    Assumption,
    DataFlow,
    DataStore,
    SystemModel,
)

#: Words that mark a look-alike of a named service, which has none of its defaults.
_LOOK_ALIKE = re.compile(
    r"compatible|emulator|minio|localstack|self-hosted|on-prem", re.IGNORECASE
)

#: A protocol field that also names a plain sibling, or negates, states no
#: intrinsic property: "HTTP and HTTPS" carries some traffic unencrypted.
_PLAIN_OR_NEGATED = re.compile(
    r"(?<![\w-])(?:http(?!/3)|ftp|ldap|smtp|imap|pop3|mqtt|amqp|ws)(?![\w-])"
    r"|\b(?:no|not|without|plain|plaintext|unencrypted|disabled|optional)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlatformDefault:
    """One property a named service has by design and cannot turn off."""

    #: How a description names the service, matched as whole words.
    names: tuple[str, ...]
    #: ``store``: the named data store's own attribute. ``flow-to``: every
    #: flow whose destination is the named element. ``flow-protocol``: every
    #: flow whose own stated ``protocol`` names it.
    applies_to: Literal["store", "flow-to", "flow-protocol"]
    attribute: Literal["encryption_at_rest", "encryption_in_transit", "authentication"]
    value: str
    #: The vendor's statement, in our words, and where it is documented.
    basis: str
    source: str


PLATFORM_DEFAULTS: tuple[PlatformDefault, ...] = (
    PlatformDefault(
        names=("amazon s3", "aws s3", "s3"),
        applies_to="store",
        attribute="encryption_at_rest",
        value="SSE-S3 (Amazon S3 default)",
        basis="Amazon S3 encrypts every new object at rest, and since January"
        " 2023 the encryption cannot be turned off. Objects written before then"
        " can still be unencrypted.",
        source="https://docs.aws.amazon.com/AmazonS3/latest/userguide/default-encryption-faq.html",
    ),
    PlatformDefault(
        names=("dynamodb", "amazon dynamodb"),
        applies_to="store",
        attribute="encryption_at_rest",
        value="AWS-owned key (DynamoDB default)",
        basis="DynamoDB encrypts all table data at rest, including backups and"
        " streams, and the encryption cannot be disabled.",
        source="https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/encryption.usagenotes.html",
    ),
    PlatformDefault(
        names=("cloudwatch logs", "amazon cloudwatch logs"),
        applies_to="store",
        attribute="encryption_at_rest",
        value="AES-GCM (CloudWatch Logs default)",
        basis="CloudWatch Logs encrypts all log groups at rest by default.",
        source="https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/data-protection.html",
    ),
    PlatformDefault(
        names=("secrets manager", "aws secrets manager"),
        applies_to="store",
        attribute="encryption_at_rest",
        value="AWS KMS (Secrets Manager default)",
        basis="Secrets Manager encrypts every version of every secret with a"
        " KMS-protected data key.",
        source="https://docs.aws.amazon.com/kms/latest/developerguide/services-secrets-manager.html",
    ),
    PlatformDefault(
        names=(
            "google cloud storage",
            "gcs",
            "bigquery",
            "cloud sql",
            "firestore",
            "bigtable",
            "cloud spanner",
            "google pub/sub",
            "cloud pub/sub",
        ),
        applies_to="store",
        attribute="encryption_at_rest",
        value="AES-256 (Google Cloud default)",
        basis="Google Cloud encrypts all customer content at rest by default,"
        " with no action by the customer.",
        source="https://docs.cloud.google.com/docs/security/encryption/default-encryption",
    ),
    PlatformDefault(
        names=(
            "azure storage",
            "azure blob storage",
            "azure files",
            "azure managed disk",
            "cosmos db",
            "azure cosmos db",
        ),
        applies_to="store",
        attribute="encryption_at_rest",
        value="Azure Storage encryption (default)",
        basis="Azure encrypts storage accounts, managed disks and Cosmos DB at"
        " rest, and the encryption cannot be disabled.",
        source="https://learn.microsoft.com/en-us/azure/storage/common/storage-service-encryption",
    ),
    PlatformDefault(
        names=("snowflake",),
        applies_to="store",
        attribute="encryption_at_rest",
        value="Snowflake default encryption",
        basis="Snowflake encrypts all customer data at rest by default.",
        source="https://docs.snowflake.com/en/user-guide/security-encryption-end-to-end",
    ),
    PlatformDefault(
        names=("snowflake",),
        applies_to="flow-to",
        attribute="encryption_in_transit",
        value="TLS (Snowflake)",
        basis="Snowflake encrypts data in transit to and from the service with TLS.",
        source="https://docs.snowflake.com/en/user-guide/security-encryption-end-to-end",
    ),
    PlatformDefault(
        names=("stripe",),
        applies_to="flow-to",
        attribute="encryption_in_transit",
        value="HTTPS (Stripe requires it)",
        basis="The Stripe API takes requests over HTTPS only and refuses plain HTTP.",
        source="https://docs.stripe.com/api",
    ),
    PlatformDefault(
        names=("stripe",),
        applies_to="flow-to",
        attribute="authentication",
        value="API key (Stripe requires it)",
        basis="The Stripe API refuses a request that carries no API key.",
        source="https://docs.stripe.com/api/authentication",
    ),
    PlatformDefault(
        names=(
            "auth0",
            "okta",
            "microsoft entra id",
            "entra id",
            "azure ad",
            "azure active directory",
            "amazon cognito",
            "cognito",
        ),
        applies_to="flow-to",
        attribute="encryption_in_transit",
        value="TLS (OAuth 2.0 authorization server)",
        basis="OAuth 2.0 and OpenID Connect require the authorization server to"
        " use TLS on its authorization and token endpoints, and this hosted"
        " identity provider serves them over HTTPS only.",
        source="https://datatracker.ietf.org/doc/html/rfc6749#section-3.1",
    ),
)


#: The protocols whose own definition guarantees a property, as rows of the
#: same table. Each cites the standard that says so.
_PROTOCOL_DEFAULTS: tuple[PlatformDefault, ...] = (
    PlatformDefault(
        names=(
            "https",
            "tls",
            "mtls",
            "wss",
            "ftps",
            "ldaps",
            "smtps",
            "imaps",
            "pop3s",
            "mqtts",
            "amqps",
            "dtls",
        ),
        applies_to="flow-protocol",
        attribute="encryption_in_transit",
        value="TLS (intrinsic to the protocol)",
        basis="The protocol runs over TLS, which encrypts all application data;"
        " TLS 1.3 has no null cipher suite, and RFC 9325 forbids negotiating one.",
        source="https://datatracker.ietf.org/doc/html/rfc8446",
    ),
    PlatformDefault(
        names=("quic", "http/3"),
        applies_to="flow-protocol",
        attribute="encryption_in_transit",
        value="QUIC packet protection (TLS 1.3)",
        basis="QUIC protects every packet with keys from a TLS 1.3 handshake and"
        " defines no unprotected mode.",
        source="https://www.rfc-editor.org/info/rfc9001/",
    ),
    PlatformDefault(
        names=("ssh", "sftp", "scp"),
        applies_to="flow-protocol",
        attribute="encryption_in_transit",
        value="SSH transport encryption (intrinsic)",
        basis="SSH encrypts its transport; its 'none' cipher exists only for"
        " debugging (RFC 4251).",
        source="https://www.rfc-editor.org/rfc/rfc4253.html",
    ),
    PlatformDefault(
        names=("wireguard",),
        applies_to="flow-protocol",
        attribute="encryption_in_transit",
        value="ChaCha20-Poly1305 (WireGuard)",
        basis="WireGuard encrypts every data packet with authenticated"
        " encryption and has no unencrypted mode.",
        source="https://www.wireguard.com/papers/wireguard.pdf",
    ),
    PlatformDefault(
        names=("wireguard",),
        applies_to="flow-protocol",
        attribute="authentication",
        value="peer public keys (WireGuard)",
        basis="WireGuard identifies and authenticates each peer by its static"
        " public key before any data moves.",
        source="https://www.wireguard.com/papers/wireguard.pdf",
    ),
    PlatformDefault(
        names=("webrtc",),
        applies_to="flow-protocol",
        attribute="encryption_in_transit",
        value="DTLS-SRTP (WebRTC requires it)",
        basis="WebRTC must secure all media with SRTP keyed by DTLS and all data"
        " channels with DTLS, and may not negotiate null encryption.",
        source="https://www.rfc-editor.org/rfc/rfc8827.html",
    ),
    PlatformDefault(
        names=("mtls", "mutual tls"),
        applies_to="flow-protocol",
        attribute="authentication",
        value="client certificates (mTLS)",
        basis="Mutual TLS authenticates the client by its certificate as part of"
        " the TLS handshake.",
        source="https://datatracker.ietf.org/doc/html/rfc8446#section-4.4.2",
    ),
    PlatformDefault(
        names=("kerberos",),
        applies_to="flow-protocol",
        attribute="authentication",
        value="Kerberos tickets",
        basis="Kerberos is an authentication protocol: a client proves its"
        " identity with a ticket from the key distribution center.",
        source="https://datatracker.ietf.org/doc/html/rfc4120",
    ),
)

PLATFORM_DEFAULTS = PLATFORM_DEFAULTS + _PROTOCOL_DEFAULTS


def _names_service(text: str, names: tuple[str, ...]) -> bool:
    """Whether ``text`` names one of ``names`` as whole words, and no look-alike."""
    if _LOOK_ALIKE.search(text):
        return False
    lowered = text.lower()
    return any(
        re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered) for name in names
    )


def _named(element: object) -> str:
    return " ".join(
        str(getattr(element, field, "")) for field in ("name", "technology")
    )


def _targets(
    model: SystemModel, default: PlatformDefault
) -> Iterator[DataStore | DataFlow]:
    """The elements whose attribute this default may fill."""
    if default.applies_to == "store":
        for store in model.data_stores:
            if _names_service(_named(store), default.names):
                yield store
        return
    if default.applies_to == "flow-protocol":
        inferred = {
            a.element_id for a in model.assumptions if a.attribute == "protocol"
        }
        for flow in model.data_flows:
            stated = flow.id not in inferred and not _PLAIN_OR_NEGATED.search(
                flow.protocol
            )
            if stated and _names_service(flow.protocol, default.names):
                yield flow
        return
    services = {
        element.id
        for element in model.elements()
        if _names_service(_named(element), default.names)
    }
    for flow in model.data_flows:
        if flow.destination in services:
            yield flow


def with_platform_defaults(model: SystemModel) -> SystemModel:
    """The model with every platform default it names written where it is silent.

    **The one writer of a platform default.** Each value it writes takes one
    :class:`Assumption`, so the gate's rule that an assumption names a value
    holds, and a value the model already states is never touched.
    """
    updated = model.model_copy(deep=True)
    for default in PLATFORM_DEFAULTS:
        for element in _targets(updated, default):
            if control_state(getattr(element, default.attribute)) != "unverified":
                continue
            setattr(element, default.attribute, default.value)
            updated.assumptions.append(
                Assumption(
                    assumption=f"{element.name}: {default.value}, a platform default"
                    " that cannot be turned off.",
                    element_id=element.id,
                    attribute=default.attribute,
                    basis=f"{default.basis} {default.source}",
                )
            )
    return updated
