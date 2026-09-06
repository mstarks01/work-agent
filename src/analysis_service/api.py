"""The `/v1` job API: the only production surface the front-end calls.

* ``POST /v1/jobs`` — submit an ordered list of sources, bounded in UTF-8 bytes
  and in count by this deployment's config, together with the frameworks to
  analyse them under. The framework list is required and non-empty, is drawn
  from what this install carries, and has no default on any path. The route
  returns a job handle. A subject may hold only ``max_active_jobs`` jobs in
  flight at once. The service refuses a submission past that with a 429 rather
  than queueing it, because a queued job still holds the caller's place in the
  provider quota, and a refusal is the only answer that sheds the load (OWASP
  LLM10).
* ``GET /v1/jobs/{id}`` — the canonical poll: status, per-node progress,
  timestamps and error info. Never the report.
* ``GET /v1/jobs/{id}/events`` — the same progression as SSE, resumable through
  ``Last-Event-ID``.
* ``GET /v1/jobs/{id}/report`` — the full report once the job completes, and a
  409 before that.
* ``GET /healthz`` — unauthenticated, for Cloud Run probes.

Every error body is RFC 9457 ``application/problem+json``. Every `/v1` route
requires a verified bearer token. Job reads are owner-only, and return 404
rather than 403 for a non-owner, so nobody can enumerate job IDs.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, cast
from uuid import uuid4

import anyio.to_thread
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)
from starlette._utils import get_route_path
from starlette.exceptions import HTTPException as StarletteHTTPException

from analysis_service import budgets
from analysis_service.auth import (
    AuthenticationError,
    TokenVerifier,
    build_verifier,
)
from analysis_service.budgets import BudgetPolicy
from analysis_service.deployment import Deployment
from analysis_service.errors import ConfigError
from analysis_service.frameworks import PACKAGES, package_for
from analysis_service.jobs import (
    TERMINAL_STATUSES,
    Admission,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    build_store,
    execute_job,
)
from analysis_service.parsing import ascii_int
from analysis_service.report import FrameworkName, FrameworkSelection
from analysis_service.sources import Source, SourceLimits, plain_name
from analysis_service.validation import ValidationIssue

logger = logging.getLogger(__name__)

# The raw-body guard is **derived** from the deployment's byte budget rather
# than configured beside it: it exists only to refuse an absurd payload before
# the JSON is parsed, and doubling the budget leaves ample room for framing and
# escaping. It is not the contract — the budget is (OWASP LLM10). Enforced by
# :class:`BodyLimitMiddleware`, which counts what arrives rather than trusting
# what was declared.
_BODY_SLACK = 2

_SSE_POLL_SECONDS = 0.2

#: How long a ``Last-Event-ID`` may be. The shape itself is
#: :func:`~analysis_service.parsing.ascii_int`'s question, and this is the
#: caller's own bound: an event sequence is a counter, so 18 digits is past any
#: run this service could produce. Anything else resumes from the start, which
#: is what a missing header does too.
_LAST_EVENT_ID_DIGITS = 18

#: How long a ``Content-Length`` may be. Nineteen digits covers any byte count
#: an HTTP body can state; a longer one is a client trying something rather
#: than a request this service could serve.
_CONTENT_LENGTH_DIGITS = 19

# The rungs of the input ladder, mapped to what HTTP calls them. An empty
# list is a malformed request rather than an oversized one: a job with no input
# is the wrong *shape*, and 413 would quote a byte count against a cap nobody
# came near. A repeated label is the same kind of wrong — malformed at any size,
# so 422 rather than a budget status. Per-source well-formedness never reaches
# here: a bad source fails body validation, which FastAPI answers with 422 too.
_STATUS_BY_RUNG = {"empty": 400, "duplicate-label": 422, "count": 413, "total": 413}

_bearer_scheme = HTTPBearer(auto_error=False)

_SUBMIT_PATH = "/v1/jobs"


class BodyLimitMiddleware:
    """Refuse an over-sized submission body, declared or not.

    Pure ASGI rather than a ``@app.middleware("http")`` function because the
    bound has to sit on the **receive channel**: the header-only version of this
    check read ``Content-Length`` and let anything without one through, so a
    chunked request — which carries no ``Content-Length`` at all — bypassed it
    entirely and was buffered and parsed in full before the source budget was
    ever consulted. A declared length is a claim; counting what arrives is the
    check (OWASP LLM10).

    Both halves are kept. The declared length is answered before a single byte
    is read, which is what lets an honest client be refused cheaply; the running
    count is what makes the bound true for a client that declares nothing or
    declares a lie.

    The body is drained here and replayed to the app rather than counted as the
    app pulls it, because a guard that raises out of ``receive`` does not get to
    write the response: FastAPI wraps any exception escaping its body parse in a
    400 ``There was an error parsing the body``, so the refusal would arrive as
    the wrong status with the cap unmentioned. Draining costs nothing this route
    was not already paying — the JSON body is buffered whole to be parsed — and
    the buffer is bounded by the cap it enforces.

    The cap is the deployment's byte budget with slack, not the budget itself:
    this exists only to refuse an absurd payload before the JSON is parsed, and
    :class:`~analysis_service.sources.SourceLimits` remains the contract.
    """

    def __init__(self, app, *, max_bytes: int, path: str = _SUBMIT_PATH) -> None:
        self._app = app
        self._max_bytes = max_bytes
        self._path = path

    async def __call__(self, scope, receive, send) -> None:
        # Match the path the router matches: get_route_path strips any ASGI
        # root_path first, while raw scope["path"] keeps it. Comparing the raw
        # path meant that under a path-prefixing proxy (uvicorn --root-path) the
        # router still routed POST /prefix/v1/jobs here while this guard saw a
        # mismatch and waved the unbounded body through.
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or get_route_path(scope) != self._path
        ):
            await self._app(scope, receive, send)
            return

        if self._declared_over_cap(scope):
            await self._refuse(scope, receive, send)
            return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            body += message.get("body", b"")
            if len(body) > self._max_bytes:
                await self._refuse(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        await self._app(scope, _replay(bytes(body), disconnected, receive), send)

    def _declared_over_cap(self, scope) -> bool:
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                # ``str.isdigit`` was this test and was wrong twice over: it
                # passes shapes ``int`` refuses, so a header a client controls
                # reached a 500 instead of a decision.
                declared = ascii_int(
                    value.decode("latin-1").strip(),
                    max_digits=_CONTENT_LENGTH_DIGITS,
                )
                return declared is not None and declared > self._max_bytes
        return False

    async def _refuse(self, scope, receive, send) -> None:
        response = _problem_response(
            413, f"request body exceeds the {self._max_bytes} byte limit"
        )
        await response(scope, receive, send)


def _replay(body: bytes, disconnected: bool, receive):
    """A receive channel that hands the drained body over exactly once.

    After it, the app falls through to the original channel, so a disconnect
    arriving later still reaches whoever is watching for one.
    """
    delivered = False

    async def replayed():
        nonlocal delivered
        if disconnected:
            return {"type": "http.disconnect"}
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replayed


class FrameworkRequest(BaseModel):
    """One framework a submission asks for, and the options it carries.

    ``options`` is declared as a free-form object here and validated against the
    named package's own ``options`` model one rung later, in the route. Two
    reasons it is not typed at this layer: the shape depends on the name in the
    same object, which Pydantic would need a discriminated union of every
    registered package to express; and *which* packages exist is a property of
    the deployment rather than of the wire schema, so a build that carries one
    framework would otherwise publish a body schema that a build carrying two
    does not.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    options: dict[str, Any] = Field(default_factory=dict)


