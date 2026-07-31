"""Tests for the canonical System Model schema and derived boundary crossings."""

import pytest
from pydantic import ValidationError

from stride_service.system_model import (
    DataFlow,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
    derive_element_id,
    make_element_id,
    make_flow_id,
    normalize_element_ids,
    normalize_name,
)
from stride_service.validation import validate
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


class TestNormalizeElementIds:
    """IDs are derived in code, and references follow."""

    def abbreviated(self) -> SystemModel:
        """The shape all 21 corpus mismatches took: a short ID, a longer name.

        The model reads the source correctly, names the element from it, then
        applies the ID rule to a shorter name than the one it emits — and the
        references it writes cite the short ID consistently.
        """
        model = valid_model()
        model.processes[0].name = "Web App Frontend Service"
        model.assumptions[0].element_id = "process:web-app"
        return model

    def test_derive_element_id_covers_both_shapes(self):
        model = valid_model()
        assert derive_element_id(model.processes[0]) == "process:web-app"
        assert (
            derive_element_id(model.data_flows[0])
            == "flow:customer-to-web-app:login"
        )

    def test_abbreviated_id_is_replaced_by_the_name_slug(self):
        normalized = normalize_element_ids(self.abbreviated())
        assert normalized.processes[0].id == "process:web-app-frontend-service"

    def test_references_to_a_rewritten_id_follow_it(self):
        normalized = normalize_element_ids(self.abbreviated())
        new_id = "process:web-app-frontend-service"
        assert normalized.data_flows[0].destination == new_id
        assert normalized.data_flows[1].source == new_id
        assert normalized.assumptions[0].element_id == new_id

    def test_flow_ids_are_rebuilt_from_the_derived_endpoints(self):
        normalized = normalize_element_ids(self.abbreviated())
        assert normalized.data_flows[0].id == (
            "flow:customer-to-web-app-frontend-service:login"
        )

    def test_normalizing_an_abbreviated_model_makes_it_valid(self):
        assert validate(self.abbreviated()) != []
        assert validate(normalize_element_ids(self.abbreviated())) == []

    def test_a_rewritten_boundary_id_carries_its_zone_members(self):
        model = valid_model()
        model.trust_boundaries[1].name = "Internal Network VPC"
        normalized = normalize_element_ids(model)
        assert normalized.processes[0].trust_zone == "boundary:internal-network-vpc"
        assert normalized.boundary_crossings()[0].destination_zone == (
            "boundary:internal-network-vpc"
        )

    def test_the_input_model_is_not_mutated(self):
        model = self.abbreviated()
        normalize_element_ids(model)
        assert model.processes[0].id == "process:web-app"

    def test_a_valid_model_normalizes_to_itself(self):
        assert normalize_element_ids(valid_model()) == valid_model()

    def test_dangling_references_are_left_for_the_gate(self):
        model = valid_model()
        model.data_flows[0].source = "entity:ghost"
        normalized = normalize_element_ids(model)
        assert normalized.data_flows[0].source == "entity:ghost"
        assert "invalid-reference" in {i.code for i in validate(normalized)}

    def test_a_name_with_no_slug_keeps_its_emitted_id(self):
        model = valid_model()
        model.processes[0].name = "???"
        normalized = normalize_element_ids(model)
        assert normalized.processes[0].id == "process:web-app"

    def test_colliding_names_surface_as_duplicate_ids(self):
        """Normalization does not hide the class/instance duplication case."""
        model = valid_model()
        twin = model.processes[0].model_copy(deep=True)
        twin.id = "process:web-app-v2"
        model.processes.append(twin)
        normalized = normalize_element_ids(model)
        assert "duplicate-id" not in {i.code for i in validate(model)}
        assert "duplicate-id" in {i.code for i in validate(normalized)}
