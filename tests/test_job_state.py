"""The Job State reads each fact a page shows of a job from one read.

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
    RunningJobState,
    StoredRefusal,
    UnreadableJobState,
    job_state,
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


def _resumed(store: InMemoryJobStore, parent: JobRecord) -> JobRecord:
    """A Resumed Job of ``parent``, in flight."""
    child = _job(
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


def _state(store: InMemoryJobStore, job_id: str, *, held: bool = False):
    async def read():
        return await job_state(store, await store.owned(job_id, "alice"), held=held)

    return asyncio.run(read())


@pytest.mark.parametrize("asks", [True, False])
def test_a_fresh_run_starts_from_its_description_and_asks_where_the_job_asks(asks):
    store = InMemoryJobStore()
    job = _job(ask_questions=asks)
    job.transition("running")
    asyncio.run(admit(store, job))

    assert _state(store, job.id) == RunningJobState(
        asks=asks, start="description", resumption=None, parent=None
    )


@pytest.mark.parametrize(
    ("continues", "start"), [(False, "answers"), (True, "controls")]
)
def test_a_resumed_job_names_its_parent_and_never_asks(continues, start):
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=not continues, continues_at_pause=continues)
    child = _resumed(store, parent)

    state = _state(store, child.id)

    assert isinstance(state, RunningJobState)
    assert (state.asks, state.start, state.page_status) == (False, start, "running")
    assert state.resumption is not None
    assert state.parent is not None and state.parent.id == parent.id


def test_a_paused_job_that_asks_waits_for_answers():
    store = InMemoryJobStore()
    job = _paused(store, ask_questions=True)

    assert _state(store, job.id) == EndedJobState(
        page_status="awaiting-answers",
        asks=True,
        waiting=True,
        continuing=False,
        refusal=None,
    )


def test_a_paused_job_whose_resumed_job_runs_still_waits():
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=True)
    _resumed(store, parent)

    assert _state(store, parent.id).waiting


def test_a_job_this_process_continues_at_its_pause_runs():
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)

    state = _state(store, job.id, held=True)

    assert isinstance(state, EndedJobState)
    assert (state.page_status, state.continuing, state.asks) == ("running", True, False)
    assert state.refusal is None


def test_a_job_nobody_continued_says_so():
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)

    state = _state(store, job.id)

    assert isinstance(state, EndedJobState)
    assert (state.page_status, state.refusal) == ("awaiting-answers", "not-continued")


def test_a_stored_refusal_comes_from_the_store_though_the_record_is_older():
    """The task can store its refusal and end between a route's read and the
    state read, so the words come from the store."""
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)
    stale = asyncio.run(store.owned(job.id, "alice"))
    asyncio.run(store.set_continue_refusal(job.id, "alice", "No room."))

    state = asyncio.run(job_state(store, stale, held=False))

    assert isinstance(state, EndedJobState)
    assert state.refusal == StoredRefusal("No room.")


def test_a_finished_job_neither_asks_nor_waits():
    store = InMemoryJobStore()
    job = _job()
    job.transition("running")
    job.transition("failed")
    asyncio.run(admit(store, job))

    assert _state(store, job.id) == EndedJobState(
        page_status="failed", asks=False, waiting=False, continuing=False, refusal=None
    )


@pytest.mark.parametrize(
    ("status", "waiting"),
    [("awaiting-answers", True), ("completed", False), (None, False)],
)
def test_an_unreadable_record_carries_only_its_status_and_whether_it_waits(
    status, waiting
):
    state = asyncio.run(job_state(InMemoryJobStore(), _unreadable(status), held=False))

    assert state == UnreadableJobState(waiting=waiting)
    assert (state.page_status, state.asks, state.continuing) == (
        "unreadable",
        False,
        False,
    )


@pytest.mark.parametrize("unreadable", [False, True])
def test_a_job_in_flight_whose_parent_the_store_cannot_give_raises(unreadable):
    probe = _job()
    store = _ParentAs(
        probe.id, _unreadable("completed", probe.id) if unreadable else None
    )
    probe.ask_questions = True
    probe.transition("running")
    probe.transition("awaiting-answers")
    asyncio.run(admit(store, probe))
    child = _resumed(store, probe)
    envelope = asyncio.run(store.owned(child.id, "alice"))
    store.replace = True

    with pytest.raises(UnreadableSchema if unreadable else LookupError):
        asyncio.run(job_state(store, envelope, held=False))


def test_a_queued_job_runs_on_the_page():
    store = InMemoryJobStore()
    job = _job()
    asyncio.run(admit(store, job))

    assert _state(store, job.id).page_status == "running"


def test_a_rejected_job_fails_on_the_page():
    store = InMemoryJobStore()
    job = _job()
    job.transition("running")
    job.transition("rejected")
    asyncio.run(admit(store, job))

    assert _state(store, job.id).page_status == "failed"


def test_a_paused_job_whose_resumed_job_ended_waits_no_more():
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=True)
    child = _resumed(store, parent)
    child.transition("completed")
    asyncio.run(store.save(child))

    assert not _state(store, parent.id).waiting


def test_a_job_continued_for_its_controls_alone_does_not_wait():
    """The Resumed Job that continues it shows the analysis, and nothing
    refused the continue."""
    store = InMemoryJobStore()
    parent = _paused(store, continues_at_pause=True)
    _resumed(store, parent)

    state = _state(store, parent.id)

    assert isinstance(state, EndedJobState)
    assert (state.waiting, state.refusal) == (False, None)


def test_a_paused_job_whose_resumed_job_was_deleted_waits_no_more():
    """A store deletes only a job that ended, so a lock that is gone ended."""
    store = InMemoryJobStore()
    parent = _paused(store, ask_questions=True)
    child = _resumed(store, parent)
    child.transition("completed")
    asyncio.run(store.save(child))
    asyncio.run(store.delete(child.id, "alice"))

    assert not _state(store, parent.id).waiting


def test_a_refusal_on_the_record_is_read_without_the_store():
    store = InMemoryJobStore()
    job = _paused(store, continues_at_pause=True)
    asyncio.run(store.set_continue_refusal(job.id, "alice", "No room."))

    state = _state(store, job.id)

    assert isinstance(state, EndedJobState)
    assert state.refusal == StoredRefusal("No room.")


def test_a_paused_job_of_another_schema_version_neither_asks_nor_reads_a_parent():
    store = InMemoryJobStore()
    job = _paused(store, ask_questions=True)
    other = asyncio.run(store.owned(job.id, "alice")).model_copy(
        update={"schema_version": "0.1"}
    )

    state = asyncio.run(job_state(store, other, held=False))

    assert isinstance(state, EndedJobState)
    assert (state.page_status, state.asks) == ("awaiting-answers", False)
