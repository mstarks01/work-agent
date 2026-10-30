"""Session workspace boundaries, using only the offline engine fixture."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from analysis_service.sources import MAX_SYSTEM_NAME_CHARS
from tests import test_webapp_questions as web
from tests.test_webapp import BREAKOUT, LOOPBACK, SAME_ORIGIN, posted
from webapp.main import DEFAULT_NAME

tiers = web.tiers


@pytest.mark.parametrize(
    "name",
    [
        "x" * (MAX_SYSTEM_NAME_CHARS + 1),
        "Orders\nforged line",
        "Orders\u202egnirdrO",
        [],
        42,
    ],
)
def test_invalid_names_do_not_claim_a_run(tiers, name):
    """A name the shared rule refuses, or one that is not text, starts nothing.

    A blank or absent name is not here: the rule reads it as no name, and
    ``tests/test_system_name_readers.py`` holds that against the other doors.
    """
    with web.client_for(tiers, web.PausingRunner()) as client:
        response = client.post(
            "/analyze", json=posted("System") | {"name": name}, headers=SAME_ORIGIN
        )
        assert response.status_code == 400
        assert client.get("/workspace/runs").json() == []


def test_reopen_keeps_questions_revision_and_related_report(tiers):
    with web.client_for(tiers, web.PausingRunner()) as client:
        response = client.post(
            "/analyze",
            json=posted("Private system description")
            | {"name": BREAKOUT, "questions": True},
            headers=SAME_ORIGIN,
        )
        run = response.json()["run"]
        paused = web.shown_list(client, run)
        listing = client.get("/workspace/runs")
        assert "no-store" in listing.headers["cache-control"]
        assert "Private system description" not in listing.text
        assert listing.json()[0]["name"] == BREAKOUT
        assert listing.json()[0]["created_at"]
        detail = client.get(f"/workspace/runs/{run}").json()
        assert detail["questions"] == paused
        assert detail["description"] == "Private system description"
        saved = client.post(
            f"/answer/{run}",
            json={"save": True, "links": [web.LINK], "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 200
        assert client.get(f"/workspace/runs/{run}").json()["questions"]["revision"] == 1
        resumed = client.post(
            f"/answer/{run}", json={"links": [], "revision": 1}, headers=SAME_ORIGIN
        )
        child = resumed.json()["run"]
        client.get(f"/events/{child}")
        rows = client.get("/workspace/runs").json()
        assert len(rows) == 1
        assert rows[0]["name"] == BREAKOUT
        report = rows[0]["reports"][0]
        assert set(report) == {"run", "name", "generated_at"}
        assert report["run"] == child
        assert report["name"] == BREAKOUT
        assert report["generated_at"]
        assert client.get(f"/workspace/runs/{run}").json()["run"] == child


def test_workspace_missing_run_and_cross_origin_write(tiers):
    with web.client_for(tiers, web.PausingRunner()) as client:
        assert client.get("/workspace/runs/missing").status_code == 404
        assert (
            client.post(
                "/analyze", json=posted("System") | {"name": "Name"}
            ).status_code
            == 403
        )
        assert client.get("/workspace/runs").json() == []


def test_completed_processing_steps_survive_snapshot_reads(tiers):
    from analysis_service import StubPipelineRunner
    from tests.test_webapp import run_to_completion

    with web.client_for(tiers, StubPipelineRunner()) as client:
        run = run_to_completion(client)
        detail = client.get(f"/workspace/runs/{run}").json()
        assert detail["completed_steps"] == list(StubPipelineRunner.nodes)
        again = client.get(f"/workspace/runs/{run}").json()
        assert again["completed_steps"] == detail["completed_steps"], (
            "Snapshot reads must not consume progress"
        )


TITLE = "My orders board"


def _workspace_client(tiers, runner=None):
    from webapp.main import Workspace

    workspace = Workspace()
    runner = web.PausingRunner() if runner is None else runner
    client = TestClient(
        web.app_for(tiers, runner, workspace=workspace), base_url=LOOPBACK
    )
    return client, workspace


def _paused_with_child(client) -> tuple[str, str]:
    """A paused run, and the Resumed Job its continue started, run to its end."""
    paused = web._paused(client)
    resumed = client.post(
        f"/answer/{paused}", json={"links": [], "revision": 0}, headers=SAME_ORIGIN
    )
    assert resumed.status_code == 200, resumed.text
    child = resumed.json()["run"]
    assert web.ran(client, child) == "completed"
    return paused, child


def _rename(client, run: str, title, headers=SAME_ORIGIN):
    return client.put(
        f"/workspace/runs/{run}/title", json={"title": title}, headers=headers
    )


class TestTheDelete:
    def test_it_removes_every_job_of_the_analysis(self, tiers, caplog):
        client, _ = _workspace_client(tiers)
        with client:
            paused, child = _paused_with_child(client)
            other = web._completed(client)
            with caplog.at_level(
                logging.INFO, logger="analysis_service.store_backends"
            ):
                deleted = client.delete(
                    f"/workspace/runs/{paused}", headers=SAME_ORIGIN
                )
            assert deleted.status_code == 204, deleted.text
            assert [run["run"] for run in client.get("/workspace/runs").json()] == [
                other
            ]
            assert client.get(f"/workspace/runs/{child}").status_code == 404
            assert client.get(f"/report/{child}").status_code == 404
        assert [line.split()[2] for line in caplog.messages] == [child, paused]
        assert all("cause owner" in line for line in caplog.messages)

    def test_it_keeps_each_jobs_usage_row(self, tiers):
        client, workspace = _workspace_client(tiers)
        with client:
            run = web._completed(client)
            assert (
                client.delete(f"/workspace/runs/{run}", headers=SAME_ORIGIN).status_code
                == 204
            )
        assert run in workspace.store._usage

    def test_it_deletes_a_paused_analysis(self, tiers):
        client, _ = _workspace_client(tiers)
        with client:
            paused = web._paused(client)
            assert (
                client.delete(
                    f"/workspace/runs/{paused}", headers=SAME_ORIGIN
                ).status_code
                == 204
            )
            assert client.get("/workspace/runs").json() == []

    def test_it_refuses_an_analysis_that_runs_and_deletes_nothing(self, tiers):
        from tests.test_webapp_store import queued_run

        client, workspace = _workspace_client(tiers)
        with client:
            web._completed(client)
            queued = queued_run(workspace.store)
            refused = client.delete(f"/workspace/runs/{queued}", headers=SAME_ORIGIN)
            assert refused.status_code == 409, refused.text
            assert queued in {
                run["run"] for run in client.get("/workspace/runs").json()
            }

    def test_a_missing_run_and_a_cross_origin_delete_change_nothing(self, tiers):
        client, _ = _workspace_client(tiers)
        with client:
            run = web._completed(client)
            assert (
                client.delete(
                    "/workspace/runs/job-missing", headers=SAME_ORIGIN
                ).status_code
                == 404
            )
            assert client.delete(f"/workspace/runs/{run}").status_code == 403
            assert (
                client.delete(f"/workspace/runs/{run}", headers=CROSS_SITE).status_code
                == 403
            )
            assert [item["run"] for item in client.get("/workspace/runs").json()] == [
                run
            ]


CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}


class TestTheTitle:
    def test_it_names_the_analysis_and_a_blank_title_removes_it(self, tiers):
        client, workspace = _workspace_client(tiers)
        with client:
            paused, child = _paused_with_child(client)
            renamed = _rename(client, child, f"  {TITLE}  ")
            assert renamed.status_code == 200, renamed.text
            assert renamed.json() == {"title": TITLE}
            [row] = client.get("/workspace/runs").json()
            assert (row["name"], row["title"]) == (TITLE, TITLE)
            assert {report["name"] for report in row["reports"]} == {TITLE}
            assert client.get(f"/workspace/runs/{child}").json()["name"] == TITLE
            for job in (paused, child):
                assert asyncio.run(workspace.store.get(job)).title == TITLE
            assert _rename(client, paused, "   ").json() == {"title": None}
            [row] = client.get("/workspace/runs").json()
            assert (row["name"], row["title"]) == (DEFAULT_NAME, None)

    def test_it_moves_no_revision(self, tiers):
        client, workspace = _workspace_client(tiers)
        with client:
            paused = web._paused(client)
            before = asyncio.run(workspace.store.get(paused))
            assert _rename(client, paused, TITLE).status_code == 200
            after = asyncio.run(workspace.store.get(paused))
            assert (after.revision, after.updated_at) == (
                before.revision,
                before.updated_at,
            )
            saved = client.post(
                f"/answer/{paused}",
                json={"save": True, "links": [web.LINK], "revision": before.revision},
                headers=SAME_ORIGIN,
            )
            assert saved.status_code == 200, saved.text

    def test_a_resumed_job_and_an_amendment_copy_it(self, tiers):
        client, workspace = _workspace_client(tiers, web.PausingRunner(catalog=False))
        with client:
            paused = web._paused(client)
            assert _rename(client, paused, TITLE).status_code == 200
            amended = client.post(
                f"/amend/{paused}",
                json={"text": web.AMENDMENT, "revision": 0},
                headers=SAME_ORIGIN,
            )
            amendment = amended.json()["run"]
            assert web.ran(client, amendment) == "awaiting-answers"
            resumed = client.post(
                f"/answer/{amendment}",
                json={"links": [], "revision": 0},
                headers=SAME_ORIGIN,
            )
            child = resumed.json()["run"]
            web.ran(client, child)
            for job in (amendment, child):
                assert asyncio.run(workspace.store.get(job)).title == TITLE

    def test_it_stays_when_the_root_job_is_deleted(self, tiers):
        client, workspace = _workspace_client(tiers)
        with client:
            paused, child = _paused_with_child(client)
            assert _rename(client, child, TITLE).status_code == 200
            owner = asyncio.run(workspace.store.get(paused)).owner_id
            assert asyncio.run(workspace.store.delete(paused, owner)) is not None
            [row] = client.get("/workspace/runs").json()
            assert row["name"] == TITLE

    def test_the_report_keeps_its_system_name(self, tiers):
        client, _ = _workspace_client(tiers)
        with client:
            started = client.post(
                "/analyze",
                json=posted("A web app.") | {"name": "Orders API"},
                headers=SAME_ORIGIN,
            )
            run = started.json()["run"]
            web.ran(client, run)
            assert _rename(client, run, TITLE).status_code == 200
            page = client.get(f"/report/{run}").text
        assert "Orders API" in page
        assert TITLE not in page

    @pytest.mark.parametrize(
        "body", [{"title": "x" * (MAX_SYSTEM_NAME_CHARS + 1)}, {"title": 42}, {}, []]
    )
    def test_a_refused_title_keeps_the_old_one(self, tiers, body):
        client, _ = _workspace_client(tiers)
        with client:
            run = web._completed(client)
            assert _rename(client, run, TITLE).status_code == 200
            refused = client.put(
                f"/workspace/runs/{run}/title", json=body, headers=SAME_ORIGIN
            )
            assert refused.status_code == 400, refused.text
            assert client.get("/workspace/runs").json()[0]["title"] == TITLE

    def test_a_missing_run_and_a_cross_origin_rename_change_nothing(self, tiers):
        client, _ = _workspace_client(tiers)
        with client:
            run = web._completed(client)
            assert _rename(client, "job-missing", TITLE).status_code == 404
            assert _rename(client, run, TITLE, headers={}).status_code == 403
            assert _rename(client, run, TITLE, headers=CROSS_SITE).status_code == 403
            assert client.get("/workspace/runs").json()[0]["title"] is None


class AdmitsAChildOnDelete:
    """A store that admits a follow-up of a job when the route deletes that
    job, as an answer in another tab would between the route's check and its
    delete."""

    def __init__(self, store) -> None:
        self._store = store
        self.child: str | None = None

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    async def delete(self, job_id, owner_id, **cutoff):
        from tests.factories import SEEDING_BUDGET
        from tests.test_job_store import follow_up

        if self.child is None:
            child = follow_up(job_id).model_copy(update={"owner_id": owner_id})
            admission = await self._store.reserve(
                child, ceiling=100, budget=SEEDING_BUDGET, principal=None
            )
            assert admission.outcome == "admitted", admission
            self.child = child.id
        return await self._store.delete(job_id, owner_id, **cutoff)


def test_a_child_started_during_the_delete_keeps_the_analysis(tiers):
    """The store keeps a parent whose child is in flight (ADR 0107), so the
    route answers 409 and the analysis stays whole."""
    from analysis_service.jobs import InMemoryJobStore
    from webapp.main import Workspace

    store = AdmitsAChildOnDelete(InMemoryJobStore())
    client = TestClient(
        web.app_for(tiers, web.PausingRunner(), workspace=Workspace(store=store)),
        base_url=LOOPBACK,
    )
    with client:
        run = web._completed(client)
        refused = client.delete(f"/workspace/runs/{run}", headers=SAME_ORIGIN)
        assert refused.status_code == 409, refused.text
        [row] = client.get("/workspace/runs").json()
        assert row["run"] == store.child
        assert asyncio.run(store.get(run)) is not None
