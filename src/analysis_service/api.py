"""The `/v1` job API: the only production surface the front-end calls.

* ``POST /v1/jobs`` — submit an ordered list of sources, bounded in UTF-8 bytes
  and in count by this deployment's config, together with the frameworks to
  analyse them under. The framework list is required and non-empty, is drawn
  from what this install carries, and has no default on any path. The route
  returns a job handle. An owner may hold only ``max_active_jobs`` jobs in
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
* ``GET /v1/jobs/{id}/changes`` — for a follow-up, how each finding moved since
  the report its answers came from, under the same rule as both reports.
* ``GET /healthz`` — unauthenticated, for Cloud Run probes.

Every error body is RFC 9457 ``application/problem+json``. Every `/v1` route
requires a verified bearer token. The token's issuer and ``sub`` name a
**Principal**, and its owner ID is what owns a job (ADR 0064). Job reads are
owner-only, and return 404 rather than 403 for a non-owner, so nobody can
enumerate job IDs. A token whose principal does not exist yet gets the same 404:
only a submission creates a principal.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from functools import partial
from http import HTTPStatus
from typing import Annotated, Any, TypeVar
from uuid import uuid4

import anyio.to_thread
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from analysis_service import budgets
from analysis_service.answer_round import (
    MAX_SKIPS,
    AlreadyResumed,
    Answers,
    SkipKey,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.answer_sets import AnswerSet
from analysis_service.auth import (
    AuthenticationError,
    TokenVerifier,
    build_verifier,
)
from analysis_service.budgets import BudgetPolicy
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.deployment import Deployment
from analysis_service.errors import ConfigError
from analysis_service.fact_answers import MAX_FACT_ANSWERS, MAX_HELD_FACTS, FactAnswer
from analysis_service.frameworks import PACKAGES
from analysis_service.graph import ENTRY_EXTRACT
from analysis_service.job_answers import (
    DraftSaved,
    JobAnswers,
    JobDataMissing,
    NoCatalog,
    NoSuchJob,
    NotAdmitted,
    NotCompleted,
    Refused,
    RunnerUnavailable,
    SaveConflict,
    Start,
    Started,
    UnreadableSchema,
    Withheld,
)
from analysis_service.jobs import (
    TERMINAL_STATUSES,
    Admission,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    event_frames,
    execute_job,
    is_follow_up,
)
from analysis_service.links import (
    MAX_LINK_ANSWERS,
    LinkAnswer,
    NoCatalogError,
    with_link_answers,
)
from analysis_service.parsing import ascii_int
from analysis_service.principals import Identity
from analysis_service.report import FrameworkSelection
from analysis_service.report_changes import report_changes
from analysis_service.selection import SelectionError, resolve_selection
from analysis_service.sources import Source, SourceLimits, clean_system_name
from analysis_service.store_backends import audited_delete, build_store, store_lifespan
from analysis_service.validation import ValidationIssue

logger = logging.getLogger(__name__)

# The raw-body guard is **derived** from the deployment's byte budget rather
# than configured beside it: it exists only to refuse an absurd payload before
# the JSON is parsed, and doubling the budget leaves ample room for framing and
# escaping. It is not the contract — the budget is (OWASP LLM10). Enforced by
# :class:`BodyLimitMiddleware`, which counts what arrives rather than trusting
# what was declared.
_BODY_SLACK = 2

#: How many event streams one owner may hold open at once. Each stream polls
#: the store until its job is terminal, and a job that waits on answers is not,
#: so a stream can stay open for as long as its caller keeps it. A page follows
#: one job at a time; eight leaves room for a few tabs and a reconnect.
MAX_EVENT_STREAMS_PER_OWNER = 8

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
    """Refuse an over-sized request body, declared or not, on every POST route.

    Every POST route is capped, not a named one. FastAPI reads and parses a
    body before it resolves the route's dependencies, and authentication is a
    dependency, so an uncapped route buffers whatever an anonymous caller sends
    before it answers 401. A cap keyed to one path would leave the other routes
    open in exactly that way.

    Pure ASGI rather than a ``@app.middleware("http")`` function because the
    bound has to sit on the **receive channel**: a ``Content-Length`` check
    alone would let a chunked request through, because it carries no
    ``Content-Length`` at all, so this counts what arrives. A declared length is
    a claim; counting what arrives is the check (OWASP LLM10).

    Both halves are kept. The declared length is answered before a single byte
    is read, which is what lets an honest client be refused cheaply; the running
    count is what makes the bound true for a client that declares nothing or
    declares a lie.

    The body is drained here and replayed to the app rather than counted as the
    app pulls it, because a guard that raises out of ``receive`` does not get to
    write the response: FastAPI wraps any exception escaping its body parse in a
    400 ``There was an error parsing the body``, so the refusal would arrive as
    the wrong status with the cap unmentioned. Draining costs nothing a POST route
    does not already pay — the JSON body is buffered whole to be parsed — and
    the buffer is bounded by the cap it enforces.

    The cap is the deployment's byte budget with slack, not the budget itself:
    this exists only to refuse an absurd payload before the JSON is parsed, and
    :class:`~analysis_service.sources.SourceLimits` remains the contract.
    """

    def __init__(self, app, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
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
                # Not ``str.isdigit``: it passes shapes ``int`` refuses, so a
                # header a client controls would reach a 500 instead of a
                # decision.
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
    #: Read by :func:`~analysis_service.sources.clean_system_name`, the same
    #: rule the in-process engine applies, so the two entry points accept and
    #: refuse the same names. It reaches the report a consumer renders, so a
    #: control, format or bidirectional character is refused: none has a
    #: reading a person wants and each has one a renderer might.
    #:
    #: No ``min_length`` or ``max_length`` beside it. The rule trims first and
    #: measures what is left, so a schema bound on the untrimmed value would
    #: refuse a padded name the rule accepts — two readers of one bound, which
    #: is what this field exists to avoid. The body as a whole stays bounded by
    #: ``BodyLimitMiddleware``.
    system_name: str | None = None
    #: Answers to an earlier report's link questions: which component each
    #: principal is. Composed into one more Source and written as stated
    #: ``represented-by`` rows; see :mod:`analysis_service.links`.
    links: list[LinkAnswer] = Field(default_factory=list, max_length=MAX_LINK_ANSWERS)
    #: Stop after the assertion pass, publish the questions, and wait for a
    #: person to answer them. Off by default, so an autonomous caller's job
    #: never waits (#1252).
    questions: bool = False
    #: An earlier job of the caller's whose answers this job carries, and
    #: whose questions take each one they still ask (ADR 0079). It needs
    #: ``questions``: a job that does not pause never reads what it carries.
    answers_from: str | None = Field(default=None, max_length=64)

    @field_validator("system_name")
    @classmethod
    def _clean_system_name(cls, value: str | None) -> str | None:
        return clean_system_name(value)


class AnswersSubmission(BaseModel):
    """Answers to a job's questions, sent against that job.

    ``links`` answers which element a principal is, and ``facts`` answers the
    open facts a report's conditional findings rest on. Both empty is legal
    only for a job waiting on answers, where it means "continue without
    answers"; against a finished report it runs the follow-up on the saved
    draft alone. ``save`` keeps a waiting job's answers without starting the
    analysis (ADR 0078), or a finished report's draft without starting its
    follow-up (ADR 0070), and ``skip`` names questions of a waiting job's
    list the submitter skips for now: a fact by its key, a link question by
    its ``key`` (:data:`SkipKey`). ``presented`` names the early questions
    the page showed: the job records them as shown, and a skip is taken only
    of one of them (ADR 0069). ``revision`` is the revision the answers
    were read against, which a waiting job and every save require. One
    request carries at most the ``answer_limits`` the questions route
    publishes; a follow-up larger than that saves its draft in batches.
    """

    model_config = ConfigDict(extra="forbid")

    links: list[LinkAnswer] = Field(default_factory=list, max_length=MAX_LINK_ANSWERS)
    facts: list[FactAnswer] = Field(default_factory=list, max_length=MAX_FACT_ANSWERS)
    # Strict, as the first-run app reads them: ``true`` is no revision, and
    # ``"yes"`` is no save.
    save: bool = Field(default=False, strict=True)
    skip: list[SkipKey] = Field(default_factory=list, max_length=MAX_SKIPS)
    presented: list[UnknownKey] = Field(default_factory=list, max_length=MAX_HELD_FACTS)
    revision: int | None = Field(default=None, ge=0, strict=True)


class AmendmentSubmission(BaseModel):
    """An amendment to a paused job's description (ADR 0072): what is true
    that the model misses or reads wrongly, in the submitter's own words.
    ``revision`` is the revision the questions were read with."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    revision: int = Field(ge=0, strict=True)


