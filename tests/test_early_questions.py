"""The open facts a paused job asks before its analysis (``QA-2026-09-26-03-E13``).

Held against the shared model for what is asked and in which order, against
the answer rules for what an answer to each may be, against the prior table
for a row per framework, and against the questions route for a paused job.
"""

from __future__ import annotations

from collections import Counter

from analysis_service.candidates import generate_candidates
from analysis_service.early_questions import (
    QUESTION_PRIOR,
    PriorRow,
    early_questions,
    element_type,
)
from analysis_service.frameworks import PACKAGES
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import FactAnswer, check_fact_answers
from analysis_service.system_model import (
    UNKNOWN,
    ZONE_ATTRIBUTE,
    Assumption,
    attribute_names,
)
from tests.factories import valid_model
from tests.test_api import auth
from tests.test_pause import catalog_client, waiting

STORE = "store:orders-db"
PROCESS = "process:web-app"


def prior_of(rates) -> dict[str, PriorRow]:
    """A STRIDE prior with these rates, and an empty row for every other package."""
    empty = PriorRow(runs=(), revision=None, cases=0, rates={})
    return {
        name: PriorRow(runs=("test",), revision="t", cases=1, rates=rates)
        if name == "stride"
        else empty
        for name in PACKAGES
    }


def keys(rates, model=None):
    asked = early_questions(model or valid_model(), ["stride"], None, prior_of(rates))
    return [question.key for question in asked]


class TestThePriorTable:
    def test_it_has_a_row_for_every_package(self):
        assert set(QUESTION_PRIOR) == set(PACKAGES)

    def test_every_rate_names_an_element_type_and_an_attribute_or_a_kind(self):
        types = {element_type(element) for element in valid_model().elements()}
        attributes = {
            name
            for element in valid_model().elements()
            for name in attribute_names(element)
        }
        for row in QUESTION_PRIOR.values():
            for kind, fields in row.rates.items():
                assert kind in types
                assert set(fields) <= attributes | set(QUESTION_KINDS)

    def test_a_row_that_counted_a_run_says_which(self):
        for row in QUESTION_PRIOR.values():
            assert bool(row.runs) == bool(row.rates) == bool(row.cases)


class TestWhatIsAsked:
    def test_an_unknown_attribute_is_asked_and_a_stated_one_is_not(self):
        asked = keys({"DataStore": {"encryption_at_rest": 1.0, "technology": 1.0}})
        assert asked == [(STORE, "encryption_at_rest", "", "", "")]

    def test_every_kind_the_prior_names_is_asked(self):
        asked = keys({"DataStore": {"audit-evidence": 1.0}})
        assert asked == [(STORE, "", "", "", "audit-evidence")]

    def test_a_zone_is_asked_only_where_the_service_inferred_it(self):
        rates = {"DataStore": {ZONE_ATTRIBUTE: 1.0}}
        assert keys(rates) == []
        model = valid_model()
        model.assumptions.append(
            Assumption(
                assumption="the store sits in the internal network",
                element_id=STORE,
                attribute=ZONE_ATTRIBUTE,
                basis="the sources are silent",
            )
        )
        assert keys(rates, model) == [(STORE, ZONE_ATTRIBUTE, "", "", "")]

    def test_a_framework_whose_row_counted_no_run_asks_nothing(self):
        assert early_questions(valid_model(), ["asvs"], None) == ()


class TestTheOrder:
    def test_the_rules_that_fire_on_an_element_raise_its_questions(self):
        """Equal rates, so the element more candidates name is asked first."""
        stride = PACKAGES["stride"]
        found = generate_candidates(valid_model(), stride.lanes, stride.rules)
        named = Counter(
            element_id
            for group in found.values()
            for candidate in group.candidates
            for element_id in set(candidate.element_ids)
        )
        flows = [flow.id for flow in valid_model().data_flows]
        assert len({named[flow] for flow in flows}) == len(flows)
        asked = keys({"DataFlow": {"audit-evidence": 1.0}})
        expected = sorted(flows, key=lambda element: -named[element])
        assert [key[0] for key in asked] == expected

    def test_a_question_says_which_rules_make_it_matter(self):
        (question,) = early_questions(
            valid_model(),
            ["stride"],
            None,
            prior_of({"DataStore": {"audit-evidence": 1.0}}),
        )
        rules = {rule.question for rule in PACKAGES["stride"].rules}
        assert question.reasons
        assert set(question.reasons) <= rules


def test_every_early_question_takes_an_answer_the_answer_rules_accept():
    """The early list and the answers route are two readers of one model."""
    model = valid_model()
    for question in early_questions(model, ["stride"], None):
        value = question.choices[0] if question.choices else "stated by the submitter"
        check_fact_answers([FactAnswer(key=question.key, value=value)], model, None)


def test_the_shipped_prior_puts_an_unknown_attribute_near_the_top():
    asked = [
        question.key for question in early_questions(valid_model(), ["stride"], None)
    ]
    assert asked[0][1] or asked[0][4]
    assert (STORE, "encryption_at_rest", "", "", "") in asked[:3]
    assert all(
        getattr(valid_model().get(key[0]), key[1]) == UNKNOWN
        for key in asked
        if key[1] and key[1] != ZONE_ATTRIBUTE
    )


class TestTheRoute:
    def test_a_waiting_job_ranks_its_open_facts(self):
        client, store = catalog_client()
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        expected = early_questions(valid_model(), ["stride"], store_catalog(store, job))
        assert body["early_questions"] == [q.to_json() for q in expected]
        assert body["early_questions"]

    def test_a_finished_job_asks_nothing_early(self):
        from tests.test_questions import TestTheRoutes

        client, store = catalog_client()
        job = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert body["early_questions"] == []


def store_catalog(store, job):
    import asyncio

    record = asyncio.run(store.get(job))
    return record.checkpoint.assertions.catalog
