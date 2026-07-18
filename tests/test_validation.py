"""Tests for the mechanical validity gate."""

from stride_service.validation import allowed_asset_tags, parse_and_validate, validate
from tests.factories import valid_model


def codes(issues):
    return [issue.code for issue in issues]


class TestValidModel:
    def test_valid_model_passes_every_rule(self):
        assert validate(valid_model()) == []

    def test_parse_and_validate_accepts_serialized_valid_model(self):
        model, issues = parse_and_validate(valid_model().model_dump())
        assert issues == []
        assert model == valid_model()


class TestUniqueIds:
    def test_duplicate_id_across_types_is_reported(self):
        model = valid_model()
        model.processes[0].id = "entity:customer"
        model.processes[0].name = "Customer"
        issues = validate(model)
        assert "duplicate-id" in codes(issues)


class TestDeterministicIds:
    def test_id_not_derived_from_name_is_reported(self):
        model = valid_model()
        model.processes[0].id = "process:webapp-v2"
        issues = [i for i in validate(model) if i.code == "id-mismatch"]
        assert len(issues) == 1
        assert "process:web-app" in issues[0].message

    def test_flow_id_must_encode_endpoints_and_label(self):
        model = valid_model()
        model.data_flows[0].id = "flow:login"
        issues = [i for i in validate(model) if i.code == "id-mismatch"]
        assert issues[0].element_id == "flow:login"
        assert "flow:customer-to-web-app:login" in issues[0].message


class TestReferentialIntegrity:
    def test_dangling_flow_endpoint_is_reported(self):
        model = valid_model()
        model.data_flows[1].destination = "store:missing-db"
        issues = [i for i in validate(model) if i.code == "invalid-reference"]
        assert any(i.field == "destination" for i in issues)

    def test_flow_may_not_terminate_at_a_boundary(self):
        model = valid_model()
        model.data_flows[1].destination = "boundary:internet"
        assert "invalid-reference" in codes(validate(model))

    def test_trust_zone_must_reference_existing_boundary(self):
        model = valid_model()
        model.data_stores[0].trust_zone = "boundary:dmz"
        issues = [i for i in validate(model) if i.code == "invalid-reference"]
        assert any(i.field == "trust_zone" for i in issues)

    def test_assumption_must_reference_existing_element(self):
        model = valid_model()
        model.assumptions[0].element_id = "process:ghost"
        issues = [i for i in validate(model) if i.code == "invalid-reference"]
        assert any(i.field == "assumptions" for i in issues)


class TestTrustZones:
    def test_model_without_zones_is_reported(self):
        model = valid_model()
        model.trust_boundaries = []
        assert "no-trust-zones" in codes(validate(model))


class TestAssetVocabulary:
    def test_tag_outside_vocabulary_is_reported(self):
        model = valid_model()
        model.data_stores[0].assets = ["crown-jewels"]
        issues = [i for i in validate(model) if i.code == "illegal-asset-tag"]
        assert issues[0].element_id == "store:orders-db"

    def test_config_extends_vocabulary(self):
        model = valid_model()
        model.data_stores[0].assets = ["crown-jewels"]
        assert validate(model, extra_asset_tags=["crown-jewels"]) == []
        assert "crown-jewels" in allowed_asset_tags(["crown-jewels"])


class TestParseFailures:
    def test_schema_failure_returns_no_model(self):
        model, issues = parse_and_validate({"processes": [{"id": "process:x"}]})
        assert model is None
        assert issues
        assert all(issue.code == "schema" for issue in issues)

    def test_schema_issue_messages_carry_field_paths(self):
        _, issues = parse_and_validate({"not_a_field": True})
        assert any("not_a_field" in issue.message for issue in issues)

    def test_gate_issues_are_returned_alongside_parsed_model(self):
        data = valid_model().model_dump()
        data["trust_boundaries"] = []
        for group in ("external_entities", "processes", "data_stores"):
            for element in data[group]:
                element["trust_zone"] = "boundary:missing"
        model, issues = parse_and_validate(data)
        assert model is not None
        assert "no-trust-zones" in codes(issues)
