"""Each part of a question kind is a predicate (ADR 0117).

These tests hold the registry rows the kinds name, the assertion pass that
does not see the ones with extraction off, and the rows an owner's answer or
a Control writes, with the subject rule that decides where a row may go.
"""

from __future__ import annotations

from typing import get_args

import pytest

from analysis_service.assertions import (
    ABSENT,
    EXTRACTED,
    MAX_ASSERTIONS,
    REGISTRY,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    AssertionProposal,
    AssertionRecord,
    CatalogProposal,
    PredicateName,
    Subject,
    SupportSpan,
    answer,
    catalog_issues,
)
from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import FactAnswer, part_answers
from analysis_service.links import ANSWERS_LABEL, apply_answers, with_link_answers
from analysis_service.prompts import render_predicates
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.sources import Source
from tests.factories import valid_model

PROCESS = "process:web-app"
STORE = "store:orders-db"
LOGIN = "flow:entity:customer>process:web-app>login"
BOUNDARY = "boundary:internet"
DESCRIPTION = Source(kind="description", label="Notes", text="Customers sign in.")

#: The predicates the assertion pass extracted before ADR 0117.
PRE_0117 = frozenset(
    {
        "authentication-mechanism",
        "credential-presented",
        "mfa-requirement",
        "authorization-grant",
        "credential-sharing",
        "credential-rotation",
        "credential-custody",
        "credential-expiry",
        "credential-lifetime",
        "credential-revocation",
        "transport-encryption",
        "storage-encryption",
        "data-classification",
        "signature-verification",
        "origin-verification",
        "destination-verification",
        "internet-exposure",
        "network-membership",
        "administrative-authority",
        "tenant-ownership",
        "record-attribution",
        "content-validation",
        "represented-by",
    }
)


@pytest.fixture
def model():
    return valid_model()


def kind_answer(element_id, kind, value=None, **facets):
    key = UnknownRef(element_id=element_id, question=kind).key
    if facets:
        return FactAnswer.model_validate(
            {"key": key, "facets": {f.replace("_", "-"): a for f, a in facets.items()}}
        )
    return FactAnswer(key=key, value=value)


def written(model, facts, held=None, controls=()):
    """The catalog the answers write, after the gate checked every quote."""
    catalog = held or AssertionCatalog()
    linked, _ = apply_answers(catalog, model, [], facts, controls)
    sources = with_link_answers([DESCRIPTION], [], facts, controls)
    texts = {source.label: source.text for source in sources}
    assert catalog_issues(linked, model=model, sources=texts) == []
    return linked


def row(catalog, subject, predicate):
    rows = answer(catalog, subject, predicate).rows
    assert len(rows) <= 1
    return rows[0] if rows else None


class TestTheRegistryHoldsEachPart:
    def test_each_part_names_a_predicate_that_asks_it(self):
        named: list[str] = []
        for kind in QUESTION_KINDS.values():
            if kind.answer == "yes-no":
                assert kind.predicate is not None and not kind.facets
                predicate = REGISTRY[kind.predicate]
                assert predicate.asked is not None
                assert predicate.question.format(subject="X") == kind.template.format(
                    element="X"
                )
                named.append(kind.predicate)
            else:
                assert kind.predicate is None
            for facet in kind.facets:
                assert REGISTRY[facet.predicate].asked is not None
                assert facet.question.endswith("?")
                named.append(facet.predicate)
        asked = [name for name, p in REGISTRY.items() if p.asked is not None]
        assert sorted(named) == sorted(asked)
        assert len(named) == 34

    def test_a_new_part_takes_its_answers_and_an_old_one_maps_them(self):
        for predicate in REGISTRY.values():
            if predicate.asked is None:
                continue
            assert set(predicate.asked.values.values()) <= predicate.terms | {ABSENT}
            if not predicate.extracted:
                assert predicate.terms == set(predicate.asked.values)
        assert REGISTRY["internet-exposure"].asked.values == {
            "yes": "internet-facing",
            "no": "internal",
        }
        assert REGISTRY["record-attribution"].asked.values == {
            "yes": "principal",
            "no": ABSENT,
        }

    def test_the_names_type_holds_every_row(self):
        assert set(get_args(PredicateName)) == set(REGISTRY)


