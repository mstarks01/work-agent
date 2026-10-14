"""The ``sql`` store on SQLite with a filesystem root, at the HTTP surface and below.

``tests/test_job_store.py`` runs the whole store contract on this store too.
This module holds what the contract cannot say: each startup refusal and the
startup order, a job that lives through a restart of the app, the JSON round
trip of every record field, the digest check, the write-once report, and the
migration against the tables the store reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from analysis_service.answer_sets import AnswerSet
from analysis_service.api import create_app
from analysis_service.certification import CertifyResult
from analysis_service.fact_answers import FactAnswer
from analysis_service.jobs import (
    RESTART_FAILURE_MESSAGE,
    Checkpoint,
    JobRecord,
    JobStore,
    JobStoreConfigError,
    Resumption,
    build_store,
)
from analysis_service.report import NodeRun
from analysis_service.sources import Source
from analysis_service.sql_store import METADATA, CorruptObject
from analysis_service.validation import ValidationIssue
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    SEEDING_BUDGET,
    admit,
    sample_report,
    sample_selection,
    valid_model,
)
from tests.sql_stores import migrate, sql_env, sqlite_url
from tests.test_api import (
    TEST_DEADLINE_SECONDS,
    TEST_LIMITS,
    TEST_MAX_ACTIVE_JOBS,
    FakeVerifier,
    auth,
    submission,
)
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
            ({"ANALYSIS_JOB_STORE": "redis"}, "unknown value 'redis'"),
            ({"ANALYSIS_OBJECT_STORE": None}, "set ANALYSIS_OBJECT_STORE"),
            ({"ANALYSIS_OBJECT_STORE": "tape"}, "unknown value 'tape'"),
            ({"ANALYSIS_JOB_STORE": "memory"}, "unset ANALYSIS_OBJECT_STORE"),
            ({"ANALYSIS_DATABASE_URL": None}, "set ANALYSIS_DATABASE_URL"),
            ({"ANALYSIS_DATABASE_URL": "not a url"}, "is not a database URL"),
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
                "unknown value 'postgresql\\+psycopg2'",
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
        with pytest.raises(JobStoreConfigError, match=message) as refused:
            start(env)
        assert "hunter2" not in str(refused.value)

    def test_a_schema_that_is_not_at_the_latest_revision(self, env, tmp_path):
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(empty_database(tmp_path))
        with pytest.raises(JobStoreConfigError, match="revision None"):
            start(env)

    def test_a_missing_database_file(self, env, tmp_path):
        """The service never makes the file: only the migration step does."""
        missing = tmp_path / "missing.db"
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(missing)
        with pytest.raises(JobStoreConfigError, match="does not exist"):
            start(env)
        assert not missing.exists()

    def test_a_database_file_that_others_can_open(self, env):
        database = env["ANALYSIS_DATABASE_URL"].removeprefix("sqlite+aiosqlite:///")
        os.chmod(database, 0o640)
        with pytest.raises(JobStoreConfigError, match="set its mode to 0600"):
            start(env)

    def test_a_database_file_that_is_a_symbolic_link(self, env, tmp_path):
        database = env["ANALYSIS_DATABASE_URL"].removeprefix("sqlite+aiosqlite:///")
        link = tmp_path / "link.db"
        link.symlink_to(database)
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(link)
        with pytest.raises(JobStoreConfigError, match="not a regular file"):
            start(env)

    def test_a_relative_database_path(self, env):
        env["ANALYSIS_DATABASE_URL"] = "sqlite+aiosqlite:///jobs.db"
        with pytest.raises(JobStoreConfigError, match="absolute path"):
            start(env)

    def test_a_missing_root(self, env):
        os.rmdir(env["ANALYSIS_OBJECT_ROOT"])
        with pytest.raises(JobStoreConfigError, match="does not exist"):
            start(env)

    def test_a_root_that_is_a_file(self, env, tmp_path):
        env["ANALYSIS_OBJECT_ROOT"] = str(tmp_path / "jobs.db")
        with pytest.raises(JobStoreConfigError, match="not a directory"):
            start(env)

    @pytest.mark.parametrize("mode", [0o750, 0o705, 0o720])
    def test_a_root_a_group_or_others_can_open(self, env, mode):
        os.chmod(env["ANALYSIS_OBJECT_ROOT"], mode)
        with pytest.raises(JobStoreConfigError, match="a group or others"):
            start(env)

    def test_a_root_another_user_owns(self, env, monkeypatch):
        monkeypatch.setattr(
            "analysis_service.objects.os.geteuid", lambda: os.getuid() + 1
        )
        with pytest.raises(JobStoreConfigError, match="another user owns"):
            start(env)

    def test_a_usable_store_starts(self, env):
        start(env)


class TestTheStartupOrder:
    """Settings, the schema revision, the object store, the restart scan, requests."""

    def test_the_schema_is_checked_before_the_object_store(self, env, tmp_path):
        env["ANALYSIS_DATABASE_URL"] = sqlite_url(empty_database(tmp_path))
        os.rmdir(env["ANALYSIS_OBJECT_ROOT"])
        with pytest.raises(JobStoreConfigError, match="revision"):
            start(env)

    def test_a_failed_check_stops_the_startup_before_the_restart_scan(self, env):
        store = build_store(env)
        job = queued(store)
        os.chmod(env["ANALYSIS_OBJECT_ROOT"], 0o750)
        with (
            pytest.raises(JobStoreConfigError),
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
    record.round_revision = 2
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
            asyncio.run(store.owned(record.id, "alice"))

    def test_the_api_answers_500_with_no_detail(self, env):
        store = build_store(env)
        record = completed_job(store)
        report_file(env, record).write_bytes(b"{}")
        app = app_over(store)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/v1/jobs/{record.id}/report", headers=auth())
        assert response.status_code == 500
        assert response.json()["detail"] == "an internal error occurred"

    def test_a_foreign_read_never_reaches_the_object(self, env):
        """The owner is checked on the row first, so a foreign read of a job
        whose object is gone is the same ``None`` as a missing job."""
        store = build_store(env)
        record = completed_job(store)
        report_file(env, record).unlink()
        assert asyncio.run(store.report_json(record.id, "mallory")) is None
        assert asyncio.run(store.owned(record.id, "mallory")) is None


class TestTheReport:
    def test_the_service_writes_a_report_once(self, env):
        store = build_store(env)
        record = completed_job(store)
        record.report = sample_report().model_copy(update={"disclaimer": "another"})
        with pytest.raises(ValueError, match="written once"):
            asyncio.run(store.save(record))

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

    def test_a_delete_removes_both_objects(self, env):
        store = build_store(env)
        record = completed_job(store)
        assert asyncio.run(store.delete(record.id, "alice")) == 2
        assert not report_file(env, record).parent.exists()


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
