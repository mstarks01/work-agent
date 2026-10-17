"""The ``sql`` store on SQLite with a filesystem root, at the HTTP surface and below.

``tests/test_job_store.py`` runs the whole store contract on this store too.
This module holds what the contract cannot say: each startup refusal and the
startup order, a job that lives through a restart of the app, the JSON round
trip of every record field, the digest check, the write-once report, and the
migration against the tables the store reads.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from analysis_service import jobs as jobs_module
from analysis_service import store_backends
from analysis_service.answer_sets import AnswerSet
from analysis_service.api import create_app
from analysis_service.certification import CertifyResult
from analysis_service.fact_answers import FactAnswer
from analysis_service.jobs import (
    GENERIC_FAILURE_MESSAGE,
    RESTART_FAILURE_MESSAGE,
    Checkpoint,
    JobRecord,
    JobStore,
    PipelineCompleted,
    Resumption,
    UnreadableRecord,
    UnreadableSchema,
    execute_job,
    parent_of,
    readable,
)
from analysis_service.objects import build_object_store, object_key
from analysis_service.report import NodeRun, Report, TokenUsage
from analysis_service.sources import Source
from analysis_service.sql_store import (
    METADATA,
    CorruptObject,
    SqlJobStore,
    engine_for,
)
from analysis_service.store_backends import JOB_STORES, build_store
from analysis_service.store_settings import StoreConfigError
from analysis_service.validation import ValidationIssue
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    SEEDING_BUDGET,
    admit,
    sample_report,
    sample_selection,
    valid_model,
)
from tests.sql_stores import database_of, migrate, sql_env, sqlite_url
from tests.test_api import (
    TEST_DEADLINE_SECONDS,
    TEST_LIMITS,
    TEST_MAX_ACTIVE_JOBS,
    FakeVerifier,
    auth,
    submission,
)
from tests.test_job_store import DeletingReads, follow_up, new_record
from tests.test_resume import LINK
from tests.test_webapp_questions import PausingRunner

#: A fact key the store keeps without reading it.
KEY = ("df-1", "encryption_in_transit", "", "", "", "")


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return sql_env(tmp_path)


def app_over(store: JobStore, runner=None):
    """The service app over ``store``, as a deployment that builds a catalog."""
    return create_app(
        store=store,
        runner=runner if runner is not None else PausingRunner(),
        verifier=FakeVerifier(),
        limits=TEST_LIMITS,
        job_deadline_seconds=TEST_DEADLINE_SECONDS,
        max_active_jobs=TEST_MAX_ACTIVE_JOBS,
        budget=SEEDING_BUDGET,
        frameworks=DEFAULT_FRAMEWORKS,
        carries_catalog=True,
    )


def start(env: dict[str, str]) -> None:
    """Build the store from ``env``, then start and stop the app over it."""
    with TestClient(app_over(build_store(env))):
        pass


def queued(store: JobStore) -> str:
    record = JobRecord.create(
        owner_id="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
    )
    asyncio.run(admit(store, record))
    return record.id


def empty_database(directory: Path) -> Path:
    """A private SQLite file that holds no schema."""
    path = directory / "empty.db"
    path.touch(mode=0o600)
    return path


def test_the_migration_makes_a_file_that_only_the_service_user_can_open(tmp_path):
    path = tmp_path / "new.db"
    previous = os.umask(0o022)
    try:
        migrate(path)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


class TestTheStartupRefusals:
    """Each unusable setting stops the startup before the app takes a request."""

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"ANALYSIS_JOB_STORE": None}, "set ANALYSIS_JOB_STORE"),
            ({"ANALYSIS_JOB_STORE": "redis"}, "unknown value for"),
            ({"ANALYSIS_OBJECT_STORE": None}, "set ANALYSIS_OBJECT_STORE"),
            ({"ANALYSIS_OBJECT_STORE": "tape"}, "unknown value for"),
            (
                {
                    "ANALYSIS_JOB_STORE": "memory",
                    "ANALYSIS_DATABASE_URL": None,
                    "ANALYSIS_OBJECT_ROOT": None,
                },
                "memory job store takes no ANALYSIS_OBJECT_STORE; unset it$",
            ),
            (
                {
                    "ANALYSIS_JOB_STORE": "memory",
                    "ANALYSIS_OBJECT_STORE": None,
                    "ANALYSIS_OBJECT_ROOT": None,
                },
                "memory job store takes no ANALYSIS_DATABASE_URL; unset it$",
            ),
            (
                {"ANALYSIS_OBJECT_BUCKET": "jobs"},
                "filesystem object store takes no ANALYSIS_OBJECT_BUCKET",
            ),
            (
                {"ANALYSIS_OBJECT_ROOTS": "/srv/objects"},
                "filesystem object store takes no ANALYSIS_OBJECT_ROOTS",
            ),
            ({"ANALYSIS_DATABASE_URL": None}, "set ANALYSIS_DATABASE_URL"),
            ({"ANALYSIS_DATABASE_URL": "not a url"}, "is not a database URL"),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+asyncpg://u:hunter2@[::1/x"},
                "is not a database URL$",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "sqlite+aiosqlite://user:hunter2@/x.db"},
                "holds a password",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "sqlite+aiosqlite:///x.db?password=hunter2"},
                "holds a password",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+psycopg2://db/jobs"},
                "unknown value for",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+asyncpg://analysis@db.internal"},
                "PostgreSQL names no database",
            ),
            (
                {"ANALYSIS_DATABASE_CREDENTIALS": "password"},
                "SQLite database takes no ANALYSIS_DATABASE_CREDENTIALS",
            ),
            (
                {"ANALYSIS_DATABASE_PASSWORD": "hunter2"},
                "SQLite database takes no ANALYSIS_DATABASE_PASSWORD;",
            ),
            ({"ANALYSIS_OBJECT_ROOT": None}, "set ANALYSIS_OBJECT_ROOT"),
            ({"ANALYSIS_OBJECT_ROOT": "objects"}, "absolute path"),
        ],
    )
    def test_an_unusable_setting(self, env, change, message):
        for var, value in change.items():
            if value is None:
                del env[var]
            else:
                env[var] = value
        with pytest.raises(StoreConfigError, match=message) as refused:
            start(env)
        assert "hunter2" not in str(refused.value)

    def test_a_schema_that_is_not_at_the_latest_revision(self, env, tmp_path):
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(empty_database(tmp_path))
        with pytest.raises(StoreConfigError, match="revision None"):
            start(env)

    def test_a_missing_database_file(self, env, tmp_path):
        """The service never makes the file: only the migration step does."""
        missing = tmp_path / "missing.db"
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(missing)
        with pytest.raises(StoreConfigError, match="does not exist"):
            start(env)
        assert not missing.exists()

    def test_a_database_file_that_others_can_open(self, env):
        database = env["ANALYSIS_DATABASE_URL"].removeprefix("sqlite+aiosqlite:///")
        os.chmod(database, 0o640)
        with pytest.raises(StoreConfigError, match="set its mode to 0600"):
            start(env)

    @pytest.mark.parametrize("mode", [0o750, 0o705, 0o1777])
    def test_a_database_directory_that_others_can_open(self, env, mode):
        """Another user who can write the directory can put a link or a hot
        journal beside the file between the check and a connection."""
        database = Path(
            env["ANALYSIS_DATABASE_URL"].removeprefix("sqlite+aiosqlite:///")
        )
        os.chmod(database.parent, mode)
        with pytest.raises(StoreConfigError, match="set its mode to 0700"):
            start(env)

    def test_the_migration_makes_no_file_in_a_directory_others_can_open(self, tmp_path):
        database = tmp_path / "shared" / "jobs.db"
        database.parent.mkdir(mode=0o700)
        os.chmod(database.parent, 0o777)
        with pytest.raises(StoreConfigError, match="set its mode to 0700"):
            migrate(database)
        assert not database.exists()

    def test_a_database_file_that_is_a_symbolic_link(self, env, tmp_path):
        database = env["ANALYSIS_DATABASE_URL"].removeprefix("sqlite+aiosqlite:///")
        link = tmp_path / "link.db"
        link.symlink_to(database)
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(link)
        with pytest.raises(StoreConfigError, match="not a regular file"):
            start(env)

    def test_a_relative_database_path(self, env):
        env["ANALYSIS_DATABASE_URL"] = "sqlite+aiosqlite:///jobs.db"
        with pytest.raises(StoreConfigError, match="absolute path"):
            start(env)

    def test_a_missing_root(self, env):
        os.rmdir(env["ANALYSIS_OBJECT_ROOT"])
        with pytest.raises(StoreConfigError, match="does not exist"):
            start(env)

    def test_a_root_that_is_a_file(self, env, tmp_path):
        env["ANALYSIS_OBJECT_ROOT"] = str(tmp_path / "jobs.db")
        with pytest.raises(StoreConfigError, match="not a directory"):
            start(env)

    @pytest.mark.parametrize("mode", [0o750, 0o705, 0o720])
    def test_a_root_a_group_or_others_can_open(self, env, mode):
        os.chmod(env["ANALYSIS_OBJECT_ROOT"], mode)
        with pytest.raises(StoreConfigError, match="a group or others"):
            start(env)

    def test_a_root_another_user_owns(self, env, monkeypatch):
        monkeypatch.setattr(
            "analysis_service.objects.os.geteuid", lambda: os.getuid() + 1
        )
        with pytest.raises(StoreConfigError, match="another user owns"):
            start(env)

    def test_a_usable_store_starts(self, env):
        start(env)


class TestTheStartupOrder:
    """Settings, the schema revision, the object store, the restart scan, requests."""

    def test_the_schema_is_checked_before_the_object_store(self, env, tmp_path):
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(empty_database(tmp_path))
        os.rmdir(env["ANALYSIS_OBJECT_ROOT"])
        with pytest.raises(StoreConfigError, match="revision"):
            start(env)

    def test_a_failed_check_stops_the_startup_before_the_restart_scan(self, env):
        store = build_store(env)
        job = queued(store)
        os.chmod(env["ANALYSIS_OBJECT_ROOT"], 0o750)
        with (
            pytest.raises(StoreConfigError),
            TestClient(app_over(store)),
        ):
            pass
        os.chmod(env["ANALYSIS_OBJECT_ROOT"], 0o700)
        assert asyncio.run(store.get(job)).status == "queued"


class TestARestart:
    """A second app over the same database and root serves what the first one kept."""

    def test_a_paused_job_is_answered_after_a_restart(self, env):
        with TestClient(app_over(build_store(env))) as client:
            response = client.post(
                "/v1/jobs", json=submission(questions=True), headers=auth()
            )
            assert response.status_code == 201
            job = response.json()["job_id"]
            assert client.get(f"/v1/jobs/{job}", headers=auth()).json()["status"] == (
                "awaiting-answers"
            )

        runner = PausingRunner()
        with TestClient(app_over(build_store(env), runner)) as client:
            questions = client.get(f"/v1/jobs/{job}/questions", headers=auth())
            assert questions.status_code == 200
            response = client.post(
                f"/v1/jobs/{job}/answers",
                json={"links": [LINK.model_dump()], "revision": 0},
                headers=auth(),
            )
            assert response.status_code == 201
            child = response.json()["job_id"]
            body = client.get(f"/v1/jobs/{child}", headers=auth()).json()
            report = client.get(f"/v1/jobs/{child}/report", headers=auth())
        assert body["status"] == "completed"
        assert runner.resumed_links == [[LINK]]
        assert report.status_code == 200

    def test_a_queued_job_reads_as_failed_after_a_restart(self, env):
        with TestClient(app_over(build_store(env))):
            job = queued(build_store(env))
        with TestClient(app_over(build_store(env))) as client:
            body = client.get(f"/v1/jobs/{job}", headers=auth()).json()
        assert (body["status"], body["error"]) == ("failed", RESTART_FAILURE_MESSAGE)

    def test_a_report_its_events_and_its_answers_live_through_a_restart(self, env):
        with TestClient(app_over(build_store(env))) as client:
            job = client.post(
                "/v1/jobs", json=submission(questions=True), headers=auth()
            ).json()["job_id"]
            child = client.post(
                f"/v1/jobs/{job}/answers",
                json={"links": [LINK.model_dump()], "revision": 0},
                headers=auth(),
            ).json()["job_id"]
            before = client.get(f"/v1/jobs/{child}/report", headers=auth()).json()
            events = client.get(f"/v1/jobs/{child}/events", headers=auth()).text

        store = build_store(env)
        with TestClient(app_over(store)) as client:
            after = client.get(f"/v1/jobs/{child}/report", headers=auth()).json()
            replayed = client.get(f"/v1/jobs/{child}/events", headers=auth()).text
            carrying = client.post(
                "/v1/jobs",
                json=submission(questions=True, answers_from=child),
                headers=auth(),
            )
        assert after == before
        assert replayed == events
        assert carrying.status_code == 201
        carried = asyncio.run(store.get(carrying.json()["job_id"])).carried
        assert carried.links == (LINK,)


class TestADeleteBeforeTheAdmission:
    """A delete of the parent that commits after the route read it, and
    before the store admits the child."""

    @pytest.mark.parametrize(
        ("route", "body"),
        [
            ("answers", {"links": [LINK.model_dump()], "revision": 0}),
            (
                "amendments",
                {"text": "The database is on a private network.", "revision": 0},
            ),
        ],
    )
    def test_the_child_reads_as_missing_and_no_run_starts(self, env, route, body):
        store = build_store(env)
        runner = PausingRunner()
        started = []
        run = runner.run

        async def counting(job, on_node):
            started.append(job.id)
            return await run(job, on_node)

        reserve = store.reserve

        async def deleting_first(record, **bounds):
            parent = parent_of(record)
            if parent is not None:
                assert await store.delete(parent, record.owner_id) is not None
            return await reserve(record, **bounds)

        runner.run = counting
        with TestClient(app_over(store, runner)) as client:
            job = client.post(
                "/v1/jobs", json=submission(questions=True), headers=auth()
            ).json()["job_id"]
            owner_id = asyncio.run(store.get(job)).owner_id
            store.reserve = deleting_first
            response = client.post(f"/v1/jobs/{job}/{route}", json=body, headers=auth())
            parent = client.get(f"/v1/jobs/{job}", headers=auth())
        assert (response.status_code, parent.status_code) == (404, 404)
        assert started == [job]
        assert asyncio.run(store.owned_jobs(owner_id)) == []


def every_field(owner_id: str = "alice") -> JobRecord:
    """A record whose every field holds a value other than its default."""
    held = Checkpoint(system_model=valid_model(), assertions=None)
    answers = AnswerSet(links=(LINK,), facts=(FactAnswer(key=KEY, value="TLS 1.3"),))
    record = JobRecord.create(
        owner_id=owner_id,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        system_name="orders",
        answers=answers,
        shown_early=[KEY],
        skipped_early=[KEY, "a free question"],
        resumption=Resumption(
            parent_id="job-" + "0" * 32, checkpoint=held, follow_up=True
        ),
        amends="job-" + "1" * 32,
        parent_revision=3,
        carried=AnswerSet(facts=(FactAnswer(key=KEY, value="mTLS"),)),
        ask_questions=True,
        reserved_tokens=500,
    )
    record.transition("running")
    record.record_node("extract")
    record.corrections = [FactAnswer(key=KEY, value="TLS 1.2")]
    record.draft = AnswerSet(facts=(FactAnswer(key=KEY, value="none"),))
    record.revision = 2
    record.checkpoint = held
    record.validation_issues = [
        ValidationIssue(code="no-trust-zones", message="the model declares none")
    ]
    record.certification = CertifyResult(certified=True)
    record.unreported_nodes = [NodeRun(node="extract", model="m", duration_ms=10)]
    record.error = "a stored message"
    record.report = sample_report()
    record.transition("completed")
    return record


class TestTheJsonRoundTrip:
    """Each field of a record survives the store unchanged (#1526's probe)."""

    def test_the_probe_sets_every_field(self):
        record = every_field()
        defaults = [
            name
            for name, field in JobRecord.model_fields.items()
            if not field.is_required()
            and getattr(record, name) == field.get_default(call_default_factory=True)
        ]
        assert defaults == []

    def test_every_field_survives_a_save_and_a_read(self, env):
        store = build_store(env)
        record = every_field()
        parent = JobRecord.create(
            owner_id="alice",
            sources=[Source.description("a web app storing orders")],
            frameworks=sample_selection(),
        )
        update = {"id": parent_of(record), "revision": record.parent_revision}
        asyncio.run(admit(store, parent.model_copy(update=update)))
        first = record.model_copy(
            update={"status": "queued", "report": None, "events": record.events[:1]}
        )
        asyncio.run(admit(store, first))
        asyncio.run(store.save(record))
        stored = asyncio.run(store.get(record.id))
        for name in JobRecord.model_fields:
            assert getattr(stored, name) == getattr(record, name), name
        assert stored.model_dump_json() == record.model_dump_json()


def completed_job(store: JobStore) -> JobRecord:
    record = JobRecord.create(
        owner_id="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
    )
    record.transition("running")
    record.report = sample_report()
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record


def report_file(env: dict[str, str], record: JobRecord) -> Path:
    return Path(env["ANALYSIS_OBJECT_ROOT"], record.owner_id, record.id, "report.json")


def of_another_version(env: dict[str, str], store: JobStore, record: JobRecord) -> None:
    """State another schema version on ``record``'s row, and store its sources
    in a shape this service cannot parse, under a digest that matches them."""
    record.schema_version = "1.0"
    asyncio.run(store.save(record))
    sources = b'[{"x": 1}]'
    report_file(env, record).with_name("sources.json").write_bytes(sources)
    database = env["ANALYSIS_DATABASE_URL"].partition(":///")[2]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE jobs SET sources_sha256 = ? WHERE id = ?",
            (hashlib.sha256(sources).hexdigest(), record.id),
        )


