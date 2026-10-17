"""The first-run app holds what its routes run on in one cell (ADR 0093, #1768).

The app builds its store and its workspace before the Deployment and without
it, and the store's startup runs either way. Each route reads the cell one
time, at its start. A job keeps the runner and the deadline that it got at
admission, and the report route judges each report with the gate of the cell
that holds now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from analysis_service import ConfigError
from analysis_service.certification import CertifyResult
from analysis_service.jobs import (
    RESTART_FAILURE_MESSAGE,
    InMemoryJobStore,
    JobRecord,
    JobStore,
    NodeCallback,
    PipelineOutcome,
    StubPipelineRunner,
)
from analysis_service.principals import Principal
from analysis_service.sources import Source
from analysis_service.store_backends import store_lifespan
from analysis_service.store_settings import StoreConfigError
from tests import test_webapp
from tests.factories import SEEDING_BUDGET, sample_selection
from tests.test_runtime_certification import FP_A, CertifyingRunner, gate
from tests.test_webapp import (
    LOOPBACK,
    SAME_ORIGIN,
    posted,
    run_to_completion,
    startup_for,
    stub_settings,
)
from webapp.main import OPERATOR, Current, Startup, Workspace, create_app

tiers = test_webapp.tiers


class Recording(StubPipelineRunner):
    """A stub runner that names each job it runs, after ``delay`` seconds."""

    def __init__(self, delay: float = 0.0) -> None:
        self.jobs: list[str] = []
        self.delay = delay

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        self.jobs.append(job.id)
        await asyncio.sleep(self.delay)
        return await super().run(job, on_node)


class SwapsOnPrincipal(InMemoryJobStore):
    """A store that calls ``swap`` at its next principal read.

    ``POST /analyze`` reads the principal after it read the cell and before
    it admits the job, so a swap here lands between two ``await`` points of
    one request.
    """

    swap: Callable[[], None] | None = None

    async def principal(self, identity):
        if self.swap is not None:
            swap, self.swap = self.swap, None
            swap()
        return await super().principal(identity)


def current_over(store: JobStore, tiers, runner, **settings) -> Current:
    """What the routes run on over ``runner``, with ``settings`` changed."""
    startup = replace(
        startup_for(tiers), settings=replace(stub_settings(runner), **settings)
    )
    current = startup.current(store)
    assert current is not None
    return current


def broken(tiers) -> Startup:
    """The startup of a Deployment that did not build."""
    return Startup(
        settings=None, frameworks=(), tiers=tiers, error=ConfigError("no model")
    )


def queued_job(store: JobStore) -> str:
    """The ID of a queued job of the operator, put in ``store``."""
    principal = Principal.new(OPERATOR)
    record = JobRecord.create(
        owner_id=principal.owner_id,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
    )

    async def seed() -> None:
        admission = await store.reserve(
            record, ceiling=1, budget=SEEDING_BUDGET, principal=principal
        )
        assert admission.outcome == "admitted", admission

    asyncio.run(seed())
    return record.id


class TestTheStoreStartsWithoutTheDeployment:
    def test_the_restart_scan_runs_when_the_deployment_does_not_build(self, tiers):
        store = InMemoryJobStore()
        queued = queued_job(store)

        with TestClient(
            create_app(broken(tiers), Workspace(store=store)), base_url=LOOPBACK
        ) as client:
            page = client.get("/")

        assert page.status_code == 503
        record = asyncio.run(store.get(queued))
        assert record is not None
        assert (record.status, record.error) == ("failed", RESTART_FAILURE_MESSAGE)

    def test_an_unusable_store_setting_stops_it(self, tiers, monkeypatch):
        monkeypatch.setenv("ANALYSIS_JOB_STORE", "redis")
        with pytest.raises(StoreConfigError, match="unknown value for"):
            create_app(broken(tiers))

    def test_an_unusable_retention_setting_stops_it(self, tiers, monkeypatch):
        monkeypatch.setenv("ANALYSIS_RETENTION_DAYS", "0")
        with (
            pytest.raises(StoreConfigError, match="ANALYSIS_RETENTION_DAYS"),
            TestClient(create_app(broken(tiers)), base_url=LOOPBACK),
        ):
            pass


def test_the_store_startup_reads_retention_from_the_mapping_it_gets(monkeypatch):
    """The process environment holds a value that stops a startup, and the
    mapping does not: only the mapping counts."""
    monkeypatch.setenv("ANALYSIS_RETENTION_DAYS", "0")

    async def start(env: dict[str, str]) -> None:
        async with store_lifespan(InMemoryJobStore(), env):
            pass

    asyncio.run(start({}))
    with pytest.raises(StoreConfigError, match="ANALYSIS_RETENTION_DAYS"):
        asyncio.run(start({"ANALYSIS_RETENTION_DAYS": "0"}))


def test_a_request_keeps_the_cell_it_read_first(tiers):
    """A swap between two ``await`` points of a submission does not reach
    it, and the next submission runs on the new cell."""
    store = SwapsOnPrincipal()
    first, second = Recording(), Recording()
    app = create_app(startup_for(tiers, first), Workspace(store=store))

    def swap() -> None:
        app.state.cell.current = current_over(store, tiers, second)

    store.swap = swap
    with TestClient(app, base_url=LOOPBACK) as client:
        before = run_to_completion(client)
        after = run_to_completion(client)

    assert (first.jobs, second.jobs) == ([before], [after])


def test_a_job_in_flight_keeps_its_runner_and_its_deadline(tiers):
    """The swap gives a deadline that the job has already passed, and the
    job still completes on the runner that admitted it."""
    store = InMemoryJobStore()
    slow, other = Recording(delay=0.3), Recording()
    app = create_app(startup_for(tiers, slow), Workspace(store=store))
    with TestClient(app, base_url=LOOPBACK) as client:
        started = client.post("/analyze", json=posted("A"), headers=SAME_ORIGIN)
        assert started.status_code == 200, started.text
        app.state.cell.current = current_over(
            store, tiers, other, deadline_seconds=0.01
        )
        run = started.json()["run"]
        events = client.get(f"/events/{run}").text

    assert '"status":"completed"' in events, events
    assert (slow.jobs, other.jobs) == ([run], [])


def test_the_report_route_uses_the_gate_of_the_current_cell(tiers):
    store = InMemoryJobStore()
    uncertified = CertifyResult(
        certified=False, uncertified=({"node": "critic", "fingerprint": FP_A},)
    )
    runner = CertifyingRunner(uncertified)
    app = create_app(startup_for(tiers, runner), Workspace(store=store))
    with TestClient(app, base_url=LOOPBACK) as client:
        run = run_to_completion(client)
        served = client.get(f"/report/{run}")
        app.state.cell.current = current_over(
            store, tiers, runner, gate=gate(require_certified=True)
        )
        withheld = client.get(f"/report/{run}")

    assert (served.status_code, withheld.status_code) == (200, 404)