#: How many sources a body may carry before the schema refuses it, whatever a
#: deployment's ``max_sources`` says. Far above any configured value — the
#: shipped one is ten — because it is not that limit: it is the point past
#: which validating the body costs more than reading it.
MAX_SOURCES_PER_BODY = 100

#: How many complaints a 422 carries. A body that is wrong in ten thousand
#: places is wrong; a caller needs to see that and the first few, not all of
#: them. Rendering every one made the refusal amplify the request 43 times.
MAX_RENDERED_ERRORS = 20


class JobSubmission(BaseModel):
    """Body of ``POST /v1/jobs``.

    ``sources`` carries a **structural** bound here and the deployment's real
    one at the route. Pydantic answers the first rung of the ladder — is each
    source well-formed? — and the route answers the rest, because the count and
    byte budgets are this deployment's config rather than a property of the
    schema.

    The structural bound exists because the first rung is not free. Without it
    Pydantic validated every element a body held and the error handler rendered
    every complaint it made, so 200 KB of empty objects — under
    ``BodyLimitMiddleware``'s cap — cost 2.2 seconds of event loop and an 8.5 MB
    response, and no admission bound saw any of it: the refusal happens while
    dependencies are being solved, before a ``JobRecord`` exists, so the
    ceiling, the rate and both token budgets count nothing. A caller could hold
    the loop with well under one request a second.

    ``frameworks`` is **required and non-empty**, with no default anywhere on
    the path. A submission that names none is refused rather than analysed under
    whatever this install happens to carry: a default set would make one body
    mean different things on two installs, and the caller would read no sign of
    it. Which names are acceptable is again the deployment's, so an unknown or
    uncarried name is refused by the route rather than by this schema.

    The list is ordered, and the order is kept: it is the order the report's
    analysis blocks carry.
    """

    model_config = ConfigDict(extra="forbid")

    # Upper bound only. An empty list is the input ladder's second rung and the
    # route answers it with a 400 and a sentence; a `min_length` here would take
    # that refusal off the ladder and give back a schema dump instead.
    sources: list[Source] = Field(max_length=MAX_SOURCES_PER_BODY)
    # Bounded by the number of frameworks this build knows: a valid selection
    # names each at most once (a repeat is refused downstream), so no legitimate
    # request exceeds it, and the schema refuses an over-long list before the
    # route's per-name work runs. The bound tracks the registry, so a new
    # package raises it with no edit here.
    frameworks: list[FrameworkRequest] = Field(min_length=1, max_length=len(PACKAGES))
    #: Bounded like a Source label rather than merely in length: it reaches the
    #: report a consumer renders, and this is the one entry point to the
    #: pipeline that refused it nothing but a length. A control, format or
    #: bidirectional character in a name has no reading a person wants and one
    #: a renderer might.
    system_name: Annotated[str, AfterValidator(plain_name)] | None = Field(
        default=None, min_length=1, max_length=200
    )


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


