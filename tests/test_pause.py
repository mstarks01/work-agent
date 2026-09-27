"""A job that asks questions pauses after its assertion pass and waits (#1252).

With the per-job toggle on, the run stops after the catalog, holds its model
and catalog, and ends in ``awaiting-answers``. The job then waits for a person
for as long as it takes, taking no in-flight slot. Answering it, or asking to
continue without answers, starts a resumed job from what it holds. An
autonomous run, with the toggle off, never pauses.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service import graph
from analysis_service.assertions import AssertionRecord
from analysis_service.jobs import (
    TERMINAL_STATUSES,
    Checkpoint,
    InMemoryJobStore,
    JobRecord,
    PipelineAwaiting,
    Resumption,
    execute_job,
)
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.report import NodeRun
from analysis_service.sources import Source
from tests.factories import (
    DESCRIPTION_TEXT,
    sample_selection,
    scripted_pipeline,
    valid_model,
)
from tests.test_api import admit, auth, submission
from tests.test_links import catalog_client
from tests.test_resume import LINK, PRINCIPAL, parent_catalog, recorded_entries


def asking_job(
    owner_subject: str = "alice",
    ask_questions: bool = True,
    resumption: Resumption | None = None,
) -> JobRecord:
    return JobRecord.create(
        owner_subject=owner_subject,
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        ask_questions=ask_questions,
        resumption=resumption,
    )


def held() -> Checkpoint:
    return Checkpoint(
        system_model=valid_model(),
        assertions=AssertionRecord(proposed=1, catalog=parent_catalog()),
    )


class TestThePausedRun:
    """The real head-only graph, with scripted models."""

    def test_the_run_stops_after_the_catalog_and_holds_it(self):
        pipeline, _ = scripted_pipeline(
            {
                "extract": valid_model().model_dump_json(),
                graph.ASSERT_NODE: json.dumps({"assertions": []}),
            },
            entry=graph.ENTRY_HEAD_ONLY,
            assertions=True,
        )
        record = asking_job()
        record.transition("running")
        visited: list[str] = []

        async def on_node(node: str) -> None:
            visited.append(node)

        outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))

        assert isinstance(outcome, PipelineAwaiting)
        assert outcome.checkpoint.system_model == valid_model()
        assert graph.ASSERT_NODE in visited
        assert graph.PREPARE_NODE not in visited
        assert outcome.nodes


class TestTheWait:
    def test_a_paused_run_ends_in_awaiting_answers_with_its_checkpoint(self):
        class Pausing:
            async def run(self, job, on_node):
                return PipelineAwaiting(
                    checkpoint=held(),
                    nodes=[NodeRun(node="extract", model="m", duration_ms=10)],
                )

        store = InMemoryJobStore()
        record = asking_job()
        asyncio.run(admit(store, record))
        asyncio.run(execute_job(store, Pausing(), record.id, deadline_seconds=30))
        waiting = asyncio.run(store.get(record.id))

        assert waiting.status == "awaiting-answers"
        assert waiting.checkpoint == held()
        assert waiting.measured_tokens is not None

    def test_a_waiting_job_is_terminal_and_moves_no_further(self):
        assert "awaiting-answers" in TERMINAL_STATUSES
        record = asking_job()
        record.transition("running")
        record.transition("awaiting-answers")
        with pytest.raises(ValueError):
            record.transition("running")

    def test_a_resumed_job_never_pauses(self):
        resumed = asking_job(resumption=Resumption(parent_id="p", checkpoint=held()))
        assert asking_job().pauses()
        assert not resumed.pauses()
        assert not asking_job(ask_questions=False).pauses()


def waiting(store, subject="alice", checkpoint=True) -> str:
    record = asking_job(owner_subject=subject)
    record.transition("running")
    record.checkpoint = held() if checkpoint else None
    record.transition("awaiting-answers")
    asyncio.run(admit(store, record))
    return record.id


class TestTheRoutes:
    def test_a_submission_with_questions_runs_the_head_only(self):
        client, _ = catalog_client()
        entries = recorded_entries(client)
        response = client.post(
            "/v1/jobs", json=submission(questions=True), headers=auth()
        )
        assert response.status_code == 201
        assert entries == [graph.ENTRY_HEAD_ONLY]

    def test_an_autonomous_submission_never_pauses(self):
        client, _ = catalog_client()
        entries = recorded_entries(client)
        client.post("/v1/jobs", json=submission(), headers=auth())
        assert entries == [graph.ENTRY_EXTRACT]

    def test_questions_are_refused_where_no_catalog_is_built(self):
        from tests.test_api import make_client

        client, _ = make_client()
        response = client.post(
            "/v1/jobs", json=submission(questions=True), headers=auth()
        )
        assert response.status_code == 400

    def test_a_waiting_job_serves_its_questions_from_its_checkpoint(self):
        client, store = catalog_client()
        job = waiting(store)
        response = client.get(f"/v1/jobs/{job}/questions", headers=auth())
        assert response.status_code == 200
        (question,) = response.json()["link_questions"]
        assert question["principal"] == "customer accounts"

    def test_answers_resume_a_waiting_job_from_its_checkpoint(self):
        client, store = catalog_client()
        job = waiting(store)
        entries = recorded_entries(client)
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"links": [LINK.model_dump()]},
            headers=auth(),
        )
        assert response.status_code == 201
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.resumption.parent_id == job
        assert child.resumption.checkpoint == held()
        assert entries == [graph.ENTRY_RESUME]

    def test_no_answers_continues_a_waiting_job(self):
        client, store = catalog_client()
        job = waiting(store)
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"links": []}, headers=auth()
        )
        assert response.status_code == 201
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.links == []
        assert [source.kind for source in child.sources] == ["description"]

    def test_a_waiting_job_takes_no_in_flight_slot(self):
        """A person may leave several jobs waiting and still submit."""
        client, store = catalog_client()
        for _ in range(3):
            waiting(store)
        store_ceiling = client.app.state.max_active_jobs
        record = asking_job()
        from tests.test_api import SEEDING_BUDGET

        admission = asyncio.run(
            store.reserve(record, ceiling=min(store_ceiling, 1), budget=SEEDING_BUDGET)
        )
        assert admission.outcome == "admitted"

    def test_another_caller_s_waiting_job_is_not_found(self):
        client, store = catalog_client()
        job = waiting(store, subject="bob")
        assert (
            client.get(f"/v1/jobs/{job}/questions", headers=auth()).status_code == 404
        )


def test_the_link_question_s_principal_is_the_catalog_s():
    """The fixture's principal is the one the paused catalog holds."""
    assert PRINCIPAL in {subject.id for subject in parent_catalog().subjects}
