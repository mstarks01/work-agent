"""The Job State of a job, its run origin, whether it takes answers, and why
its continue did not happen.

Each test reads the record as an envelope from an in-memory store, as a route
does, so a Resumed Job's resumption is read by name and not off the record.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from analysis_service.job_state import (
    EndedJobState,
    InFlightJobState,
    RunOrigin,
    StoredRefusal,
    UnreadableJobState,
    job_state,
    run_origin,
    takes_answers,
    why_not_continued,
)
from analysis_service.jobs import (
    Checkpoint,
    Envelope,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    Resumption,
    UnreadableRecord,
    UnreadableSchema,
)
from analysis_service.sources import Source
from tests.factories import DESCRIPTION_TEXT, admit, sample_report, sample_selection


def _job(**fields: Any) -> JobRecord:
    return JobRecord.create(
        owner_id="alice",
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        **fields,
    )


def _paused(store: InMemoryJobStore, **fields: Any) -> JobRecord:
    """A job of ``store`` that ran and paused."""
    job = _job(**fields)
    job.transition("running")
    job.transition("awaiting-answers")
    asyncio.run(admit(store, job))
    return job


def _resumed(store: InMemoryJobStore, parent: JobRecord, **fields: Any) -> JobRecord:
    """A Resumed Job of ``parent``, in flight."""
    child = _job(
        **fields,
        resumption=Resumption(
            parent_id=parent.id,
            checkpoint=Checkpoint(
                system_model=sample_report([]).system_model, assertions=None
            ),
            follow_up=False,
        ),
        parent_revision=parent.revision,
    )
    child.transition("running")
    asyncio.run(admit(store, child))
    return child


def _unreadable(status: JobStatus | None, job_id: str = "job-1") -> UnreadableRecord:
    now = datetime.now(UTC)
    return UnreadableRecord(
        id=job_id,
        owner_id="alice",
        schema_version="0",
        system_name=None,
        title=None,
        created_at=now,
        updated_at=now,
        status=status,
        reserved_tokens=0,
        measured_tokens=None,
    )


class _ParentAs(InMemoryJobStore):
    """A store that, once ``replace`` is set, answers a read of the job
    ``parent_id`` with ``read``: no store lets a locked parent go, so only a
    stand-in can."""

    def __init__(self, parent_id: str, read: Envelope | None) -> None:
        super().__init__()
        self._parent_id = parent_id
        self._read = read
        self.replace = False

    # The JobStore protocol returns an Envelope; the in-memory store never
    # holds an unreadable record, so its own read says JobRecord.
    async def owned(  # type: ignore[override]
        self, job_id: str, owner_id: str
    ) -> Envelope | None:
        if self.replace and job_id == self._parent_id:
            return self._read
        return await super().owned(job_id, owner_id)


class _LockedBy(InMemoryJobStore):
    """A store where the job ``locker`` locks the job ``locked``, which the
    store need not hold: an unreadable record is no job the in-memory store
    can admit."""

    def __init__(self, locked: str, locker: str) -> None:
        super().__init__()
        self._locked = locked
        self._locker = locker

    async def resumed_by(self, job_id: str, owner_id: str) -> str | None:
        if job_id == self._locked:
            return self._locker
        return await super().resumed_by(job_id, owner_id)


def _envelope(store: InMemoryJobStore, job_id: str) -> Envelope:
    envelope = asyncio.run(store.owned(job_id, "alice"))
    assert envelope is not None
    return envelope


def _state(store: InMemoryJobStore, job_id: str, *, runs_here: bool = False):
    return asyncio.run(job_state(store, _envelope(store, job_id), runs_here=runs_here))


def _takes_answers(store: InMemoryJobStore, job_id: str) -> bool:
    return asyncio.run(takes_answers(store, _envelope(store, job_id)))


def _refusal(store: InMemoryJobStore, job_id: str, *, runs_here: bool = False):
    record = _envelope(store, job_id)
    assert isinstance(record, JobRecord)
    return asyncio.run(why_not_continued(store, record, runs_here=runs_here))


# --- The Job State ----------------------------------------------------------


@pytest.mark.parametrize("asks", [True, False])
def test_a_fresh_run_asks_where_the_job_asks(asks):
    store = InMemoryJobStore()
    job = _job(ask_questions=asks)
    job.transition("running")
    asyncio.run(admit(store, job))

    assert _state(store, job.id) == InFlightJobState(asks=asks)


def test_a_resumed_job_never_asks():
    """The envelope leaves the resumption out, so it reads as a job that
    pauses to ask, and the state reads the resumption by name."""
    store = InMemoryJobStore()
    child = _resumed(store, _paused(store, ask_questions=True), ask_questions=True)
    envelope = _envelope(store, child.id)
    assert isinstance(envelope, JobRecord) and envelope.pauses_to_ask()

    assert _state(store, child.id) == InFlightJobState(asks=False)


# --- The run origin ---------------------------------------------------------


def test_a_fresh_run_starts_from_its_description():
    store = InMemoryJobStore()
    job = _job()
    job.transition("running")
    asyncio.run(admit(store, job))
    envelope = _envelope(store, job.id)
    assert isinstance(envelope, JobRecord)

    assert asyncio.run(run_origin(store, envelope)) == RunOrigin(
        "description", None, None
    )


@pytest.mark.parametrize(
    ("continues", "start"), [(False, "answers"), (True, "controls")]
)
def test_a_resumed_job_starts_from_what_its_parent_waited_for(continues, start):
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=not continues, continues_at_pause=continues)
    envelope = _envelope(store, _resumed(store, parent).id)
    assert isinstance(envelope, JobRecord)

    origin = asyncio.run(run_origin(store, envelope))

    assert origin.start == start
    assert origin.resumption is not None
    assert origin.parent is not None and origin.parent.id == parent.id


@pytest.mark.parametrize("unreadable", [False, True])
def test_a_job_whose_parent_the_store_cannot_give_raises(unreadable):
    parent = _job(ask_questions=True)
    store = _ParentAs(
        parent.id, _unreadable("completed", parent.id) if unreadable else None
    )
    parent.transition("running")
    parent.transition("awaiting-answers")
    asyncio.run(admit(store, parent))
    envelope = _envelope(store, _resumed(store, parent).id)
    assert isinstance(envelope, JobRecord)
    store.replace = True

    if unreadable:
        with pytest.raises(UnreadableSchema, match="schema version 0;"):
            asyncio.run(run_origin(store, envelope))
    else:
        with pytest.raises(LookupError):
            asyncio.run(run_origin(store, envelope))


# --- The page status --------------------------------------------------------


def test_a_queued_job_runs_on_the_page():
    store = InMemoryJobStore()
    job = _job()
    asyncio.run(admit(store, job))

    assert _state(store, job.id).page_status == "running"


@pytest.mark.parametrize(
    ("ending", "page_status"), [("failed", "failed"), ("rejected", "failed")]
)
def test_a_job_that_ended_without_a_pause_neither_asks_nor_continues(
    ending, page_status
):
    store = InMemoryJobStore()
    job = _job()
    job.transition("running")
    job.transition(ending)
    asyncio.run(admit(store, job))

    assert _state(store, job.id) == EndedJobState(
        page_status=page_status, asks=False, continuing=False
    )


def test_a_paused_job_that_asks_shows_its_questions():
    store = InMemoryJobStore()
    job = _paused(store, ask_questions=True)

    assert _state(store, job.id) == EndedJobState(
        page_status="awaiting-answers", asks=True, continuing=False
    )


@pytest.mark.parametrize(
    ("runs_here", "page_status"), [(True, "running"), (False, "awaiting-answers")]
)
def test_a_job_runs_while_this_process_continues_it_at_its_pause(
    runs_here, page_status
):
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)

    assert _state(store, job.id, runs_here=runs_here) == EndedJobState(
        page_status=page_status, asks=False, continuing=runs_here
    )


def test_a_paused_job_of_another_schema_version_does_not_ask():
    store = InMemoryJobStore()
    job = _paused(store, ask_questions=True)
    other = _envelope(store, job.id).model_copy(update={"schema_version": "0.1"})

    state = asyncio.run(job_state(store, other, runs_here=False))

    assert state == EndedJobState(
        page_status="awaiting-answers", asks=False, continuing=False
    )


def test_an_unreadable_record_carries_only_its_page_status():
    state = asyncio.run(
        job_state(InMemoryJobStore(), _unreadable("awaiting-answers"), runs_here=True)
    )

    assert state == UnreadableJobState()
    assert (state.page_status, state.asks, state.continuing) == (
        "unreadable",
        False,
        False,
    )


# --- Whether a job takes answers -------------------------------------------


def test_a_paused_job_that_no_job_locks_takes_answers():
    store = InMemoryJobStore()

    assert _takes_answers(store, _paused(store, ask_questions=True).id)


def test_a_paused_job_whose_resumed_job_runs_still_takes_answers():
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=True)
    _resumed(store, parent)

    assert _takes_answers(store, parent.id)


@pytest.mark.parametrize("deleted", [False, True])
def test_a_paused_job_whose_resumed_job_ended_takes_no_answers(deleted):
    """A store deletes only a job that ended, so a lock that is gone ended."""
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=True)
    child = _resumed(store, parent)
    child.transition("completed")
    asyncio.run(store.save(child))
    if deleted:
        asyncio.run(store.delete(child.id, "alice"))

    assert not _takes_answers(store, parent.id)


def test_a_job_continued_for_its_controls_alone_takes_no_answers():
    """The Resumed Job that continues it shows the analysis."""
    store = InMemoryJobStore()
    parent = _paused(store, continues_at_pause=True)
    _resumed(store, parent)

    assert not _takes_answers(store, parent.id)


def test_a_job_that_did_not_pause_takes_no_answers():
    store = InMemoryJobStore()
    job = _job()
    job.transition("running")
    asyncio.run(admit(store, job))

    assert not _takes_answers(store, job.id)


@pytest.mark.parametrize(
    ("status", "locker_ended", "takes"),
    [
        ("awaiting-answers", None, True),
        ("awaiting-answers", False, True),
        ("awaiting-answers", True, False),
        ("completed", None, False),
        (None, None, False),
    ],
)
def test_an_unreadable_record_takes_answers_as_its_stored_status_and_lock_say(
    status, locker_ended, takes
):
    """An unreadable record cannot say what it paused for, so a running lock
    keeps it taking answers."""
    locker = _job()
    locker.transition("running")
    if locker_ended:
        locker.transition("completed")
    store = _LockedBy("job-1", locker.id) if locker_ended is not None else None
    store = store or InMemoryJobStore()
    if locker_ended is not None:
        asyncio.run(admit(store, locker))

    assert asyncio.run(takes_answers(store, _unreadable(status))) is takes


# --- The continue refusal ---------------------------------------------------


def test_a_job_that_waits_to_continue_with_nothing_stored_was_not_continued():
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)

    assert _refusal(store, job.id) == "not-continued"


def test_a_job_this_process_continues_has_no_refusal():
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)

    assert _refusal(store, job.id, runs_here=True) is None


@pytest.mark.parametrize("stale", [False, True])
def test_a_stored_refusal_wins_whether_the_record_holds_it_or_the_store_does(stale):
    """The task can store its refusal and end between a route's read and this
    read, so a record read before it still gets the words from the store."""
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)
    before = _envelope(store, job.id)
    asyncio.run(store.set_continue_refusal(job.id, "alice", "No room."))
    record = before if stale else _envelope(store, job.id)
    assert isinstance(record, JobRecord)

    refusal = asyncio.run(why_not_continued(store, record, runs_here=False))
    assert refusal == StoredRefusal("No room.")


def test_a_job_whose_resumed_job_continues_it_has_no_refusal():
    store = InMemoryJobStore()
    parent = _paused(store, continues_at_pause=True)
    _resumed(store, parent)

    assert _refusal(store, parent.id) is None


def test_a_job_that_asks_has_no_refusal():
    store = InMemoryJobStore()

    assert _refusal(store, _paused(store, ask_questions=True).id) is None


@pytest.mark.parametrize("unreadable", [False, True])
def test_a_job_the_store_no_longer_gives_as_a_record_was_not_continued(unreadable):
    """The second read finds no stored refusal, and no Resumed Job locks it."""
    job = _job(continues_at_pause=True)
    store = _ParentAs(
        job.id, _unreadable("awaiting-answers", job.id) if unreadable else None
    )
    job.transition("running")
    job.transition("awaiting-answers")
    asyncio.run(admit(store, job))
    record = _envelope(store, job.id)
    assert isinstance(record, JobRecord)
    store.replace = True

    refusal = asyncio.run(why_not_continued(store, record, runs_here=False))
    assert refusal == "not-continued"


def test_a_queued_job_of_another_schema_version_runs_on_the_page():
    """A record of another schema version counts as ended, so its status goes
    through the page status table."""
    store = InMemoryJobStore()
    job = _job()
    asyncio.run(admit(store, job))
    other = _envelope(store, job.id).model_copy(update={"schema_version": "0.1"})

    state = asyncio.run(job_state(store, other, runs_here=False))

    assert state == EndedJobState(page_status="running", asks=False, continuing=False)


def test_a_job_whose_parent_is_of_another_schema_version_raises():
    parent = _job(ask_questions=True)
    parent.transition("running")
    parent.transition("awaiting-answers")
    other = parent.model_copy(update={"schema_version": "0.1"})
    store = _ParentAs(parent.id, other)
    asyncio.run(admit(store, parent))
    envelope = _envelope(store, _resumed(store, parent).id)
    assert isinstance(envelope, JobRecord)
    store.replace = True

    with pytest.raises(UnreadableSchema, match="schema version 0.1;"):
        asyncio.run(run_origin(store, envelope))
