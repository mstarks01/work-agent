"""Route contracts for the /v1 job API: auth, ownership, lifecycle, SSE."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from stride_service.api import create_app
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
from stride_service.sources import Source, SourceLimits
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


# Small enough that an over-budget body is a few hundred bytes rather than a
# hundred kilobytes, so the size tests stay readable.
TEST_LIMITS = SourceLimits(max_total_bytes=512, max_sources=3)


def make_client(
    runner=None, store=None, limits: SourceLimits = TEST_LIMITS
) -> tuple[TestClient, InMemoryJobStore]:
    store = store if store is not None else InMemoryJobStore()
    app = create_app(
        store=store,
        runner=runner if runner is not None else StubPipelineRunner(),
        verifier=FakeVerifier(),
        limits=limits,
    )
    return TestClient(app), store


def one_source(text: str = "a web app storing orders", **kwargs) -> list[dict]:
    return [{"kind": "description", "label": "Doc", "text": text} | kwargs]


def submit(client: TestClient, token: str = "alice-token") -> str:
    response = client.post(
        "/v1/jobs",
        json={"sources": one_source()},
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
            json={"sources": one_source(), "system_name": "Orders"},
            headers=auth(),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "queued"
        assert response.headers["location"] == f"/v1/jobs/{body['job_id']}"

    def test_a_transcript_and_a_document_submit_together(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={
                "sources": [
                    {"kind": "description", "label": "Doc", "text": "a web app"},
                    {
                        "kind": "transcript",
                        "label": "Kickoff call",
                        "text": "Ana: it writes to Postgres.",
                    },
                ]
            },
            headers=auth(),
        )
        assert response.status_code == 201

    def test_the_old_description_shape_is_refused(self):
        # Hard cutover: an integrator finds out at the boundary, not by having
        # their prose silently ignored.
        client, _ = make_client()
        response = client.post(
            "/v1/jobs", json={"description": "a web app"}, headers=auth()
        )
        assert response.status_code == 422

    def test_missing_sources_is_422_problem(self):
        client, _ = make_client()
        response = client.post("/v1/jobs", json={}, headers=auth())
        assert response.status_code == 422
        body = response.json()
        assert response.headers["content-type"] == "application/problem+json"
        assert body["status"] == 422
        assert any("sources" in error["loc"] for error in body["errors"])

    def test_unknown_field_rejected(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={"sources": one_source(), "model_tier": "pro"},
            headers=auth(),
        )
        assert response.status_code == 422


class TestInputLadder:
    """Five rungs, shape before size (#52)."""

    @pytest.mark.parametrize(
        "bad_source",
        [
            {"kind": "voicemail", "label": "Call", "text": "hi"},
            {"kind": "description", "label": "", "text": "hi"},
            {"kind": "description", "label": "Doc", "text": ""},
            {"kind": "description", "label": "two\nlines", "text": "hi"},
            {"kind": "description", "label": "Doc"},
            {"kind": "description", "label": "Doc", "text": "hi", "authority": "x"},
        ],
    )
    def test_rung_one_a_malformed_source_is_422(self, bad_source):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs", json={"sources": [bad_source]}, headers=auth()
        )
        assert response.status_code == 422

    def test_rung_two_an_empty_list_is_400(self):
        # Not 413: a job with no input is the wrong shape, and quoting a byte
        # count of zero against a cap would explain nothing.
        client, _ = make_client()
        response = client.post("/v1/jobs", json={"sources": []}, headers=auth())
        assert response.status_code == 400
        assert response.headers["content-type"] == "application/problem+json"
        assert "at least one source" in response.json()["detail"]

    def test_rung_three_a_repeated_label_is_422_naming_the_label(self):
        # A label is the citation key every source_excerpt names. Two sources
        # sharing one both resolve against the gate's label set, so the report
        # would cite 'Notes' with no way to say which 'Notes' it quoted.
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={
                "sources": [
                    {"kind": "description", "label": "Notes", "text": "one"},
                    {"kind": "transcript", "label": "Notes", "text": "two"},
                ]
            },
            headers=auth(),
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        detail = response.json()["detail"]
        assert "unique" in detail
        assert "Notes" in detail

    def test_a_repeated_label_is_refused_at_any_size(self):
        # Not a budget question: two tiny sources are nowhere near either cap,
        # so this rung has to sit above them rather than beside them.
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={
                "sources": [
                    {"kind": "description", "label": "Doc", "text": "a"},
                    {"kind": "description", "label": "Doc", "text": "b"},
                ]
            },
            headers=auth(),
        )
        assert response.status_code == 422

    def test_distinct_labels_are_accepted(self):
        # Guards the guard: the check must not reject an ordinary multi-source
        # job, which is the shape the whole N-sources contract exists for.
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            json={
                "sources": [
                    {"kind": "description", "label": "Doc", "text": "a"},
                    {"kind": "transcript", "label": "Call", "text": "b"},
                ]
            },
            headers=auth(),
        )
        assert response.status_code == 201

    def test_rung_four_too_many_sources_is_413_naming_the_count(self):
        client, _ = make_client()
        sources = [
            {"kind": "description", "label": f"Doc {n}", "text": "x"}
            for n in range(TEST_LIMITS.max_sources + 1)
        ]
        response = client.post("/v1/jobs", json={"sources": sources}, headers=auth())
        assert response.status_code == 413
        detail = response.json()["detail"]
        assert str(TEST_LIMITS.max_sources) in detail
        assert str(len(sources)) in detail

    def test_rung_five_over_budget_names_no_culprit_but_breaks_it_down(self):
        # There is no per-source cap, so nothing here is individually too big:
        # the overspend belongs to the sum, and the caller gets the arithmetic
        # to decide what to cut.
        client, _ = make_client()
        half = TEST_LIMITS.max_total_bytes // 2
        response = client.post(
            "/v1/jobs",
            json={
                "sources": [
                    {"kind": "description", "label": "Doc", "text": "a" * half},
                    {"kind": "transcript", "label": "Call", "text": "b" * half},
                    {"kind": "transcript", "label": "Standup", "text": "c" * half},
                ]
            },
            headers=auth(),
        )
        assert response.status_code == 413
        detail = response.json()["detail"]
        assert str(TEST_LIMITS.max_total_bytes) in detail
        for label in ("Doc", "Call", "Standup"):
            assert label in detail

    def test_shape_is_checked_before_size(self):
        # A submission that is both malformed and over-count hears about the
        # malformed source, which is the one it can actually act on. Kept small
        # enough that the pre-parse body guard is not what answers.
        client, _ = make_client()
        sources = [
            {"kind": "voicemail", "label": f"S{n}", "text": "x"}
            for n in range(TEST_LIMITS.max_sources + 1)
        ]
        response = client.post("/v1/jobs", json={"sources": sources}, headers=auth())
        assert response.status_code == 422

    def test_an_absurd_body_is_refused_before_it_is_parsed(self):
        client, _ = make_client()
        response = client.post(
            "/v1/jobs",
            content=b'{"sources": []}',
            headers=auth()
            | {
                "content-type": "application/json",
                "content-length": str(TEST_LIMITS.max_total_bytes * 4),
            },
        )
        assert response.status_code == 413
        assert "request body" in response.json()["detail"]


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
        record = JobRecord.create(
            owner_subject="alice", sources=[Source.description("an app")]
        )
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
