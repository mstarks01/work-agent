"""What a protocol guarantees by its own definition, written where a description is silent.

A flow that names SFTP is encrypted, and one that names WireGuard
authenticates its peers, because the standard defines no other way to speak
the protocol. A description that names it has said the property, so asking
the owner about it again only spends an answer (ADR 0082). Each row cites the
standard that defines it, and standards of this kind change seldom.

**Only a property the standard makes inherent is a row.** A property a
protocol only can have, such as TLS on gRPC or on HTTP/2, is a question about
this system. A vendor's default is never a row, however hard it is to turn off
(ADR 0083): it is the vendor's policy, not the protocol's design, and it
changes.

**Only a protocol the flow states counts.** A protocol the model recorded as
an assumption states nothing, and neither does a ``protocol`` field that also
names a plain sibling ("HTTP and HTTPS", HTTP/2) or negates ("HTTP without
TLS"). **A stated value always wins**: a row fills only an attribute whose
control state is unverified. Each value it writes carries an
:class:`Assumption` whose basis cites the standard, so a reader can tell a
protocol default from a fact the description stated.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from analysis_service.analysis import control_state
from analysis_service.system_model import Assumption, DataFlow, SystemModel

#: A protocol field that also names a plain sibling states no inherent
#: property: "HTTP and HTTPS" carries some traffic unencrypted.
PLAIN = re.compile(
    r"(?<![\w-])(?:http(?!/3)|ftp|ldap|smtp|imap|pop3|mqtt|amqp|ws)(?![\w-])"
    r"|\b(?:plain|plaintext|unencrypted)\b",
    re.IGNORECASE,
)

#: A protocol field that negates states no protocol it names: "HTTPS, not
#: HTTP/2" does not state HTTP/2.
NEGATED = re.compile(r"\b(?:no|not|without|disabled|optional)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ProtocolDefault:
    """One property a protocol has by its own definition."""

    #: How a flow's ``protocol`` field names the protocol, matched as whole words.
    names: tuple[str, ...]
    attribute: Literal["encryption_in_transit", "authentication"]
    value: str
    #: The standard's statement, in our words, and where it is defined.
    basis: str
    source: str


#: The protocols that run over TLS, and the two that protect every packet
#: with a TLS 1.3 handshake. Together they are the TLS family.
TLS_NAMES = (
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
)
QUIC_NAMES = ("quic", "http/3")

PROTOCOL_DEFAULTS: tuple[ProtocolDefault, ...] = (
    ProtocolDefault(
        names=TLS_NAMES,
        attribute="encryption_in_transit",
        value="TLS (intrinsic to the protocol)",
        basis="The protocol runs over TLS, which encrypts all application data;"
        " TLS 1.3 has no null cipher suite, and RFC 9325 forbids negotiating one.",
        source="https://datatracker.ietf.org/doc/html/rfc8446",
    ),
    ProtocolDefault(
        names=QUIC_NAMES,
        attribute="encryption_in_transit",
        value="QUIC packet protection (TLS 1.3)",
        basis="QUIC protects every packet with keys from a TLS 1.3 handshake and"
        " defines no unprotected mode.",
        source="https://www.rfc-editor.org/info/rfc9001/",
    ),
    ProtocolDefault(
        names=("ssh", "sftp", "scp"),
        attribute="encryption_in_transit",
        value="SSH transport encryption (intrinsic)",
        basis="SSH encrypts its transport; its 'none' cipher exists only for"
        " debugging (RFC 4251).",
        source="https://www.rfc-editor.org/rfc/rfc4253.html",
    ),
    ProtocolDefault(
        names=("wireguard",),
        attribute="encryption_in_transit",
        value="ChaCha20-Poly1305 (WireGuard)",
        basis="WireGuard encrypts every data packet with authenticated"
        " encryption and has no unencrypted mode.",
        source="https://www.wireguard.com/papers/wireguard.pdf",
    ),
    ProtocolDefault(
        names=("wireguard",),
        attribute="authentication",
        value="peer public keys (WireGuard)",
        basis="WireGuard identifies and authenticates each peer by its static"
        " public key before any data moves.",
        source="https://www.wireguard.com/papers/wireguard.pdf",
    ),
    ProtocolDefault(
        names=("webrtc",),
        attribute="encryption_in_transit",
        value="DTLS-SRTP (WebRTC requires it)",
        basis="WebRTC must secure all media with SRTP keyed by DTLS and all data"
        " channels with DTLS, and may not negotiate null encryption.",
        source="https://www.rfc-editor.org/rfc/rfc8827.html",
    ),
    ProtocolDefault(
        names=("mtls", "mutual tls"),
        attribute="authentication",
        value="client certificates (mTLS)",
        basis="Mutual TLS authenticates the client by its certificate as part of"
        " the TLS handshake.",
        source="https://datatracker.ietf.org/doc/html/rfc8446#section-4.4.2",
    ),
    ProtocolDefault(
        names=("kerberos",),
        attribute="authentication",
        value="Kerberos tickets",
        basis="Kerberos is an authentication protocol: a client proves its"
        " identity with a ticket from the key distribution center.",
        source="https://datatracker.ietf.org/doc/html/rfc4120",
    ),
)


def names_protocol(text: str, names: tuple[str, ...]) -> bool:
    """Whether ``text`` names one of ``names`` as whole words."""
    lowered = text.lower()
    return any(
        re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered) for name in names
    )


def stated_protocol_flows(model: SystemModel) -> Iterator[DataFlow]:
    """The flows whose protocol the sources state, not one the model assumed.

    **The one reader of whether a flow's protocol counts.**
    """
    inferred = {a.element_id for a in model.assumptions if a.attribute == "protocol"}
    return (flow for flow in model.data_flows if flow.id not in inferred)


def states_protocol(flow: DataFlow, names: tuple[str, ...]) -> bool:
    """Whether ``flow`` names one of ``names`` and no negation."""
    negated = NEGATED.search(flow.protocol)
    return not negated and names_protocol(flow.protocol, names)


def states_inherently(flow: DataFlow, names: tuple[str, ...]) -> bool:
    """Whether ``flow`` states one of ``names`` and names no plain sibling."""
    plain = PLAIN.search(flow.protocol)
    return not plain and states_protocol(flow, names)


def _flows(model: SystemModel, default: ProtocolDefault) -> Iterator[DataFlow]:
    """The flows whose own stated protocol is this row's protocol."""
    for flow in stated_protocol_flows(model):
        if states_inherently(flow, default.names):
            yield flow


def with_protocol_defaults(model: SystemModel) -> SystemModel:
    """The model with what each stated protocol guarantees written where it is silent.

    **The one writer of a protocol default.** Each value it writes takes one
    :class:`Assumption`, so the gate's rule that an assumption names a value
    holds, and a value the model already states is never touched.
    """
    updated = model.model_copy(deep=True)
    for default in PROTOCOL_DEFAULTS:
        for flow in _flows(updated, default):
            if control_state(getattr(flow, default.attribute)) != "unverified":
                continue
            setattr(flow, default.attribute, default.value)
            updated.assumptions.append(
                Assumption(
                    assumption=f"{flow.name}: {default.value}, which the stated"
                    " protocol guarantees by its own definition.",
                    element_id=flow.id,
                    attribute=default.attribute,
                    basis=f"{default.basis} {default.source}",
                )
            )
    return updated
