"""A job resumed from a finished one, with answers, starting at ``prepare`` (#1252).

The resumed job runs on its parent's own model and catalog, so an answer
applies to the catalog that asked the question and no extraction or assertion
pass runs again. The first class drives the real graph offline, because a
green unit test says the pieces work and never that the path does.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType

import pytest

from analysis_service import graph
from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    answer,
)
from analysis_service.jobs import (
    InMemoryJobStore,
    JobRecord,
    PipelineCompleted,
    Resumption,
)
from analysis_service.links import LinkAnswer, with_link_answers
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.sources import Source
from tests.factories import (
    DESCRIPTION_TEXT,
    sample_report,
    sample_selection,
    scripted_pipeline,
    valid_model,
)
from tests.test_api import admit, auth, seed
from tests.test_links import catalog_client

PRINCIPAL = "principal:customer-accounts"
LINK = LinkAnswer(principal="customer accounts", element="entity:customer")


def parent_catalog() -> AssertionCatalog:
    return AssertionCatalog(
        subjects=[Subject(id=PRINCIPAL, type="principal", label="customer accounts")],
        entries=[
            Assertion(
                subject=PRINCIPAL,
                predicate="mfa-requirement",
                value=ABSENT,
                basis="inferred",
                explanation="the description names one factor",
            )
        ],
    )


class TestTheResumedRun:
    """The real graph, from ``prepare``, with scripted models."""

    def resumed_job(self) -> JobRecord:
        links = [LINK]
        record = JobRecord.create(
            owner_subject="idp|user-1",
            sources=with_link_answers([Source.description(DESCRIPTION_TEXT)], links),
            frameworks=sample_selection(),
            links=links,
            resumption=Resumption(
                parent_id="job-parent",
                system_model=valid_model(),
                assertions=AssertionRecord(proposed=1, catalog=parent_catalog()),
            ),
        )
        record.transition("running")
        return record

    def run(self):
        pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME)
        visited: list[str] = []

        async def on_node(node: str) -> None:
            visited.append(node)

        async def scenario():
            return await AdkPipelineRunner(pipeline).run(self.resumed_job(), on_node)

        return asyncio.run(scenario()), visited

    def test_it_runs_no_extraction_and_no_assertion_pass(self):
        outcome, visited = self.run()

        assert isinstance(outcome, PipelineCompleted)
        assert graph.PREPARE_NODE in visited
        assert not {graph.EXTRACT_NODE, graph.ASSERT_NODE, graph.VALIDATE_NODE} & set(
            visited
        )

    def test_the_report_carries_the_parent_s_model_and_the_link(self):
        outcome, _ = self.run()
        report = outcome.report

        assert report.system_model == valid_model()
        assert report.assertions is not None
        assert report.assertions.issues == []
        (row,) = answer(report.assertions.catalog, PRINCIPAL, "represented-by").settled
        assert (row.value, row.basis) == ("entity:customer", "stated")


def test_the_builder_refuses_an_assertion_pass_on_a_resumed_graph():
    with pytest.raises(ValueError, match="runs no\\s+assertion pass"):
        scripted_pipeline({}, entry=graph.ENTRY_RESUME, assertions=True)


class TestTheAnswersRoute:
    def completed(self, store, subject="alice", assertions=True, links=()):
        record = JobRecord.create(
            owner_subject=subject,
            sources=with_link_answers(
                [Source.description(DESCRIPTION_TEXT)], list(links)
            ),
            frameworks=sample_selection(),
            links=list(links),
        )
        record.transition("running")
        held = AssertionRecord(proposed=1, catalog=parent_catalog())
        record.report = sample_report().model_copy(
            update={"assertions": held if assertions else None}
        )
        record.transition("completed")
        asyncio.run(admit(store, record))
        return record.id

    def post(self, client, job, *links):
        return client.post(
            f"/v1/jobs/{job}/answers",
            json={"links": [dict(link) for link in links]},
            headers=auth(),
        )

    def child(self, store, response):
        assert response.status_code == 201, response.text
        return asyncio.run(store.get(response.json()["job_id"]))

    def test_answers_resume_the_job_from_its_own_model_and_catalog(self):
        client, store = catalog_client()
        parent = self.completed(store)
        child = self.child(store, self.post(client, parent, LINK.model_dump()))

        assert child.resumption.parent_id == parent
        assert child.resumption.assertions.catalog == parent_catalog()
        assert child.links == [LINK]
        assert [source.kind for source in child.sources] == ["description", "answers"]

    def test_the_resumed_job_is_run_from_prepare(self):
        client, store = catalog_client()
        entries = []
        runner = client.app.state.runner_for

        def recording(selection, entry=graph.ENTRY_EXTRACT):
            entries.append(entry)
            return runner(selection, entry)

        client.app.state.runner_for = recording
        self.post(client, self.completed(store), LINK.model_dump())
        assert entries == [graph.ENTRY_RESUME]

    def test_a_new_answer_replaces_the_parent_s_about_one_principal(self):
        client, store = catalog_client()
        earlier = LinkAnswer(principal="Customer accounts", element="none")
        other = LinkAnswer(principal="ml engineers", element="none")
        parent = self.completed(store, links=[earlier, other])
        child = self.child(store, self.post(client, parent, LINK.model_dump()))

        assert child.links == [LINK, other]
        (answers,) = [s for s in child.sources if s.kind == "answers"]
        assert "entity:customer" in answers.text
        assert "none of the elements" in answers.text

    def test_a_deployment_with_no_catalog_refuses(self):
        from tests.test_api import make_client

        client, store = make_client()
        parent = self.completed(store)
        assert self.post(client, parent, LINK.model_dump()).status_code == 400

    def test_a_report_with_no_catalog_refuses(self):
        client, store = catalog_client()
        parent = self.completed(store, assertions=False)
        assert self.post(client, parent, LINK.model_dump()).status_code == 409

    def test_a_running_job_cannot_be_answered(self):
        client, store = catalog_client()
        record = seed(store, "alice", "running")
        assert self.post(client, record.id, LINK.model_dump()).status_code == 409

    def test_another_caller_s_job_is_not_found(self):
        client, store = catalog_client()
        parent = self.completed(store, subject="bob")
        assert self.post(client, parent, LINK.model_dump()).status_code == 404

    def test_two_answers_about_one_principal_are_refused(self):
        client, store = catalog_client()
        parent = self.completed(store)
        twice = LinkAnswer(principal="Customer Accounts", element="none")
        response = self.post(client, parent, LINK.model_dump(), twice.model_dump())
        assert response.status_code == 400

    def test_no_answers_is_malformed(self):
        client, store = catalog_client()
        parent = self.completed(store)
        assert self.post(client, parent).status_code == 422


def test_a_resumption_is_absent_on_an_ordinary_job():
    """The field is optional, so every record written before it still loads."""
    record = JobRecord.create(
        owner_subject="a",
        sources=[Source.description("an app")],
        frameworks=sample_selection(),
    )
    assert record.resumption is None
    assert (
        JobRecord.model_validate(MappingProxyType(record.model_dump())).resumption
        is None
    )


def test_store_round_trips_a_resumed_job():
    store = InMemoryJobStore()
    record = TestTheResumedRun().resumed_job()
    asyncio.run(admit(store, record))
    assert asyncio.run(store.get(record.id)).resumption == record.resumption