class TestTheDigestCheck:
    """A changed or missing object is refused, and the API says no more than 500."""

    def test_a_changed_report_is_refused_and_logged(self, env, caplog):
        store = build_store(env)
        record = completed_job(store)
        path = report_file(env, record)
        path.write_bytes(path.read_bytes().replace(b'"', b"'", 1))
        with (
            caplog.at_level(logging.ERROR, "analysis_service.sql_store"),
            pytest.raises(CorruptObject),
        ):
            asyncio.run(store.report_json(record.id, "alice"))
        assert record.id in caplog.text
        assert "alice" in caplog.text

    def test_a_missing_sources_object_is_refused(self, env):
        store = build_store(env)
        record = completed_job(store)
        report_file(env, record).with_name("sources.json").unlink()
        with pytest.raises(CorruptObject):
            asyncio.run(store.sources(record.id, "alice"))
        with pytest.raises(CorruptObject):
            asyncio.run(store.get(record.id))

    def test_the_api_answers_500_with_no_detail(self, env):
        store = build_store(env)
        record = completed_job(store)
        report_file(env, record).write_bytes(b"{}")
        app = app_over(store)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/v1/jobs/{record.id}/report", headers=auth())
        assert response.status_code == 500
        assert response.json()["detail"] == "an internal error occurred"

    @pytest.mark.parametrize(
        ("path", "name"),
        [("report", "report.json"), ("questions", "sources.json")],
    )
    def test_a_delete_during_the_read_answers_404(self, env, path, name):
        objects = DeletingReads(build_object_store(env))
        store = objects.store = JOB_STORES["sql"].build(env, objects)
        record = completed_job(store)
        objects.deleting = {object_key("alice", record.id, name)}
        with TestClient(app_over(store), raise_server_exceptions=False) as client:
            response = client.get(f"/v1/jobs/{record.id}/{path}", headers=auth())
        assert response.status_code == 404

    def test_a_foreign_read_never_reaches_the_object(self, env):
        """The owner is checked on the row first, so a foreign read of a job
        whose object is gone is the same ``None`` as a missing job."""
        store = build_store(env)
        record = completed_job(store)
        report_file(env, record).unlink()
        assert asyncio.run(store.report_json(record.id, "mallory")) is None
        assert asyncio.run(store.owned(record.id, "mallory")) is None


