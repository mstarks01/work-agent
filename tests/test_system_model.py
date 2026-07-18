"""Tests for the canonical System Model schema and derived boundary crossings."""

import pytest
from pydantic import ValidationError

from stride_service.system_model import (
    DataFlow,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
    make_element_id,
    make_flow_id,
    normalize_name,
)
from tests.factories import valid_model


class TestIdentity:
    def test_normalize_name_slugifies(self):
        assert normalize_name("Auth Service") == "auth-service"
        assert normalize_name("  API / Gateway v2  ") == "api-gateway-v2"

    def test_normalize_name_rejects_empty_slug(self):
        with pytest.raises(ValueError):
            normalize_name("!!!")

    def test_make_element_id_is_deterministic_from_type_and_name(self):
        assert make_element_id("process", "Auth Service") == "process:auth-service"

    def test_make_flow_id_strips_endpoint_prefixes(self):
        flow_id = make_flow_id("entity:user", "process:web-app", "Login")
        assert flow_id == "flow:user-to-web-app:login"


class TestSchema:
    def test_extra_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            SystemModel.model_validate({"components": []})

    def test_unknown_is_a_legal_security_attribute_value(self):
        model = valid_model()
        store = model.data_stores[0]
        assert store.encryption_at_rest == "unknown"

    def test_illegal_enum_value_is_rejected(self):
        with pytest.raises(ValidationError):
            TrustBoundary(
                id="boundary:dmz", name="DMZ", kind="firewall", source_excerpt="dmz"
            )

    def test_exposure_accepts_unknown(self):
        process = Process(
            id="process:batch-job",
            name="Batch Job",
            technology="unknown",
            trust_zone="boundary:internal-network",
            exposure="unknown",
        )
        assert process.exposure == "unknown"

    def test_roundtrips_through_json(self):
        model = valid_model()
        assert SystemModel.model_validate_json(model.model_dump_json()) == model


class TestLookups:
    def test_get_finds_elements_of_every_type(self):
        model = valid_model()
        assert isinstance(model.get("entity:customer"), ExternalEntity)
        assert isinstance(model.get("flow:customer-to-web-app:login"), DataFlow)
        assert model.get("process:nonexistent") is None

    def test_elements_returns_all_in_stable_order(self):
        model = valid_model()
        ids = [element.id for element in model.elements()]
        assert len(ids) == len(set(ids))
        assert ids[0] == "entity:customer"
        assert ids[-1] == "boundary:internal-network"


class TestBoundaryCrossings:
    def test_cross_zone_flow_is_derived_as_crossing(self):
        crossings = valid_model().boundary_crossings()
        assert [crossing.flow_id for crossing in crossings] == [
            "flow:customer-to-web-app:login"
        ]
        assert crossings[0].source_zone == "boundary:internet"
        assert crossings[0].destination_zone == "boundary:internal-network"

    def test_same_zone_flow_is_not_a_crossing(self):
        crossing_ids = {c.flow_id for c in valid_model().boundary_crossings()}
        assert "flow:web-app-to-orders-db:store-order" not in crossing_ids

    def test_dangling_endpoint_fails_closed(self):
        model = valid_model()
        model.data_flows[0].source = "entity:ghost"
        with pytest.raises(ValueError, match="entity:ghost"):
            model.boundary_crossings()
