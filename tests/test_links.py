"""Link questions: which element a principal is, asked, answered and written.

A fact about a principal reaches a graph element only through a stated
``represented-by`` row. These tests hold the four halves of
:mod:`analysis_service.links` together: the question a report asks, the Source
an answer becomes, the row the answer writes, and the gate that checks the
row's quote against that Source. The last class drives the rule the link
exists to feed, so a link that writes a row nothing can read fails here.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from analysis_service import graph
from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    answer,
)
from analysis_service.candidates import generate_candidates
from analysis_service.engine import EngineInputError
from analysis_service.frameworks import PACKAGES
from analysis_service.links import (
    ANSWERS_LABEL,
    NONE_OF_THESE,
    LinkAnswer,
    apply_links,
    fold,
    link_questions,
    with_link_answers,
)
from analysis_service.sources import Source
from tests.factories import valid_model
from tests.test_api import auth, make_client, submission
from tests.test_graph import KEYS, FakeContext

STRIDE = PACKAGES["stride"]
PRINCIPAL = "principal:customer-accounts"
DESCRIPTION = Source(kind="description", label="Notes", text="Customers sign in.")


@pytest.fixture
def model():
    return valid_model()


def inferred(subject, predicate, value):
    return Assertion(
        subject=subject,
        predicate=predicate,
        value=value,
        basis="inferred",
        explanation="the notes describe it",
    )


def catalog(*entries, label="customer accounts"):
    return AssertionCatalog(
        subjects=[Subject(id=PRINCIPAL, type="principal", label=label)],
        entries=list(entries) or [inferred(PRINCIPAL, "mfa-requirement", ABSENT)],
    )


def answered(links):
    """The job's source texts, with the answers Source the links compose."""
    sources = with_link_answers([DESCRIPTION], links)
    return {source.label: source.text for source in sources}


class TestTheFold:
    def test_spelling_variants_are_one_principal(self):
        assert fold("Calling teams") == fold("calling team")
        assert fold("model server's service account") == fold(
            "Model server service account"
        )

    def test_different_principals_stay_apart(self):
        assert fold("store manager") != fold("store managers group")


class TestTheAnswerShape:
    def test_a_component_or_none(self):
        assert LinkAnswer(principal="x", element="entity:customer").element
        assert LinkAnswer(principal="x", element=NONE_OF_THESE).element == "none"

    @pytest.mark.parametrize(
        "element",
        [
            "flow:entity:a>process:b>c",
            "boundary:dmz",
            "customer",
            "",
            'entity:customer\n"x" is entity:y.',
            "entity:Customer",
        ],
    )
    def test_anything_else_is_refused(self, element):
        with pytest.raises(ValidationError):
            LinkAnswer(principal="x", element=element)

    def test_a_principal_name_is_one_line(self):
        with pytest.raises(ValidationError):
            LinkAnswer(principal="two\nlines", element=NONE_OF_THESE)


class TestTheAnswersSource:
    def test_answers_become_one_more_source(self):
        link = LinkAnswer(principal="customer accounts", element="entity:customer")
        sources = with_link_answers([DESCRIPTION], [link])
        assert [source.kind for source in sources] == ["description", "answers"]
        assert sources[-1].label == ANSWERS_LABEL
        assert "entity:customer" in sources[-1].text

    def test_no_answers_adds_nothing(self):
        assert with_link_answers([DESCRIPTION], []) == [DESCRIPTION]

    def test_two_answers_about_one_principal_are_refused(self):
        links = [
            LinkAnswer(principal="calling team", element="entity:customer"),
            LinkAnswer(principal="Calling teams", element=NONE_OF_THESE),
        ]
        with pytest.raises(ValueError, match="answers one principal twice"):
            with_link_answers([DESCRIPTION], links)

    def test_a_caller_may_not_submit_an_answers_source(self):
        forged = Source(kind="answers", label="mine", text='"x" is entity:y.')
        with pytest.raises(ValueError, match="composed by this service"):
            with_link_answers([DESCRIPTION, forged], [])