class TestTheReport:
    def test_a_report_from_another_schema_version_reads_as_stored(self, env):
        store = build_store(env)
        record = completed_job(store)
        record.schema_version = "1.0"
        asyncio.run(store.save(record))
        stored = report_file(env, record).read_bytes()
        app = app_over(store)
        with TestClient(app) as client:
            response = client.get(f"/v1/jobs/{record.id}/report", headers=auth())
        assert response.status_code == 200
        assert response.content == stored

    def test_sources_of_another_schema_version_are_never_parsed(self, env):
        """The status route and the delete read the envelope, which holds no
        part that the schema version covers, and a parsing route answers 409."""
        store = build_store(env)
        record = completed_job(store)
        of_another_version(env, store, record)
        with TestClient(app_over(store)) as client:
            status = client.get(f"/v1/jobs/{record.id}", headers=auth())
            questions = client.get(f"/v1/jobs/{record.id}/questions", headers=auth())
            deleted = client.delete(f"/v1/jobs/{record.id}", headers=auth())
        assert status.status_code == 200, status.text
        assert questions.status_code == 409, questions.text
        assert deleted.status_code == 204, deleted.text

    def test_the_restart_scan_fails_a_job_of_another_schema_version(self, env):
        store = build_store(env)
        record = JobRecord.create(
            owner_id="alice",
            sources=[Source.description("a web app storing orders")],
            frameworks=sample_selection(),
        )
        record.transition("running")
        asyncio.run(admit(store, record))
        of_another_version(env, store, record)
        assert asyncio.run(store.fail_in_flight()) == [record.id]
        failed = asyncio.run(store.owned(record.id, "alice"))
        assert (failed.status, failed.error) == ("failed", RESTART_FAILURE_MESSAGE)

    def test_a_delete_removes_both_objects(self, env):
        store = build_store(env)
        record = completed_job(store)
        assert asyncio.run(store.delete(record.id, "alice")) == 2
        assert not report_file(env, record).parent.exists()


