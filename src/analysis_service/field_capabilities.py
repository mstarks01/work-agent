"""Capabilities that a closed field of the model proves, written where nothing states them.

A flow that states HTTPS between two of the application's own parts proves
that the application uses TLS, and a process that is internet-facing with a
web interface proves that it exposes an HTTP service. Asking the owner about
either one only spends an answer (ADR 0085).

**Only a field with a closed set of values proves a row**: an element's kind,
a process's exposure and interface, the asset tags, a stated protocol from the
protocol table's families, and a control state. A row never reads free text
such as a technology name.

**A row only marks a capability present.** A model is silent about most of
what an application lacks, so no field proves an absence, and a wrong
"present" only makes a requirement apply. **A statement always wins**: a
capability an answer or another statement names is left as it is, and a
later answer replaces the statement this module wrote. Each statement quotes
the first element that proves it and names the element and the rule in
:attr:`~analysis_service.system_model.CapabilityStatement.basis`.

The application's own parts are the model's processes and data stores.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from analysis_service.analysis import SENSITIVE_ASSET_TAGS, control_state
from analysis_service.protocol_defaults import (
    QUIC_NAMES,
    TLS_NAMES,
    names_protocol,
    stated_protocol_flows,
    states_inherently,
)
from analysis_service.system_model import (
    CapabilityStatement,
    DataFlow,
    DataStore,
    Element,
    Process,
    SystemModel,
)

#: The TLS family: every protocol that runs over TLS or a TLS 1.3 handshake.
TLS_FAMILY = (*TLS_NAMES, *QUIC_NAMES)
#: The HTTP family, plain or over TLS.
HTTP_FAMILY = ("http", "https", "http/2", "http/3", "h2", "h3")
#: HTTP/2, and gRPC, which is defined over it.
HTTP2_NAMES = ("http/2", "h2", "grpc")
WEBSOCKET_NAMES = ("ws", "wss", "websocket", "websockets")
#: Protocols whose messages are XML.
XML_NAMES = ("soap", "xmpp")
#: The asset tags whose disclosure is a loss, less ``business-critical-data``:
#: extraction adds that tag where a reference has none more than any other
#: (``QA-2026-10-12-01-E10``), so a row that read it would state a capability
#: on the weakest evidence the model holds.
SENSITIVE_TAGS = SENSITIVE_ASSET_TAGS - {"business-critical-data"}
#: The two tags that name secrets.
SECRET_TAGS = frozenset({"credentials", "secrets"})


@dataclass(frozen=True)
class _Parts:
    """What the rows read off one model."""

    flows: tuple[DataFlow, ...]
    stated: tuple[DataFlow, ...]
    processes: tuple[Process, ...]
    stores: tuple[DataStore, ...]
    own: frozenset[str]
    process_ids: frozenset[str]
    store_ids: frozenset[str]
    systems: frozenset[str]
    assumed: frozenset[tuple[str, str]]

    @classmethod
    def of(cls, model: SystemModel) -> _Parts:
        processes = tuple(model.processes)
        stores = tuple(model.data_stores)
        return cls(
            flows=tuple(model.data_flows),
            stated=tuple(stated_protocol_flows(model)),
            processes=processes,
            stores=stores,
            own=frozenset(e.id for e in (*processes, *stores)),
            process_ids=frozenset(p.id for p in processes),
            store_ids=frozenset(s.id for s in stores),
            systems=frozenset(
                e.id for e in model.external_entities if e.kind == "external-system"
            ),
            assumed=frozenset((a.element_id, a.attribute) for a in model.assumptions),
        )

    def ends(self, flow: DataFlow) -> frozenset[str]:
        return frozenset({flow.source, flow.destination})


def _tls(parts: _Parts) -> Iterator[DataFlow]:
    """Stated TLS-family flows with one end that is the application's own."""
    for flow in parts.stated:
        if states_inherently(flow, TLS_FAMILY) and parts.ends(flow) & parts.own:
            yield flow


def _tls_client(parts: _Parts) -> Iterator[DataFlow]:
    return (flow for flow in _tls(parts) if flow.source in parts.process_ids)


def _internal(parts: _Parts, names: tuple[str, ...]) -> Iterator[DataFlow]:
    """Stated flows of a family between two of the application's own parts."""
    for flow in parts.stated:
        if parts.ends(flow) <= parts.own and names_protocol(flow.protocol, names):
            yield flow


def _internal_tls(parts: _Parts) -> Iterator[DataFlow]:
    for flow in parts.stated:
        if parts.ends(flow) <= parts.own and states_inherently(flow, TLS_FAMILY):
            yield flow


def _internal_service(parts: _Parts) -> Iterator[DataFlow]:
    """A process and a data store that talk over a TLS-family or HTTP-family protocol."""
    for flow in _internal(parts, (*TLS_FAMILY, *HTTP_FAMILY)):
        ends = parts.ends(flow)
        if ends & parts.process_ids and ends & parts.store_ids:
            yield flow


def _sensitive_http(parts: _Parts) -> Iterator[DataFlow]:
    for flow in parts.stated:
        own_end = parts.ends(flow) & parts.own
        sensitive = set(flow.assets) & SENSITIVE_TAGS
        if own_end and sensitive and names_protocol(flow.protocol, HTTP_FAMILY):
            yield flow


def _to_system(parts: _Parts) -> Iterator[DataFlow]:
    for flow in parts.flows:
        if flow.source in parts.process_ids and flow.destination in parts.systems:
            yield flow