def _resolve_selection(
    carried: Sequence[FrameworkName], requested: Sequence[FrameworkRequest]
) -> list[FrameworkSelection]:
    """The submission's framework selection, or the 422 that refuses it.

    Three rungs, all of them shape rather than budget, and all refused before a
    job record exists — which is the point: a name this install does not carry
    can never reach the registry, so every later lookup is a defect rather than
    a caller's mistake.

    A repeat is refused rather than collapsed. ``analyses`` is a list in the
    report so a dropped block is visible, and silently de-duplicating here would
    hand back one block for two the caller asked for — the same invisible loss,
    moved one layer earlier.
    """
    names = [entry.name for entry in requested]
    repeated = sorted(name for name, count in Counter(names).items() if count > 1)
    if repeated:
        raise HTTPException(
            status_code=422,
            detail=f"frameworks repeats {', '.join(repeated)};"
            " name each framework at most once",
        )
    unknown = [name for name in names if name not in carried]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"this service does not carry {', '.join(unknown)};"
            f" it carries {', '.join(carried)}",
        )

    selections = []
    for entry in requested:
        # The check above is the narrowing: a name that reaches here is one this
        # deployment carries, and a carried name is a FrameworkName by
        # construction. The cast is what says so to the type checker, which
        # cannot read a membership test over a Literal.
        name = cast(FrameworkName, entry.name)
        # Validated against the package's own options model, which declares no
        # defaulted field — so an option this framework requires and the caller
        # omitted is refused here rather than filled in with a value the caller
        # never chose.
        try:
            package_for(name).options.model_validate(entry.options)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"options for framework {entry.name!r} are invalid:"
                f" {exc.error_count()} problem(s)",
            ) from None
        selections.append(FrameworkSelection(name=name, options=dict(entry.options)))
    return selections


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
        "the report is withheld: its execution identity is not blessed by this"
        " deployment's manifest",
        uncertified_nodes=[node.to_json() for node in result.uncertified],
        unexercised_tiers=list(result.unexercised),
    )


