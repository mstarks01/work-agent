"""Capabilities a closed field of the model proves are written as present (ADR 0085)."""

from __future__ import annotations

import pytest

from analysis_service import graph
from analysis_service.capabilities import CAPABILITIES
from analysis_service.field_capabilities import (
    FIELD_CAPABILITIES,
    with_field_capabilities,
)
from analysis_service.system_model import (
    UNKNOWN,
    Assumption,
    CapabilityStatement,
    DataFlow,
    ExternalEntity,
)
from analysis_service.validation import validate
from tests.factories import valid_model
from tests.test_graph import KEYS, FakeContext

LOGIN = "flow:entity:customer>process:web-app>login"
STORE = "flow:process:web-app>store:orders-db>store-order"


def _model(**protocols):
    """The shared model, with no asset tags and no exposure, and each named
    flow's protocol set."""
    model = valid_model()
    for element in model.elements():
        element.assets = []
    model.get("process:web-app").exposure = "internal"
    for flow_id, protocol in protocols.items():
        model.get(flow_id).protocol = protocol
    return model


def _written(model):
    return {
        statement.capability: statement
        for statement in with_field_capabilities(model).capabilities
    }


def test_the_example_rows_write_present_with_their_basis_and_a_quote_the_gate_accepts():
    model = valid_model()
    written = with_field_capabilities(model)
    statements = {s.capability: s for s in written.capabilities}
    assert set(statements) == {
        "tls-use",
        "cryptographic-operations",
        "cryptographic-keys",
        "sensitive-data",
        "external-facing-http-services",
    }
    assert {s.state for s in statements.values()} == {"present"}
    login = model.get(LOGIN)
    assert statements["tls-use"].source_excerpt == login.source_excerpt
    assert statements["tls-use"].basis.startswith(f"{login.name}: ")
    assert validate(written) == []
    (evidence,) = written.capability_facts()["tls-use"].evidence
    assert evidence.endswith(f"({statements['tls-use'].basis})")


@pytest.mark.parametrize(
    ("protocols", "expected"),
    [
        (
            {STORE: "TLS"},
            {"tls-client", "internal-tls-connections", "internal-services"},
        ),
        ({STORE: "HTTP"}, {"internal-http-connections", "internal-services"}),
        ({STORE: "gRPC"}, {"http2-messages"}),
        ({LOGIN: "gRPC"}, {"http2-messages", "http2-request-reception"}),
        ({LOGIN: "WSS"}, {"websocket"}),
        ({LOGIN: "SOAP over HTTP"}, {"xml-parsing"}),
    ],
)
def test_a_stated_protocol_proves_the_rows_its_family_and_its_ends_prove(
    protocols, expected
):
    written = set(_written(_model(**{LOGIN: "SFTP", STORE: "SFTP"} | protocols)))
    tls = {"tls-use", "cryptographic-operations", "cryptographic-keys"}
    assert written - tls == expected


def test_sensitive_tags_on_an_http_flow_prove_sensitive_requests():
    model = _model(**{LOGIN: "HTTP"})
    model.get(LOGIN).assets = ["credentials"]
    assert "sensitive-http-requests" in _written(model)
    model.get(LOGIN).protocol = "SFTP"
    assert "sensitive-http-requests" not in _written(model)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["pii"], {"sensitive-data"}),
        (["secrets"], {"sensitive-data", "secret-assets"}),
        (["business-critical-data"], set()),
    ],
)
def test_a_tag_on_a_process_or_a_store_proves_what_it_names(tags, expected):
    model = _model(**{LOGIN: "SFTP", STORE: "SFTP"})
    model.get("store:orders-db").assets = tags
    assert (
        set(_written(model))
        - {
            "tls-use",
            "cryptographic-operations",
            "cryptographic-keys",
            "tls-client",
            "internal-tls-connections",
            "internal-services",
        }
        == expected
    )


def test_a_tag_on_an_external_entity_proves_nothing():
    model = _model(**{LOGIN: "plain text", STORE: "plain text"})
    model.get("entity:customer").assets = ["pii"]
    assert _written(model) == {}


