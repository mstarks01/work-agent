"""The **Job State**: what a page reads about one job, from one read.

A page asks of a job its status, whether its run stops to ask a person, and
whether this process continues it at its pause. :func:`job_state` makes the
store reads these facts need, so a caller reads the facts and never the store.

The state has three shapes. A job in flight is readable. A job that ended may
have lost its parent to a delete, so its state reads no parent. A record whose
envelope does not parse carries only its page status (ADR 0095).

Three facts have their own reads, because only some pages need them and each
costs store reads: :func:`run_origin` says what a run does first and which job
it resumes, :func:`awaits_answers` says whether a job waits for answers, and
:func:`pause_refusal` says why a job waits at its pause.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from analysis_service.jobs import (
    Envelope,
    JobRecord,
    JobStore,
    Resumption,
    UnreadableSchema,
    ended,
    readable,
)

#: A job's status as the pages read it.
PageStatus = Literal[
    "running", "awaiting-answers", "completed", "failed", "stopped", "unreadable"
]

#: What a run does first: it reads a description, or it is a **Resumed Job**
#: and continues the job it resumes, with answers or with Controls alone.
RunStart = Literal["description", "answers", "controls"]


@dataclass(frozen=True)
class StoredRefusal:
    """The words the service stored when it did not continue a job at its
    pause (ADR 0125)."""

    words: str


#: Why a job waits at its pause: the stored refusal, or ``not-continued``
#: where nothing continued the job and nothing refused it, as after a restart.
PauseRefusal = StoredRefusal | Literal["not-continued"]


@dataclass(frozen=True)
class RunningJobState:
    """A job in flight."""

    page_status: ClassVar[PageStatus] = "running"
    continuing: ClassVar[bool] = False

    #: Whether the run stops to ask a person. A Resumed Job never does.
    asks: bool


@dataclass(frozen=True)
class EndedJobState:
    """A job that ended, or a record of another schema version, which
    :func:`~analysis_service.jobs.ended` counts as ended."""

    page_status: PageStatus
    #: Whether the job paused to ask a person.
    asks: bool
    #: Whether this process continues the job at its pause (ADR 0125).
    continuing: bool


@dataclass(frozen=True)
class UnreadableJobState:
    """A record whose envelope does not parse (ADR 0095)."""

    page_status: ClassVar[PageStatus] = "unreadable"
    asks: ClassVar[bool] = False
    continuing: ClassVar[bool] = False


JobState = RunningJobState | EndedJobState | UnreadableJobState


@dataclass(frozen=True)
class RunOrigin:
    """What a run does first, and the job it resumes."""

    start: RunStart
    #: The resumption and the job it resumes: both ``None`` for a job that
    #: resumes no job.
    resumption: Resumption | None
    parent: JobRecord | None


async def job_state(store: JobStore, record: Envelope, *, runs_here: bool) -> JobState:
    """The Job State of ``record``. ``runs_here`` says whether this process
    runs the task of the job, which is what continues it at its pause.

    A job in flight that pauses to ask reads its resumption, which the
    envelope leaves out, because a Resumed Job never asks.
    """
    if not isinstance(record, JobRecord):
        return UnreadableJobState()
    if not ended(record):
        asks = record.pauses_to_ask() and (
            await store.resumption(record.id, record.owner_id) is None
        )
        return RunningJobState(asks=asks)
    continuing = continues(record, runs_here=runs_here)
    return EndedJobState(
        page_status="running" if continuing else _page_status(record),
        asks=(
            readable(record)
            and record.pauses_to_ask()
            and record.status == "awaiting-answers"
        ),
        continuing=continuing,
    )


def continues(record: JobRecord, *, runs_here: bool) -> bool:
    """Whether this process continues ``record`` at its pause: the job waits
    to continue, and this process runs its task (ADR 0125)."""
    return record.waits_to_continue() and runs_here


async def run_origin(store: JobStore, record: JobRecord) -> RunOrigin:
    """What the run of ``record`` does first, and the job it resumes. The
    envelope leaves the resumption out, so this reads it by name.

    Raises :class:`LookupError` where the job resumes a job the store lacks,
    and :class:`UnreadableSchema` where it resumes an unreadable job. A job
    in flight locks its parent, so the store holds it.
    """
    resumption = await store.resumption(record.id, record.owner_id)
    parent = None
    if resumption is not None:
        stored = await store.owned(resumption.parent_id, record.owner_id)
        if stored is None:
            raise LookupError(f"job {record.id} resumes a job the store lacks")
        if not isinstance(stored, JobRecord):
            raise UnreadableSchema(f"job {record.id} resumes an unreadable job")
        parent = stored
    return RunOrigin(run_start_of(parent), resumption, parent)


def run_start_of(parent: JobRecord | None) -> RunStart:
    """What a run does first, from the job it resumes: ``None`` for a run
    that resumes no job. A parent that waits to continue for its Controls
    alone (``continues_at_pause``) gives ``controls``. Each other parent gave
    answers. ``tests/test_progress_words.py`` holds the answer to
    :func:`~analysis_service.pipeline.entry_of`."""
    if parent is None:
        return "description"
    return "controls" if parent.continues_at_pause else "answers"


def _page_status(record: JobRecord) -> PageStatus:
    """A job's status as the pages read it: a queued job is running, and a
    rejected one stopped."""
    status = record.status
    if status == "queued":
        return "running"
    return "failed" if status == "rejected" else status


async def awaits_answers(store: JobStore, record: Envelope) -> bool:
    """True while the job is **Awaiting Answers**.

    A paused job that no job locks waits. A paused job whose locking job
    still runs also waits: that job can fail, and this one is then where its
    answers resume. A paused job whose locking job has
    :func:`~analysis_service.jobs.ended` waits no more. That job has a report
    or pauses itself, and a paused job never fails, so this one takes no
    answers again. A locking job that the store no longer gives has ended
    too, because a store deletes only a job that has ended.
    """
    if record.status != "awaiting-answers":
        return False
    locker = await store.resumed_by(record.id, record.owner_id)
    if locker is None:
        return True
    if isinstance(record, JobRecord) and not record.pauses_to_ask():
        # The job paused for its Controls alone, so the job that continues
        # it shows the analysis.
        return False
    child = await store.owned(locker, record.owner_id)
    return child is not None and not ended(child)


async def pause_refusal(
    store: JobStore, record: JobRecord, *, runs_here: bool
) -> PauseRefusal | None:
    """Why ``record`` waits at its pause, or ``None`` for a job that does not
    wait there for a continue. ``runs_here`` is as :func:`job_state` takes
    it. A job that waits to continue, with no task in this process, no stored
    refusal and no Resumed Job, did not continue, and the owner continues it
    (ADR 0125)."""
    if record.continue_refusal is not None:
        return StoredRefusal(record.continue_refusal)
    if not record.waits_to_continue() or continues(record, runs_here=runs_here):
        return None
    # The task ends after it stores its refusal or admits the Resumed Job, so
    # the store holds either one now, though ``record`` was read before.
    current = await store.owned(record.id, record.owner_id)
    if isinstance(current, JobRecord) and current.continue_refusal is not None:
        return StoredRefusal(current.continue_refusal)
    if await store.resumed_by(record.id, record.owner_id) is None:
        return "not-continued"
    return None