class CorrectionsSubmission(BaseModel):
    """Corrections to a final report's answers (ADR 0054): changed values, or
    "I don't know" in place of an answer that was a guess."""

    model_config = ConfigDict(extra="forbid")

    facts: list[FactAnswer] = Field(min_length=1, max_length=MAX_FACT_ANSWERS)


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

    The rule is :func:`~analysis_service.selection.resolve_selection`, the one
    reader every entry point shares; this translates its refusal into the
    status the input ladder answers shape faults with. Refused before a job
    record exists, so a name this install does not carry never reaches the
    registry and every later lookup is a defect rather than a caller's mistake.
    """
    try:
        return resolve_selection(carried, requested)
    except SelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _answers(request: Request) -> JobAnswers:
    """The job answer operations this app serves, under its deployment's bounds.

    Built from the app's state on each request rather than held once, so a
    setting the state holds is read where it is, and a test that replaces
    one replaces what the routes read.
    """
    state = request.app.state
    return JobAnswers(
        store=state.store,
        runner_for=state.runner_for,
        limits=state.limits,
        ceiling=state.max_active_jobs,
        budget=state.budget,
        carries_catalog=state.carries_catalog,
        gate=state.certification,
    )


def _starter(request: Request, background_tasks: BackgroundTasks) -> Start:
    """Schedules an admitted job's run as this request's background task."""
    store: JobStore = request.app.state.store
    deadline: float = request.app.state.job_deadline_seconds

    def start(record: JobRecord, runner: PipelineRunner) -> None:
        background_tasks.add_task(
            execute_job, store, runner, record.id, deadline_seconds=deadline
        )

    return start


def _started(started: Started) -> JSONResponse:
    """``201`` with the new job's ID, its status and where to read it."""
    return JSONResponse(
        {"job_id": started.job_id, "status": started.status},
        status_code=201,
        headers={"Location": f"/v1/jobs/{started.job_id}"},
    )


