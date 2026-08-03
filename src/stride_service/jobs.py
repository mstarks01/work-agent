"""Job lifecycle, persistence interface, and pipeline-runner interface.

The job side of the front-end API contract: the ``queued -> running ->
completed | failed | rejected`` state machine with illegal transitions refused,
an append-only per-job event log that backs both the poll response and the SSE
stream, and two open seams:

* :class:`JobStore` — the API only ever talks to this interface. Backends are
  selected at deploy time by ``STRIDE_JOB_STORE`` and constructed through
  :func:`build_store`, so a durable or shared backend is one registry entry.
  :class:`InMemoryJobStore` is the ``memory`` backend — the default,
  non-durable and per-instance.
* :class:`PipelineRunner` — the API runs jobs through this interface, never
  against a graph directly. :class:`stride_service.pipeline.AdkPipelineRunner`
  is the implementation; :class:`StubPipelineRunner` is the no-model stand-in
  that exercises the contract end to end.

A ``failed`` job stores only a generic error message — internal detail is
logged, never surfaced.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stride_service.certification import CertifyResult
from stride_service.report import (
    InputRef,
    Job,
    NodeRun,
    StrideReport,
    build_summary,
)
from stride_service.sources import Source
from stride_service.system_model import Process, SystemModel, TrustBoundary
from stride_service.validation import ValidationIssue

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "completed", "failed", "rejected"]

TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {"completed", "failed", "rejected"}
)

_LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    "queued": frozenset({"running"}),
    "running": TERMINAL_STATUSES,
    "completed": frozenset(),
    "failed": frozenset(),
    "rejected": frozenset(),
}

# Stored on a failed job in place of any internal detail.
GENERIC_FAILURE_MESSAGE = "internal error while running the analysis pipeline"

# Stored on a job the deadline killed. Deliberately *not* the generic message:
# a deadline is an operational fact about this deployment's bounds, not a
# detail of how the pipeline is built, so saying it leaks nothing a caller
# could act on (OWASP A09) while "internal error" would send them to retry an
# identical submission against an identical bound. It names no node, no model
# and no elapsed time — those go to the log, where the operator reads them.
DEADLINE_FAILURE_MESSAGE = (
    "the analysis exceeded this service's time budget and was stopped"
)


class InvalidTransitionError(ValueError):
    """A job was asked to move along an edge the lifecycle does not have."""


class JobEvent(BaseModel):
    """One entry in a job's append-only event log.

    ``seq`` is 1-based and dense (event N sits at ``events[N - 1]``), which is
    what lets the SSE endpoint resume from a ``Last-Event-ID`` by slicing.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    kind: Literal["status", "node"]
    at: datetime
    status: JobStatus | None = None
    node: str | None = None

    @model_validator(mode="after")
    def _check_payload_matches_kind(self) -> Self:
        if self.kind == "status" and (self.status is None or self.node is not None):
            raise ValueError("a status event carries exactly a status")
        if self.kind == "node" and (self.node is None or self.status is not None):
            raise ValueError("a node event carries exactly a node name")
        return self


