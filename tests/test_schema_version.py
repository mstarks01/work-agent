"""A route that parses a stored record refuses one of another version (#1532).

Each test seeds a job, then states an older ``schema_version`` on its record
through :meth:`~analysis_service.jobs.JobStore.save`. The record content stays
current, so a route that does not ask
:func:`~analysis_service.jobs.same_schema` serves it, and only the gate refuses.
"""

from __future__ import annotations

import asyncio

import pytest

from analysis_service.claims import UnknownRef, Verdict
from analysis_service.fact_answers import FactAnswer
from analysis_service.jobs import JobRecord
from analysis_service.report import SCHEMA_VERSION
from analysis_service.sources import Source
from tests.factories import admit, sample_report, sample_selection, sample_threat
from tests.test_api import auth, submission
from tests.test_corrections import ASKED, finished
from tests.test_job_store import completed, final
from tests.test_links import catalog_client
from tests.test_pause import waiting

#: A schema version older than the one this service writes.
OLDER = "2.10"

#: The one fact :func:`a_report_that_asks` asks.
OPEN = UnknownRef(subject="Whether the audit log is append-only")

CORRECTION = {"facts": [FactAnswer(key=ASKED.key, value="unknown").model_dump()]}
DRAFT = {
    "facts": [FactAnswer(key=OPEN.key, value="yes").model_dump()],
    "save": True,
    "revision": 0,
}


def aged(store, job_id: str, version: str) -> None:
    """State ``version`` as the schema version that created ``job_id``'s record."""
    record = asyncio.run(store.get(job_id))
    record.schema_version = version
    asyncio.run(store.save(record))


def a_waiting_job(store) -> tuple[str, str]:
    job = waiting(store)
    return job, job


def a_report(store) -> tuple[str, str]:
    job = completed(store)
    return job, job


def a_report_that_asks(store) -> tuple[str, str]:
    """A completed job whose report waits on :data:`OPEN`."""
    record = JobRecord.create(
        owner_id="alice",
        sources=[Source.description("A web app writes to an audit log.")],
        frameworks=sample_selection(),
    )
    asking = Verdict(status="needs-info", reason="open", related_unknowns=[OPEN])
    record.transition("running")
    record.report = sample_report([sample_threat(verdict=asking)])
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id, record.id


def skip_the_first_early_question(client, job: str) -> dict:
    """A waiting job's save that skips the first early question it asks."""
    asked = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
    key = asked["early_questions"][0]["key"]
    return {"save": True, "skip": [key], "presented": [key], "revision": 0}


def a_final_report(store) -> tuple[str, str]:
    job = finished(store)
    return job, job


def a_follow_up(store) -> tuple[str, str]:
    child = final(store, completed(store))
    return child, child


def the_parent_of_a_follow_up(store) -> tuple[str, str]:
    parent = completed(store)
    return final(store, parent), parent


#: Each route that parses a stored record: the seed, which returns the job to
#: request and the job whose record states the version, the request, and the
#: status the route answers where the record has this service's version. A
#: callable body is built from the job before its record states the version.
PARSING_ROUTES = [
    pytest.param(
        a_waiting_job, "GET", "questions", None, 200, id="questions-of-a-waiting-job"
    ),
    pytest.param(a_report, "GET", "questions", None, 200, id="questions-of-a-report"),
    pytest.param(
        a_waiting_job, "POST", "answers", {"revision": 0}, 201, id="answers-continue"
    ),
    pytest.param(
        a_waiting_job,
        "POST",
        "answers",
        skip_the_first_early_question,
        200,
        id="answers-save-a-skip",
    ),
    pytest.param(a_report_that_asks, "POST", "answers", DRAFT, 200, id="drafts"),
    pytest.param(
        a_waiting_job,
        "POST",
        "amendments",
        {"text": "The API also writes to an audit log.", "revision": 0},
        201,
        id="amendments",
    ),
    pytest.param(
        a_final_report, "POST", "corrections", CORRECTION, 200, id="corrections"
    ),
    pytest.param(a_follow_up, "GET", "changes", None, 200, id="changes"),
    pytest.param(
        the_parent_of_a_follow_up,
        "GET",
        "changes",
        None,
        200,
        id="changes-of-an-older-parent",
    ),
]


def call(client, method: str, job: str, route: str, body, token: str = "alice-token"):
    return client.request(
        method, f"/v1/jobs/{job}/{route}", json=body, headers=auth(token)
    )


@pytest.mark.parametrize(("seed", "method", "route", "body", "ok"), PARSING_ROUTES)
def test_a_parsing_route_refuses_a_record_of_an_older_major(
    seed, method, route, body, ok
):
    client, store = catalog_client()
    job, stored = seed(store)
    body = body(client, job) if callable(body) else body
    aged(store, stored, OLDER)

    response = call(client, method, job, route, body)

    assert response.status_code == 409, response.text
    assert response.headers["content-type"] == "application/problem+json"
    detail = response.json()["detail"]
    assert f"schema version {OLDER}" in detail
    assert f"schema version {SCHEMA_VERSION}" in detail
    assert detail.endswith("submit the job again")


@pytest.mark.parametrize(("seed", "method", "route", "body", "ok"), PARSING_ROUTES)
def test_a_record_of_this_version_is_served(seed, method, route, body, ok):
    client, store = catalog_client()
    job, stored = seed(store)
    body = body(client, job) if callable(body) else body
    aged(store, stored, SCHEMA_VERSION)

    response = call(client, method, job, route, body)

    assert response.status_code == ok, response.text


def test_a_submission_refuses_to_carry_answers_from_a_record_of_an_older_major():
    client, store = catalog_client()
    earlier = waiting(store)
    aged(store, earlier, OLDER)

    response = client.post(
        "/v1/jobs",
        json=submission(questions=True, answers_from=earlier),
        headers=auth(),
    )

    assert response.status_code == 409, response.text
    assert "submit the job again" in response.json()["detail"]


def test_the_report_and_the_status_serve_a_record_of_an_older_major():
    """The report route returns what the store holds for any version (#1532)."""
    client, store = catalog_client()
    job = completed(store)
    aged(store, job, OLDER)

    report = client.get(f"/v1/jobs/{job}/report", headers=auth())
    status = client.get(f"/v1/jobs/{job}", headers=auth())

    assert report.status_code == 200, report.text
    assert report.json()["schema_version"] == SCHEMA_VERSION
    assert status.status_code == 200, status.text


def test_another_owner_reads_a_record_of_an_older_major_as_missing():
    client, store = catalog_client()
    job = waiting(store)
    aged(store, job, OLDER)
    # bob holds a principal, so the read reaches the ownership check.
    waiting(store, owner="bob")

    response = call(client, "GET", job, "questions", None, token="bob-token")

    assert response.status_code == 404, response.text