class TestTheAssertionPassSeesNoNewPart:
    def test_the_pass_extracts_what_it_extracted_before(self):
        assert set(EXTRACTED) == PRE_0117

    def test_the_prompt_table_and_the_schema_name_only_extracted_ones(self):
        table = render_predicates("catalog")
        for name in REGISTRY:
            assert (f"| `{name}` |" in table) == (name in EXTRACTED), name
        schema = CatalogProposal.model_json_schema()
        enum = schema["$defs"]["AssertionProposal"]["properties"]["predicate"]["enum"]
        assert set(enum) == PRE_0117

    def test_the_resolver_refuses_a_part_the_pass_does_not_extract(self, model):
        proposal = CatalogProposal(
            assertions=[
                AssertionProposal(
                    subject_type="component",
                    subject=PROCESS,
                    predicate="capacity-limits-rate",
                    value="yes",
                    basis="inferred",
                    explanation="it says so",
                )
            ]
        )
        record = AssertionRecord.of(proposal, model, {"Notes": "text"})
        assert record.catalog.entries == []
        assert [issue.code for issue in record.issues] == ["unknown-predicate"]


class TestTheRowsAnAnswerWrites:
    def test_each_answered_facet_writes_a_stated_row_quoting_its_line(self, model):
        fact = kind_answer(
            PROCESS, "capacity-limits", rate="yes", size="no", quota="not applicable"
        )
        catalog = written(model, [fact])
        assert row(catalog, PROCESS, "capacity-limits-rate").value == "yes"
        assert row(catalog, PROCESS, "capacity-limits-size").value == "no"
        assert row(catalog, PROCESS, "capacity-limits-quota").value == "not applicable"
        assert row(catalog, PROCESS, "capacity-limits-concurrency") is None
        rate = row(catalog, PROCESS, "capacity-limits-rate")
        assert (rate.basis, rate.origin) == ("stated", "answer")
        assert rate.support[0].source_label == ANSWERS_LABEL
        assert rate.support[0].quote.startswith("Asked ")

    def test_an_unknown_part_writes_no_row(self, model):
        fact = kind_answer(PROCESS, "capacity-limits", rate=UNKNOWN, size="yes")
        catalog = written(model, [fact])
        assert row(catalog, PROCESS, "capacity-limits-rate") is None
        assert row(catalog, PROCESS, "capacity-limits-size").value == "yes"

    def test_a_yes_no_kind_writes_its_own_row(self, model):
        catalog = written(model, [kind_answer(STORE, "stored-copy-integrity", "no")])
        assert row(catalog, STORE, "stored-copy-integrity").value == "no"
        assert part_answers(kind_answer(STORE, "stored-copy-integrity", UNKNOWN)) == (
            ("stored-copy-integrity", UNKNOWN),
        )

    def test_a_flow_takes_a_row_of_its_own(self, model):
        catalog = written(model, [kind_answer(LOGIN, "capacity-limits", rate="no")])
        assert row(catalog, LOGIN, "capacity-limits-rate").value == "no"

    def test_a_part_about_a_zone_writes_nothing(self, model):
        catalog = written(model, [kind_answer(BOUNDARY, "capacity-limits", rate="no")])
        assert catalog.entries == []

    def test_an_old_predicate_takes_the_mapped_value(self, model):
        model.processes[0].exposure = "unknown"
        facts = [
            kind_answer(PROCESS, "reachability", internet="no", named_callers="yes"),
            kind_answer(PROCESS, "audit-evidence", records_actor="no"),
        ]
        catalog = written(model, facts)
        assert row(catalog, PROCESS, "internet-exposure").value == "internal"
        assert row(catalog, PROCESS, "reachability-named-callers").value == "yes"
        assert row(catalog, PROCESS, "record-attribution").value == ABSENT

    def test_not_applicable_writes_no_row_of_an_old_predicate(self, model):
        model.processes[0].exposure = "unknown"
        fact = kind_answer(PROCESS, "reachability", internet="not applicable")
        assert row(written(model, [fact]), PROCESS, "internet-exposure") is None

    def test_a_control_row_quotes_the_controls_line(self, model):
        fact = kind_answer(PROCESS, "capacity-limits", rate="yes")
        catalog = written(model, [fact], controls=[("Gateway limits", fact)])
        quote = row(catalog, PROCESS, "capacity-limits-rate").support[0].quote
        assert 'From the Control "Gateway limits".' in quote


