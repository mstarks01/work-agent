"""The first-run app's job store: its settings, its startup and its bound (#1676).

The app reads the service's backend settings, and ``memory`` where
``ANALYSIS_JOB_STORE`` is unset. It runs the service's startup over its store
before it takes a request. A second app over the same ``sql`` store serves
what the first one kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analysis_service.answer_sets import NO_ANSWERS
from analysis_service.jobs import RESTART_FAILURE_MESSAGE, InMemoryJobStore, JobRecord
from analysis_service.sources import Source
from analysis_service.store_backends import build_store
from analysis_service.store_settings import StoreConfigError
from tests import test_webapp
from tests.factories import SEEDING_BUDGET, sample_selection
from tests.sql_stores import sql_env
from tests.test_api import completed_days_ago
from tests.test_webapp import (
    LOOPBACK,
    SAME_ORIGIN,
    posted,
    run_to_completion,
    startup_for,
    stub_settings,
)
from tests.test_webapp_questions import FACT, PausingRunner, ran, shown_list
from webapp.main import OPERATOR, Workspace, create_app

#: The shipped tier config, as the webapp tests build it: one fixture, used here.
tiers = test_webapp.tiers

#: The settings a test may set, cleared first so the developer's shell decides
#: nothing.
STORE_SETTINGS = (
    "ANALYSIS_JOB_STORE",
    "ANALYSIS_OBJECT_STORE",
    "ANALYSIS_DATABASE_URL",
    "ANALYSIS_OBJECT_ROOT",
    "ANALYSIS_RETENTION_DAYS",
)


@pytest.fixture(autouse=True)
def _no_store_setting(monkeypatch):
    for name in STORE_SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def durable(tmp_path: Path, monkeypatch) -> dict[str, str]:
    """A ``sql`` store on SQLite and a filesystem root, set as the app reads it."""
    env = sql_env(tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env


def app(tiers) -> TestClient:
    """A new app that builds its own store from the settings."""
    runner = PausingRunner(catalog=False)
    return TestClient(
        create_app(startup_for(tiers, runner, catalog=False)), base_url=LOOPBACK
    )


def submit(client, *, questions: bool) -> str:
    """Start a STRIDE run, whose stub report reads back from a ``sql`` store."""
    started = client.post(
        "/analyze",
        json=posted("A web app talks to a database.", ("stride",))
        | {"questions": questions},
        headers=SAME_ORIGIN,
    )
    assert started.status_code == 200, started.text
    return started.json()["run"]


def completed(client) -> str:
    run = submit(client, questions=False)
    assert ran(client, run) == "completed"
    return run


def payload(page: str, block: str) -> dict:
    """The JSON of the report page's ``<script id=block>``."""
    match = re.search(rf'id="{block}"[^>]*>(.*?)</script>', page, re.DOTALL)
    assert match is not None, block
    return json.loads(match.group(1))


def assert_store_is_named(tiers, caplog, restart: str) -> None:
    """The startup log and each of the page's three hints carry one note."""
    with caplog.at_level(logging.INFO, logger="webapp"):
        client = app(tiers)
    with client:
        page = client.get("/").text
    [note] = [line for line in caplog.messages if "restart" in line]
    assert restart in note
    assert page.count(note) == 3