def test_a_flow_to_an_external_system_proves_a_separate_service_connection():
    model = _model()
    model.external_entities.append(
        ExternalEntity(
            id="entity:stripe",
            name="Stripe",
            kind="external-system",
            trust_zone="boundary:internet",
            source_excerpt=model.get(LOGIN).source_excerpt,
            source_label=model.get(LOGIN).source_label,
        )
    )
    model.data_flows.append(
        model.get(LOGIN).model_copy(
            update={
                "id": "flow:process:web-app>entity:stripe>pay",
                "name": "pay",
                "source": "process:web-app",
                "destination": "entity:stripe",
                "protocol": UNKNOWN,
            }
        )
    )
    assert "separate-service-connections" in _written(model)


def test_an_internet_facing_web_process_proves_an_external_http_service():
    model = _model()
    process = model.get("process:web-app")
    process.exposure = "internet-facing"
    assert "external-facing-http-services" in _written(model)
    process.interface_kind = "non-web"
    assert "external-facing-http-services" not in _written(model)


def test_a_stated_encryption_at_rest_proves_encryption_and_an_assumed_one_does_not():
    model = _model()
    model.get("store:orders-db").encryption_at_rest = "AES-256 (KMS)"
    assert "encryption" in _written(model)
    model.assumptions.append(
        Assumption(
            assumption="the database is encrypted",
            element_id="store:orders-db",
            attribute="encryption_at_rest",
            basis="the model inferred it",
        )
    )
    assert "encryption" not in _written(model)


@pytest.mark.parametrize("protocol", ["HTTP and HTTPS", "HTTPS without TLS 1.3"])
def test_a_plain_sibling_or_a_negation_proves_no_tls(protocol):
    assert "tls-use" not in _written(_model(**{LOGIN: protocol}))


def test_an_assumed_protocol_proves_nothing():
    model = _model(**{LOGIN: "HTTPS"})
    model.assumptions.append(
        Assumption(
            assumption="the login is HTTPS",
            element_id=LOGIN,
            attribute="protocol",
            basis="the model inferred it",
        )
    )
    assert "tls-use" not in _written(model)


def test_a_capability_a_statement_names_is_left_as_it_is():
    model = _model(**{LOGIN: "HTTPS"})
    answer = CapabilityStatement(
        capability="tls-use",
        state="absent",
        source_excerpt="no",
        source_label="Answers",
    )
    model.capabilities.append(answer)
    assert _written(model)["tls-use"] == answer


def test_a_proof_with_no_quote_writes_nothing():
    model = _model(**{LOGIN: "HTTPS"})
    login: DataFlow = model.get(LOGIN)
    login.source_excerpt = ""
    assert "tls-use" not in _written(model)


def test_every_row_names_a_capability_of_the_table_once():
    names = [row.capability for row in FIELD_CAPABILITIES]
    assert len(names) == len(set(names))
    assert set(names) <= set(CAPABILITIES)
    assert all(row.basis and not row.basis.endswith(".") for row in FIELD_CAPABILITIES)


def test_the_gate_publishes_the_model_with_its_field_capabilities():
    """The statements are written after the gate, into the model the analysis
    reads, and never into what extraction emitted."""
    payload = valid_model().model_dump(mode="json")
    ctx = FakeContext()
    event = graph.validate_extraction(ctx, KEYS, payload)
    assert event.actions.route == graph.ROUTE_VALID
    published = ctx.state[graph.STATE_VALID_MODEL]["capabilities"]
    assert "tls-use" in {statement["capability"] for statement in published}
    assert "capabilities" not in payload or payload["capabilities"] == []


def test_a_second_pass_writes_nothing_more():
    """A resumed or amended job passes the gate again."""
    once = with_field_capabilities(valid_model())
    assert with_field_capabilities(once) == once


def test_the_sensitive_rows_read_the_disclosure_tags_but_the_most_over_tagged():
    from analysis_service.analysis import SENSITIVE_ASSET_TAGS
    from analysis_service.field_capabilities import SECRET_TAGS, SENSITIVE_TAGS

    assert SENSITIVE_ASSET_TAGS - SENSITIVE_TAGS == {"business-critical-data"}
    assert SECRET_TAGS <= SENSITIVE_TAGS
