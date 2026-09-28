"""A job store in one SQLite file, which a restart of the service keeps.

The ``memory`` store loses every job when the process stops, and a job that
asks questions waits for a person with no time limit (ADR 0045). This store
keeps each job, its checkpoint and its report in a file on disk, so a paused
job survives a restart and is answered as before (ADR 0049).

**One process at a time.** The store takes an exclusive lock on a file beside
the database, and a second process that opens the same path fails at startup.
That is what makes the recovery below correct: every queued or running job the
file holds when this process opens it belonged to a process that has stopped.

**Recovery on open.** Such a job will never run, so it ends as failed through
:meth:`~analysis_service.jobs.JobRecord.interrupt`. Left alone it would count
against its caller's concurrency ceiling for good.

**Admission is one transaction.** :meth:`SqliteJobStore.reserve` reads the held
jobs and inserts the new one inside ``BEGIN IMMEDIATE``, and the bounds are
:func:`~analysis_service.jobs.admit`'s, so this store and ``memory`` admit on
one reader of the rules.

**The file holds what callers submitted.** Sources, answers and reports rest
in it, so it is created readable and writable by its owner only, and every
statement binds its values rather than formatting them into SQL.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from analysis_service.jobs import (
    Admission,
    BudgetPolicy,
    JobEvent,
    JobRecord,
    JobStatus,
    JobStoreConfigError,
    admit,
)

__all__ = ["SqliteJobStore"]

_T = TypeVar("_T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    reserved_tokens INTEGER NOT NULL,
    measured_tokens INTEGER,
    record TEXT NOT NULL,
    report TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_subject ON jobs (owner_subject);
CREATE INDEX IF NOT EXISTS jobs_by_created ON jobs (created_at);
"""


@dataclass(frozen=True)
class _Held:
    """The columns admission reads of a job the file holds."""

    owner_subject: str
    status: JobStatus
    created_at: datetime
    reserved_tokens: int
    measured_tokens: int | None


def _columns(record: JobRecord) -> tuple[Any, ...]:
    """A record as the row stores it: the report apart from the rest."""
    return (
        record.owner_subject,
        record.status,
        record.created_at.timestamp(),
        record.reserved_tokens,
        record.measured_tokens,
        record.model_dump_json(exclude={"report"}),
        None if record.report is None else record.report.model_dump_json(),
    )


class SqliteJobStore:
    """Jobs in one SQLite file, for one process at a time."""

    def __init__(self, path: Path) -> None:
        if not path.parent.is_dir():
            raise JobStoreConfigError(
                f"the job store's directory {path.parent} does not exist"
            )
        self._lock_file = open(path.with_name(path.name + ".lock"), "a")  # noqa: SIM115
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_file.close()
            raise JobStoreConfigError(
                f"another process holds the job store {path}; one process at a"
                " time may open it"
            ) from None
        # Created owner-only before SQLite opens it, because it will hold what
        # callers submitted.
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.executescript(_SCHEMA)
        self._guard = threading.Lock()
        self._interrupt_unfinished()

    def close(self) -> None:
        """Close the database and release the lock, so another process may open it."""
        self._db.close()
        self._lock_file.close()

    def _interrupt_unfinished(self) -> None:
        rows = self._db.execute(
            "SELECT record, report FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        for record_json, report_json in rows:
            record = self._record(record_json, report_json)
            record.interrupt()
            self._update(record)

    async def _run(self, work: Callable[[], _T]) -> _T:
        """``work`` on a worker thread, one statement group at a time."""

        def guarded() -> _T:
            with self._guard:
                return work()

        return await asyncio.to_thread(guarded)

    @staticmethod
    def _record(record_json: str, report_json: str | None) -> JobRecord:
        data = json.loads(record_json)
        data["report"] = None if report_json is None else json.loads(report_json)
        return JobRecord.model_validate(data)

    def _update(self, record: JobRecord) -> int:
        owner, status, created, reserved, measured, body, report = _columns(record)
        return self._db.execute(
            "UPDATE jobs SET owner_subject = ?, status = ?, created_at = ?,"
            " reserved_tokens = ?, measured_tokens = ?, record = ?, report = ?"
            " WHERE id = ?",
            (owner, status, created, reserved, measured, body, report, record.id),
        ).rowcount

    async def reserve(
        self, record: JobRecord, *, ceiling: int, budget: BudgetPolicy
    ) -> Admission:
        """Check every admission bound and insert, in one transaction."""

        def work() -> Admission:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if self._db.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (record.id,)
                ).fetchone():
                    admission = Admission(outcome="duplicate", active=0)
                else:
                    since = budget.window_start().timestamp()
                    rows = self._db.execute(
                        "SELECT owner_subject, status, created_at, reserved_tokens,"
                        " measured_tokens FROM jobs"
                        " WHERE owner_subject = ? OR created_at >= ?",
                        (record.owner_subject, since),
                    ).fetchall()
                    held = [
                        _Held(
                            owner_subject=owner,
                            status=status,
                            created_at=datetime.fromtimestamp(created, UTC),
                            reserved_tokens=reserved,
                            measured_tokens=measured,
                        )
                        for owner, status, created, reserved, measured in rows
                    ]
                    admission = admit(record, held, ceiling, budget)
                    if admission.outcome == "admitted":
                        self._db.execute(
                            "INSERT INTO jobs (id, owner_subject, status, created_at,"
                            " reserved_tokens, measured_tokens, record, report)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (record.id, *_columns(record)),
                        )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            return admission

        return await self._run(work)

    async def get(self, job_id: str) -> JobRecord | None:
        def work() -> JobRecord | None:
            row = self._db.execute(
                "SELECT record, report FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return None if row is None else self._record(*row)

        return await self._run(work)

    async def owned(self, job_id: str, subject: str) -> JobRecord | None:
        """The record ``subject`` owns, without its report, or ``None``.

        A foreign job and a missing one give the same answer, so a job id
        cannot be probed for existence.
        """

        def work() -> JobRecord | None:
            row = self._db.execute(
                "SELECT record FROM jobs WHERE id = ? AND owner_subject = ?",
                (job_id, subject),
            ).fetchone()
            return None if row is None else self._record(row[0], None)

        return await self._run(work)

    async def report_json(self, job_id: str, subject: str) -> dict[str, Any] | None:
        def work() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT report FROM jobs WHERE id = ? AND owner_subject = ?",
                (job_id, subject),
            ).fetchone()
            return None if row is None or row[0] is None else json.loads(row[0])

        return await self._run(work)

    async def events_after(
        self, job_id: str, seen: int
    ) -> tuple[JobStatus, list[JobEvent]] | None:
        def work() -> tuple[JobStatus, list[JobEvent]] | None:
            row = self._db.execute(
                "SELECT record FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            record = self._record(row[0], None)
            return record.status, record.events[seen:]

        return await self._run(work)

    async def save(self, record: JobRecord) -> None:
        def work() -> None:
            if self._update(record) == 0:
                raise ValueError(f"job {record.id!r} does not exist")

        await self._run(work)


def sqlite_store(env: Mapping[str, str]) -> SqliteJobStore:
    """The store at ``ANALYSIS_JOB_STORE_PATH``; fail closed where it is unset."""
    raw = env.get("ANALYSIS_JOB_STORE_PATH", "").strip()
    if not raw:
        raise JobStoreConfigError(
            "set ANALYSIS_JOB_STORE_PATH to the SQLite file the sqlite job store uses"
        )
    return SqliteJobStore(Path(raw))