def _named(
    names: tuple[str, ...], ends: Callable[[_Parts, DataFlow], bool]
) -> Callable[[_Parts], Iterator[DataFlow]]:
    """Stated flows that name one of ``names``, where ``ends`` holds."""

    def proof(parts: _Parts) -> Iterator[DataFlow]:
        for flow in parts.stated:
            if names_protocol(flow.protocol, names) and ends(parts, flow):
                yield flow

    return proof


def _own_end(parts: _Parts, flow: DataFlow) -> bool:
    return bool(parts.ends(flow) & parts.own)


def _process_end(parts: _Parts, flow: DataFlow) -> bool:
    return bool(parts.ends(flow) & parts.process_ids)


def _process_destination(parts: _Parts, flow: DataFlow) -> bool:
    return flow.destination in parts.process_ids


def _tagged(tags: frozenset[str]) -> Callable[[_Parts], Iterator[Element]]:
    """The processes and data stores tagged with one of ``tags``."""

    def proof(parts: _Parts) -> Iterator[Element]:
        owned: tuple[Process | DataStore, ...] = (*parts.processes, *parts.stores)
        for element in owned:
            if set(element.assets) & tags:
                yield element

    return proof


def _web_on_internet(parts: _Parts) -> Iterator[Process]:
    for process in parts.processes:
        if process.exposure == "internet-facing" and process.interface_kind == "web":
            yield process


def _encrypted_store(parts: _Parts) -> Iterator[DataStore]:
    """Data stores whose encryption at rest is stated, and not assumed."""
    for store in parts.stores:
        assumed = (store.id, "encryption_at_rest") in parts.assumed
        if not assumed and control_state(store.encryption_at_rest) == "stated":
            yield store


@dataclass(frozen=True)
class FieldCapability:
    """One capability that a closed field of the model proves present."""

    capability: str
    #: The elements whose fields prove it, in the model's order.
    proof: Callable[[_Parts], Iterator[Element]]
    #: Why the fields prove it, in our words.
    basis: str


FIELD_CAPABILITIES: tuple[FieldCapability, ...] = (
    FieldCapability(
        "tls-use",
        _tls,
        "a TLS-family protocol on a flow with an end in the application",
    ),
    FieldCapability(
        "tls-client",
        _tls_client,
        "a process starts a TLS-family flow, so it is the TLS client",
    ),
    FieldCapability(
        "cryptographic-operations",
        _tls,
        "TLS is cryptography, on a flow with an end in the application",
    ),
    FieldCapability(
        "cryptographic-keys",
        _tls,
        "TLS makes and uses session keys, on a flow with an end in the application",
    ),
    FieldCapability(
        "internal-tls-connections",
        _internal_tls,
        "two parts of the application talk over a TLS-family protocol",
    ),
    FieldCapability(
        "internal-http-connections",
        lambda parts: _internal(parts, HTTP_FAMILY),
        "two parts of the application talk over an HTTP-family protocol",
    ),
    FieldCapability(
        "internal-services",
        _internal_service,
        "a process and a data store of the application talk over a network protocol",
    ),
    FieldCapability(
        "sensitive-http-requests",
        _sensitive_http,
        "an HTTP-family flow of the application carries data tagged as sensitive",
    ),
    FieldCapability(
        "separate-service-connections",
        _to_system,
        "a process connects to an external system",
    ),
    FieldCapability(
        "websocket",
        _named(WEBSOCKET_NAMES, _own_end),
        "a flow of the application states WebSocket",
    ),
    FieldCapability(
        "xml-parsing",
        _named(XML_NAMES, _process_end),
        "a process speaks a protocol whose messages are XML",
    ),
    FieldCapability(
        "http2-messages",
        _named(HTTP2_NAMES, _own_end),
        "a flow of the application states HTTP/2, or gRPC, which runs over it",
    ),
    FieldCapability(
        "http2-request-reception",
        _named(HTTP2_NAMES, _process_destination),
        "a process receives HTTP/2, or gRPC, which runs over it",
    ),
    FieldCapability(
        "sensitive-data",
        _tagged(SENSITIVE_TAGS),
        "a process or a data store holds data tagged as sensitive",
    ),
    FieldCapability(
        "secret-assets",
        _tagged(SECRET_TAGS),
        "a process or a data store holds data tagged as credentials or secrets",
    ),
    FieldCapability(
        "external-facing-http-services",
        _web_on_internet,
        "a process with a web interface is internet-facing",
    ),
    FieldCapability(
        "encryption",
        _encrypted_store,
        "a data store states its encryption at rest",
    ),
)


def with_field_capabilities(model: SystemModel) -> SystemModel:
    """The model with each capability its closed fields prove written as present.

    **The one writer of a field capability.** A capability that a statement
    already names is left as it is. A row whose proving elements carry no
    quote writes nothing, because a statement must quote its source.
    """
    named = {statement.capability for statement in model.capabilities}
    parts = _Parts.of(model)
    updated = model.model_copy(deep=True)
    for row in FIELD_CAPABILITIES:
        if row.capability in named:
            continue
        cited = next(
            (e for e in row.proof(parts) if e.source_excerpt and e.source_label),
            None,
        )
        if cited is None:
            continue
        updated.capabilities.append(
            CapabilityStatement(
                capability=row.capability,
                state="present",
                source_excerpt=cited.source_excerpt,
                source_label=cited.source_label,
                basis=f"{cited.name}: {row.basis}",
            )
        )
    return updated