#: A stored value that this service cannot read: a value that a closed set
#: does not list, for each closed set the maintainer's ruling of 2026-10-16
#: names, and text that is not JSON (#1816). Each entry holds the statement
#: that writes the value, and the value.
UNLISTED: dict[str, tuple[str, str]] = {
    "row text that is not JSON": (
        "UPDATE jobs SET validation_issues = ? WHERE id = ?",
        "{",
    ),
    "event text that is not JSON": (
        "UPDATE job_events SET event = ? WHERE job_id = ? AND seq = 1",
        "{",
    ),
    "an issue code": (
        "UPDATE jobs SET validation_issues = ? WHERE id = ?",
        json.dumps([{"code": "a-removed-code", "message": "the model is invalid"}]),
    ),
    "a status": ("UPDATE jobs SET status = ? WHERE id = ?", "a-removed-status"),
    "an event kind": (
        "UPDATE job_events SET event = ? WHERE job_id = ? AND seq = 1",
        json.dumps({"seq": 1, "kind": "a-removed-kind", "at": "2026-10-16T00:00:00Z"}),
    ),
}


def store_unlisted(database: str, job_id: str, case: str) -> None:
    """Write the value of ``case`` into the stored job ``job_id``."""
    statement, value = UNLISTED[case]
    with sqlite3.connect(database) as connection:
        assert connection.execute(statement, (value, job_id)).rowcount == 1


