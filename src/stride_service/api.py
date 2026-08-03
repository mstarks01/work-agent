"""The `/v1` job API: the only production surface the front-end calls.

* ``POST /v1/jobs`` — submit an ordered list of sources, bounded in UTF-8 bytes
  and in count by this deployment's config; returns a job handle.
* ``GET /v1/jobs/{id}`` — canonical poll: status, per-node progress,
  timestamps, error info; never the report.
* ``GET /v1/jobs/{id}/events`` — the same progression as SSE, resumable via
  ``Last-Event-ID``.
* ``GET /v1/jobs/{id}/report`` — the full report once completed; 409 before.
* ``GET /healthz`` — unauthenticated, for Cloud Run probes.

Every error body is RFC 9457 ``application/problem+json``. Every `/v1` route
requires a verified bearer token; job reads are owner-only and return 404 — not
403 — for non-owners, so job IDs cannot be enumerated.
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
    TokenVerifier,
    build_verifier,
)
from stride_service.deployment import Deployment
from stride_service.errors import ConfigError
from stride_service.jobs import (
    TERMINAL_STATUSES,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    build_store,
    execute_job,
)
from stride_service.sources import Source, SourceLimits
from stride_service.validation import ValidationIssue

logger = logging.getLogger(__name__)

# The raw-body guard is **derived** from the deployment's byte budget rather
# than configured beside it: it exists only to refuse an absurd payload before
# the JSON is parsed, and doubling the budget leaves ample room for framing and
# escaping. It is not the contract — the budget is (OWASP LLM10).
_BODY_SLACK = 2

_SSE_POLL_SECONDS = 0.2

# The rungs of the input ladder, mapped to what HTTP calls them. An empty
# list is a malformed request rather than an oversized one: a job with no input
# is the wrong *shape*, and 413 would quote a byte count against a cap nobody
# came near. A repeated label is the same kind of wrong — malformed at any size,
# so 422 rather than a budget status. Per-source well-formedness never reaches
# here: a bad source fails body validation, which FastAPI answers with 422 too.
_STATUS_BY_RUNG = {"empty": 400, "duplicate-label": 422, "count": 413, "total": 413}

_bearer_scheme = HTTPBearer(auto_error=False)


class JobSubmission(BaseModel):
    """Body of ``POST /v1/jobs``; no client-controlled knobs in v1.

    ``sources`` is typed but not bounded here. Pydantic answers the first rung
    of the ladder — is each source well-formed? — and the route answers the
    rest, because the count and byte budgets are this deployment's config
    rather than a property of the schema.
    """

    model_config = ConfigDict(extra="forbid")

    sources: list[Source]
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


def _withheld_report(request: Request, record: JobRecord) -> JSONResponse | None:
    """The problem response for a report this deployment must not serve, if any.

    Withholding the report rather than failing the job is deliberate: a
    ``failed`` job carries no report at all, and the fingerprints that *prove*
    the drift live in the report, so failing would destroy the evidence an
    operator needs. The job stays ``completed`` and the envelope refuses.

    The body names the unblessed nodes and their hashes, never the report's
    contents — enough to act on, nothing that leaks the analysis past a gate
    that just decided not to serve it.
    """
    gate = getattr(request.app.state, "certification", None)
    result = record.certification
    if gate is None or result is None or not gate.withholds(result):
        return None
    return _problem_response(
        409,
        "the report is withheld: its generation identity is not blessed by this"
        " deployment's manifest",
        uncertified_nodes=[node.to_json() for node in result.uncertified],
        unexercised_tiers=list(result.unexercised),
    )


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
    """FastAPI dependency: verify the bearer token, return its subject."""
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
    deployment: Deployment | None = None,
    store: JobStore | None = None,
    runner: PipelineRunner | None = None,
    verifier: TokenVerifier | None = None,
    limits: SourceLimits | None = None,
) -> FastAPI:
    """Build the service app; production defaults, injectable seams for tests.

    The runner and the gate the report route consults both come from one
    :class:`~stride_service.deployment.Deployment`, so the manifest a job was
    certified against and the one the route enforces are the same object by
    construction. An injected ``runner`` is a test stand-in and carries no
    gate — a report it produced was never certified, so there is nothing for
    the route to withhold on.

    ``limits`` bounds what one job may carry. It comes from the deployment
    wherever there is one; a caller who injects a runner instead must state it,
    because reading a second configuration behind their back is how an app
    comes to enforce bounds its deployment never chose.
    """
    app = FastAPI(title="STRIDE Threat-Modeling Service")
    app.state.store = store if store is not None else build_store()
    if runner is not None:
        app.state.runner = runner
        app.state.certification = None
    else:
        deployment = deployment if deployment is not None else Deployment.from_env()
        app.state.runner = deployment.runner()
        app.state.certification = deployment.gate()
    if limits is None:
        if deployment is None:
            raise ConfigError(
                "create_app needs limits= when it is given a runner instead of "
                "a deployment"
            )
        limits = deployment.resilience.source_limits()
    app.state.limits = limits
    max_body_bytes = limits.max_total_bytes * _BODY_SLACK
    app.state.verifier = verifier if verifier is not None else build_verifier()

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
            if content_length.isdigit() and int(content_length) > max_body_bytes:
                return _problem_response(
                    413,
                    f"request body exceeds the {max_body_bytes} byte limit",
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
        breach = request.app.state.limits.breach(submission.sources)
        if breach is not None:
            raise HTTPException(
                status_code=_STATUS_BY_RUNG[breach.rung], detail=breach.message
            )
        record = JobRecord.create(
            owner_subject=subject,
            sources=submission.sources,
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
        withheld = _withheld_report(request, record)
        if withheld is not None:
            return withheld
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