async def _servable_report(
    request: Request, job_id: str, owner_id: str
) -> dict[str, Any]:
    """The report payload this caller may read, or the refusal that withholds it."""
    answers = _answers(request)
    record = await answers.owned(job_id, owner_id)
    answers.servable(record)
    store: JobStore = request.app.state.store
    payload = await store.report_json(job_id, owner_id)
    if payload is None:
        logger.error("completed job %s has no report attached", record.id)
        raise HTTPException(status_code=500, detail="an internal error occurred")
    return payload


class _CountedStream(StreamingResponse):
    """A stream that gives its slot back however it ends.

    Released around the whole response rather than in the generator: a client
    that leaves before the first frame never starts the generator, and under
    ASGI 2.4 a disconnect raises before a background task runs.
    """

    def __init__(self, release: Callable[[], None], *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


def _release_stream(streams: Counter[str], owner_id: str) -> None:
    streams[owner_id] -= 1
    if streams[owner_id] <= 0:
        del streams[owner_id]


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


async def require_identity(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> Identity:
    """FastAPI dependency: verify the bearer token, return its issuer and ``sub``.

    ``verify`` is synchronous and may reach the network to fetch JWKS. Run it in
    a worker thread rather than inline: a blocking call on the event loop would
    stall every other in-flight request for the fetch, so one unverified token
    could freeze the whole worker (OWASP LLM10).

    A token under the reserved local issuer is refused like any invalid token,
    whatever verifier accepted it. Only the engine and the web app name a
    principal under that issuer, so no token can reach their jobs.
    """
    if credentials is None:
        raise _unauthorized()
    verifier: TokenVerifier = request.app.state.verifier
    try:
        identity = await anyio.to_thread.run_sync(
            verifier.verify, credentials.credentials
        )
    except AuthenticationError:
        raise _unauthorized() from None
    if identity.is_local:
        logger.warning("rejected bearer token: its issuer is the reserved local one")
        raise _unauthorized()
    return identity


async def require_owner(
    request: Request, identity: Annotated[Identity, Depends(require_identity)]
) -> str:
    """FastAPI dependency: the owner ID of the caller's principal, by
    :meth:`JobAnswers.owner_id`. A caller with no principal owns no job, so
    every job route answers the 404 of a missing job."""
    job_id = request.path_params.get("job_id", "")
    return await _answers(request).owner_id(identity, job_id)


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


# What a caller is told for each refusal, keyed by outcome rather than branched
# on. Every message names the bound that stopped them and what clears it, and
# **none of them names another caller**: a global refusal reports the caller's
# own window and says the deployment is at its limit, because a figure covering
# every owner is activity a caller has no business reading. The operator's log
# carries it instead.
#
# A table because the machinery has one entry per bound, which is what
# ``AGENTS.md`` says to key — and because a missing key raises here rather than
# falling through to a message about the wrong bound. Each entry carries its
# status: a bound that clears with time is a 429, and a job that already has
# its resumed job is a 409.
_REFUSALS: dict[str, tuple[int, Callable[[Admission, int], str]]] = {
    "at_ceiling": (
        429,
        lambda admission, ceiling: (
            f"this token already has {admission.active} jobs in flight; the"
            f" limit is {ceiling}. Wait for one to reach a terminal state."
        ),
    ),
    "over_rate": (
        429,
        lambda admission, ceiling: (
            "this token has started too many jobs in the current window."
            " Wait for the window to roll past your earlier jobs."
        ),
    ),
    "over_owner_budget": (
        429,
        lambda admission, ceiling: (
            "this token has committed too many tokens in the current window."
            " Wait for the window to roll past your earlier jobs, or submit"
            " less text."
        ),
    ),
    "over_global_budget": (
        429,
        lambda admission, ceiling: (
            "this deployment is at its consumption limit for the current"
            " window. Retry later; nothing you can do clears this one."
        ),
    ),
    "resumed_already": (
        409,
        lambda admission, ceiling: (
            "this job's answers already started a job, whose ID the first"
            " answer returned; answer again only if that job fails"
        ),
    ),
    "parent_moved": (
        409,
        lambda admission, ceiling: (
            "the saved answers changed while this request read them; read the"
            " questions again"
        ),
    ),
    "principal_exists": (
        409,
        lambda admission, ceiling: (
            "another request with this token started its first job while this"
            " one was read; submit again"
        ),
    ),
}


#: Each typed refusal of :mod:`analysis_service.job_answers` and of the answer
#: rules, with the status it answers under. The message is the refusal's own.
_REFUSAL_STATUS: dict[type[Exception], int] = {
    NoSuchJob: 404,
    NotCompleted: 409,
    NoCatalog: 400,
    Refused: 400,
    SaveConflict: 409,
    AlreadyResumed: 409,
    NoCatalogError: 409,
    UnreadableSchema: 409,
    JobDataMissing: 500,
    RunnerUnavailable: 503,
}


async def _refusal_response(status: int, request: Request, exc: Exception):
    fields: dict[str, Any] = exc.fields() if isinstance(exc, Refused) else {}
    return _problem_response(status, str(exc), **fields)


T = TypeVar("T")


def _setting(
    value: T | None,
    deployment: Deployment | None,
    name: str,
    read: Callable[[Deployment], T],
) -> T:
    """One optional ``create_app`` setting: the caller's, else the deployment's.

    A caller who injects a runner instead of a deployment must state every
    setting, because reading a second configuration behind their back is how
    an app comes to enforce bounds its deployment never chose.
    """
    if value is not None:
        return value
    if deployment is None:
        raise ConfigError(
            f"create_app needs {name}= when it is given a runner instead of a"
            " deployment"
        )
    return read(deployment)


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
    carries_catalog: bool | None = None,
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

    ``carries_catalog`` says whether a job here builds an assertion catalog,
    which is the only reader of a link answer. It is the deployment's wherever
    there is one. Without a deployment it defaults to ``False``, so an app
    that cannot know refuses link answers rather than accepting what nothing
    reads.

    ``limits`` bounds what one job may carry, ``job_deadline_seconds`` bounds
    how long one may run, ``max_active_jobs`` bounds how many one caller may
    have in flight, and ``frameworks`` is what a submission may select from. All
    four come from the deployment wherever there is one; a caller who injects a
    runner instead must state them, because reading a second configuration
    behind their back is how an app comes to enforce bounds its deployment never
    chose.

    At startup, before the app takes a request, the app runs
    :func:`~analysis_service.store_backends.store_lifespan` over its store: it reads the
    retention setting, the store runs its startup check, and then the restart
    scan fails each job that is queued or running in the store.
    """
    # No schema routes: the schema states the bounds and the source kinds a
    # caller may not send, and the three webapps close them too.
    app = FastAPI(
        title="Security Analysis Service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lambda app: store_lifespan(app.state.store),
    )
    app.state.store = store if store is not None else build_store()
    app.state.event_streams = Counter()
    if runner is not None:
        app.state.runner_for = lambda selection, entry=ENTRY_EXTRACT: runner
        app.state.certification = None
    else:
        deployment = deployment if deployment is not None else Deployment.from_env()
        app.state.runner_for = deployment.runner
        app.state.certification = deployment.gate
    frameworks = _setting(frameworks, deployment, "frameworks", lambda d: d.frameworks)
    app.state.frameworks = tuple(frameworks)
    app.state.carries_catalog = (
        carries_catalog
        if carries_catalog is not None
        else deployment is not None and deployment.carries_catalog
    )
    app.state.limits = limits = _setting(
        limits, deployment, "limits", lambda d: d.resilience.source_limits()
    )
    app.state.job_deadline_seconds = _setting(
        job_deadline_seconds,
        deployment,
        "job_deadline_seconds",
        lambda d: d.job_deadline_seconds(),
    )
    app.state.max_active_jobs = _setting(
        max_active_jobs,
        deployment,
        "max_active_jobs",
        lambda d: d.resilience.max_active_jobs,
    )
    app.state.budget = _setting(
        budget, deployment, "budget", lambda d: d.resilience.budget_policy()
    )
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

    for refusal, status in _REFUSAL_STATUS.items():
        app.add_exception_handler(refusal, partial(_refusal_response, status))

    @app.exception_handler(Withheld)
    async def _withheld(request: Request, exc: Withheld):
        # The body names the unblessed nodes and their hashes, never the
        # report's contents.
        return _problem_response(
            409,
            str(exc),
            uncertified_nodes=[node.to_json() for node in exc.result.uncertified],
            unexercised_tiers=list(exc.result.unexercised),
        )

    @app.exception_handler(NotAdmitted)
    async def _not_admitted(request: Request, exc: NotAdmitted):
        if exc.admission.outcome in _REFUSALS:
            status, message = _REFUSALS[exc.admission.outcome]
            return _problem_response(status, message(exc.admission, exc.ceiling))
        # A duplicate id, or an outcome the table does not answer, is this
        # service's defect and not something the caller can act on or
        # provoke: the API mints the id, and the store's outcomes are held
        # to the table by test. Fails closed: only an admission starts a job.
        logger.error("job was not admitted: %s", exc.admission.outcome)
        return _problem_response(500, "an internal error occurred")

    @app.exception_handler(StaleRevision)
    async def _stale(request: Request, exc: StaleRevision):
        return _problem_response(409, f"{exc}; read the questions again")

    @app.exception_handler(SourcesOverLimit)
    async def _over_limit(request: Request, exc: SourcesOverLimit):
        return _problem_response(_STATUS_BY_RUNG[exc.breach.rung], str(exc))

    app.add_middleware(BodyLimitMiddleware, max_bytes=max_body_bytes)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(_SUBMIT_PATH, status_code=201)
    async def submit_job(
        submission: JobSubmission,
        request: Request,
        background_tasks: BackgroundTasks,
        identity: Annotated[Identity, Depends(require_identity)],
    ) -> JSONResponse:
        # Shape before budget: a framework this service does not carry is the
        # wrong request at any size, and answering it with a byte count would
        # quote a cap the caller never came near.
        selection = _resolve_selection(
            request.app.state.frameworks, submission.frameworks
        )
        if submission.links and not request.app.state.carries_catalog:
            raise HTTPException(
                status_code=400,
                detail="this deployment builds no assertion catalog, so nothing"
                " would read a link answer",
            )
        # Only onto a job that has sources of its own: answers alone would
        # carry the body past the ladder's empty-sources rung.
        try:
            sources = (
                with_link_answers(submission.sources, submission.links)
                if submission.sources
                else list(submission.sources)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        breach = request.app.state.limits.breach(sources)
        if breach is not None:
            raise HTTPException(
                status_code=_STATUS_BY_RUNG[breach.rung], detail=breach.message
            )
        # The ladder runs first because the ceiling is enforced by the same
        # call that inserts the record, and that call needs the record. The
        # ordering is the price of the atomicity: a count taken before the
        # ladder is a count another submission can land behind, which is the
        # race this seam exists to close. What the caller loses is that a
        # submission which breaches a rung *and* sits on the ceiling hears
        # about the rung; both answers refuse it, and neither runs a model.
        answers = _answers(request)
        submitter = await answers.submitter(identity)
        carried = await answers.carried_from(
            submission.answers_from, submitter.owner_id, asks=submission.questions
        )
        record = JobRecord.create(
            owner_id=submitter.owner_id,
            sources=sources,
            frameworks=selection,
            system_name=submission.system_name,
            answers=AnswerSet(links=tuple(submission.links)),
            carried=carried,
            ask_questions=submission.questions,
            reserved_tokens=budgets.estimate(sources, selection),
        )
        started = await answers.admit(
            record,
            start=_starter(request, background_tasks),
            principal=submitter.new,
        )
        return _started(started)

    @app.post("/v1/jobs/{job_id}/answers", status_code=201)
    async def answer_job(
        job_id: str,
        answers: AnswersSubmission,
        request: Request,
        background_tasks: BackgroundTasks,
        owner_id: str = Depends(require_owner),
    ) -> JSONResponse:
        """Answer a finished report's questions, and resume its job from ``prepare``.

        The new job runs on the parent's own model and catalog with the answers
        written in, so an answer applies to the catalog that asked the
        question, and no extraction or assertion pass runs again (#1252). It
        is admitted as a new job, against the ceiling and the token budget,
        because its lanes and critics spend model calls.

        With ``save``, a waiting job keeps the answers, and a finished
        report keeps them as a draft; either answers ``200`` with its own ID,
        no model runs, and a save never starts the analysis. A report's run
        composes its draft with what it is sent (ADR 0070). A waiting job,
        and every save, takes answers only against the current revision:
        ``400`` where none is sent, ``409`` where it has moved.
        """
        outcome = await _answers(request).answer(
            job_id,
            owner_id,
            Answers(
                given=AnswerSet(links=tuple(answers.links), facts=tuple(answers.facts)),
                save=answers.save,
                skips=answers.skip,
                presented=answers.presented,
                revision=answers.revision,
            ),
            start=_starter(request, background_tasks),
        )
        if isinstance(outcome, Started):
            return _started(outcome)
        body: dict[str, Any] = {"job_id": outcome.job_id, "saved": True}
        if isinstance(outcome, DraftSaved):
            body["revision"] = outcome.revision
        return JSONResponse(body)

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobStatusView,
        response_model_exclude_none=True,
    )
    async def get_job(
        job_id: str, request: Request, owner_id: str = Depends(require_owner)
    ) -> JobStatusView:
        record = await _answers(request).owned(job_id, owner_id)
        return _status_view(record)

    @app.delete("/v1/jobs/{job_id}", status_code=204)
    async def delete_job(
        job_id: str, request: Request, owner_id: str = Depends(require_owner)
    ) -> Response:
        """Delete a job in a terminal status, and keep its usage (ADR 0066).

        ``204`` where the job is deleted, and each route then reads it as
        missing. ``409`` where the job is queued or running. A missing job,
        another owner's job and an ID of another shape share one ``404``.
        """
        record = await _answers(request).owned(job_id, owner_id)
        if record.status not in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="the job is queued or running; delete it when it ends",
            )
        store: JobStore = request.app.state.store
        if await audited_delete(store, job_id, owner_id, "owner") is None:
            raise NoSuchJob(job_id)
        return Response(status_code=204)

    @app.get("/v1/jobs/{job_id}/report")
    async def get_report(
        job_id: str, request: Request, owner_id: str = Depends(require_owner)
    ) -> JSONResponse:
        return JSONResponse(await _servable_report(request, job_id, owner_id))

    @app.get("/v1/jobs/{job_id}/changes")
    async def get_changes(
        job_id: str, request: Request, owner_id: str = Depends(require_owner)
    ) -> JSONResponse:
        """How each finding of a follow-up moved since the report before it (#561).

        Both reports are served under their own rule first, so a comparison
        never names a finding of a report this caller may not read. ``409``
        where the job is not a follow-up, the store no longer holds the report
        its answers came from, or either record has a schema version of
        another major.
        """
        answers = _answers(request)
        answers.servable(await answers.parsable(job_id, owner_id))
        store: JobStore = request.app.state.store
        # The envelope leaves the resumption out, so it is read by name.
        resumption = await store.resumption(job_id, owner_id)
        if not is_follow_up(resumption):
            raise HTTPException(
                status_code=409,
                detail="the job is not a follow-up: it resumed from no finished report",
            )
        try:
            parent = await answers.parsable(resumption.parent_id, owner_id)
        except NoSuchJob:
            raise HTTPException(
                status_code=409,
                detail="the report this follow-up's answers came from is no longer held",
            ) from None
        answers.servable(parent)
        before = await store.report(resumption.parent_id, owner_id)
        after = await store.report(job_id, owner_id)
        if before is None or after is None:
            logger.error("follow-up %s or its parent has no report attached", job_id)
            raise HTTPException(status_code=500, detail="an internal error occurred")
        changes = await anyio.to_thread.run_sync(report_changes, before, after)
        return JSONResponse(
            {
                "job_id": job_id,
                "parent_id": resumption.parent_id,
                "findings": [change.to_json() for change in changes],
            }
        )

    @app.post("/v1/jobs/{job_id}/amendments", status_code=201)
    async def amend_job(
        job_id: str,
        body: AmendmentSubmission,
        request: Request,
        background_tasks: BackgroundTasks,
        owner_id: str = Depends(require_owner),
    ) -> JSONResponse:
        """Extract a paused job's description again with an amendment (ADR 0072).

        Starts a **new** job from the paused job's sources with the amendment
        added; it pauses again, and carries the paused job's answers, which
        its own questions take where they still ask them. ``201`` with its
        ``job_id``. ``400`` where the job does not wait on answers, ``409``
        where its revision has moved or a job already locks it.
        """
        started = await _answers(request).amend(
            job_id,
            owner_id,
            body.text,
            revision=body.revision,
            start=_starter(request, background_tasks),
        )
        return _started(started)

    @app.post("/v1/jobs/{job_id}/corrections")
    async def correct_job(
        job_id: str,
        body: CorrectionsSubmission,
        request: Request,
        owner_id: str = Depends(require_owner),
    ) -> JSONResponse:
        """Correct a final report's answers, and run nothing (ADR 0054).

        The corrections are kept beside the report; the report is not
        rewritten. ``400`` where the job's report is not final, a correction
        names no answer the report read, or changes none.
        """
        corrected = await _answers(request).correct(job_id, owner_id, body.facts)
        return JSONResponse(
            {
                "job_id": corrected.job_id,
                "corrections": [
                    fact.model_dump(mode="json") for fact in corrected.corrections
                ],
                "corrected_findings": corrected.corrected_findings,
            }
        )

    @app.get("/v1/jobs/{job_id}/questions")
    async def get_questions(
        job_id: str, request: Request, owner_id: str = Depends(require_owner)
    ) -> JSONResponse:
        """What a job asks the submitter, derived at read time.

        A job waiting on answers asks about the checkpoint it holds, and its
        open facts are ranked before any finding exists. A finished job asks
        about its report, served under the report's own rule, so a report that
        is withheld withholds its questions too.
        """
        payload = await _answers(request).questions(job_id, owner_id)
        return JSONResponse({"job_id": job_id} | payload)

    @app.get("/v1/jobs/{job_id}/events")
    async def stream_events(
        job_id: str, request: Request, owner_id: str = Depends(require_owner)
    ) -> StreamingResponse:
        await _answers(request).owned(job_id, owner_id)
        streams: Counter[str] = request.app.state.event_streams
        if streams[owner_id] >= MAX_EVENT_STREAMS_PER_OWNER:
            raise HTTPException(
                status_code=429,
                detail=f"{MAX_EVENT_STREAMS_PER_OWNER} event streams are open"
                " for this token; close one first",
            )
        streams[owner_id] += 1
        last_event_id = request.headers.get("last-event-id", "")
        seen = ascii_int(last_event_id, max_digits=_LAST_EVENT_ID_DIGITS) or 0
        store: JobStore = request.app.state.store
        frames = event_frames(store, job_id, seen, gone=request.is_disconnected)

        return _CountedStream(
            partial(_release_stream, streams, owner_id),
            frames,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    return app
