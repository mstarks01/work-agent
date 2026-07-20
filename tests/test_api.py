"""Route contracts for the /v1 job API: auth, ownership, lifecycle, SSE."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from stride_service.api import MAX_DESCRIPTION_BYTES, create_app
from stride_service.auth import AuthenticationError
from stride_service.jobs import (
    InMemoryJobStore,
    JobRecord,
    NodeCallback,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
)
from stride_service.report import StrideReport
from stride_service.validation import ValidationIssue

TOKENS = {"alice-token": "alice", "bob-token": "bob"}


class FakeVerifier:
    def verify(self, token: str) -> str:
        try:
            return TOKENS[token]
        except KeyError:
            raise AuthenticationError("invalid or expired credentials") from None


class RejectingRunner:
    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        return PipelineRejected(
            issues=[
                ValidationIssue(
                    code="no-trust-zones",
                    message="the model declares no Trust Boundary",
                )
            ]
        )


class FailingRunner:
    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        raise RuntimeError("db password hunter2 leaked in traceback")


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_client(runner=None, store=None) -> tuple[TestClient, InMemoryJobStore]:
    store = store if store is not None else InMemoryJobStore()
    app = create_app(
        store=store,
        runner=runner if runner is not None else StubPipelineRunner(),
        verifier=FakeVerifier(),
    )
    return TestClient(app), store


def submit(client: TestClient, token: str = "alice-token") -> str:
    response = client.post(
        "/v1/jobs",
        json={"description": "a web app storing orders"},
        headers=auth(token),
    )
    assert response.status_code == 201
    return response.json()["job_id"]


def parse_sse(body: str) -> list[dict]:
    frames = []
    for block in body.strip().split("\n\n"):
        frame = {}
        for line in block.splitlines():
            field, _, value = line.partition(": ")
            frame[field] = value
        frame["data"] = json.loads(frame["data"])
        frames.append(frame)
    return frames


class TestHealthAndAuth:
    def test_healthz_is_unauthenticated(self):
        client, _ = make_client()
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/v1/jobs"),
            ("get", "/v1/jobs/job-x"),
            ("get", "/v1/jobs/job-x/events"),
            ("get", "/v1/jobs/job-x/report"),
        ],
    )
    def test_v1_routes_require_bearer_token(self, method, path):
        client, _ = make_client()
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["www-authenticate"] == "Bearer"

    def test_bad_token_is_401(self):
        client, _ = make_client()
        response = client.get("/v1/jobs/job-x", headers=auth("forged-token"))
        assert response.status_code == 401


class TestSubmit:
    def test_submit_returns_handle_and_location(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={"description": "a web app", "system_name": "Orders"},
            headers=auth(),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "queued"
        assert response.headers["location"] == f"/v1/jobs/{body['job_id']}"

    def test_oversized_description_is_413_problem(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={"description": "x" * (MAX_DESCRIPTION_BYTES + 1)},
            headers=auth(),
        )
        assert response.status_code == 413
        assert response.headers["content-type"] == "application/problem+json"

    def test_missing_description_is_422_problem(self):
        client, _ = make_client()
        response = client.post("/v1/jobs", json={}, headers=auth())
        assert response.status_code == 422
        body = response.json()
        assert response.headers["content-type"] == "application/problem+json"
        assert body["status"] == 422
        assert any("description" in error["loc"] for error in body["errors"])

    def test_unknown_field_rejected(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={"description": "app", "model_tier": "pro"},
            headers=auth(),
        )
        assert response.status_code == 422


class TestPoll:
    def test_completed_job_shows_progress_never_report(self):
        client, _ = make_client()
        job_id = submit(client)
        response = client.get(f"/v1/jobs/{job_id}", headers=auth())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert [p["node"] for p in body["progress"]] == list(
            StubPipelineRunner.nodes
        )
        assert "report" not in body
        assert "validation_issues" not in body
        assert "error" not in body

    def test_rejected_job_embeds_validation_issues(self):
        client, _ = make_client(runner=RejectingRunner())
        job_id = submit(client)
        body = client.get(f"/v1/jobs/{job_id}", headers=auth()).json()
        assert body["status"] == "rejected"
        assert [issue["code"] for issue in body["validation_issues"]] == [
            "no-trust-zones"
        ]

    def test_failed_job_shows_generic_error_only(self):
        client, _ = make_client(runner=FailingRunner())
        job_id = submit(client)
        response = client.get(f"/v1/jobs/{job_id}", headers=auth())
        body = response.json()
        assert body["status"] == "failed"
        assert "hunter2" not in response.text
        assert body["error"] == "internal error while running the analysis pipeline"

    def test_unknown_job_is_404_problem(self):
        client, _ = make_client()
        response = client.get("/v1/jobs/job-missing", headers=auth())
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"


class TestOwnership:
    def test_non_owner_reads_are_404_matching_missing(self):
        client, _ = make_client()
        job_id = submit(client, "alice-token")
        for path in (
            f"/v1/jobs/{job_id}",
            f"/v1/jobs/{job_id}/report",
            f"/v1/jobs/{job_id}/events",
        ):
            foreign = client.get(path, headers=auth("bob-token"))
            missing = client.get(
                path.replace(job_id, "job-missing"), headers=auth("bob-token")
            )
            assert foreign.status_code == 404
            assert foreign.json() == missing.json()


class TestReport:
    def test_completed_job_returns_self_contained_report(self):
        client, _ = make_client()
        job_id = submit(client)
        response = client.get(f"/v1/jobs/{job_id}/report", headers=auth())
        assert response.status_code == 200
        report = StrideReport.model_validate(response.json())
        assert report.job.id == job_id
        assert "owner" not in response.text
        assert "alice" not in response.text

    def test_queued_job_report_is_409(self):
        store = InMemoryJobStore()
        record = JobRecord.create(owner_subject="alice", description="an app")
        asyncio.run(store.create(record))
        client, _ = make_client(store=store)
        response = client.get(f"/v1/jobs/{record.id}/report", headers=auth())
        assert response.status_code == 409
        assert response.headers["content-type"] == "application/problem+json"

    @pytest.mark.parametrize("runner", [RejectingRunner(), FailingRunner()])
    def test_unfinishable_job_report_is_409(self, runner):
        client, _ = make_client(runner=runner)
        job_id = submit(client)
        response = client.get(f"/v1/jobs/{job_id}/report", headers=auth())
        assert response.status_code == 409


class TestEvents:
    def test_stream_replays_full_progression_and_ends_terminal(self):
        client, _ = make_client()
        job_id = submit(client)
        response = client.get(f"/v1/jobs/{job_id}/events", headers=auth())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse(response.text)
        statuses = [f["data"]["status"] for f in frames if f["event"] == "status"]
        nodes = [f["data"]["node"] for f in frames if f["event"] == "node"]
        assert statuses == ["queued", "running", "completed"]
        assert nodes == list(StubPipelineRunner.nodes)
        assert frames[-1]["data"]["status"] == "completed"
        assert [int(f["id"]) for f in frames] == list(range(1, len(frames) + 1))

    def test_last_event_id_resumes_after_seen_events(self):
        client, _ = make_client()
        job_id = submit(client)
        response = client.get(
            f"/v1/jobs/{job_id}/events",
            headers=auth() | {"Last-Event-ID": "3"},
        )
        frames = parse_sse(response.text)
        assert [int(f["id"]) for f in frames] == list(
            range(4, 4 + len(frames))
        )
        assert frames[-1]["data"]["status"] == "completed"
