"""Tests for the canonical System Model schema and derived boundary crossings."""

import pytest
from pydantic import ValidationError

from analysis_service.system_model import (
    DataFlow,
    ExternalEntity,
    ModelIndex,
    Process,
    SystemModel,
    TrustBoundary,
    derive_element_id,
    make_element_id,
    make_flow_id,
    normalize_element_ids,
    normalize_name,
)
from analysis_service.validation import validate
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
            interface_kind="non-web",
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


class TestSharedNames:
    """The class/instance duplication a typed ID hides from ``duplicate-id``."""

    @staticmethod
    def doubled():
        """The gate-passing model with its store renamed onto the process.

        Renamed and then normalized, which is the path a model really takes:
        the name is authoritative, the ID follows, and every reference to it
        follows too.
        """
        model = valid_model()
        model.data_stores[0].name = "Web App"
        return normalize_element_ids(model)

    def test_a_process_and_a_store_sharing_a_name_are_reported(self):
        assert self.doubled().shared_names() == {
            "web-app": ["process:web-app", "store:web-app"]
        }

    def test_the_gate_passes_the_very_pair_this_reports(self):
        # The whole reason the mark exists: two types make two IDs, and
        # `duplicate-id` compares whole IDs, so nothing here is a gate failure.
        assert validate(self.doubled()) == []

    def test_distinct_names_report_nothing(self):
        assert valid_model().shared_names() == {}

    def test_a_same_type_collision_belongs_to_the_gate_instead(self):
        # Two processes of one name hold one ID, so this is `duplicate-id`'s to
        # report and must not be doubled up as a shared name.
        model = valid_model()
        model.processes.append(model.processes[0].model_copy(deep=True))
        assert model.shared_names() == {}
        assert "duplicate-id" in {issue.code for issue in validate(model)}

    def test_a_trust_boundary_sharing_a_name_is_not_a_collision(self):
        # A boundary is a zone, not a thing in the system, so a process named
        # after the zone it sits in is ordinary naming.
        model = valid_model()
        model.processes[0].name = "Internal Network"
        normalized = normalize_element_ids(model)
        assert normalized.get("process:internal-network") is not None
        assert normalized.shared_names() == {}

    def test_three_types_on_one_name_come_back_together(self):
        model = valid_model()
        model.data_stores[0].name = "Web App"
        model.external_entities[0].name = "Web App"
        assert normalize_element_ids(model).shared_names() == {
            "web-app": ["entity:web-app", "process:web-app", "store:web-app"]
        }


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
            derive_element_id(model.data_flows[0]) == "flow:customer-to-web-app:login"
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


class TestModelIndex:
    """The lookups a validated model answers repeatedly, computed once.

    The index answers what the model answers — that is the whole contract — so
    each test compares the two rather than asserting the index's own idea of a
    right answer.
    """

    def test_every_element_is_found_by_id(self):
        model = valid_model()

        index = ModelIndex.of(model)

        assert [index.get(element.id) for element in model.elements()] == (
            model.elements()
        )

    def test_an_absent_id_answers_none(self):
        assert ModelIndex.of(valid_model()).get("process:nonexistent") is None

    def test_a_flow_reaches_its_two_endpoints(self):
        model = valid_model()
        flow = model.data_flows[0]

        assert ModelIndex.of(model).reach([flow.id]) == frozenset(
            {flow.id, flow.source, flow.destination}
        )

    def test_an_element_reaches_the_flows_that_touch_it(self):
        """One hop from an element is the flows on it and the elements at their
        far ends — never a second hop, which is reach a description narrates
        rather than a place an action lands."""
        model = valid_model()
        flow = model.data_flows[0]

        place = flow.source

        reach = ModelIndex.of(model).reach([place])

        touching = [
            other
            for other in model.data_flows
            if place in (other.source, other.destination)
        ]
        assert reach == frozenset(
            {place}
            | {other.id for other in touching}
            | {other.source for other in touching}
            | {other.destination for other in touching}
        )

    def test_a_place_the_model_does_not_hold_reaches_only_itself(self):
        assert ModelIndex.of(valid_model()).reach(["process:nonexistent"]) == (
            frozenset({"process:nonexistent"})
        )

    def test_reach_is_the_union_over_the_places(self):
        """Every caller hands ``reach`` a set, so one call over two places must
        answer what two calls answer together."""
        model = valid_model()
        index = ModelIndex.of(model)
        places = [element.id for element in model.zoned_elements()]

        assert index.reach(places) == frozenset().union(
            *(index.reach([place]) for place in places)
        )

    def test_the_index_does_not_follow_a_model_it_indexed(self):
        """It is a snapshot, and normalization rewrites IDs in place. An index
        that a model kept would answer for the IDs held before the rewrite, with
        nothing at the call site to say so."""
        model = valid_model()
        index = ModelIndex.of(model)
        renamed = model.processes[0].id

        model.processes[0].id = "process:renamed"

        assert index.get(renamed) is not None
        assert index.get("process:renamed") is None