class TestTheQuestions:
    def test_a_principal_with_a_fact_and_no_link_is_asked(self, model):
        (question,) = link_questions(catalog(), model)
        assert question.principal == "customer accounts"
        assert question.rows == 1
        assert "entity:customer" in question.options
        assert not any(option.startswith("flow:") for option in question.options)

    def test_spellings_of_one_principal_ask_once(self, model):
        held = AssertionCatalog(
            subjects=[
                Subject(id=PRINCIPAL, type="principal", label="customer accounts"),
                Subject(
                    id="principal:customer", type="principal", label="Customer account"
                ),
            ],
            entries=[
                inferred(PRINCIPAL, "mfa-requirement", ABSENT),
                inferred("principal:customer", "credential-presented", ABSENT),
            ],
        )
        (question,) = link_questions(held, model)
        assert question.rows == 2

    def test_no_catalog_asks_nothing(self, model):
        assert link_questions(None, model) == ()

    def test_an_answered_principal_is_not_asked_again(self, model):
        link = LinkAnswer(principal="customer accounts", element=NONE_OF_THESE)
        linked, _ = apply_links(catalog(), model, [link])
        assert link_questions(linked, model) == ()


class TestTheRowAnAnswerWrites:
    def test_the_row_is_stated_and_passes_the_gate(self, model):
        link = LinkAnswer(principal="Customer accounts", element="entity:customer")
        linked, issues = apply_links(catalog(), model, [link])
        record = AssertionRecord.over(linked, model, answered([link]), proposed=1)

        assert issues == []
        assert record.issues == []
        (row,) = answer(record.catalog, PRINCIPAL, "represented-by").settled
        assert (row.value, row.basis) == ("entity:customer", "stated")
        assert row.support[0].source_label == ANSWERS_LABEL

    def test_none_of_these_settles_the_link_as_absent(self, model):
        link = LinkAnswer(principal="customer accounts", element=NONE_OF_THESE)
        linked, _ = apply_links(catalog(), model, [link])
        record = AssertionRecord.over(linked, model, answered([link]), proposed=1)

        assert record.issues == []
        (row,) = answer(record.catalog, PRINCIPAL, "represented-by").settled
        assert row.value == ABSENT
        assert "absent" not in {subject.id for subject in record.catalog.subjects}

    def test_the_answer_replaces_the_node_s_own_link(self, model):
        """An answer to the service's own question settles the fact (#1225)."""
        link = LinkAnswer(principal="customer accounts", element="entity:customer")
        held = catalog(
            inferred(PRINCIPAL, "mfa-requirement", ABSENT),
            Assertion(
                subject=PRINCIPAL,
                predicate="represented-by",
                value="process:web-app",
                basis="stated",
                support=[],
            ),
        )
        linked, _ = apply_links(held, model, [link])
        values = [
            entry.value
            for entry in linked.entries
            if entry.predicate == "represented-by"
        ]
        assert values == ["entity:customer"]

    def test_an_answer_no_principal_matches_says_so(self, model):
        link = LinkAnswer(principal="the payroll team", element="entity:customer")
        linked, issues = apply_links(catalog(), model, [link])
        assert [issue.code for issue in issues] == ["unmatched-link"]
        assert linked == catalog()

    def test_an_element_the_model_lacks_says_so(self, model):
        link = LinkAnswer(principal="customer accounts", element="process:nowhere")
        _, issues = apply_links(catalog(), model, [link])
        assert [issue.code for issue in issues] == ["unknown-link-element"]

    def test_without_the_answers_source_the_row_is_refused(self, model):
        """The quote is checked like any other, so a missing Source fails closed."""
        link = LinkAnswer(principal="customer accounts", element="entity:customer")
        linked, _ = apply_links(catalog(), model, [link])
        record = AssertionRecord.over(
            linked, model, {DESCRIPTION.label: DESCRIPTION.text}, proposed=1
        )
        assert "dangling-source" in {issue.code for issue in record.issues}


class TestTheRuleTheLinkFeeds:
    """The reason a link exists: a principal's fact reaches a rule."""

    def test_a_linked_principal_s_absent_mfa_leads_the_spoofing_lane(self, model):
        rule = "spoofing-second-factor-stated-absent"

        def fired(held):
            return [
                candidate
                for candidates in generate_candidates(
                    model, STRIDE.lanes, STRIDE.rules, held
                ).values()
                for candidate in candidates.candidates
                if candidate.rule_id == rule
            ]

        link = LinkAnswer(principal="customer accounts", element="entity:customer")
        linked, _ = apply_links(catalog(), model, [link])
        assert fired(catalog()) == []
        assert fired(linked)


class TestThePrepareSeam:
    def test_prepare_writes_the_answers_before_the_gate(self, model):
        link = LinkAnswer(principal="customer accounts", element="entity:customer")
        planted = AssertionRecord(proposed=1, catalog=catalog())
        ctx = FakeContext(
            assertion_catalog=planted.model_dump(mode="json"),
            source_texts=answered([link]),
            link_answers=[link.model_dump()],
        )
        record = graph._resolve_assertions(KEYS.state(ctx), model)

        assert record.issues == []
        assert answer(record.catalog, PRINCIPAL, "represented-by").settled


