"""Capabilities a source states, on the System Model: gate, reader and transport."""

from __future__ import annotations

from analysis_service.capabilities import CAPABILITIES
from analysis_service.compact import expand
from analysis_service.system_model import (
    CapabilityStatement,
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


def test_the_compact_transport_carries_the_statements_unchanged():
    statement = _stated("oauth", "absent", "Ana").model_dump()
    expanded, issues = expand({"capabilities": [statement]})
    assert issues == []
    assert expanded is not None
    assert expanded["capabilities"] == [statement]