#: Each value of :data:`UNLISTED` on a job that its stored status puts in
#: flight. A removed status is itself the stored status.
UNREADABLE_IN_FLIGHT = [
    *(
        (case, status)
        for case in UNLISTED
        if case != "a status"
        for status in ("queued", "running")
    ),
    ("a status", "queued"),
]


def in_flight(
    store: JobStore, status: str, days_ago: int = 0, owner: str = "alice"
) -> str:
    """The ID of a job of ``owner`` in ``status``, last updated ``days_ago``
    days ago."""
    record = JobRecord.create(
        owner_id=owner,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
    )
    if status == "running":
        record.transition("running")
    record.updated_at = datetime.now(UTC) - timedelta(days=days_ago, minutes=1)
    asyncio.run(admit(store, record))
    return record.id


class TestAStoredValueThisServiceDoesNotList:
    """A stored part that does not parse makes the job unreadable, as another
    schema version does (ADR 0095, the maintainer's ruling of 2026-10-16)."""

    @pytest.mark.parametrize("case", UNLISTED)
    def test_the_list_holds_the_job_as_unreadable(self, env, case):
        store = build_store(env)
        kept = completed_job(store)
        record = completed_job(store)
        store_unlisted(database_of(env), record.id, case)
        listed = asyncio.run(store.owned_jobs("alice"))
        assert [item.id for item in listed] == [kept.id, record.id]
        assert readable(listed[0])
        assert isinstance(listed[1], UnreadableRecord)
        assert not readable(listed[1])
        assert listed[1].status == (None if case == "a status" else "completed")

    @pytest.mark.parametrize("case", UNLISTED)
    def test_the_status_route_says_unreadable_and_each_page_answers_409(
        self, env, case
    ):
        store = build_store(env)
        record = completed_job(store)
        store_unlisted(database_of(env), record.id, case)
        with TestClient(app_over(store)) as client:
            status = client.get(f"/v1/jobs/{record.id}", headers=auth())
            pages = [
                client.get(f"/v1/jobs/{record.id}/{page}", headers=auth())
                for page in ("report", "questions", "events")
            ]
        assert status.status_code == 200, status.text
        assert status.json() == {
            "job_id": record.id,
            "status": "unreadable",
            "created_at": status.json()["created_at"],
            "updated_at": status.json()["updated_at"],
            "progress": [],
        }
        for page in pages:
            assert page.status_code == 409, page.text
            assert "submit the job again" in page.json()["detail"]

    @pytest.mark.parametrize(("case", "status"), UNREADABLE_IN_FLIGHT)
    def test_its_owner_deletes_it_and_another_owner_finds_nothing(
        self, env, case, status
    ):
        """An unreadable job has ended, whatever its stored status (ADR 0095)."""
        store = build_store(env)
        job = in_flight(store, status)
        in_flight(store, "running", owner="bob")
        store_unlisted(database_of(env), job, case)
        with TestClient(app_over(store)) as client:
            foreign = client.delete(f"/v1/jobs/{job}", headers=auth("bob-token"))
            missing = client.delete(
                f"/v1/jobs/job-{'0' * 32}", headers=auth("bob-token")
            )
            deleted = client.delete(f"/v1/jobs/{job}", headers=auth())
        assert foreign.status_code == missing.status_code == 404, foreign.text
        assert foreign.json() == missing.json()
        assert deleted.status_code == 204, deleted.text

    @pytest.mark.parametrize(("case", "status"), UNREADABLE_IN_FLIGHT)
    def test_it_holds_no_slot(self, env, case, status):
        store = build_store(env)
        job = in_flight(store, status)
        store_unlisted(database_of(env), job, case)
        record = JobRecord.create(
            owner_id="alice",
            sources=[Source.description("a web app storing orders")],
            frameworks=sample_selection(),
        )
        admission = asyncio.run(store.reserve(record, ceiling=1, budget=SEEDING_BUDGET))
        assert admission.outcome == "admitted"

    @pytest.mark.parametrize(("case", "status"), UNREADABLE_IN_FLIGHT)
    def test_the_retention_sweep_removes_it_and_keeps_its_status(
        self, env, case, status
    ):
        store = build_store(env)
        job = in_flight(store, status, days_ago=30)
        store_unlisted(database_of(env), job, case)
        with sqlite3.connect(database_of(env)) as connection:
            (stored,) = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job,)
            ).fetchone()
        assert asyncio.run(store.expired(datetime.now(UTC))) == [(job, "alice")]
        asyncio.run(store_backends.expire_jobs(store, 1))
        assert asyncio.run(store.owned(job, "alice")) is None
        with sqlite3.connect(database_of(env)) as connection:
            (kept,) = connection.execute(
                "SELECT status FROM usage_rows WHERE job_id = ?", (job,)
            ).fetchone()
        assert kept == stored

    @pytest.mark.parametrize(("case", "status"), UNREADABLE_IN_FLIGHT)
    @pytest.mark.parametrize("deleted", [False, True], ids=["held", "deleted"])
    def test_it_does_not_lock_its_parent(self, env, case, status, deleted):
        """An unreadable child has ended and read nothing into a report, so
        its parent takes answers again (ADR 0095)."""
        store = build_store(env)
        parent = completed_job(store).id
        child = follow_up(parent)
        if status == "running":
            child.transition("running")
        asyncio.run(admit(store, child))
        store_unlisted(database_of(env), child.id, case)
        if deleted:
            assert asyncio.run(store.delete(child.id, "alice")) is not None
        assert asyncio.run(store.resumed_by(parent, "alice")) is None
        asyncio.run(admit(store, follow_up(parent)))

    @pytest.mark.parametrize(
        "certification", ['{"certified": "maybe"}', '{"certified": true, "x": 1}']
    )
    def test_a_certification_that_does_not_parse_is_never_served(
        self, env, certification
    ):
        store = build_store(env)
        record = completed_job(store)
        with TestClient(app_over(store)) as client:
            served = client.get(f"/v1/jobs/{record.id}/report", headers=auth())
            with sqlite3.connect(database_of(env)) as connection:
                connection.execute(
                    "UPDATE jobs SET certification = ? WHERE id = ?",
                    (certification, record.id),
                )
            refused = client.get(f"/v1/jobs/{record.id}/report", headers=auth())
        assert served.status_code == 200, served.text
        assert refused.status_code == 409, refused.text

    def test_a_part_read_by_name_that_does_not_parse_is_unreadable(self, env):
        store = build_store(env)
        record = completed_job(store)
        sources = b'[{"x": 1}]'
        report_file(env, record).with_name("sources.json").write_bytes(sources)
        with sqlite3.connect(database_of(env)) as connection:
            connection.execute(
                "UPDATE jobs SET sources_sha256 = ? WHERE id = ?",
                (hashlib.sha256(sources).hexdigest(), record.id),
            )
        with pytest.raises(UnreadableSchema):
            asyncio.run(store.sources(record.id, "alice"))
        with TestClient(app_over(store)) as client:
            questions = client.get(f"/v1/jobs/{record.id}/questions", headers=auth())
        assert questions.status_code == 409, questions.text

    def test_the_restart_scan_leaves_an_unreadable_job_as_stored(self, env, caplog):
        store = build_store(env)
        job = queued(store)
        store_unlisted(database_of(env), job, "an event kind")
        with caplog.at_level(logging.ERROR, "analysis_service.sql_store"):
            assert asyncio.run(store.fail_in_flight()) == []
        assert job in caplog.text
        with sqlite3.connect(database_of(env)) as connection:
            (status,) = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job,)
            ).fetchone()
        assert status == "queued"
        start(env)

    def test_the_log_names_the_field_and_never_the_value(self, env, caplog):
        store = build_store(env)
        record = completed_job(store)
        store_unlisted(database_of(env), record.id, "an issue code")
        with caplog.at_level(logging.WARNING, "analysis_service.sql_store"):
            asyncio.run(store.owned(record.id, "alice"))
        assert "validation_issues.0.code" in caplog.text
        assert "a-removed-code" not in caplog.text

    def test_the_log_names_the_field_whose_text_is_not_json(self, env, caplog):
        store = build_store(env)
        record = completed_job(store)
        store_unlisted(database_of(env), record.id, "event text that is not JSON")
        with caplog.at_level(logging.WARNING, "analysis_service.sql_store"):
            asyncio.run(store.owned(record.id, "alice"))
        assert "['events.0']" in caplog.text


