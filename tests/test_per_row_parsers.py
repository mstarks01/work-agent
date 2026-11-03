"""The parser of each table that a ``str`` field names a key of.

A stored row holds the key as a ``str``, so a row with a key the table does
not hold still loads. The parser gives a key the table holds back as the
table's ``Literal``. A check refuses any other key on that row alone, with
the code and message it states, and a label shows the key as written.
"""

from __future__ import annotations

import pytest

from analysis_service.answer_forms import (
    CONTROL_SUGGESTIONS,
    answer_choices,
    answer_form,
    answer_suggestions,
)
from analysis_service.assertions import (
    REGISTRY,
    catalog_issues,
    parse_predicate,
    quarantine,
)
from analysis_service.capabilities import CAPABILITIES, parse_capability_key
from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import FactAnswer, answer_facets
from analysis_service.open_facts import group_of, label_of
from analysis_service.question_kinds import QUESTION_KINDS, parse_question_kind
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import CapabilityStatement, SystemModel
from analysis_service.validation import validate
from tests.test_assertions import FLOW, SOURCES, catalog, gate_model, stated

ELEMENT = "process:storefront-api"


class TestEachParserGivesTheTableKey:
    """A key the table holds comes back as itself, and any other as ``None``."""

    @pytest.mark.parametrize(
        ("parse", "table"),
        [
            (parse_predicate, REGISTRY),
            (parse_capability_key, CAPABILITIES),
            (parse_question_kind, QUESTION_KINDS),
        ],
    )
    def test_each_key_parses_and_no_other_does(self, parse, table):
        assert [parse(key) for key in table] == list(table)
        assert parse("vibes") is None
        assert parse("") is None


class TestAnUnregisteredPredicateIsRefusedOnItsRow:
    def test_the_gate_names_the_row(self):
        held = catalog([stated(), stated(predicate="vibes")])

        issues = catalog_issues(held, model=gate_model(), sources=SOURCES)

        refused = [issue for issue in issues if issue.code == "unknown-predicate"]
        assert [(issue.message, issue.subject) for issue in refused] == [
            ("'vibes' is not a registered predicate", FLOW)
        ]

    def test_quarantine_sets_that_row_aside_and_keeps_the_rest(self):
        held = catalog([stated(), stated(predicate="vibes")])

        kept, removed = quarantine(
            held, catalog_issues(held, model=gate_model(), sources=SOURCES)
        )

        assert [entry.predicate for entry in kept.entries] == [
            "authentication-mechanism"
        ]
        assert [(row.identity, row.codes) for row in removed] == [
            ("", ["unknown-predicate"])
        ]


class TestAnUnknownCapabilityIsRefusedOnItsStatement:
    def statement(self, capability):
        return CapabilityStatement(
            capability=capability,
            state="present",
            source_excerpt="yes",
            source_label=ANSWERS_LABEL,
            origin="answer",
        )

    def test_the_gate_names_the_statement(self):
        model = SystemModel(capabilities=[self.statement("telepathy")])

        refused = [
            (issue.message, issue.field)
            for issue in validate(model)
            if issue.code == "invalid-reference"
        ]

        assert refused == [
            (
                f"capability 'telepathy' is not one of {sorted(CAPABILITIES)}",
                "capabilities",
            )
        ]

    def test_a_known_capability_passes_the_gate(self):
        model = SystemModel(capabilities=[self.statement("cookies")])

        assert "invalid-reference" not in {issue.code for issue in validate(model)}

    def test_a_label_reads_the_question_or_the_key(self):
        names: dict[str, str] = {}

        assert label_of(UnknownRef(capability="cookies"), names) == (
            CAPABILITIES["cookies"].question
        )
        assert label_of(UnknownRef(capability="telepathy"), names) == "telepathy"


class TestAnUnknownQuestionKindReadsAsWritten:
    def test_a_label_and_a_group_read_the_kind_or_the_name(self):
        known = UnknownRef(element_id=ELEMENT, question="capacity-limits")
        unknown = UnknownRef(element_id=ELEMENT, question="vibes")

        assert label_of(known, {}) == QUESTION_KINDS["capacity-limits"].template.format(
            element=ELEMENT
        )
        assert group_of(known)[0] == "capacity-limits"
        assert label_of(unknown, {}) == "vibes"
        assert group_of(unknown) == ("vibes", "vibes")

    def test_an_unknown_kind_has_no_facets_and_no_choices(self):
        key = UnknownRef(element_id=ELEMENT, question="vibes").key

        assert answer_facets(key) == ()
        assert answer_choices(key, SystemModel(), None) == ()

    def test_a_facet_answer_to_an_unknown_kind_is_refused(self):
        key = UnknownRef(element_id=ELEMENT, question="vibes").key

        with pytest.raises(ValueError, match="facets answer only a question kind"):
            FactAnswer.model_validate({"key": key, "facets": {"rate": "yes"}})

    def test_a_known_kind_reads_its_facets(self):
        key = UnknownRef(element_id=ELEMENT, question="capacity-limits").key

        assert answer_facets(key) == QUESTION_KINDS["capacity-limits"].facets


class TestAnAttributeReadsItsAnswerTables:
    @pytest.mark.parametrize("attribute", sorted(CONTROL_SUGGESTIONS))
    def test_a_control_attribute_offers_its_suggestions(self, attribute):
        key = UnknownRef(element_id=ELEMENT, attribute=attribute).key

        assert answer_suggestions(key) == CONTROL_SUGGESTIONS[attribute]
        assert answer_form(key, SystemModel(), None) == "control"

    def test_another_attribute_offers_none(self):
        key = UnknownRef(element_id=ELEMENT, attribute="protocol").key

        assert answer_suggestions(key) == ()
        assert answer_form(key, SystemModel(), None) == "text"

    def test_the_trust_zone_offers_the_model_s_boundaries(self):
        model = gate_model()
        element = model.elements()[0]
        key = UnknownRef(element_id=element.id, attribute="trust_zone").key

        assert answer_choices(key, model, None) == tuple(
            boundary.id for boundary in model.trust_boundaries
        )
