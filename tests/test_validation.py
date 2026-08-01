"""Tests for the mechanical validity gate."""

from stride_service.system_model import SystemModel
from stride_service.validation import (
    MAX_ELEMENTS,
    allowed_asset_tags,
    parse_and_validate,
    validate,
)
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


class TestElementCap:
    """The ticket-010 admission cap: model size is bounded before analyst spend."""

    def sized(self, total):
        """A valid model padded with cloned processes to exactly ``total`` elements."""
        model = valid_model()
        template = model.processes[0]
        padding = total - len(model.elements())
        model.processes += [
            template.model_copy(
                update={"id": f"process:worker-{index}", "name": f"Worker {index}"}
            )
            for index in range(padding)
        ]
        assert len(model.elements()) == total
        return model

    def test_model_at_the_limit_is_accepted(self):
        model = valid_model()
        assert validate(model, max_elements=len(model.elements())) == []

    def test_model_over_the_limit_is_reported(self):
        model = self.sized(8)
        issues = validate(model, max_elements=5)
        assert codes(issues) == ["too-many-elements"]

    def test_cap_message_names_both_numbers(self):
        issues = validate(self.sized(8), max_elements=5)
        assert "5-element limit" in issues[0].message
        assert "8 elements" in issues[0].message

    def test_cap_reports_alone_and_suppresses_other_issues(self):
        model = self.sized(8)
        model.trust_boundaries = []
        issues = validate(model, max_elements=5)
        assert codes(issues) == ["too-many-elements"]

    def test_default_cap_is_the_configured_limit(self):
        assert validate(self.sized(MAX_ELEMENTS), max_elements=MAX_ELEMENTS) == []
        over = validate(self.sized(MAX_ELEMENTS + 1))
        assert codes(over) == ["too-many-elements"]

    def test_parse_and_validate_threads_the_cap(self):
        _, issues = parse_and_validate(self.sized(8).model_dump(), max_elements=5)
        assert codes(issues) == ["too-many-elements"]


class TestNormalizeIds:
    """The pipeline derives IDs; hand-authored models do not."""

    def abbreviated(self):
        model = valid_model()
        model.processes[0].name = "Web App Frontend Service"
        return model.model_dump()

    def test_off_by_default_so_authored_models_still_report_mismatch(self):
        model, issues = parse_and_validate(self.abbreviated())
        assert "id-mismatch" in codes(issues)
        assert model.processes[0].id == "process:web-app"

    def test_on_request_the_model_is_normalized_and_passes(self):
        model, issues = parse_and_validate(self.abbreviated(), normalize_ids=True)
        assert issues == []
        assert model.processes[0].id == "process:web-app-frontend-service"

    def test_schema_failures_still_fail_closed(self):
        model, issues = parse_and_validate({"not_a_field": True}, normalize_ids=True)
        assert model is None
        assert codes(issues) == ["schema"]


class TestCitationsResolve:
    """The fifth invalid-reference rule (#56).

    The one gate rule taking data from outside the model: an excerpt's label
    has to name a source the *job* carried, so the traceability chain a reader
    follows leads somewhere.
    """

    LABELS = ("Kickoff call", "Payments doc")

    def model_citing(self, label: str, excerpt: str = "a quote") -> SystemModel:
        model = valid_model()
        for element in model.elements():
            element.source_excerpt = ""
            element.source_label = ""
        model.processes[0].source_excerpt = excerpt
        model.processes[0].source_label = label
        return model

    def test_a_label_naming_one_of_the_jobs_sources_passes(self):
        issues = validate(self.model_citing("Kickoff call"), source_labels=self.LABELS)
        assert issues == []

    def test_a_label_naming_no_source_the_job_carried_is_invalid(self):
        # Worse than no citation: a reader who follows it finds nothing.
        issues = validate(
            self.model_citing("Some other call"), source_labels=self.LABELS
        )
        assert [issue.code for issue in issues] == ["invalid-reference"]
        assert issues[0].field == "source_label"
        assert issues[0].element_id == "process:web-app"

    def test_an_excerpt_with_no_label_at_all_is_invalid(self):
        # Excerpt and label are coupled: a quote with no label cites nothing.
        issues = validate(self.model_citing(""), source_labels=self.LABELS)
        assert [issue.code for issue in issues] == ["invalid-reference"]

    def test_an_element_with_no_excerpt_needs_no_label(self):
        model = self.model_citing("Kickoff call", excerpt="")
        model.processes[0].source_label = ""
        assert validate(model, source_labels=self.LABELS) == []

    def test_the_rule_does_not_run_without_a_jobs_labels(self):
        # A hand-authored model checked outside a job has no set to check
        # against, and inventing one would fail it on a citation that is fine.
        assert validate(self.model_citing("Anything at all")) == []

    def test_the_speaker_is_never_gated(self):
        # Its case is redactability, not correctness: a wrong or missing
        # speaker must not fail a job.
        model = self.model_citing("Kickoff call")
        model.processes[0].source_speaker = "Someone Not On The Call"
        assert validate(model, source_labels=self.LABELS) == []

    def test_the_labels_reach_the_gate_through_parse_and_validate(self):
        model, issues = parse_and_validate(
            self.model_citing("Some other call").model_dump(mode="json"),
            normalize_ids=True,
            source_labels=self.LABELS,
        )
        assert model is not None
        assert "invalid-reference" in codes(issues)