class JobRecord(BaseModel):
    """Everything the service knows about one job.

    ``owner_subject`` is the auth token subject captured at submission; every
    read is checked against it. The record never leaves the service — API
    responses are separate views that expose only what each route contracts.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    owner_subject: str
    sources: list[Source] = Field(min_length=1)
    system_name: str | None = None
    status: JobStatus = "queued"
    created_at: datetime
    updated_at: datetime
    events: list[JobEvent] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    error: str | None = None
    report: StrideReport | None = None
    # The certification verdict for this run, or None if the job produced no
    # report. It lives on the record rather than on the report because every
    # derived report field recomputes *from the report* and this one cannot —
    # it depends on a mutable, deployment-local manifest. The report is
    # portable; the manifest is not. Operator-only: no route exposes it.
    certification: CertifyResult | None = None

    @classmethod
    def create(
        cls,
        *,
        owner_subject: str,
        sources: Sequence[Source],
        system_name: str | None = None,
    ) -> Self:
        """A fresh queued job with its initial status event recorded."""
        now = datetime.now(UTC)
        record = cls(
            id=f"job-{uuid4().hex}",
            owner_subject=owner_subject,
            sources=list(sources),
            system_name=system_name,
            created_at=now,
            updated_at=now,
        )
        record._append_event(kind="status", status="queued")
        return record

    def transition(self, new_status: JobStatus) -> None:
        """Move to ``new_status``, refusing edges outside the lifecycle."""
        if new_status not in _LEGAL_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"job {self.id} cannot move from {self.status!r} to {new_status!r}"
            )
        self.status = new_status
        self._append_event(kind="status", status=new_status)

    def record_node(self, node: str) -> None:
        """Log completion of one pipeline node."""
        self._append_event(kind="node", node=node)

    def _append_event(
        self, *, kind: Literal["status", "node"], status: JobStatus | None = None,
        node: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.events.append(
            JobEvent(seq=len(self.events) + 1, kind=kind, at=now, status=status,
                     node=node)
        )
        self.updated_at = now


class JobStore(Protocol):
    """Persistence seam for job records; the real backend is a deferred decision."""

    async def create(self, record: JobRecord) -> None: ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def save(self, record: JobRecord) -> None: ...


class InMemoryJobStore:
    """Dict-backed store; hands out copies so callers must ``save`` mutations."""

    def __init__(self) -> None:
        self._records: dict[str, JobRecord] = {}

    async def create(self, record: JobRecord) -> None:
        if record.id in self._records:
            raise ValueError(f"job {record.id!r} already exists")
        self._records[record.id] = record.model_copy(deep=True)

    async def get(self, job_id: str) -> JobRecord | None:
        record = self._records.get(job_id)
        return None if record is None else record.model_copy(deep=True)

    async def save(self, record: JobRecord) -> None:
        if record.id not in self._records:
            raise ValueError(f"job {record.id!r} does not exist")
        self._records[record.id] = record.model_copy(deep=True)


class JobStoreConfigError(ValueError):
    """The job-store configuration is missing or unusable."""


JobStoreFactory = Callable[[Mapping[str, str]], JobStore]

_FACTORIES: dict[str, JobStoreFactory] = {
    "memory": lambda env: InMemoryJobStore(),
}


def build_store(env: Mapping[str, str] = os.environ) -> JobStore:
    """Select and construct the configured job store; fail closed.

    The backend is chosen by ``STRIDE_JOB_STORE`` at deploy time — never by the
    request — so an empty or unknown value raises rather than silently falling
    back to non-durable, per-instance ``memory`` storage that loses every job on
    restart and isolates jobs behind a load balancer. Durable or shared storage
    is a new registry entry implementing the :class:`JobStore` protocol.
    """
    name = env.get("STRIDE_JOB_STORE", "").strip().lower()
    if not name:
        raise JobStoreConfigError("set STRIDE_JOB_STORE")
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise JobStoreConfigError(
            f"unknown job store {name!r}; known stores: {known}"
        ) from None
    return factory(env)


@dataclass(frozen=True)
class PipelineCompleted:
    """The pipeline produced a report, and the runner certified it.

    ``certification`` is ``None`` only where no gate was configured — the
    offline stand-ins, which have no manifest to certify against.
    """

    report: StrideReport
    certification: CertifyResult | None = None


@dataclass(frozen=True)
class PipelineRejected:
    """The input failed the validity gate after the repair pass."""

    issues: list[ValidationIssue]


PipelineOutcome = PipelineCompleted | PipelineRejected

# Called after each pipeline node completes, with the node's name.
NodeCallback = Callable[[str], Awaitable[None]]


class PipelineRunner(Protocol):
    """Execution seam for the ADK analysis graph."""

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome: ...


class StubPipelineRunner:
    """Stand-in runner: walks a few named nodes, returns an empty valid report."""

    nodes: tuple[str, ...] = ("extract", "validate", "prepare", "critic", "assemble")

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        for node in self.nodes:
            await on_node(node)
        return PipelineCompleted(report=self._stub_report(job))

    def _stub_report(self, job: JobRecord) -> StrideReport:
        model = SystemModel(
            processes=[
                Process(
                    id="process:stub-system",
                    name="Stub System",
                    technology="unknown",
                    trust_zone="boundary:system",
                    exposure="unknown",
                )
            ],
            trust_boundaries=[
                TrustBoundary(id="boundary:system", name="System", kind="other")
            ],
        )
        return StrideReport(
            job=Job(
                id=job.id,
                created_at=job.created_at,
                completed_at=datetime.now(UTC),
            ),
            # Built through the same constructor the real runner uses: two
            # independent digest computations is how a fixture comes to carry
            # an InputRef production would never produce.
            input=InputRef.of(
                system_name=job.system_name or "Stub System", sources=job.sources
            ),
            nodes=[NodeRun(node=node, duration_ms=0) for node in self.nodes],
            system_model=model,
            boundary_crossings=model.boundary_crossings(),
            threats=[],
            summary=build_summary([], [], model),
        )


async def execute_job(
    store: JobStore,
    runner: PipelineRunner,
    job_id: str,
    *,
    deadline_seconds: float,
) -> None:
    """Drive one queued job through the runner to a terminal state, under a deadline.

    Runner exceptions become a ``failed`` job carrying only the generic
    message — the exception itself is logged, never stored or surfaced.

    ``deadline_seconds`` bounds the whole run rather than any one call, which is
    the only place the bound can hold: the per-request timeout is multiplied by
    the retry count, and again by the number of LLM stages on the graph's
    longest path, so a job's tail was previously a product of numbers nobody
    chose. It is a required keyword rather than a defaulted one — a default here
    would be a deployment inheriting a deadline it never picked, which is the
    state :data:`~stride_service.resilience.SUPPORTED_VERSION` 4 exists to end.

    Expiry cancels the graph, so the in-flight provider calls are dropped rather
    than left to finish into a job nobody is waiting on. What the run had
    already completed is logged by node name: a deadline that keeps firing at
    the same node is the evidence that sizes ``timeout_ms``, and it is not
    recoverable from the record, whose event log the caller can see but the
    operator's alert cannot.

    A partial run is never a partial report. The deadline path stores no
    ``report`` at all — six analyst lanes are what makes the output a STRIDE
    model, and one that stopped halfway is a different method, not a shorter
    answer.
    """
    record = await store.get(job_id)
    if record is None:
        logger.error("job %s vanished before execution", job_id)
        return

    record.transition("running")
    await store.save(record)

    async def on_node(node: str) -> None:
        record.record_node(node)
        await store.save(record)

    started = datetime.now(UTC)
    try:
        async with asyncio.timeout(deadline_seconds):
            outcome = await runner.run(record, on_node)
    except TimeoutError:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        logger.error(
            "job %s exceeded the %.0fs deadline after %.1fs; nodes completed: %s",
            job_id,
            deadline_seconds,
            elapsed,
            [event.node for event in record.events if event.kind == "node"] or "none",
        )
        record.transition("failed")
        record.error = DEADLINE_FAILURE_MESSAGE
        await store.save(record)
        return
    except Exception:
        logger.exception("job %s failed in the pipeline", job_id)
        record.transition("failed")
        record.error = GENERIC_FAILURE_MESSAGE
        await store.save(record)
        return

    if isinstance(outcome, PipelineCompleted):
        record.transition("completed")
        record.report = outcome.report
        record.certification = outcome.certification
    else:
        record.transition("rejected")
        record.validation_issues = outcome.issues
    await store.save(record)