#: The tokens that the run of :func:`metered_report` measured.
METERED = 150


def metered_report() -> Report:
    """A report whose one node run measured :data:`METERED` tokens."""
    usage = TokenUsage(prompt_tokens=METERED, total_tokens=METERED)
    return sample_report().model_copy(
        update={"nodes": [NodeRun(node="extract", duration_ms=10, usage=usage)]}
    )


class CountingRunner:
    """A runner that completes with :func:`metered_report`, and counts its runs."""

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, record, on_node):
        self.runs += 1
        return PipelineCompleted(metered_report())


class ReportPutFails:
    """The object store of ``env``, whose put of a report raises ``OSError``,
    or never returns where ``hang`` is set."""

    def __init__(self, env: dict[str, str], *, hang: bool = False) -> None:
        self.wrapped = build_object_store(env)
        self.hang = hang

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    async def put(self, key: str, data: bytes) -> None:
        if not key.endswith("/report.json"):
            await self.wrapped.put(key, data)
        elif self.hang:
            await asyncio.Event().wait()
        else:
            raise OSError("the disk is full")


def executed(store: JobStore, runner: CountingRunner) -> JobRecord:
    """The stored record of a new job after :func:`execute_job` ran it."""

    async def scenario() -> JobRecord | None:
        record = new_record()
        await admit(store, record)
        await execute_job(store, runner, record.id, deadline_seconds=5)
        return await store.get(record.id)

    stored = asyncio.run(scenario())
    assert stored is not None
    return stored


