"""Capabilities a source states, on the System Model: gate, reader and transport."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis_service.capabilities import CAPABILITIES
from analysis_service.compact import expand
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import (
    CapabilityStatement,
    EmittedSystemModel,
    SystemModel,
    normalize_element_ids,
)
from analysis_service.validation import validate
from tests.factories import valid_model

SOURCES = {"Kickoff call": "Ana: we never accept files. Customers upload PDFs."}


def _model(*statements: CapabilityStatement) -> SystemModel:
    model = valid_model()
    for element in model.elements():
        element.source_excerpt = "Ana"
        element.source_label = "Kickoff call"
    model.capabilities = list(statements)
    return model


def _stated(capability: str, state: str, quote: str) -> CapabilityStatement:
    return CapabilityStatement.model_validate(
        {
            "capability": capability,
            "state": state,
            "source_excerpt": quote,
            "source_label": "Kickoff call",
            "origin": "reader",
        }
    )


def test_a_quoted_statement_passes_the_gate():
    model = _model(_stated("file-upload", "absent", "we never accept files"))
    assert validate(model, sources=SOURCES) == []


def test_a_capability_outside_the_table_is_an_invalid_reference():
    issues = validate(_model(_stated("fax-machine", "present", "Ana")), sources=SOURCES)
    assert [(issue.code, issue.field) for issue in issues] == [
        ("invalid-reference", "capabilities")
    ]


def test_a_quote_the_source_does_not_hold_is_refused_like_an_elements():
    issues = validate(
        _model(_stated("oauth", "absent", "there is no OAuth")), sources=SOURCES
    )
    assert [issue.code for issue in issues] == ["unverifiable-excerpt"]
    assert issues[0].is_citation
    assert "capability 'oauth'" in issues[0].message


def test_a_label_naming_no_source_is_refused():
    statement = _stated("oauth", "absent", "Ana")
    statement.source_label = "Some other call"
    issues = validate(_model(statement), sources=SOURCES)
    assert [(issue.code, issue.field) for issue in issues] == [
        ("invalid-reference", "source_label")
    ]


def test_the_schema_states_the_closed_set():
    schema = CapabilityStatement.model_json_schema()
    assert schema["properties"]["capability"]["enum"] == sorted(CAPABILITIES)
    assert schema["properties"]["state"]["enum"] == ["present", "absent"]
    assert schema["properties"]["origin"]["enum"] == ["answer", "field", "reader"]
    assert "origin" in schema["required"]


def test_a_statement_states_its_origin():
    raw = _stated("oauth", "absent", "Ana").model_dump()
    del raw["origin"]
    with pytest.raises(ValidationError, match="origin"):
        CapabilityStatement.model_validate(raw)


@pytest.mark.parametrize(
    ("origin", "basis"),
    [("field", ""), ("answer", "login: a rule"), ("reader", "login: a rule")],
)
def test_a_basis_is_on_a_field_statement_and_on_no_other(origin, basis):
    raw = _stated("oauth", "present", "Ana").model_dump()
    with pytest.raises(ValidationError, match="basis"):
        CapabilityStatement.model_validate(raw | {"origin": origin, "basis": basis})


@pytest.mark.parametrize(
    ("origin", "label", "basis"),
    [
        ("answer", "Kickoff call", ""),
        ("field", ANSWERS_LABEL, "login: a rule"),
        ("reader", ANSWERS_LABEL, ""),
    ],
)
def test_an_answer_statement_quotes_the_answers_source_and_no_other_does(
    origin, label, basis
):
    raw = _stated("oauth", "present", "Ana").model_dump()
    with pytest.raises(ValidationError, match="answers Source"):
        CapabilityStatement.model_validate(
            raw | {"origin": origin, "source_label": label, "basis": basis}
        )


def test_a_stated_capability_is_a_fact_with_its_quote():
    facts = _model(_stated("file-upload", "present", "Customers upload PDFs"))
    fact = facts.capability_facts()["file-upload"]
    assert fact.state == "present"
    assert fact.evidence == ("Kickoff call: Customers upload PDFs",)


def test_two_sources_that_disagree_leave_the_capability_unknown():
    model = _model(
        _stated("file-upload", "absent", "we never accept files"),
        _stated("file-upload", "present", "Customers upload PDFs"),
    )
    assert model.capability_facts()["file-upload"].state == "unknown"


def test_a_capability_no_statement_names_is_not_a_fact():
    assert _model().capability_facts() == {}


def test_normalization_snaps_a_statements_label_to_the_jobs_spelling():
    statement = _stated("oauth", "absent", "Ana")
    statement.source_label = "kickoff CALL"
    model = normalize_element_ids(_model(statement), source_labels=list(SOURCES))
    assert model.capabilities[0].source_label == "Kickoff call"


def test_no_extraction_transport_admits_a_capability_statement():
    """Extraction states no capability, so a model that writes one is refused."""
    statement = _stated("oauth", "absent", "Ana").model_dump()

    _, issues = expand({"capabilities": [statement]})
    assert [issue.code for issue in issues] == ["schema"]
    with pytest.raises(ValidationError, match="capabilities"):
        EmittedSystemModel.model_validate({"capabilities": [statement]})


def test_the_schema_a_provider_reads_names_the_model_and_no_reason():
    """The extraction schema's head is the System Model's (#1395)."""
    emitted = EmittedSystemModel.model_json_schema()
    full = SystemModel.model_json_schema()
    assert (emitted["title"], emitted["description"]) == (
        full["title"],
        full["description"],
    )
    assert "capabilities" not in emitted["properties"]
