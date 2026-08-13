"""Route contracts for the /v1 job API: auth, ownership, lifecycle, SSE."""

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from stride_service.api import _BODY_SLACK, create_app
from stride_service.auth import AuthenticationError
from stride_service.errors import ConfigError
from stride_service.jobs import (
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    NodeCallback,
    PipelineOutcome,
    PipelineRejected,
    StubPipelineRunner,
)
from stride_service.report import Report
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
# Far above anything the stub runner takes: these tests are about the routes,
# not the deadline. The deadline's own behaviour is tested in ``test_jobs``.
TEST_DEADLINE_SECONDS = 30
# High enough that the route tests never trip the ceiling — the stub runner
# finishes each job before the next submission, but a test that submits several
# should not depend on that. The ceiling's own behaviour is tested below.
TEST_MAX_ACTIVE_JOBS = 100


def make_client(
    runner=None,
    store=None,
    limits: SourceLimits = TEST_LIMITS,
    max_active_jobs: int = TEST_MAX_ACTIVE_JOBS,
) -> tuple[TestClient, InMemoryJobStore]:
    store = store if store is not None else InMemoryJobStore()
    app = create_app(
        store=store,
        runner=runner if runner is not None else StubPipelineRunner(),
        verifier=FakeVerifier(),
        limits=limits,
        job_deadline_seconds=TEST_DEADLINE_SECONDS,
        max_active_jobs=max_active_jobs,
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


class TestInjectedBoundsMustBeStated:
    """An injected runner brings no config, so its bounds must be named.

    Reading a second configuration behind the caller's back is how an app comes
    to enforce bounds its deployment never chose — the same rule for what a job
    may carry, how long it may run, and how many a caller may have in flight.
    """

    def test_a_runner_without_a_deadline_is_refused(self):
        with pytest.raises(ConfigError, match="job_deadline_seconds"):
            create_app(
                store=InMemoryJobStore(),
                runner=StubPipelineRunner(),
                verifier=FakeVerifier(),
                limits=TEST_LIMITS,
            )

    def test_a_runner_without_limits_is_refused(self):
        with pytest.raises(ConfigError, match="limits"):
            create_app(
                store=InMemoryJobStore(),
                runner=StubPipelineRunner(),
                verifier=FakeVerifier(),
                job_deadline_seconds=TEST_DEADLINE_SECONDS,
                max_active_jobs=TEST_MAX_ACTIVE_JOBS,
            )

    def test_a_runner_without_a_concurrency_ceiling_is_refused(self):
        with pytest.raises(ConfigError, match="max_active_jobs"):
            create_app(
                store=InMemoryJobStore(),
                runner=StubPipelineRunner(),
                verifier=FakeVerifier(),
                limits=TEST_LIMITS,
                job_deadline_seconds=TEST_DEADLINE_SECONDS,
            )


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

    def test_an_undeclared_body_is_counted_rather_than_trusted(self):
        # The bypass the header-only guard had: a chunked request carries no
        # content-length at all, so a check that reads the header sees nothing
        # to compare and waves the whole payload through to be buffered and
        # parsed. Streaming the body from a generator is what makes httpx send
        # it chunked.
        client, _ = make_client()
        over_cap = TEST_LIMITS.max_total_bytes * _BODY_SLACK * 2

        def chunks():
            sent = 0
            while sent < over_cap:
                yield b"x" * 256
                sent += 256

        response = client.post(
            "/v1/jobs",
            content=chunks(),
            headers=auth() | {"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert "request body" in response.json()["detail"]

    def test_an_undeclared_body_under_the_cap_still_reaches_the_route(self):
        # The other half: counting must not refuse a chunked request that is
        # simply small. This one is well-formed and under budget, so it is the
        # route that answers, not the guard.
        client, _ = make_client()
        body = json.dumps(
            {"sources": [{"kind": "description", "label": "Doc", "text": "a system"}]}
        ).encode()

        response = client.post(
            "/v1/jobs",
            content=iter([body]),
            headers=auth() | {"content-type": "application/json"},
        )
        assert response.status_code == 201


class TestPoll:
    def test_completed_job_shows_progress_never_report(self):
        client, _ = make_client()
        job_id = submit(client)
        response = client.get(f"/v1/jobs/{job_id}", headers=auth())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert [p["node"] for p in body["progress"]] == list(StubPipelineRunner.nodes)
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
        report = Report.model_validate(response.json())
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


def seed(store: InMemoryJobStore, subject: str, status: JobStatus) -> JobRecord:
    """Park a job of ``subject``'s in ``status`` directly in the store.

    Submitting through the route cannot leave a job in flight: ``BackgroundTasks``
    runs the stub to completion before ``TestClient`` returns, so every submitted
    job is already terminal by the time the next one is sent.
    """
    record = JobRecord.create(
        owner_subject=subject, sources=[Source.description("an app")]
    )
    if status != "queued":
        record.transition("running")
    if status not in ("queued", "running"):
        record.transition(status)
    asyncio.run(store.create(record))
    return record


class TestConcurrencyCeiling:
    """The one bound that is per caller rather than per job (#113).

    Every other bound the route enforces is about one submission — its shape,
    its bytes, its duration. A caller who respects all of them and simply keeps
    submitting spends the deployment's whole provider quota, because each
    accepted job fans six category agents out on the ``strong`` tier
    (OWASP LLM10).
    """

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_a_submission_at_the_ceiling_is_refused(self, status):
        store = InMemoryJobStore()
        for _ in range(2):
            seed(store, "alice", status)
        client, _ = make_client(store=store, max_active_jobs=2)
        response = client.post(
            "/v1/jobs", json={"sources": one_source()}, headers=auth()
        )
        assert response.status_code == 429
        assert response.headers["content-type"] == "application/problem+json"
        assert "2" in response.json()["detail"]

    def test_a_refusal_queues_nothing(self):
        # A refusal that still created the record would be a queue with extra
        # steps: the caller's place in the provider quota would be held anyway.
        store = InMemoryJobStore()
        seed(store, "alice", "running")
        client, _ = make_client(store=store, max_active_jobs=1)
        client.post("/v1/jobs", json={"sources": one_source()}, headers=auth())
        assert asyncio.run(store.active_for("alice")) == 1

    def test_below_the_ceiling_a_submission_is_accepted(self):
        store = InMemoryJobStore()
        seed(store, "alice", "running")
        client, _ = make_client(store=store, max_active_jobs=2)
        response = client.post(
            "/v1/jobs", json={"sources": one_source()}, headers=auth()
        )
        assert response.status_code == 201

    def test_terminal_jobs_do_not_hold_a_slot(self):
        # The ceiling is self-clearing: finishing a job is what buys the next
        # one, which is why it needs no window and no timer.
        store = InMemoryJobStore()
        for _ in range(5):
            seed(store, "alice", "completed")
        client, _ = make_client(store=store, max_active_jobs=1)
        response = client.post(
            "/v1/jobs", json={"sources": one_source()}, headers=auth()
        )
        assert response.status_code == 201

    def test_the_ceiling_is_per_subject_not_per_service(self):
        # One noisy token must not lock every other caller out — that would be
        # the unbounded-consumption problem inverted, not solved.
        store = InMemoryJobStore()
        seed(store, "alice", "running")
        client, _ = make_client(store=store, max_active_jobs=1)
        response = client.post(
            "/v1/jobs", json={"sources": one_source()}, headers=auth("bob-token")
        )
        assert response.status_code == 201

    def test_the_ceiling_outranks_the_input_ladder(self):
        # A caller at their ceiling gets the same answer whatever they sent:
        # this is their budget, not a fact about the submission. Checking it
        # after the ladder would let a malformed body outrank it and make the
        # ceiling probe-able through requests that were never going to run.
        store = InMemoryJobStore()
        seed(store, "alice", "running")
        client, _ = make_client(store=store, max_active_jobs=1)
        response = client.post("/v1/jobs", json={"sources": []}, headers=auth())
        assert response.status_code == 429

    def test_a_refusal_is_logged(self, caplog):
        # A 429 nobody recorded makes the bound observable only to the caller it
        # refused; an operator cannot tell a client needing a larger share from
        # the consumption the ceiling exists to stop.
        store = InMemoryJobStore()
        seed(store, "alice", "running")
        client, _ = make_client(store=store, max_active_jobs=1)
        with caplog.at_level(logging.WARNING, logger="stride_service.api"):
            client.post("/v1/jobs", json={"sources": one_source()}, headers=auth())
        assert "alice" in caplog.text
        assert "concurrency ceiling" in caplog.text

    def test_an_unauthenticated_submission_is_still_401(self):
        # The ceiling is per subject, so it cannot be consulted before there is
        # one. Auth stays the outermost gate.
        store = InMemoryJobStore()
        seed(store, "alice", "running")
        client, _ = make_client(store=store, max_active_jobs=1)
        response = client.post("/v1/jobs", json={"sources": one_source()})
        assert response.status_code == 401


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
        assert [int(f["id"]) for f in frames] == list(range(4, 4 + len(frames)))
        assert frames[-1]["data"]["status"] == "completed"