def _problem_response(
    status_code: int,
    detail: str,
    headers: Mapping[str, str] | None = None,
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
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> str:
    """FastAPI dependency: verify the bearer token, return its subject.

    ``verify`` is synchronous and may reach the network to fetch JWKS. Run it in
    a worker thread rather than inline: a blocking call on the event loop would
    stall every other in-flight request for the fetch, so one unverified token
    could freeze the whole worker (OWASP LLM10).
    """
    if credentials is None:
        raise _unauthorized()
    verifier: TokenVerifier = request.app.state.verifier
    try:
        return await anyio.to_thread.run_sync(verifier.verify, credentials.credentials)
    except AuthenticationError:
        raise _unauthorized() from None


async def _owned_job(request: Request, job_id: str, subject: str) -> JobRecord:
    """Fetch a job the subject owns; missing and foreign jobs are the same 404.

    The record comes back without its report. Every route here reads the
    envelope, and the one that serves the analysis asks the store for it
    separately, so no read copies a report to decide something about it.
    """
    store: JobStore = request.app.state.store
    record = await store.owned(job_id, subject)
    if record is None:
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
            # ``node`` is non-None on every node event; JobEvent's validator
            # rejects the pairing that would make it None.
            if event.kind == "node" and event.node is not None
        ],
        validation_issues=record.validation_issues or None,
        error=record.error,
    )


def _sse_frame(event) -> str:
    data = event.model_dump_json(exclude={"seq", "kind"}, exclude_none=True)
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {data}\n\n"


# What a caller is told for each refusal, keyed by outcome rather than branched
# on. Every message names the bound that stopped them and what clears it, and
# **none of them names another caller**: a global refusal reports the caller's
# own window and says the deployment is at its limit, because a figure covering
# every subject is activity a caller has no business reading. The operator's log
# carries it instead.
#
# A table because the machinery grew one entry per bound, which is what
# ``CLAUDE.md`` says to key — and because a missing key raises here rather than
# falling through to a message about the wrong bound.
_REFUSALS: dict[str, Callable[[Admission, int], str]] = {
    "at_ceiling": lambda admission, ceiling: (
        f"this token already has {admission.active} jobs in flight; the limit"
        f" is {ceiling}. Wait for one to reach a terminal state."
    ),
    "over_rate": lambda admission, ceiling: (
        "this token has started too many jobs in the current window."
        " Wait for the window to roll past your earlier jobs."
    ),
    "over_subject_budget": lambda admission, ceiling: (
        "this token has committed too many tokens in the current window."
        " Wait for the window to roll past your earlier jobs, or submit less"
        " text."
    ),
    "over_global_budget": lambda admission, ceiling: (
        "this deployment is at its consumption limit for the current window."
        " Retry later; nothing you can do clears this one."
    ),
}


