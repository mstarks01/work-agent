"""Job lifecycle, persistence interface, and pipeline-runner interface.

This is the job side of the front-end API contract. It holds the ``queued ->
running -> completed | failed | rejected`` state machine, which refuses illegal
transitions. A restart also fails a queued job, along the ``queued -> failed``
edge. It holds an append-only per-job event log that backs both the poll
response and the SSE stream. It also holds two open seams:

* :class:`JobStore`. The API only ever talks to this interface. A deployment
  selects a backend with ``ANALYSIS_JOB_STORE``, and :func:`build_store`
  constructs it from its row of :data:`JOB_STORES`. :class:`InMemoryJobStore`
  is the ``memory`` backend, which is non-durable and per-instance. The
  ``sql`` backend, :class:`analysis_service.sql_store.SqlJobStore`, keeps
  jobs in a database and their sources and reports in an object store, so
  they live through a restart. There is no default: :func:`build_store` fails
  closed rather than choosing one for a deployment that did not.
* :class:`PipelineRunner`. The API runs jobs through this interface, and never
  against a graph directly.
  :class:`analysis_service.pipeline.AdkPipelineRunner` is the implementation,
  and :class:`StubPipelineRunner` is the no-model stand-in that exercises the
  contract end to end.

A ``failed`` job stores only a generic error message. The service logs the
internal detail and never surfaces it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import chain
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self, TypeGuard, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from analysis_service.answer_sets import NO_ANSWERS, AnswerSet, HeldAnswerSet
from analysis_service.assertion_use import assertion_use
from analysis_service.assertions import AssertionRecord
from analysis_service.budgets import BudgetPolicy, measured_tokens, spent_tokens
from analysis_service.certification import CertifyResult
from analysis_service.claims import FrameworkAnalysis, FrameworkName, UnknownKey
from analysis_service.execution import GraphFailed
from analysis_service.fact_answers import MAX_HELD_FACTS, FactAnswer, SkipKey
from analysis_service.links import MAX_HELD_LINKS
from analysis_service.parsing import ascii_int
from analysis_service.principals import Identity, Principal, PrincipalTable
from analysis_service.report import (
    SCHEMA_VERSION,
    FrameworkSelection,
    InputRef,
    Job,
    NodeRun,
    Report,
)
from analysis_service.sources import Source
from analysis_service.system_model import Process, SystemModel, TrustBoundary
from analysis_service.validation import ValidationIssue

if TYPE_CHECKING:
    from analysis_service.objects import ObjectStore

logger = logging.getLogger(__name__)

#: The shape of every job ID :meth:`JobRecord.create` mints.
#: :meth:`~analysis_service.job_answers.JobAnswers.owned` checks a requested ID
#: against it before the lookup (#1528).
JOB_ID_PATTERN = re.compile(r"job-[0-9a-f]{32}")

JobStatus = Literal[
    "queued", "running", "completed", "failed", "rejected", "awaiting-answers"
]

# ``awaiting-answers`` ends the job's run: the job stopped after its assertion
# pass to ask a person, and holds its model and catalog. It is terminal for the
# lifecycle and for admission, so a job waiting on a person takes no in-flight
# slot and its tokens settle. Answering it starts a resumed job (#1252).
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {"completed", "failed", "rejected", "awaiting-answers"}
)

_LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    # A restart fails a job that never started (#1529).
    "queued": frozenset({"running", "failed"}),
    "running": TERMINAL_STATUSES,
    "completed": frozenset(),
    "failed": frozenset(),
    "rejected": frozenset(),
    "awaiting-answers": frozenset(),
}

# The statuses of a resumed job that read none of its answers into a report.
# A resumed job in one of these does not lock its parent, so the parent takes
# answers again.
NON_LOCKING_STATUSES: frozenset[JobStatus] = frozenset({"failed", "rejected"})


def parent_of(record: JobRecord) -> str | None:
    """The parent of ``record``: the job it resumes, or the one its amendment replaces.

    **The one reader of "which job can this one lock".** A resumed job and a
    job an amendment started each lock their parent, so the parent takes no
    answers, while they are in flight or have a report (ADR 0072).
    """
    if record.resumption is not None:
        return record.resumption.parent_id
    return record.amends


def locks_its_parent(status: JobStatus) -> bool:
    """True where a resumed job in ``status`` locks its parent: the parent takes
    no answers.

    **The one reader of "does a resumed job lock its parent".** The
    job store and the first-run app both ask it.
    """
    return status not in NON_LOCKING_STATUSES


def same_major(version: str) -> bool:
    """Whether ``version`` has the major number of this service's ``SCHEMA_VERSION``.

    **The one comparison of schema versions** (#1532). A record that another
    major wrote can hold a shape this service cannot parse, so each route that
    parses a stored record asks this first. A minor bump only adds fields, so a
    record from an older minor of this major reads as before.

    The major is the text before the first dot. Text that this service never
    writes, such as an empty string, has no major that matches, so it reads as
    another major.
    """
    return version.partition(".")[0] == SCHEMA_VERSION.partition(".")[0]


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

# Stored on a job that was queued or running when the service stopped. Like the
# deadline message, it names an operational fact and no internal detail. The
# service never runs the job again: that is a second paid run nobody approved.
RESTART_FAILURE_MESSAGE = (
    "the service restarted during the analysis; submit the job again"
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

    def sse_frame(self) -> str:
        """The event as one server-sent frame: ``seq`` as the id, the kind as
        the event name, and the rest as data. :func:`event_frames` writes it."""
        data = self.model_dump_json(exclude={"seq", "kind"}, exclude_none=True)
        return f"id: {self.seq}\nevent: {self.kind}\ndata: {data}\n\n"


class Checkpoint(BaseModel):
    """A run's **Valid System Model** and gated assertion record: what ``prepare`` reads.

    Held by a job that paused after its assertion pass, and copied onto a job
    resumed from it or from a finished report.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_model: SystemModel
    #: ``None`` where the run built no assertion catalog: its answers can then
    #: be about attributes and subjects, and never about a link or a row.
    assertions: AssertionRecord | None


class Resumption(BaseModel):
    """What a job resumed from an earlier one starts from (#1252).

    The parent's checkpoint, copied onto the new job at submission rather than
    read from the parent at run time: the job then carries everything its run
    needs, and a store that no longer holds the parent cannot change what the
    job was admitted to run.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_id: str
    checkpoint: Checkpoint
    #: True where the job resumes from a finished report's answers: the one
    #: follow-up. Its own report is final (ADR 0054).
    follow_up: bool
    #: The parent's certification verdict. The resumed report rests on the
    #: model and catalog the parent's run built, so its own verdict is combined
    #: with this one.
    certification: CertifyResult | None = None


def is_follow_up(resumption: Resumption | None) -> TypeGuard[Resumption]:
    """Whether a job resumed as a report's one follow-up, so its report is
    final (ADR 0054)."""
    return resumption is not None and resumption.follow_up


class JobRecord(BaseModel):
    """Everything the service knows about one job.

    ``owner_id`` is the owner ID of the **Principal** that submitted the job;
    every read is checked against it. The record never leaves the service — API
    responses are separate views that expose only what each route contracts.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    owner_id: str
    # The ``SCHEMA_VERSION`` of the service that created this record. It
    # covers the checkpoint, the resumption, the sources and the report, and
    # :func:`same_major` reads it before a route parses any of them (#1532).
    # No default, so a record that does not state its version fails to load.
    schema_version: str
    sources: list[Source] = Field(min_length=1)
    # What this job asked to be analysed under, in the order its report's blocks
    # will carry. Required and non-empty here as well as on the wire: the record
    # is what the runner is selected by and what the report's own ``job.frameworks``
    # is stamped from, so a record with no selection would name a graph that
    # cannot be built. No default, for the reason ``config/frameworks.toml``
    # carries none — a defaulted selection makes one submission mean different
    # things on two installs.
    frameworks: list[FrameworkSelection] = Field(min_length=1)
    system_name: str | None = None
    # The submitter's answers to an earlier report's link questions and open
    # facts. They are also composed into the answers Source, which is what an
    # answer's row quotes; these are what ``prepare`` writes the rows from. See
    # :mod:`analysis_service.links` and :mod:`analysis_service.questions`. One
    # request carries at most MAX_FACT_ANSWERS; a job holds every save's
    # (ADR 0070).
    answers: HeldAnswerSet = NO_ANSWERS
    # Every early question the pause presented, answered or not. The report
    # reads it to say what became of a follow-up question before the analysis.
    shown_early: list[UnknownKey] = Field(
        default_factory=list, max_length=MAX_HELD_FACTS
    )
    # A final report's corrections: answers its owner changed after the run,
    # kept beside the answers the run read. No model reads them (ADR 0054).
    corrections: list[FactAnswer] = Field(
        default_factory=list, max_length=MAX_HELD_FACTS
    )
    # A finished report's draft answers: what its follow-up saved in batches
    # and has not run. The run composes them with what it is sent (ADR 0070).
    draft: HeldAnswerSet = NO_ANSWERS
    # How many rounds a waiting job, or drafts a finished report, has saved. A
    # save or a continue names the revision it read, so a page left open on an
    # earlier round cannot write over a later one.
    round_revision: int = 0
    # Every early question and link question a waiting job's submitter
    # skipped for now and has not answered since. No round shows it again.
    skipped_early: list[SkipKey] = Field(
        default_factory=list, max_length=MAX_HELD_FACTS + MAX_HELD_LINKS
    )
    # Set on a job resumed from a finished one: its run starts at ``prepare``
    # from these, and runs no extraction and no assertion pass.
    resumption: Resumption | None = None
    # Set on a job an amendment started (ADR 0072): the paused job whose
    # description it extracts again with the amendment added. It locks that
    # job as a resumed job does.
    amends: str | None = None
    # The parent job's round revision this job's answers were composed from.
    # Admission refuses the job where a save moved it since, so a batch saved
    # in another tab never misses the run in silence (ADR 0070).
    parent_revision: int | None = None
    # The answers this job carries from an earlier job: the paused job its
    # amendment extracts again (ADR 0072), or the job a new analysis names
    # (ADR 0079). Its question set takes each one whose fact it still asks,
    # and lists the rest. The extraction never reads them.
    carried: HeldAnswerSet = NO_ANSWERS
    # Whether this job stops after its assertion pass to ask a person, and
    # waits for the answers. Off unless a submission asks for it, so an
    # autonomous run never waits.
    ask_questions: bool = False
    # What a paused job holds while it waits: the answers resume from it.
    checkpoint: Checkpoint | None = None
    status: JobStatus = "queued"
    created_at: datetime
    updated_at: datetime
    events: list[JobEvent] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    error: str | None = None
    report: Report | None = None
    # The certification verdict for this run, or None if the job produced no
    # report. It lives on the record rather than on the report because every
    # derived report field recomputes *from the report* and this one cannot —
    # it depends on a mutable, deployment-local manifest. The report is
    # portable; the manifest is not. Operator-only: no route exposes it.
    certification: CertifyResult | None = None
    # The two halves of this job's charge against its owner's window budget.
    # ``reserved_tokens`` is what admission held before anything was spent, from
    # the submission and the selection alone. ``measured_tokens`` replaces it
    # once the job is terminal, and is ``None`` until then — which is what makes
    # a window's total a scan over records rather than a counter something has
    # to remember to decrement. See :mod:`analysis_service.budgets`.
    reserved_tokens: int = Field(default=0, ge=0)
    measured_tokens: int | None = Field(default=None, ge=0)
    # What the graph ran on a job that finished without a report. A rejected
    # input still paid for extraction and repair, and the report that would
    # normally carry that measurement does not exist, so it is kept here and
    # settled from. A failed job's runs are what finished before a node
    # raised, which is a floor on its spend.
    unreported_nodes: list[NodeRun] = Field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        owner_id: str,
        sources: Sequence[Source],
        frameworks: Sequence[FrameworkSelection],
        system_name: str | None = None,
        answers: AnswerSet = NO_ANSWERS,
        shown_early: Sequence[UnknownKey] = (),
        skipped_early: Sequence[SkipKey] = (),
        resumption: Resumption | None = None,
        amends: str | None = None,
        parent_revision: int | None = None,
        carried: AnswerSet = NO_ANSWERS,
        ask_questions: bool = False,
        reserved_tokens: int = 0,
    ) -> Self:
        """A fresh queued job with its initial status event recorded."""
        now = datetime.now(UTC)
        record = cls(
            id=f"job-{uuid4().hex}",
            owner_id=owner_id,
            schema_version=SCHEMA_VERSION,
            sources=list(sources),
            frameworks=list(frameworks),
            system_name=system_name,
            answers=answers,
            shown_early=list(shown_early),
            skipped_early=list(skipped_early),
            resumption=resumption,
            amends=amends,
            parent_revision=parent_revision,
            carried=carried,
            ask_questions=ask_questions,
            created_at=now,
            updated_at=now,
            reserved_tokens=reserved_tokens,
        )
        record._append_event(kind="status", status="queued")
        return record

    def pauses(self) -> bool:
        """Whether this job's run stops after the assertion pass to ask a person.

        A resumed job never pauses: it is the run that answers came back to.
        """
        return self.ask_questions and self.resumption is None

    def final(self) -> bool:
        """Whether this job's report is final: the one follow-up wrote it (ADR 0054)."""
        return is_follow_up(self.resumption)

    def held_answers(self) -> AnswerSet:
        """Every answer this job holds, each later one over the earlier.

        **The one reader of what a new analysis carries** (ADR 0079). A final
        report's corrections lie over a report's draft, the draft over the
        answers the job's run read or its pause saved, and those over what
        the job carried. Nothing is checked here: the job that carries them
        checks each one against its own questions.
        """
        corrected = AnswerSet(facts=tuple(self.corrections))
        return corrected.over(self.draft.over(self.answers.over(self.carried)))

    def selection(self) -> tuple[FrameworkName, ...]:
        """This job's frameworks by name, in selection order.

        What a runner is looked up by. The options ride on the record and on the
        report's ``job`` block; they are job data rather than graph shape, so
        two jobs selecting the same frameworks with different options share one
        built graph.
        """
        return tuple(selection.name for selection in self.frameworks)

    def transition(self, new_status: JobStatus) -> None:
        """Move to ``new_status``, refusing edges outside the lifecycle.

        Reaching a terminal state also **settles this job's token charge**: the
        reservation admission held is replaced by what the run measured, so the
        owner's window shows what happened rather than what was feared. It
        happens here rather than at each of the four terminal call sites because
        this is the single funnel into a terminal state — a reservation a path
        forgot to settle would hold budget until the window rolled past it.

        **A job nothing measured keeps its reservation.** Settling it to zero
        would free the whole estimate for a run that had already reached node
        nine and paid for every call on the way, which turns the token budget
        into a bound any failing job clears: a caller whose submissions outrun
        the deadline would spend without limit while their window read empty.
        On the deadline path the graph is cancelled before it returns its node
        runs, and the reservation is the only figure the service holds. It
        over-counts, which is the direction a bound that must hold before
        anything is spent has to err in, and the window rolls past it either
        way.

        **A failed job settles to the larger of the two.** A node that raised
        leaves the runs that finished before it, which is a floor on the spend
        and not the spend, so the reservation stays where it is larger and the
        floor replaces it where the floor is larger.
        """
        if new_status not in _LEGAL_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"job {self.id} cannot move from {self.status!r} to {new_status!r}"
            )
        self.status = new_status
        if new_status in TERMINAL_STATUSES:
            measured = self.spent_tokens()
            if measured is not None and new_status == "failed":
                measured = max(measured, self.reserved_tokens)
            if measured is not None:
                self.measured_tokens = measured
        self._append_event(kind="status", status=new_status)

    def spent_tokens(self) -> int | None:
        """What this job's node runs measured, or ``None`` if nothing measured them.

        A completed job reads its report's node runs; a rejected one reads the
        runs the graph returned before the validity gate refused its output.
        Both are a real measurement, so both settle — including to zero, where a
        provider declined to meter every call. A failed job reads the runs that
        finished before a node raised.

        ``None`` means no node run reached this record at all, which is a
        different fact from a measured zero and is why the two are not one value:
        a job that ran out of time is unmeasured rather than free.
        """
        nodes = self.report.nodes if self.report is not None else self.unreported_nodes
        if not nodes:
            return None
        return measured_tokens(nodes)

    def record_node(self, node: str) -> None:
        """Log completion of one pipeline node."""
        self._append_event(kind="node", node=node)

    def _append_event(
        self,
        *,
        kind: Literal["status", "node"],
        status: JobStatus | None = None,
        node: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.events.append(
            JobEvent(
                seq=len(self.events) + 1, kind=kind, at=now, status=status, node=node
            )
        )
        self.updated_at = now


# What one admission attempt did.
#
# Four refusals rather than one, because they clear at different times and a
# caller needs to know which: ``at_ceiling`` clears when one of their jobs
# finishes, ``over_rate`` and ``over_owner_budget`` clear as the window rolls
# past their older jobs, and ``over_global_budget`` is a deployment-wide bound
# that no action of theirs will clear. ``duplicate`` says the store already
# holds this job id, which the API mints itself, so it reports a defect rather
# than a caller error. ``principal_exists`` says another admission created the
# principal of this job's identity after the caller read that it had none, so
# the caller submits again under the owner ID that admission created.
AdmissionOutcome = Literal[
    "admitted",
    "at_ceiling",
    "over_rate",
    "over_owner_budget",
    "over_global_budget",
    "duplicate",
    "resumed_already",
    "parent_moved",
    "principal_exists",
]


@dataclass(frozen=True)
class Admission:
    """The result of one :meth:`JobStore.reserve` call.

    ``active`` is how many of the reserving owner's jobs the store observed in
    flight *before* this one, so an ``at_ceiling`` refusal can quote the number
    it refused against. It counts only the owner named on the record, so no
    outcome here describes another caller's activity.

    A backend failure is not an outcome. It raises, and the API's unhandled-error
    handler turns it into an opaque ``500`` — a store that cannot answer must not
    be read as a store that said no.
    """

    outcome: AdmissionOutcome
    active: int
    #: Tokens the reserving owner has committed inside the window, before this
    #: job. Measured where a job of theirs has finished, reserved where it has
    #: not. Zero on every outcome the budget did not decide.
    owner_tokens: int = 0
    #: The same total across every owner. It is the *only* field here that
    #: describes activity beyond the caller, and the API never puts it in a
    #: response — a caller learning the deployment's load learns about other
    #: callers. It goes to the operator's log.
    global_tokens: int = 0


class JobStore(Protocol):
    """Persistence seam for job records; the real backend is a deferred decision.

    :meth:`reserve` is the seam the per-caller concurrency ceiling is enforced
    through, and it lives here rather than in process state on purpose: a counter
    beside the API would be per-instance no matter what backend a deployment
    configured, so behind two instances the effective ceiling would silently be
    double the configured number and would reset on every deploy — precisely the
    defect :func:`build_store` fails closed over. Asked of the store instead, the
    ceiling is exactly as shared as the deployment's storage is, and a shared
    backend makes it a shared ceiling with no change at the call site.

    **Counting and creating is one operation, not two.** The protocol exposes no
    unconditional create and no bare count, because a caller holding both writes
    the check-then-act race by hand: two submissions that each read the count
    before either inserts both pass a ceiling of one. Handing a backend a single
    method makes the atomicity requirement a signature rather than a paragraph an
    implementer can miss.

    **Every bound admission enforces is enforced by that one call.** The
    concurrency ceiling, the per-owner rate, the per-owner token budget and
    the deployment's global token budget all read state the insert then changes,
    so splitting any of them out would put the race back — one bound at a time.

    **The same call creates the principal of a first submission** (ADR 0064).
    ``principal`` is a new principal whose owner ID is the record's. The store
    keeps it only where it admits the job, so a refused submission leaves no
    principal, and it refuses with ``principal_exists`` where the identity
    already holds one. Without ``principal``, the record's owner must already
    hold a principal. A read creates none: :meth:`principal` finds nothing for
    an unknown identity, and every read by an owner ID that owns nothing
    answers as for a missing job.

    **The app calls :meth:`startup_check` before the restart scan.** A store
    checks there what it needs to serve, such as its schema revision and its
    object store, and raises where it cannot serve, so the app stops before
    it takes a request.
    """

    async def startup_check(self) -> None: ...

    async def reserve(
        self,
        record: JobRecord,
        *,
        ceiling: int,
        budget: BudgetPolicy,
        principal: Principal | None = None,
    ) -> Admission: ...

    async def principal(self, identity: Identity) -> Principal | None: ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def owned(self, job_id: str, owner_id: str) -> JobRecord | None: ...

    async def report_json(
        self, job_id: str, owner_id: str
    ) -> dict[str, Any] | None: ...

    async def report(self, job_id: str, owner_id: str) -> Report | None: ...

    async def checkpoint(self, job_id: str, owner_id: str) -> Checkpoint | None: ...

    async def resumption(self, job_id: str, owner_id: str) -> Resumption | None: ...

    async def resumed_by(self, job_id: str, owner_id: str) -> str | None: ...

    async def events_after(
        self, job_id: str, seen: int
    ) -> tuple[JobStatus, list[JobEvent]] | None: ...

    async def save(self, record: JobRecord) -> None: ...

    async def save_round(
        self,
        job_id: str,
        owner_id: str,
        answers: AnswerSet,
        shown: Sequence[UnknownKey],
        skipped: Sequence[SkipKey],
        revision: int,
    ) -> bool: ...

    async def save_corrections(
        self, job_id: str, owner_id: str, corrections: Sequence[FactAnswer]
    ) -> bool: ...

    async def save_draft(
        self,
        job_id: str,
        owner_id: str,
        draft: AnswerSet,
        revision: int,
    ) -> bool: ...

    async def owned_jobs(self, owner_id: str) -> list[JobRecord]: ...

    async def delete(
        self, job_id: str, owner_id: str, *, updated_before: datetime | None = None
    ) -> int | None:
        """Delete the owned job's content and keep its usage row (ADR 0066).

        The number of objects the delete removed, or ``None`` where nothing
        changed: the job is missing, another owner's, or not in a terminal
        status. Where ``updated_before`` is set, a job whose last update is
        not before it also answers ``None``. The store checks it in the step
        that deletes, so a job that changed after the retention sweep listed
        it stays.
        """
        ...

    async def expired(self, before: datetime) -> list[tuple[str, str]]:
        """The ID and the owner ID of each terminal job last updated before
        ``before``. The retention sweep deletes these (#1530)."""
        ...

    async def fail_in_flight(self) -> list[str]: ...


@dataclass(frozen=True)
class UsageRow:
    """What admission still reads of a deleted job (ADR 0066).

    A delete removes a job's content and keeps this row, so an owner cannot
    delete jobs to reset the rate limit, the token budgets or the rule that a
    job takes one follow-up. No read serves it, and the job reads as missing.
    """

    job_id: str
    owner_id: str
    created_at: datetime
    reserved_tokens: int
    measured_tokens: int | None
    status: JobStatus
    parent_id: str | None


def _parent_id(row: JobRecord | UsageRow) -> str | None:
    return parent_of(row) if isinstance(row, JobRecord) else row.parent_id


def check_bounds(
    record: JobRecord,
    held: Iterable[JobRecord | UsageRow],
    *,
    ceiling: int,
    budget: BudgetPolicy,
) -> Admission:
    """Check ``record`` against the four admission bounds, over the jobs ``held``.

    **The one reader of the bounds.** Each store calls it inside its own
    atomic step, after it settles the duplicate, the principal and the
    parent, and inserts the record only where this admits it. ``held`` is
    every job the store counts: its records and its usage rows. A store can
    leave out a terminal job that started before the window, because no bound
    counts it.

    **The bounds run cheapest-first, and the order is the answer a caller
    gets.** A submission over several of them hears about the concurrency
    ceiling before the rate, and the rate before either budget, because that
    is the order in which they clear: a job of theirs finishing, then the
    window rolling, then the window rolling further. Telling a caller about
    the bound that clears soonest is the one that helps them.
    """
    since = budget.window_start()
    held = list(held)
    mine = [job for job in held if job.owner_id == record.owner_id]

    active = sum(1 for job in mine if job.status not in TERMINAL_STATUSES)
    if active >= ceiling:
        return Admission(outcome="at_ceiling", active=active)

    started = sum(1 for job in mine if job.created_at >= since)
    if started >= budget.max_jobs_per_window:
        return Admission(outcome="over_rate", active=active)

    owner_tokens = spent_tokens(
        (job.reserved_tokens, job.measured_tokens)
        for job in mine
        if job.created_at >= since
    )
    if owner_tokens + record.reserved_tokens > budget.max_tokens_per_window:
        return Admission(
            outcome="over_owner_budget", active=active, owner_tokens=owner_tokens
        )

    global_tokens = spent_tokens(
        (job.reserved_tokens, job.measured_tokens)
        for job in held
        if job.created_at >= since
    )
    outcome: AdmissionOutcome = "admitted"
    if global_tokens + record.reserved_tokens > budget.global_max_tokens_per_window:
        outcome = "over_global_budget"
    return Admission(
        outcome=outcome,
        active=active,
        owner_tokens=owner_tokens,
        global_tokens=global_tokens,
    )


class InMemoryJobStore:
    """Dict-backed store; hands out copies so callers must ``save`` mutations.

    A deleted job leaves a :class:`UsageRow`, which this store keeps for the
    life of the process: admission reads it, and no route does.
    """

    def __init__(self) -> None:
        self._records: dict[str, JobRecord] = {}
        self._usage: dict[str, UsageRow] = {}
        self._principals = PrincipalTable()

    def _rows(self) -> Iterator[JobRecord | UsageRow]:
        """Every job admission counts: the records held, and the usage rows left."""
        return chain(self._records.values(), self._usage.values())

    async def startup_check(self) -> None:
        """Nothing to check: this store needs no schema and no object store."""

    async def reserve(
        self,
        record: JobRecord,
        *,
        ceiling: int,
        budget: BudgetPolicy,
        principal: Principal | None = None,
    ) -> Admission:
        """Check every admission bound and insert, as one step.

        Atomic here because the body awaits nothing: asyncio can only switch
        tasks at an ``await``, so no second submission runs between the count and
        the insert. That is this backend's whole claim, and it is a property of
        the coroutine rather than of a lock — a networked backend cannot borrow
        it and needs the count and the insert inside one transaction or one
        conditional write.

        The count is a scan rather than a maintained counter, because the records
        are the truth and a counter is a second copy of it that can drift — every
        path that ends a job would have to remember to decrement, and the one that
        forgets leaks ceiling until the process restarts. This store retains
        terminal records, so the scan is linear in everything the process has ever
        run; that is the same growth the dict itself already has, and bounding it
        is a property of the backend rather than of this count.

        A duplicate id is settled before every bound, so a record the store
        already holds is never also counted against the budgets it is already
        inside. The principal is settled next, and the store keeps a new
        principal with the record it admits, in the same step.

        A record whose owner ID is not ``principal``'s, or whose owner holds no
        principal where none is given, is the caller's defect, and it raises.
        :func:`check_bounds` then checks the bounds.
        """
        if record.id in self._records:
            return Admission(outcome="duplicate", active=0)
        if principal is None:
            if not self._principals.holds(record.owner_id):
                raise ValueError(f"owner {record.owner_id} holds no principal")
        elif principal.owner_id != record.owner_id:
            raise ValueError(f"job {record.id} names another owner than its principal")
        elif self._principals.find(principal.identity) is not None:
            return Admission(outcome="principal_exists", active=0)
        parent = parent_of(record)
        if parent is not None and self._resumed_by(parent) is not None:
            return Admission(outcome="resumed_already", active=0)
        parent_record = None if parent is None else self._records.get(parent)
        if (
            parent_record is not None
            and parent_record.round_revision != record.parent_revision
        ):
            return Admission(outcome="parent_moved", active=0)

        admission = check_bounds(record, self._rows(), ceiling=ceiling, budget=budget)
        if admission.outcome == "admitted":
            if principal is not None:
                self._principals.add(principal)
            self._records[record.id] = record.model_copy(deep=True)
        return admission

    def _resumed_by(self, parent_id: str) -> str | None:
        """The resumed job of ``parent_id`` that locks it, if any.

        **The one reader of "does this job still take answers".** A job takes
        one resumed job: a report's follow-up runs the analysis once (ADR
        0054), and a paused job's answers start one analysis. A job an
        amendment started locks its paused job the same way (ADR 0072). A
        child job that failed or was rejected read nothing into a report, so
        its parent takes answers again. Admission, a saved round and the questions route
        all ask this.
        """
        return next(
            (
                child.job_id if isinstance(child, UsageRow) else child.id
                for child in self._rows()
                if _parent_id(child) == parent_id and locks_its_parent(child.status)
            ),
            None,
        )

    async def resumed_by(self, job_id: str, owner_id: str) -> str | None:
        """The ID of the owned job's resumed job that locks it, or ``None``.

        ``None`` too where the job is not the owner's, as every other read
        answers a foreign job.
        """
        record = self._records.get(job_id)
        if record is None or record.owner_id != owner_id:
            return None
        return self._resumed_by(job_id)

    async def principal(self, identity: Identity) -> Principal | None:
        """The principal of ``identity``, or ``None``. Creates nothing."""
        return self._principals.find(identity)

    async def get(self, job_id: str) -> JobRecord | None:
        record = self._records.get(job_id)
        return None if record is None else record.model_copy(deep=True)

    async def owned(self, job_id: str, owner_id: str) -> JobRecord | None:
        """The record ``owner_id`` owns, as an envelope, or ``None``.

        What the status and event routes need: they read the envelope and never
        the analysis. The report, the checkpoint and the resumption are nearly
        all of a record's weight, and none of them is read by the envelope.
        Copying them is work no caller asked for, and a read carries none of
        the bounds admission does — no ceiling, no rate, no budget — so a caller
        who polls a job pays each of them per request and the process pays it
        on the event loop. A route that needs one of them asks for it by name:
        :meth:`report_json`, :meth:`report`, :meth:`checkpoint`.

        Ownership is checked before anything is copied, so a foreign job costs
        what a missing one costs. Answering the same ``None`` for both is what
        keeps a job id from being probed for existence, and answering it at the
        same *speed* is the other half of that.
        """
        record = self._records.get(job_id)
        if record is None or record.owner_id != owner_id:
            return None
        envelope = record.model_copy(
            update={"report": None, "checkpoint": None, "resumption": None}
        )
        return envelope.model_copy(deep=True)

    async def report_json(self, job_id: str, owner_id: str) -> dict[str, Any] | None:
        """The owned job's report, already JSON, or ``None`` if it has none.

        Serialized straight off the stored record. ``model_dump`` builds a fresh
        structure that aliases nothing the record holds, so the deep copy a
        caller would otherwise take first is a second copy of the same bytes.

        Ownership is re-checked rather than assumed of the caller: this reads
        the one thing on the record worth reading.
        """
        record = self._records.get(job_id)
        if record is None or record.owner_id != owner_id:
            return None
        if record.report is None:
            return None
        return record.report.model_dump(mode="json")

    async def report(self, job_id: str, owner_id: str) -> Report | None:
        """The owned job's report, as a copy the caller may keep, or ``None``.

        One pass over the stored report. Serializing it with :meth:`report_json`
        and validating the result again is two passes to reach the same object.
        Ownership is re-checked, as :meth:`report_json` does.
        """
        record = self._records.get(job_id)
        if record is None or record.owner_id != owner_id:
            return None
        if record.report is None:
            return None
        return record.report.model_copy(deep=True)

    async def checkpoint(self, job_id: str, owner_id: str) -> Checkpoint | None:
        """The owned job's checkpoint, as a copy the caller may keep, or ``None``.

        The only route that reads a checkpoint asks for it here, so a status
        read never copies it. Ownership is re-checked, as :meth:`report_json`
        does.
        """
        record = self._records.get(job_id)
        if record is None or record.owner_id != owner_id:
            return None
        if record.checkpoint is None:
            return None
        return record.checkpoint.model_copy(deep=True)

    async def resumption(self, job_id: str, owner_id: str) -> Resumption | None:
        """The owned job's resumption, as a copy, or ``None``.

        The envelope leaves it out for its weight, and a finished job's
        questions need it: whether the job came from a report's answers is
        whether its report is final (ADR 0054).
        """
        record = self._records.get(job_id)
        if record is None or record.owner_id != owner_id:
            return None
        if record.resumption is None:
            return None
        return record.resumption.model_copy(deep=True)

    async def events_after(
        self, job_id: str, seen: int
    ) -> tuple[JobStatus, list[JobEvent]] | None:
        """The job's status and the events past ``seen``, and nothing else.

        The SSE endpoint asks this on every poll, so it copies the events a
        client has not seen rather than the record they hang off, whose report
        can be hundreds of kilobytes.
        """
        record = self._records.get(job_id)
        if record is None:
            return None
        return record.status, [
            event.model_copy(deep=True) for event in record.events[seen:]
        ]

    async def save(self, record: JobRecord) -> None:
        if record.id not in self._records:
            raise ValueError(f"job {record.id!r} does not exist")
        self._records[record.id] = record.model_copy(deep=True)

    async def save_round(
        self,
        job_id: str,
        owner_id: str,
        answers: AnswerSet,
        shown: Sequence[UnknownKey],
        skipped: Sequence[SkipKey],
        revision: int,
    ) -> bool:
        """Keep a saved round's answers on a job that still waits for them.

        ``shown`` is every early question the pause has shown, and ``skipped``
        every one its submitter skipped for now. ``revision`` is the round
        revision the save read: the check and the next revision are one step,
        so of two saves that read one revision only the first lands. Only these
        change, so the checkpoint an envelope read leaves
        behind is kept. False where the job is not the owner's, no longer
        waits, or its answers already started a resumed job that nothing
        reads a later save into, and nothing is written.
        """
        record = self._records.get(job_id)
        if (
            record is None
            or record.owner_id != owner_id
            or record.status != "awaiting-answers"
            or record.round_revision != revision
            or self._resumed_by(job_id) is not None
        ):
            return False
        record.round_revision += 1
        record.answers = answers
        record.shown_early = list(shown)
        record.skipped_early = list(skipped)
        record.updated_at = datetime.now(UTC)
        return True

    async def save_draft(
        self,
        job_id: str,
        owner_id: str,
        draft: AnswerSet,
        revision: int,
    ) -> bool:
        """Keep a finished report's draft answers, which run nothing (ADR 0070).

        ``draft`` is every draft answer so far. ``revision`` is
        the revision the save read: the check and the next revision are one
        step, as for :meth:`save_round`. False where the job is not the
        owner's, has no report, has a final report, or its answers already
        started a resumed job, and nothing is written.
        """
        record = self._records.get(job_id)
        if (
            record is None
            or record.owner_id != owner_id
            or record.status != "completed"
            or record.final()
            or record.round_revision != revision
            or self._resumed_by(job_id) is not None
        ):
            return False
        record.round_revision += 1
        record.draft = draft
        record.updated_at = datetime.now(UTC)
        return True

    async def save_corrections(
        self, job_id: str, owner_id: str, corrections: Sequence[FactAnswer]
    ) -> bool:
        """Keep a final report's corrections. False where the job is not the
        owner's or its report is not final, and nothing is written."""
        record = self._records.get(job_id)
        if (
            record is None
            or record.owner_id != owner_id
            or record.status != "completed"
            or not record.final()
        ):
            return False
        record.corrections = list(corrections)
        record.updated_at = datetime.now(UTC)
        return True

    async def owned_jobs(self, owner_id: str) -> list[JobRecord]:
        """Every job of the owner, as envelopes, oldest first.

        A deleted job is not listed: its usage row is admission's to read.
        """
        return [
            await self.owned(record.id, owner_id)  # type: ignore[misc]
            for record in self._records.values()
            if record.owner_id == owner_id
        ]

    async def delete(
        self, job_id: str, owner_id: str, *, updated_before: datetime | None = None
    ) -> int | None:
        """Remove the owned job's content, and keep its usage row (ADR 0066).

        Only a job in a terminal status can be deleted: a job in flight holds
        a slot its run releases. ``None`` where the job is missing, not the
        owner's, not terminal or not last updated before ``updated_before``,
        and nothing changes. Else ``0``: this store holds no objects.
        """
        record = self._records.get(job_id)
        if (
            record is None
            or record.owner_id != owner_id
            or record.status not in TERMINAL_STATUSES
            or (updated_before is not None and record.updated_at >= updated_before)
        ):
            return None
        del self._records[job_id]
        self._usage[job_id] = UsageRow(
            job_id=job_id,
            owner_id=record.owner_id,
            created_at=record.created_at,
            reserved_tokens=record.reserved_tokens,
            measured_tokens=record.measured_tokens,
            status=record.status,
            parent_id=parent_of(record),
        )
        return 0

    async def expired(self, before: datetime) -> list[tuple[str, str]]:
        return [
            (record.id, record.owner_id)
            for record in self._records.values()
            if record.status in TERMINAL_STATUSES and record.updated_at < before
        ]

    async def fail_in_flight(self) -> list[str]:
        """Fail each queued or running job with :data:`RESTART_FAILURE_MESSAGE`.

        The restart scan (#1529). A job in flight at startup has no run left
        to finish it. Each failed job keeps its full charge and gets a status
        event, as :meth:`JobRecord.transition` settles it. A terminal job,
        which includes one in ``awaiting-answers``, stays as it is. Returns
        the IDs of the failed jobs.
        """
        failed = []
        for record in self._records.values():
            if record.status in TERMINAL_STATUSES:
                continue
            record.transition("failed")
            record.error = RESTART_FAILURE_MESSAGE
            failed.append(record.id)
        return failed


#: How long an event stream waits before it asks the store for new events.
POLL_SECONDS = 0.2


async def event_frames(
    store: JobStore,
    job_id: str,
    seen: int = 0,
    *,
    gone: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """The job's events past ``seen``, as server-sent frames, as the store
    records them.

    **The one reader of "when does a job's stream end".** It ends when the job
    reaches a terminal status, when the store holds no such job, or when
    ``gone`` says the client left. A job that emits nothing sends nothing, so
    a send to a client that left never fails: ``gone`` is how such a stream
    ends and gives its slot back.
    """
    while True:
        progress = await store.events_after(job_id, seen)
        if progress is None:
            return
        status, events = progress
        for event in events:
            seen = event.seq
            yield event.sse_frame()
        if status in TERMINAL_STATUSES or (gone is not None and await gone()):
            return
        await asyncio.sleep(POLL_SECONDS)


class JobStoreConfigError(ValueError):
    """A job store setting or an object store setting is missing or unusable."""


Name = TypeVar("Name", bound=str)


def backend_name(value: str, setting: str, table: Mapping[Name, object]) -> Name:
    """The row of ``table`` that ``value`` names. An empty or unknown value raises.

    **The one reader of a backend value**: the job store, the object store and
    the database all read theirs here. ``setting`` names where the value comes
    from, for the message. The value is never read from a request.
    """
    value = value.strip().lower()
    if not value:
        raise JobStoreConfigError(f"set {setting}")
    for name in table:
        if name == value:
            return name
    known = ", ".join(sorted(table))
    raise JobStoreConfigError(
        f"unknown value {value!r} for {setting}; known values: {known}"
    )


JobStoreName = Literal["memory", "sql"]


@dataclass(frozen=True)
class JobStoreBackend:
    """One ``ANALYSIS_JOB_STORE`` value: a row of :data:`JOB_STORES`.

    No field has a default, so a new row states every fact.
    """

    #: Builds the store from the environment and the object store, which is
    #: ``None`` where :attr:`objects` is False.
    build: Callable[[Mapping[str, str], ObjectStore | None], JobStore]
    #: Whether the store keeps sources and reports in an object store. Where
    #: True, ``ANALYSIS_OBJECT_STORE`` is required; where False, it is refused,
    #: so a half-durable configuration cannot start.
    objects: bool


def _sql_store(env: Mapping[str, str], objects: ObjectStore | None) -> JobStore:
    """The ``sql`` store, from the ``sql`` extra (ADR 0023)."""
    try:
        from analysis_service.sql_store import SqlJobStore
    except ModuleNotFoundError as missing:
        raise JobStoreConfigError(
            f"the sql job store needs the sql extra ({missing.name} is not installed):"
            " install analysis-service[sql]"
        ) from None
    if objects is None:
        raise JobStoreConfigError("the sql job store needs an object store")
    return SqlJobStore.from_env(env, objects)


#: Each job store a deployment can select. The table answers for every name of
#: :data:`JobStoreName`.
JOB_STORES: dict[JobStoreName, JobStoreBackend] = {
    "memory": JobStoreBackend(
        build=lambda env, objects: InMemoryJobStore(), objects=False
    ),
    "sql": JobStoreBackend(build=_sql_store, objects=True),
}


def build_store(env: Mapping[str, str] = os.environ) -> JobStore:
    """Select and construct the configured job store; fail closed.

    The backend is chosen by ``ANALYSIS_JOB_STORE`` at deploy time — never by the
    request — so an empty or unknown value raises rather than silently falling
    back to non-durable, per-instance ``memory`` storage that loses every job on
    restart and isolates jobs behind a load balancer. The object store follows
    the row: ``ANALYSIS_OBJECT_STORE`` is required where the row keeps objects
    and refused where it does not. This builds the store and opens no
    connection; :meth:`JobStore.startup_check` checks what the store reaches.
    """
    value = env.get("ANALYSIS_JOB_STORE", "")
    backend = JOB_STORES[backend_name(value, "ANALYSIS_JOB_STORE", JOB_STORES)]
    if backend.objects:
        from analysis_service.objects import build_object_store

        return backend.build(env, build_object_store(env))
    if env.get("ANALYSIS_OBJECT_STORE", "").strip():
        raise JobStoreConfigError(
            "this job store keeps no objects; unset ANALYSIS_OBJECT_STORE"
        )
    return backend.build(env, None)


#: Why a job is deleted: its owner asked, its retention period passed, or the
#: web app's workspace deleted it to make room for a new job.
DeleteCause = Literal["owner", "retention", "workspace"]


async def audited_delete(
    store: JobStore,
    job_id: str,
    owner_id: str,
    cause: DeleteCause,
    *,
    updated_before: datetime | None = None,
) -> int | None:
    """Delete the owned job with :meth:`JobStore.delete`, and log the delete.

    **The one writer of the delete log line** (ADR 0066). The delete route and
    the retention sweep both delete through it. The line names the job ID, the
    owner ID, the cause and the number of objects removed, and no content.
    ``updated_before`` goes to :meth:`JobStore.delete`. Returns what
    :meth:`JobStore.delete` returns.
    """
    removed = await store.delete(job_id, owner_id, updated_before=updated_before)
    if removed is not None:
        logger.info(
            "deleted job %s of owner %s: cause %s, %d object(s) removed",
            job_id,
            owner_id,
            cause,
            removed,
        )
    return removed


#: The longest retention period: a century. The bound keeps the cutoff of a
#: sweep inside the range of ``datetime``.
MAX_RETENTION_DAYS = 36_500


def retention_days(env: Mapping[str, str] = os.environ) -> int | None:
    """The retention period from ``ANALYSIS_RETENTION_DAYS``, in days.

    ``None`` where the setting is unset: the service keeps every job (#1530).
    A value that is not a whole number from 1 to :data:`MAX_RETENTION_DAYS`
    raises, so the startup stops.
    """
    value = env.get("ANALYSIS_RETENTION_DAYS")
    if value is None:
        return None
    days = ascii_int(value, max_digits=len(str(MAX_RETENTION_DAYS)))
    if days is None or not 1 <= days <= MAX_RETENTION_DAYS:
        raise JobStoreConfigError(
            "ANALYSIS_RETENTION_DAYS must be a whole number of days from 1 to"
            f" {MAX_RETENTION_DAYS}, or unset to keep every job"
        )
    return days


async def expire_jobs(store: JobStore, days: int) -> None:
    """Delete each terminal job whose last update is more than ``days`` days old.

    One pass of the retention sweep (#1530). Each delete goes through
    :func:`audited_delete` with the cause ``retention``, so the job keeps its
    usage row and the log gets the line of the delete route. A ``queued`` or
    ``running`` job is never expired. Each delete carries the cutoff, so a
    job that its owner changed after the listing stays.
    """
    before = datetime.now(UTC) - timedelta(days=days)
    for job_id, owner_id in await store.expired(before):
        await audited_delete(
            store, job_id, owner_id, "retention", updated_before=before
        )


#: How often the retention sweep runs. A job outlives its retention period by
#: one interval at most.
SWEEP_SECONDS = 3600


async def _sweep(store: JobStore, days: int) -> None:
    """Run the retention sweep now and then each :data:`SWEEP_SECONDS`, until
    the app stops. A failed pass is logged, and the next pass runs."""
    while True:
        try:
            await expire_jobs(store, days)
        except Exception:
            logger.exception("the retention sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)


@asynccontextmanager
async def store_lifespan(store: JobStore) -> AsyncIterator[None]:
    """Check the store, then fail each job in flight, before an app takes a request.

    **The one startup of a job store**: the ``/v1`` service and the web app
    both run it as the lifespan of their app, after they built the store from
    the backend settings. The order is: the retention setting, the store's
    startup check (the schema revision, then the object store), the restart
    scan (#1529), then requests. A failed check stops the startup. Where a
    retention period is set, the retention sweep starts after the restart scan
    and stops with the app.
    """
    days = retention_days()
    await store.startup_check()
    # The scan is correct for a single instance only. It fails every queued or
    # running job in the store, so with a second live instance on one store it
    # would fail the jobs that instance still runs.
    failed = await store.fail_in_flight()
    if failed:
        logger.warning(
            "restart scan failed %d job(s) in flight: %s", len(failed), failed
        )
    sweep = None if days is None else asyncio.create_task(_sweep(store, days))
    try:
        yield
    finally:
        if sweep is not None:
            sweep.cancel()
            with suppress(asyncio.CancelledError):
                await sweep


@dataclass(frozen=True)
class PipelineCompleted:
    """The pipeline produced a report, and the runner certified it.

    ``certification`` is ``None`` only where no gate was configured — the
    offline stand-ins, which have no manifest to certify against.
    """

    report: Report
    certification: CertifyResult | None = None


@dataclass(frozen=True)
class PipelineRejected:
    """The input failed the validity gate after the repair pass.

    ``nodes`` is what the graph ran before it rejected. A rejected job builds no
    report, so the measurement the report would have carried is here instead —
    extraction and repair were paid for whether or not their output validated,
    and a rejection that settled to zero would hand a caller those calls free.
    """

    issues: list[ValidationIssue]
    nodes: list[NodeRun] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineAwaiting:
    """The run stopped after its assertion pass to ask a person.

    ``checkpoint`` is what the answers resume from. ``nodes`` is what the run
    spent, for the reason :class:`PipelineRejected` carries it: no report exists
    to carry the measurement, and a pause that settled to zero would hand a
    caller the extraction and the assertion pass free.
    """

    checkpoint: Checkpoint
    nodes: list[NodeRun] = field(default_factory=list)
    #: The verdict over the head's nodes; ``None`` where no gate is configured.
    certification: CertifyResult | None = None


PipelineOutcome = PipelineCompleted | PipelineRejected | PipelineAwaiting

# Called after each pipeline node completes, with the node's name.
NodeCallback = Callable[[str], Awaitable[None]]


class PipelineRunner(Protocol):
    """Execution seam for the ADK analysis graph."""

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome: ...


class StubPipelineRunner:
    """Stand-in runner: walks a few named nodes, returns an empty valid report.

    It answers the job's selection with one empty block per framework, because
    the envelope refuses a report whose blocks are not the job's frameworks in
    order — and that check is exactly the contract this stand-in exists to
    exercise. The blocks are the neutral :class:`~analysis_service.claims.
    FrameworkAnalysis` rather than each package's own narrowed shape: nothing
    here judges anything, so claiming a package's block type would assert a
    method that never ran.

    The node names are the shared half of the topology only. A stub that spelled
    per-framework node names would be inventing graph structure it never built.
    """

    nodes: tuple[str, ...] = ("extract", "validate", "prepare", "assemble")

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        for node in self.nodes:
            await on_node(node)
        return PipelineCompleted(report=self._stub_report(job))

    def _stub_report(self, job: JobRecord) -> Report:
        model = SystemModel(
            processes=[
                Process(
                    id="process:stub-system",
                    name="Stub System",
                    technology="unknown",
                    trust_zone="boundary:system",
                    exposure="unknown",
                    interface_kind="non-web",
                )
            ],
            trust_boundaries=[
                TrustBoundary(id="boundary:system", name="System", kind="other")
            ],
        )
        return Report(
            job=Job(
                id=job.id,
                created_at=job.created_at,
                completed_at=datetime.now(UTC),
                frameworks=list(job.frameworks),
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
            elements_analyzed=len(model.elements()),
            analyses=[self._stub_block(selection) for selection in job.frameworks],
        )

    def _stub_block(self, selection: FrameworkSelection) -> FrameworkAnalysis:
        # The version comes from the registered package rather than from a
        # literal: this stand-in runs in the build it is stubbing, and a report
        # carrying a version no package here declares is a fixture nothing could
        # be checked against.
        from analysis_service.frameworks import package_for

        return FrameworkAnalysis(
            framework=selection.name,
            framework_version=package_for(selection.name).version,
            disclaimer=(
                f"Stub run: no {selection.name} analysis was performed and this"
                " block asserts nothing about the system."
            ),
            summary=FrameworkAnalysis.summarize([], []),
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
    longest path, so without it a job's tail is a product of numbers nobody
    chose. It is a required keyword rather than a defaulted one — a default here
    would be a deployment inheriting a deadline it never picked, which is what
    ``job_deadline_ms`` in ``config/resilience.toml`` exists to prevent.

    Expiry cancels the graph, so the in-flight provider calls are dropped rather
    than left to finish into a job nobody is waiting on. What the run had
    already completed is logged by node name: a deadline that keeps firing at
    the same node is the evidence that sizes ``timeout_ms``, and it is not
    recoverable from the record, whose event log the caller can see but the
    operator's alert cannot.

    A partial run is never a partial report. The deadline path stores no
    ``report`` at all — every lane of every framework the job selected is what
    makes the output that method's answer, and one that stopped halfway is a
    different method rather than a shorter answer. The envelope enforces the
    same rule from the other side: a block missing from ``analyses`` is refused
    rather than served as a report the caller has to notice is short.
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
    except Exception as error:
        logger.exception("job %s failed in the pipeline", job_id)
        # The node runs first, for the settling reason below.
        if isinstance(error, GraphFailed):
            record.unreported_nodes = list(error.node_runs)
        record.transition("failed")
        record.error = GENERIC_FAILURE_MESSAGE
        await store.save(record)
        return

    try:
        _attach(record, outcome)
    except ValidationError:
        # The record checks what it is given: a report that does not answer
        # the job's selection, or does not match its own model, is refused.
        # That is this service's defect and never the caller's. The job ends
        # ``failed`` rather than staying ``running``, so it frees its slot and
        # its stream ends; the refusal goes to the log, never to the record.
        logger.exception("job %s refused its outcome from the runner", job_id)
        record.transition("failed")
        record.error = GENERIC_FAILURE_MESSAGE
    await store.save(record)


def _attach(record: JobRecord, outcome: PipelineOutcome) -> None:
    """Attach a run's outcome to its record and move it to the terminal status."""
    if isinstance(outcome, PipelineCompleted):
        # The report first, then the transition: settling reads the report, and
        # a transition that ran before it was attached would settle to zero and
        # give a caller a completed job for free.
        record.report = outcome.report
        record.certification = outcome.certification
        record.transition("completed")
        # One line per job, because the job store keeps no history to read
        # these figures from later. Counts only: no source text, no claim.
        use = assertion_use(outcome.report)
        if use is not None:
            logger.info(
                "job %s assertion use: %s", record.id, json.dumps(use.to_json())
            )
    elif isinstance(outcome, PipelineAwaiting):
        # The node runs first, for the settling reason below.
        record.checkpoint = outcome.checkpoint
        record.certification = outcome.certification
        record.unreported_nodes = outcome.nodes
        record.transition("awaiting-answers")
    else:
        # The node runs first, for the reason the report goes first above:
        # settling reads them, and a transition that ran before they were
        # attached would settle a rejection to zero and give a caller the
        # extraction and repair it paid for free.
        record.unreported_nodes = outcome.nodes
        record.transition("rejected")
        record.validation_issues = outcome.issues