def statuses(record: JobRecord) -> list[str | None]:
    return [event.status for event in record.events if event.kind == "status"]


class TestAStoreFailureAsARunStartsOrEnds:
    """A store call that fails or hangs as a run starts or ends fails the job,
    with the charge the run measured, and never runs it again (#1816)."""

    def test_a_first_read_that_fails(self, env, monkeypatch, caplog):
        store = build_store(env)
        record = new_record()
        asyncio.run(admit(store, record))

        async def refuse(job_id):
            raise OSError("the database is gone")

        monkeypatch.setattr(store, "get", refuse)
        runner = CountingRunner()
        with caplog.at_level(logging.ERROR, "analysis_service.jobs"):
            asyncio.run(execute_job(store, runner, record.id, deadline_seconds=5))
        assert runner.runs == 0
        assert "until the restart scan fails it" in caplog.text

    def test_a_start_that_the_store_refuses(self, env, monkeypatch):
        store = build_store(env)
        write = SqlJobStore._write

        async def refuse_the_start(self, record, values):
            if record.status == "running":
                raise OSError("the database refused the write")
            await write(self, record, values)

        monkeypatch.setattr(SqlJobStore, "_write", refuse_the_start)
        runner = CountingRunner()
        stored = executed(store, runner)
        assert runner.runs == 0
        assert stored.status == "failed"
        assert "running" not in statuses(stored)

    def test_a_report_put_that_fails(self, env):
        store = SqlJobStore(engine_for(env), ReportPutFails(env))
        runner = CountingRunner()
        stored = executed(store, runner)
        assert runner.runs == 1
        assert stored.status == "failed"
        assert stored.error == GENERIC_FAILURE_MESSAGE
        assert stored.report is None
        assert stored.measured_tokens == METERED
        assert statuses(stored)[-2:] == ["running", "failed"]

    def test_a_row_write_that_fails_after_the_report_put(self, env, monkeypatch):
        store = build_store(env)
        write = SqlJobStore._write

        async def refuse_the_completion(self, record, values):
            if record.status == "completed":
                raise OSError("the database refused the write")
            await write(self, record, values)

        monkeypatch.setattr(SqlJobStore, "_write", refuse_the_completion)
        runner = CountingRunner()
        stored = executed(store, runner)
        assert runner.runs == 1
        assert stored.status == "failed"
        assert stored.measured_tokens == METERED
        assert statuses(stored)[-2:] == ["running", "failed"]
        later = datetime.now(UTC) + timedelta(days=2)
        assert asyncio.run(store.sweep_orphans(later)) == 1

    def test_a_report_put_that_hangs(self, env, monkeypatch):
        monkeypatch.setattr(jobs_module, "STORE_TIMEOUT_SECONDS", 0.05)
        store = SqlJobStore(engine_for(env), ReportPutFails(env, hang=True))
        stored = executed(store, CountingRunner())
        assert stored.status == "failed"
        assert stored.measured_tokens == METERED

    def test_a_database_that_refuses_each_end_leaves_the_restart_scan_a_job(
        self, env, monkeypatch, caplog
    ):
        store = build_store(env)
        write = SqlJobStore._write

        async def refuse_each_end(self, record, values):
            if record.status != "running":
                raise OSError("the database is gone")
            await write(self, record, values)

        monkeypatch.setattr(SqlJobStore, "_write", refuse_each_end)
        runner = CountingRunner()
        with caplog.at_level(logging.ERROR, "analysis_service.jobs"):
            stored = executed(store, runner)
        assert runner.runs == 1
        assert stored.status == "running"
        assert "until the restart scan fails it" in caplog.text
        monkeypatch.setattr(SqlJobStore, "_write", write)
        assert asyncio.run(store.fail_in_flight()) == [stored.id]


class TestTheObjectRoot:
    def test_each_object_is_owner_only(self, env):
        store = build_store(env)
        record = completed_job(store)
        path = report_file(env, record)
        modes = [
            os.stat(part).st_mode & 0o777
            for part in (path.parent.parent, path.parent, path)
        ]
        assert modes == [0o700, 0o700, 0o600]


def test_the_migration_creates_the_tables_the_store_reads(tmp_path):
    """Two readers of one schema: the Alembic tree and the store's tables."""
    path = tmp_path / "jobs.db"
    migrate(path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), METADATA)
    engine.dispose()
    assert differences == []