def create_app(
    *,
    deployment: Deployment | None = None,
    store: JobStore | None = None,
    runner: PipelineRunner | None = None,
    verifier: TokenVerifier | None = None,
    limits: SourceLimits | None = None,
    job_deadline_seconds: float | None = None,
    max_active_jobs: int | None = None,
    budget: BudgetPolicy | None = None,
    frameworks: Sequence[FrameworkName] | None = None,
) -> FastAPI:
    """Build the service app; production defaults, injectable seams for tests.

    The runner and the gate the report route consults both come from one
    :class:`~analysis_service.deployment.Deployment`, so the manifest a job was
    certified against and the one the route enforces are the same object by
    construction. An injected ``runner`` is a test stand-in and carries no
    gate — a report it produced was never certified, so there is nothing for
    the route to withhold on.

    **The runner is resolved per submission, not held as one object.** A graph is
    built for one framework selection, so the app holds a *function* from a
    selection to its runner and the deployment memoizes behind it: two jobs
    naming the same frameworks share one composed graph, and a selection nobody
    has asked for yet costs nothing. An injected runner serves every selection,
    because a stand-in that answers one is answering the seam rather than the
    graph.

    ``limits`` bounds what one job may carry, ``job_deadline_seconds`` bounds
    how long one may run, ``max_active_jobs`` bounds how many one caller may
    have in flight, and ``frameworks`` is what a submission may select from. All
    four come from the deployment wherever there is one; a caller who injects a
    runner instead must state them, because reading a second configuration
    behind their back is how an app comes to enforce bounds its deployment never
    chose.
    """
    app = FastAPI(title="Security Analysis Service")
    app.state.store = store if store is not None else build_store()
    if runner is not None:
        app.state.runner_for = lambda selection: runner
        app.state.certification = None
    else:
        deployment = deployment if deployment is not None else Deployment.from_env()
        app.state.runner_for = deployment.runner
        app.state.certification = deployment.gate()
    if frameworks is None:
        if deployment is None:
            raise ConfigError(
                "create_app needs frameworks= when it is given a runner instead "
                "of a deployment"
            )
        frameworks = deployment.frameworks
    app.state.frameworks = tuple(frameworks)
    if limits is None:
        if deployment is None:
            raise ConfigError(
                "create_app needs limits= when it is given a runner instead of "
                "a deployment"
            )
        limits = deployment.resilience.source_limits()
    if job_deadline_seconds is None:
        if deployment is None:
            raise ConfigError(
                "create_app needs job_deadline_seconds= when it is given a "
                "runner instead of a deployment"
            )
        job_deadline_seconds = deployment.resilience.deadline_seconds()
    if max_active_jobs is None:
        if deployment is None:
            raise ConfigError(
                "create_app needs max_active_jobs= when it is given a runner "
                "instead of a deployment"
            )
        max_active_jobs = deployment.resilience.max_active_jobs
    if budget is None:
        if deployment is None:
            raise ConfigError(
                "create_app needs budget= when it is given a runner "
                "instead of a deployment"
            )
        budget = deployment.resilience.budget_policy()
    app.state.limits = limits
    app.state.job_deadline_seconds = job_deadline_seconds
    app.state.max_active_jobs = max_active_jobs
    app.state.budget = budget
    max_body_bytes = limits.max_total_bytes * _BODY_SLACK
    app.state.verifier = verifier if verifier is not None else build_verifier()

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        return _problem_response(exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        found = exc.errors()
        errors = [
            {
                "loc": ".".join(str(part) for part in error["loc"]),
                "msg": error["msg"],
            }
            for error in found[:MAX_RENDERED_ERRORS]
        ]
        if len(found) > MAX_RENDERED_ERRORS:
            errors.append(
                {
                    "loc": "",
                    "msg": f"and {len(found) - MAX_RENDERED_ERRORS} more problems"
                    " not shown",
                }
            )
        return _problem_response(422, "request body is invalid", errors=errors)

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        error_id = uuid4().hex
        logger.exception("unhandled error %s", error_id)
        return _problem_response(500, "an internal error occurred", error_id=error_id)

    app.add_middleware(BodyLimitMiddleware, max_bytes=max_body_bytes)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(_SUBMIT_PATH, status_code=201)
    async def submit_job(
        submission: JobSubmission,
        request: Request,
        background_tasks: BackgroundTasks,
        subject: str = Depends(require_subject),
    ) -> JSONResponse:
        store: JobStore = request.app.state.store
        ceiling: int = request.app.state.max_active_jobs
        # Shape before budget: a framework this service does not carry is the
        # wrong request at any size, and answering it with a byte count would
        # quote a cap the caller never came near.
        selection = _resolve_selection(
            request.app.state.frameworks, submission.frameworks
        )
        breach = request.app.state.limits.breach(submission.sources)
        if breach is not None:
            raise HTTPException(
                status_code=_STATUS_BY_RUNG[breach.rung], detail=breach.message
            )
        # The ladder runs first because the ceiling is now enforced by the same
        # call that inserts the record, and that call needs the record. The
        # ordering is the price of the atomicity: a count taken before the
        # ladder is a count another submission can land behind, which is the
        # race this seam exists to close. What the caller loses is that a
        # submission which breaches a rung *and* sits on the ceiling now hears
        # about the rung; both answers refuse it, and neither runs a model.
        record = JobRecord.create(
            owner_subject=subject,
            sources=submission.sources,
            frameworks=selection,
            system_name=submission.system_name,
            reserved_tokens=budgets.estimate(submission.sources, selection),
        )
        budget = request.app.state.budget
        admission = await store.reserve(record, ceiling=ceiling, budget=budget)
        if admission.outcome in _REFUSALS:
            # Every refusal is logged, because it is the only place these bounds
            # are observable from outside the caller they refused: a caller
            # sitting on one is either a client that needs a larger share or the
            # consumption they exist to stop, and neither is visible from a 429
            # nobody recorded. The log carries the deployment-wide figure the
            # response withholds.
            logger.warning(
                "subject %s refused (%s): %d in flight of %d, %d tokens of %d"
                " in this subject's window, %d of %d across the deployment",
                subject,
                admission.outcome,
                admission.active,
                ceiling,
                admission.subject_tokens,
                budget.max_tokens_per_window,
                admission.global_tokens,
                budget.global_max_tokens_per_window,
            )
            raise HTTPException(
                status_code=429, detail=_REFUSALS[admission.outcome](admission, ceiling)
            )
        if admission.outcome == "duplicate":
            # The API mints the id, so a collision is this service's defect and
            # not something the caller can act on or provoke. It gets the same
            # opaque 500 every unhandled error gets, and the id goes to the log.
            logger.error("job id %s was already held by the store", record.id)
            raise HTTPException(status_code=500, detail="an internal error occurred")
        background_tasks.add_task(
            execute_job,
            store,
            request.app.state.runner_for(record.selection()),
            record.id,
            deadline_seconds=request.app.state.job_deadline_seconds,
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
        withheld = _withheld_report(request, record)
        if withheld is not None:
            return withheld
        store: JobStore = request.app.state.store
        payload = await store.report_json(job_id, subject)
        if payload is None:
            logger.error("completed job %s has no report attached", record.id)
            raise HTTPException(status_code=500, detail="an internal error occurred")
        return JSONResponse(payload)

    @app.get("/v1/jobs/{job_id}/events")
    async def stream_events(
        job_id: str, request: Request, subject: str = Depends(require_subject)
    ) -> StreamingResponse:
        await _owned_job(request, job_id, subject)
        last_event_id = request.headers.get("last-event-id", "")
        seen = ascii_int(last_event_id, max_digits=_LAST_EVENT_ID_DIGITS) or 0
        store: JobStore = request.app.state.store

        async def event_stream():
            nonlocal seen
            while True:
                progress = await store.events_after(job_id, seen)
                if progress is None:
                    return
                status, events = progress
                for event in events:
                    seen = event.seq
                    yield _sse_frame(event)
                if status in TERMINAL_STATUSES:
                    return
                await asyncio.sleep(_SSE_POLL_SECONDS)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    return app