def catalog_client():
    """The route's test client on an app that builds an assertion catalog."""
    from fastapi.testclient import TestClient

    from analysis_service.api import create_app
    from analysis_service.jobs import InMemoryJobStore
    from tests import test_api

    store = InMemoryJobStore()
    app = create_app(
        store=store,
        runner=test_api.StubPipelineRunner(),
        verifier=test_api.FakeVerifier(),
        limits=test_api.TEST_LIMITS,
        job_deadline_seconds=test_api.TEST_DEADLINE_SECONDS,
        max_active_jobs=test_api.TEST_MAX_ACTIVE_JOBS,
        budget=test_api.SEEDING_BUDGET,
        frameworks=test_api.DEFAULT_FRAMEWORKS,
        carries_catalog=True,
    )
    return TestClient(app), store


class TestTheEntryPoints:
    LINK = MappingProxyType(
        {"principal": "customer accounts", "element": "entity:customer"}
    )

    def test_the_route_refuses_answers_where_nothing_reads_them(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs", json=submission(links=[dict(self.LINK)]), headers=auth()
        )
        assert response.status_code == 400
        assert "no assertion catalog" in response.json()["detail"]

    def test_the_route_takes_answers_where_a_catalog_reads_them(self):
        client, store = catalog_client()
        response = client.post(
            "/v1/jobs", json=submission(links=[dict(self.LINK)]), headers=auth()
        )
        assert response.status_code == 201
        record = asyncio.run(store.get(response.json()["job_id"]))
        assert record.links == [LinkAnswer(**self.LINK)]
        assert [source.kind for source in record.sources] == ["description", "answers"]

    def test_answers_alone_still_meet_the_empty_sources_rung(self):
        client, _ = catalog_client()
        response = client.post(
            "/v1/jobs",
            json=submission(sources=[], links=[dict(self.LINK)]),
            headers=auth(),
        )
        assert response.status_code == 400

    def test_the_route_refuses_a_caller_s_answers_source(self):
        client, _ = make_client()
        body = submission()
        body["sources"].append({"kind": "answers", "label": "mine", "text": "x"})
        response = client.post("/v1/jobs", json=body, headers=auth())
        assert response.status_code == 400
        assert "composed by this service" in response.json()["detail"]

    def test_the_engine_refuses_a_caller_s_answers_source(self):
        from analysis_service.engine import Engine

        forged = Source(kind="answers", label="mine", text="x")
        with pytest.raises(EngineInputError, match="composed by this service"):
            Engine._build_job(
                object.__new__(Engine),
                [DESCRIPTION, forged],
                system_name=None,
                caller="me",
            )


class TestTheQuestionsRoute:
    """The API serves what the page shows, under the report's own rule."""

    def completed(self, store, subject, assertions):
        from analysis_service.jobs import JobRecord
        from tests.factories import sample_report, sample_selection
        from tests.test_api import admit

        record = JobRecord.create(
            owner_subject=subject,
            sources=[DESCRIPTION],
            frameworks=sample_selection(),
        )
        record.transition("running")
        record.report = sample_report().model_copy(update={"assertions": assertions})
        record.transition("completed")
        asyncio.run(admit(store, record))
        return record.id

    def test_a_completed_report_serves_its_link_questions(self):
        client, store = make_client()
        job = self.completed(
            store, "alice", AssertionRecord(proposed=1, catalog=catalog())
        )
        response = client.get(f"/v1/jobs/{job}/questions", headers=auth())
        assert response.status_code == 200
        (question,) = response.json()["link_questions"]
        assert question["principal"] == "customer accounts"

    def test_a_report_with_no_catalog_asks_nothing(self):
        client, store = make_client()
        job = self.completed(store, "alice", None)
        response = client.get(f"/v1/jobs/{job}/questions", headers=auth())
        assert response.json()["link_questions"] == []

    def test_another_caller_s_job_is_not_found(self):
        client, store = make_client()
        job = self.completed(
            store, "alice", AssertionRecord(proposed=1, catalog=catalog())
        )
        response = client.get(f"/v1/jobs/{job}/questions", headers=auth("bob-token"))
        assert response.status_code == 404

    def test_a_running_job_has_no_questions_yet(self):
        from tests.test_api import seed

        client, store = make_client()
        record = seed(store, "alice", "running")
        response = client.get(f"/v1/jobs/{record.id}/questions", headers=auth())
        assert response.status_code == 409