class TestTheSettings:
    def test_with_no_store_setting_a_restart_loses_every_run(self, tiers):
        with app(tiers) as client:
            run_to_completion(client)
        with app(tiers) as client:
            assert client.get("/workspace/runs").json() == []

    def test_the_log_and_the_page_say_a_restart_clears_a_memory_store(
        self, tiers, caplog
    ):
        assert_store_is_named(tiers, caplog, "a restart clears them")

    def test_the_log_and_the_page_say_a_restart_keeps_a_sql_store(
        self, tiers, caplog, durable
    ):
        assert_store_is_named(tiers, caplog, "a restart keeps them")

    @pytest.mark.parametrize(
        ("setting", "value", "message"),
        [
            ("ANALYSIS_JOB_STORE", "redis", "unknown value 'redis'"),
            ("ANALYSIS_JOB_STORE", "", "set ANALYSIS_JOB_STORE"),
            ("ANALYSIS_OBJECT_STORE", "filesystem", "unset ANALYSIS_OBJECT_STORE"),
        ],
    )
    def test_an_unusable_store_setting_stops_the_startup(
        self, tiers, monkeypatch, setting, value, message
    ):
        monkeypatch.setenv(setting, value)
        with pytest.raises(StoreConfigError, match=message):
            app(tiers)

    def test_a_retention_value_that_is_not_a_period_stops_the_startup(
        self, tiers, monkeypatch
    ):
        monkeypatch.setenv("ANALYSIS_RETENTION_DAYS", "0")
        with (
            pytest.raises(StoreConfigError, match="ANALYSIS_RETENTION_DAYS"),
            app(tiers),
        ):
            pass

    def test_the_retention_sweep_starts_with_the_app(self, tiers, monkeypatch):
        monkeypatch.setenv("ANALYSIS_RETENTION_DAYS", "30")
        store = InMemoryJobStore()
        expired = completed_days_ago(store, 31)
        kept = completed_days_ago(store, 29)
        workspace = Workspace(stub_settings(), store=store)
        client = TestClient(
            create_app(startup_for(tiers), workspace), base_url=LOOPBACK
        )
        deadline = time.monotonic() + 5
        with client:
            while (
                client.portal.call(store.get, expired) is not None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert client.portal.call(store.get, expired) is None
            assert client.portal.call(store.get, kept) is not None


class TestARestart:
    """A second app over the same database and root serves what the first kept."""

    def test_the_runs_their_reports_and_the_saved_answers_live_through_it(
        self, tiers, durable
    ):
        with app(tiers) as client:
            paused = submit(client, questions=True)
            key = shown_list(client, paused)["early_questions"][0]["key"]
            saved = client.post(
                f"/answer/{paused}",
                json={
                    "links": [],
                    "facts": [{"key": key, "value": "unknown"}],
                    "save": True,
                    "revision": 0,
                },
                headers=SAME_ORIGIN,
            )
            assert saved.status_code == 200, saved.text
            drafted = completed(client)
            draft = client.post(
                f"/answer/{drafted}",
                json={"links": [], "facts": [dict(FACT)], "save": True, "revision": 0},
                headers=SAME_ORIGIN,
            )
            assert draft.json() == {"saved": True, "revision": 1}
            answered = completed(client)
            final = client.post(
                f"/answer/{answered}",
                json={"links": [], "facts": [dict(FACT)]},
                headers=SAME_ORIGIN,
            ).json()["run"]
            assert ran(client, final) == "completed"
            correction = {"facts": [{"key": list(FACT["key"]), "value": "unknown"}]}
            corrected = client.post(
                f"/correct/{final}", json=correction, headers=SAME_ORIGIN
            )
            assert corrected.status_code == 200, corrected.text
            before = client.get("/workspace/runs").json()

        with app(tiers) as client:
            after = client.get("/workspace/runs").json()
            reports = [
                client.get(report["url"]) for run in after for report in run["reports"]
            ]
            questions = client.get(f"/workspace/runs/{paused}").json()["questions"]
            stale = client.post(
                f"/answer/{paused}",
                json={"links": [], "facts": [], "save": True, "revision": 0},
                headers=SAME_ORIGIN,
            )
            draft_page = client.get(f"/report/{drafted}").text
            final_page = client.get(f"/report/{final}").text

        assert after == before
        assert {run["run"] for run in after} == {paused, drafted, final}
        assert len(reports) == 3
        assert all(report.status_code == 200 for report in reports)
        assert questions["revision"] == 1
        assert key in [answer["key"] for answer in questions["answered_early"]]
        assert stale.status_code == 409
        assert [
            fact["value"] for fact in payload(draft_page, "earlier")["draft"]["facts"]
        ] == [FACT["value"]]
        (row,) = payload(final_page, "corrections")["answers"]
        assert (row["answer"]["value"], row["corrected"]) == ("unknown", True)

    def test_a_queued_run_reads_as_failed_after_it(self, tiers, durable):
        with app(tiers) as client:
            completed(client)
        queued = queued_run(build_store(durable))
        with app(tiers) as client:
            body = client.get(f"/workspace/runs/{queued}").json()
        assert (body["status"], body["error"]) == ("failed", RESTART_FAILURE_MESSAGE)


class TestARecordOfAnotherSchemaVersion:
    """#1665: each read that parses a stored record asks ``same_schema`` first.

    Each test stores a part that the schema version covers in a shape this
    service cannot parse, under a version other than this service's. The list
    shows the run, and each page that parses the record answers 409.
    """

    def test_an_unparsable_resumption_is_never_parsed(self, tiers, durable):
        with app(tiers) as client:
            answered = completed(client)
            child = client.post(
                f"/answer/{answered}",
                json={"links": [], "facts": [dict(FACT)]},
                headers=SAME_ORIGIN,
            ).json()["run"]
            assert ran(client, child) == "completed"
        with sqlite3.connect(database_of(durable)) as connection:
            connection.execute(
                "UPDATE jobs SET schema_version = '0.1', resumption = '{\"x\": 1}'"
                " WHERE id = ?",
                (child,),
            )
        assert_listed_and_refused(tiers, child)

    def test_unparsable_sources_are_never_parsed(self, tiers, durable):
        with app(tiers) as client:
            run = completed(client)
        sources = b'[{"x": 1}]'
        with sqlite3.connect(database_of(durable)) as connection:
            (owner_id,) = connection.execute(
                "SELECT owner_id FROM jobs WHERE id = ?", (run,)
            ).fetchone()
            connection.execute(
                "UPDATE jobs SET schema_version = '0.1', sources_sha256 = ?"
                " WHERE id = ?",
                (hashlib.sha256(sources).hexdigest(), run),
            )
        root = Path(durable["ANALYSIS_OBJECT_ROOT"])
        (root / owner_id / run / "sources.json").write_bytes(sources)
        assert_listed_and_refused(tiers, run)


def assert_listed_and_refused(tiers, run: str) -> None:
    """The list shows ``run``, and its run page and report page answer 409."""
    with app(tiers) as client:
        listed = client.get("/workspace/runs")
        page = client.get(f"/workspace/runs/{run}")
        report = client.get(f"/report/{run}")
    assert listed.status_code == 200, listed.text
    assert run in {item["run"] for item in listed.json()}
    assert page.status_code == 409, page.text
    assert report.status_code == 409, report.text


def database_of(env: dict[str, str]) -> str:
    """The path of the SQLite database that ``env`` names."""
    return env["ANALYSIS_DATABASE_URL"].partition(":///")[2]


def queued_run(store) -> str:
    """The ID of a queued job of the app's operator, put in ``store``."""

    async def seed() -> str:
        principal = await store.principal(OPERATOR)
        assert principal is not None
        record = JobRecord.create(
            owner_id=principal.owner_id,
            sources=[Source.description("a web app storing orders")],
            frameworks=sample_selection(),
        )
        admission = await store.reserve(record, ceiling=1, budget=SEEDING_BUDGET)
        assert admission.outcome == "admitted", admission
        return record.id

    return asyncio.run(seed())


class ChangesTheOldestOnAdmission:
    """A store that saves a draft on the oldest job when it admits a new one,
    so the job changes after :meth:`Workspace.make_room` chose it."""

    def __init__(self, store: InMemoryJobStore) -> None:
        self._store = store
        self.oldest: str | None = None

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    async def reserve(self, record, **bounds):
        if self.oldest is not None:
            assert await self._store.save_draft(
                self.oldest, record.owner_id, NO_ANSWERS, 0
            )
        return await self._store.reserve(record, **bounds)


class TestTheBound:
    def test_a_job_that_changes_after_it_was_chosen_stays(self, tiers):
        store = ChangesTheOldestOnAdmission(InMemoryJobStore())
        workspace = Workspace(stub_settings(), store=store, max_jobs=2)
        client = TestClient(
            create_app(startup_for(tiers), workspace), base_url=LOOPBACK
        )
        with client:
            oldest, newest = run_to_completion(client), run_to_completion(client)
            store.oldest = oldest
            started = client.post("/analyze", json=posted("C"), headers=SAME_ORIGIN)
            assert started.status_code == 200, started.text
            listed = {run["run"] for run in client.get("/workspace/runs").json()}
        assert listed == {oldest, newest, started.json()["run"]}

    def test_each_delete_writes_the_line_with_the_cause_workspace(self, tiers, caplog):
        workspace = Workspace(stub_settings(), max_jobs=1)
        client = TestClient(
            create_app(startup_for(tiers), workspace), base_url=LOOPBACK
        )
        with client:
            oldest = run_to_completion(client)
            with caplog.at_level(
                logging.INFO, logger="analysis_service.store_backends"
            ):
                run_to_completion(client)
        [line] = caplog.messages
        assert re.fullmatch(
            f"deleted job {oldest} of owner .+: cause workspace,"
            r" \d+ object\(s\) removed",
            line,
        )