class TestTheSubjectRule:
    def stated(self, subject, predicate, value):
        return Assertion(
            subject=subject,
            predicate=predicate,
            value=value,
            basis="inferred",
            origin="assertion-pass",
            explanation="the notes describe it",
        )

    def held(self, model, *entries):
        return AssertionCatalog(
            subjects=[
                Subject(id=element.id, type=t, label=element.name)
                for element, t in (
                    (model.processes[0], "component"),
                    (model.data_flows[0], "interaction"),
                )
            ],
            entries=list(entries),
        )

    def test_a_stated_row_about_the_element_stands(self, model):
        held = self.held(model, self.stated(PROCESS, "record-attribution", "principal"))
        fact = kind_answer(PROCESS, "audit-evidence", records_actor="no")
        catalog = written(model, [fact], held)
        rows = answer(catalog, PROCESS, "record-attribution").rows
        assert [(r.value, r.origin) for r in rows] == [("principal", "assertion-pass")]

    def test_a_flows_row_never_answers_a_question_about_an_element(self, model):
        held = self.held(model, self.stated(LOGIN, "capacity-limits-rate", "yes"))
        fact = kind_answer(PROCESS, "capacity-limits", rate="no")
        catalog = written(model, [fact], held)
        assert row(catalog, PROCESS, "capacity-limits-rate").value == "no"
        assert row(catalog, LOGIN, "capacity-limits-rate").value == "yes"

    def test_a_stated_attribute_stands_for_a_part_that_projects_into_it(self, model):
        assert model.processes[0].exposure == "internet-facing"
        fact = kind_answer(PROCESS, "reachability", internet="no")
        assert row(written(model, [fact]), PROCESS, "internet-exposure") is None

    def test_an_open_row_is_answered_by_the_part(self, model):
        open_row = Assertion(
            subject=PROCESS,
            predicate="record-attribution",
            value=UNKNOWN,
            reason="silent",
            basis="inferred",
            origin="assertion-pass",
            explanation="the notes do not say",
        )
        fact = kind_answer(PROCESS, "audit-evidence", records_actor="yes")
        catalog = written(model, [fact], self.held(model, open_row))
        assert row(catalog, PROCESS, "record-attribution").value == "principal"

    def test_a_later_run_writes_the_row_again_from_the_held_answer(self, model):
        model.processes[0].exposure = "unknown"
        first = written(model, [kind_answer(PROCESS, "reachability", internet="yes")])
        # A follow-up changes the answer: the earlier row goes, the new one stands.
        later = kind_answer(PROCESS, "reachability", internet="no")
        catalog = written(model, [later], first)
        assert row(catalog, PROCESS, "internet-exposure").value == "internal"
        # And a run with the same answer writes the same one row again.
        again = written(model, [later], catalog)
        assert row(again, PROCESS, "internet-exposure").value == "internal"


class TestTheBound:
    def test_rows_an_answer_wrote_do_not_count_toward_the_extraction_cap(self, model):
        span = SupportSpan(
            source_label=ANSWERS_LABEL, digest="0" * 64, start=0, end=1, quote="A"
        )
        entry = Assertion(
            subject=PROCESS,
            predicate="capacity-limits-rate",
            value="yes",
            basis="stated",
            origin="answer",
            support=[span],
        )
        held = AssertionCatalog(
            subjects=[Subject(id=PROCESS, type="component", label="Web app")],
            entries=[entry] * (MAX_ASSERTIONS + 1),
        )
        codes = {i.code for i in catalog_issues(held, model=model, sources={})}
        assert "too-many-assertions" not in codes
        extracted = held.model_copy(
            update={
                "entries": [
                    entry.model_copy(update={"origin": "assertion-pass", "support": []})
                ]
                * (MAX_ASSERTIONS + 1)
            }
        )
        codes = {i.code for i in catalog_issues(extracted, model=model, sources={})}
        assert codes == {"too-many-assertions"}
