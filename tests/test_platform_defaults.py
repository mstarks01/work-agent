"""What a named platform always does is written where a description is silent (ADR 0082)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from analysis_service import graph
from analysis_service.platform_defaults import PLATFORM_DEFAULTS, with_platform_defaults
from analysis_service.system_model import UNKNOWN, Assumption, ExternalEntity
from analysis_service.validation import validate
from tests.factories import valid_model
from tests.test_graph import KEYS, FakeContext

STORE = "store:orders-db"


def _store_named(technology, encryption=UNKNOWN):
    model = valid_model()
    store = model.get(STORE)
    store.technology = technology
    store.encryption_at_rest = encryption
    return model


def test_a_named_service_fills_a_silent_attribute_with_its_basis():
    model = with_platform_defaults(_store_named("Amazon DynamoDB"))
    assert model.get(STORE).encryption_at_rest == "AWS-owned key (DynamoDB default)"
    (assumption,) = [a for a in model.assumptions if a.element_id == STORE]
    assert assumption.attribute == "encryption_at_rest"
    assert "cannot be disabled" in assumption.basis
    assert "docs.aws.amazon.com" in assumption.basis
    assert validate(model) == []


def test_a_stated_value_is_never_changed():
    model = with_platform_defaults(_store_named("S3 bucket", encryption="none"))
    assert model.get(STORE).encryption_at_rest == "none"
    assert not [a for a in model.assumptions if a.element_id == STORE]


@pytest.mark.parametrize(
    "technology",
    ["MinIO, S3-compatible", "an S3 emulator", "our cloud storage", "a pub/sub queue"],
)
def test_a_look_alike_or_a_generic_name_is_not_the_service(technology):
    model = with_platform_defaults(_store_named(technology))
    assert model.get(STORE).encryption_at_rest == UNKNOWN


def test_a_flow_to_a_named_service_takes_its_transport_defaults():
    model = valid_model()
    model.external_entities.append(
        ExternalEntity(
            id="entity:stripe",
            name="Stripe",
            kind="external-system",
            trust_zone=UNKNOWN,
            source_excerpt="payments go to Stripe",
            source_label=model.external_entities[0].source_label,
        )
    )
    flow = model.data_flows[0].model_copy(
        update={
            "id": "flow:process:web-app>entity:stripe>charge",
            "name": "charge",
            "source": "process:web-app",
            "destination": "entity:stripe",
            "authentication": UNKNOWN,
            "encryption_in_transit": UNKNOWN,
        }
    )
    model.data_flows.append(flow)
    charged = with_platform_defaults(model).get(flow.id)
    assert charged.encryption_in_transit == "HTTPS (Stripe requires it)"
    assert charged.authentication == "API key (Stripe requires it)"


def test_every_default_says_what_it_writes_and_where_the_vendor_says_so():
    """The table is checked, not trusted: a legal attribute for where it
    applies, a value, a basis, and an https source."""
    attributes = {
        "store": {"encryption_at_rest"},
        "flow-to": {"encryption_in_transit", "authentication"},
        "flow-protocol": {"encryption_in_transit", "authentication"},
    }
    for default in PLATFORM_DEFAULTS:
        assert default.attribute in attributes[default.applies_to], default
        assert default.value and default.value != UNKNOWN
        assert default.basis.endswith(".")
        assert urlparse(default.source).scheme == "https"
        assert all(name == name.lower() for name in default.names)


def test_the_gate_publishes_the_model_with_its_platform_defaults():
    """The default is written after the gate, into the model the analysis
    reads, and never into what extraction emitted."""
    ctx = FakeContext()
    emitted = valid_model().model_dump(mode="json")
    event = graph.validate_extraction(ctx, KEYS, emitted)
    assert event.actions.route == graph.ROUTE_VALID
    published = ctx.state[graph.STATE_VALID_MODEL]
    (store,) = published["data_stores"]
    assert store["encryption_at_rest"] == "AES-256 (Google Cloud default)"
    assert emitted["data_stores"][0]["encryption_at_rest"] == UNKNOWN


FLOW = "flow:entity:customer>process:web-app>login"


def _flow_over(protocol, *, inferred=False):
    model = valid_model()
    flow = model.get(FLOW)
    flow.protocol = protocol
    flow.encryption_in_transit = UNKNOWN
    flow.authentication = UNKNOWN
    if inferred:
        model.assumptions.append(
            Assumption(
                assumption="the flow is HTTPS",
                element_id=FLOW,
                attribute="protocol",
                basis="the model inferred it",
            )
        )
    return with_platform_defaults(model)


def test_a_stated_protocol_states_what_its_definition_guarantees():
    """SFTP is always encrypted, and WireGuard always authenticates its peers."""
    sftp = _flow_over("SFTP").get(FLOW)
    assert sftp.encryption_in_transit == "SSH transport encryption (intrinsic)"
    assert sftp.authentication == UNKNOWN, "SSH defines a 'none' client method"
    tunnel = _flow_over("WireGuard").get(FLOW)
    assert tunnel.encryption_in_transit == "ChaCha20-Poly1305 (WireGuard)"
    assert tunnel.authentication == "peer public keys (WireGuard)"
    assert _flow_over("HTTP/3").get(FLOW).encryption_in_transit.startswith("QUIC")


@pytest.mark.parametrize(
    "protocol", ["HTTP and HTTPS", "HTTP without TLS", "plain HTTP", "HTTP/2", "gRPC"]
)
def test_a_plain_sibling_or_a_negation_states_nothing(protocol):
    flow = _flow_over(protocol).get(FLOW)
    assert flow.encryption_in_transit == UNKNOWN


def test_an_inferred_protocol_states_nothing():
    """A property derived from a protocol nobody stated is an inference on an
    inference."""
    assert _flow_over("HTTPS", inferred=True).get(FLOW).encryption_in_transit == UNKNOWN


def test_every_protocol_the_extraction_prompt_names_is_a_row_of_the_table():
    """Two readers of one rule: the prompt asks the model to write it, and the
    table writes it where the model did not. They must name the same protocols."""
    rule = next(
        line
        for line in Path("prompts/extract.md").read_text(encoding="utf-8").splitlines()
        if "An encrypted protocol states" in line
    )
    encrypted, authenticating = set(), set()
    for default in PLATFORM_DEFAULTS:
        if default.applies_to != "flow-protocol":
            continue
        target = (
            encrypted
            if default.attribute == "encryption_in_transit"
            else authenticating
        )
        target.update(default.names)
    for name in ("HTTPS", "TLS", "mTLS", "WSS", "FTPS", "SSH", "SFTP"):
        assert name in rule and name.lower() in encrypted, name
    assert "mTLS also states `authentication`" in rule
    assert "mtls" in authenticating
