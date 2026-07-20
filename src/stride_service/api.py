"""The `/v1` job API: the only production surface the front-end calls.

Implements the REST contract from wayfinder ticket 008 — a custom job-oriented
API wrapping the (stubbed) pipeline runner; ADK's stock routes are never
mounted here:

* ``POST /v1/jobs`` — submit, 100 KB description cap, returns a job handle.
* ``GET /v1/jobs/{id}`` — canonical poll: status, per-node progress,
  timestamps, error info; never the report.
* ``GET /v1/jobs/{id}/events`` — the same progression as SSE, resumable via
  ``Last-Event-ID``.
* ``GET /v1/jobs/{id}/report`` — the full report once completed; 409 before.
* ``GET /healthz`` — unauthenticated, for Cloud Run probes.

Every error body is RFC 9457 ``application/problem+json``. Every `/v1` route
requires a Ping JWT; job reads are owner-only and return 404 — not 403 — for
non-owners, so job IDs cannot be enumerated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from http import HTTPStatus
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from stride_service.auth import (
    AuthenticationError,
    PingAuthSettings,
    PingJwtVerifier,
    TokenVerifier,
)
from stride_service.jobs import (
    TERMINAL_STATUSES,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    StubPipelineRunner,
    execute_job,
)
from stride_service.validation import ValidationIssue

logger = logging.getLogger(__name__)

# Authoritative input cap from ticket 008: the submitted description, in UTF-8
# bytes. The raw-body limit adds slack for JSON framing and escaping so the
# middleware can reject oversized payloads before parsing them.
MAX_DESCRIPTION_BYTES = 100 * 1024
MAX_REQUEST_BODY_BYTES = 120 * 1024

_SSE_POLL_SECONDS = 0.2

_bearer_scheme = HTTPBearer(auto_error=False)


class JobSubmission(BaseModel):
    """Body of ``POST /v1/jobs``; no client-controlled knobs in v1."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    system_name: str | None = Field(default=None, min_length=1, max_length=200)


class NodeCompletion(BaseModel):
    """One finished pipeline node, as shown in the poll response."""

    model_config = ConfigDict(extra="forbid")

    node: str
    at: datetime


class JobStatusView(BaseModel):
    """The poll response: lightweight, never embeds the report."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: list[NodeCompletion]
    validation_issues: list[ValidationIssue] | None = None
    error: str | None = None


def _problem_response(
    status_code: int,
    detail: str,
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    """An RFC 9457 ``application/problem+json`` response."""
    body = {
        "type": "about:blank",
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        **extensions,
    }
    return JSONResponse(
        body,
        status_code=status_code,
        headers=headers,
        media_type="application/problem+json",
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="valid bearer credentials are required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_subject(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: verify the Ping JWT, return its subject."""
    if credentials is None:
        raise _unauthorized()
    verifier: TokenVerifier = request.app.state.verifier
    try:
        return verifier.verify(credentials.credentials)
    except AuthenticationError:
        raise _unauthorized() from None


async def _owned_job(request: Request, job_id: str, subject: str) -> JobRecord:
    """Fetch a job the subject owns; missing and foreign jobs are the same 404."""
    store: JobStore = request.app.state.store
    record = await store.get(job_id)
    if record is None or record.owner_subject != subject:
        raise HTTPException(status_code=404, detail="job not found")
    return record


def _status_view(record: JobRecord) -> JobStatusView:
    return JobStatusView(
        job_id=record.id,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        progress=[
            NodeCompletion(node=event.node, at=event.at)
            for event in record.events
            if event.kind == "node"
        ],
        validation_issues=record.validation_issues or None,
        error=record.error,
    )


def _sse_frame(event) -> str:
    data = event.model_dump_json(exclude={"seq", "kind"}, exclude_none=True)
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {data}\n\n"


def create_app(
    *,
    store: JobStore | None = None,
    runner: PipelineRunner | None = None,
    verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Build the service app; production defaults, injectable seams for tests."""
    app = FastAPI(title="STRIDE Threat-Modeling Service")
    app.state.store = store if store is not None else InMemoryJobStore()
    app.state.runner = runner if runner is not None else StubPipelineRunner()
    app.state.verifier = (
        verifier
        if verifier is not None
        else PingJwtVerifier(PingAuthSettings.from_env())
    )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        return _problem_response(exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {
                "loc": ".".join(str(part) for part in error["loc"]),
                "msg": error["msg"],
            }
            for error in exc.errors()
        ]
        return _problem_response(422, "request body is invalid", errors=errors)

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        error_id = uuid4().hex
        logger.exception("unhandled error %s", error_id)
        return _problem_response(500, "an internal error occurred", error_id=error_id)

    @app.middleware("http")
    async def _limit_submission_body(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/v1/jobs":
            content_length = request.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > MAX_REQUEST_BODY_BYTES:
                return _problem_response(
                    413,
                    f"request body exceeds the {MAX_REQUEST_BODY_BYTES} byte limit",
                )
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/jobs", status_code=201)
    async def submit_job(
        submission: JobSubmission,
        request: Request,
        background_tasks: BackgroundTasks,
        subject: str = Depends(require_subject),
    ) -> JSONResponse:
        if len(submission.description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"description exceeds the {MAX_DESCRIPTION_BYTES} byte limit",
            )
        record = JobRecord.create(
            owner_subject=subject,
            description=submission.description,
            system_name=submission.system_name,
        )
        await request.app.state.store.create(record)
        background_tasks.add_task(
            execute_job, request.app.state.store, request.app.state.runner, record.id
        )
        return JSONResponse(
            {"job_id": record.id, "status": record.status},
            status_code=201,
            headers={"Location": f"/v1/jobs/{record.id}"},
        )

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobStatusView,
        response_model_exclude_none=True,
    )
    async def get_job(
        job_id: str, request: Request, subject: str = Depends(require_subject)
    ) -> JobStatusView:
        record = await _owned_job(request, job_id, subject)
        return _status_view(record)

    @app.get("/v1/jobs/{job_id}/report")
    async def get_report(
        job_id: str, request: Request, subject: str = Depends(require_subject)
    ) -> JSONResponse:
        record = await _owned_job(request, job_id, subject)
        if record.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"job status is {record.status!r};"
                " the report exists only once the job is completed",
            )
        if record.report is None:
            logger.error("completed job %s has no report attached", record.id)
            raise HTTPException(status_code=500, detail="an internal error occurred")
        return JSONResponse(record.report.model_dump(mode="json"))

    @app.get("/v1/jobs/{job_id}/events")
    async def stream_events(
        job_id: str, request: Request, subject: str = Depends(require_subject)
    ) -> StreamingResponse:
        await _owned_job(request, job_id, subject)
        last_event_id = request.headers.get("last-event-id", "")
        seen = int(last_event_id) if last_event_id.isdigit() else 0
        store: JobStore = request.app.state.store

        async def event_stream():
            nonlocal seen
            while True:
                record = await store.get(job_id)
                if record is None:
                    return
                for event in record.events[seen:]:
                    seen = event.seq
                    yield _sse_frame(event)
                if record.status in TERMINAL_STATUSES and seen >= len(record.events):
                    return
                await asyncio.sleep(_SSE_POLL_SECONDS)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    return app
