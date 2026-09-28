"""The durable job store: what survives a restart, and what a restart ends.

The contract every store answers is in ``tests/test_jobs.py``, which runs it on
this store and on ``memory``. These tests hold what only a durable store owes:
a paused job answered after a restart, the jobs a restart interrupts, one
process at a time, and a file only its owner reads.
"""

from __future__ import annotations

import asyncio
import stat

import pytest

from analysis_service.jobs import (
    INTERRUPTED_FAILURE_MESSAGE,
    JobStoreConfigError,
    build_store,
)
from analysis_service.sqlite_store import SqliteJobStore
from tests.test_api import admit, auth
from tests.test_links import catalog_client
from tests.test_pause import LINK, asking_job, waiting


@pytest.fixture
def path(tmp_path):
    return tmp_path / "jobs.db"


def test_a_paused_job_is_answered_after_a_restart(path):
    before = SqliteJobStore(path)
    client, _ = catalog_client(before)
    job = waiting(before)
    asked = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
    before.close()

    after = SqliteJobStore(path)
    client, _ = catalog_client(after)
    again = client.get(f"/v1/jobs/{job}/questions", headers=auth())
    answered = client.post(
        f"/v1/jobs/{job}/answers", json={"links": [LINK.model_dump()]}, headers=auth()
    )
    after.close()

    assert again.status_code == 200
    assert again.json() == asked
    assert answered.status_code == 201, answered.text


def test_a_restart_ends_the_jobs_it_interrupted_and_keeps_the_rest(path):
    store = SqliteJobStore(path)
    queued, running = asking_job(), asking_job()
    asyncio.run(admit(store, queued))
    asyncio.run(admit(store, running))
    running.transition("running")
    asyncio.run(store.save(running))
    paused = waiting(store)
    store.close()

    reopened = SqliteJobStore(path)
    ended = [asyncio.run(reopened.get(job.id)) for job in (queued, running)]
    kept = asyncio.run(reopened.get(paused))
    reopened.close()

    assert [(job.status, job.error) for job in ended if job] == [
        ("failed", INTERRUPTED_FAILURE_MESSAGE),
        ("failed", INTERRUPTED_FAILURE_MESSAGE),
    ]
    assert kept is not None and kept.status == "awaiting-answers"
    assert kept.checkpoint is not None


def test_one_process_at_a_time(path):
    first = SqliteJobStore(path)
    with pytest.raises(JobStoreConfigError, match="another process"):
        SqliteJobStore(path)
    first.close()
    SqliteJobStore(path).close()


def test_the_file_is_readable_by_its_owner_only(path):
    SqliteJobStore(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_directory_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(JobStoreConfigError, match="does not exist"):
        SqliteJobStore(tmp_path / "missing" / "jobs.db")


class TestTheRegistry:
    def test_sqlite_needs_a_path(self):
        with pytest.raises(JobStoreConfigError, match="ANALYSIS_JOB_STORE_PATH"):
            build_store({"ANALYSIS_JOB_STORE": "sqlite"})

    def test_sqlite_opens_the_named_file(self, path):
        store = build_store(
            {"ANALYSIS_JOB_STORE": "sqlite", "ANALYSIS_JOB_STORE_PATH": str(path)}
        )
        assert isinstance(store, SqliteJobStore)
        store.close()
        assert path.is_file()
