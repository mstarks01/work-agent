"""A Resumed Job whose selection the Deployment no longer carries (ADR 0093, #1774).

A live swap can remove a framework that a paused job selected. The resume is
then refused with a 409 that names the framework, in both drivers, and the
writes that start no job stay allowed.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from analysis_service.deployment import Deployment, FrameworkNotCarried
from analysis_service.errors import ConfigError
from analysis_service.fact_answers import FactAnswer
from analysis_service.framework_config import CARRIED_VAR
from analysis_service.jobs import InMemoryJobStore, JobRecord
from tests import test_webapp
from tests.factories import sample_selection
from tests.test_answer_admission import _known_answer
from tests.test_api import auth
from tests.test_corrections import ASKED, correct, finished
from tests.test_deployment import VERTEX_TIERS
from tests.test_job_answers import OWNER, Starts, operations, valid_model_sources
from tests.test_links import catalog_client
from tests.test_pause import waiting
from tests.test_questions import TestTheRoutes
from tests.test_webapp_questions import (
    SAME_ORIGIN,
    PausingRunner,
    client_for,
    shown_list,
    start,
)

tiers = test_webapp.tiers

#: A Deployment after a save that removed ``stride``.
NARROWED = Deployment.from_env(env=VERTEX_TIERS | {CARRIED_VAR: "asvs"})


def removed(selection, entry):
    """A runner lookup on ``NARROWED``: its selection check is the real one."""
    NARROWED.selection(selection)
    raise AssertionError("the selection names stride, which NARROWED refuses")


def test_a_framework_not_carried_is_its_own_refusal():
    with pytest.raises(FrameworkNotCarried, match="does not carry stride"):
        NARROWED.selection(("stride",))


@pytest.mark.parametrize("names", [(), ("asvs", "asvs")], ids=["empty", "repeated"])
def test_every_other_selection_refusal_stays_a_plain_config_error(names):
    with pytest.raises(ConfigError) as refused:
        NARROWED.selection(names)
    assert not isinstance(refused.value, FrameworkNotCarried)


def test_the_admission_passes_it_through_and_holds_no_slot():
    store = InMemoryJobStore()
    record = JobRecord.create(
        owner_id=OWNER,
        sources=list(valid_model_sources()),
        frameworks=sample_selection(),
    )
    starts = Starts()
    with pytest.raises(FrameworkNotCarried):
        asyncio.run(operations(store, runner_for=removed).admit(record, start=starts))
    assert asyncio.run(store.get(record.id)) is None
    assert starts.started == []


class TestV1:
    def test_a_resume_is_a_409_that_names_the_framework(self):
        client, store = catalog_client()
        waited = waiting(store)
        client.app.state.runner_for = removed

        refused = client.post(
            f"/v1/jobs/{waited}/answers",
            headers=auth(),
            json={"links": [], "revision": 0},
        )

        assert refused.status_code == 409, refused.text
        assert "does not carry stride" in refused.json()["detail"]

    def test_an_amendment_is_a_409_that_names_the_framework(self):
        client, store = catalog_client()
        waited = waiting(store)
        client.app.state.runner_for = removed

        refused = client.post(
            f"/v1/jobs/{waited}/amendments",
            headers=auth(),
            json={"text": "a CDN", "revision": 0},
        )

        assert refused.status_code == 409, refused.text
        assert "does not carry stride" in refused.json()["detail"]

    def test_a_save_on_the_paused_job_still_passes(self):
        client, store = catalog_client()
        waited = waiting(store)
        body = client.get(f"/v1/jobs/{waited}/questions", headers=auth()).json()
        client.app.state.runner_for = removed

        saved = client.post(
            f"/v1/jobs/{waited}/answers",
            headers=auth(),
            json={
                "facts": [_known_answer(body["early_questions"][0])],
                "save": True,
                "revision": 0,
            },
        )

        assert saved.status_code == 200, saved.text

    def test_a_draft_on_a_report_still_passes(self):
        client, store = catalog_client()
        done = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{done}/questions", headers=auth()).json()
        client.app.state.runner_for = removed

        saved = client.post(
            f"/v1/jobs/{done}/answers",
            headers=auth(),
            json={
                "facts": [_known_answer(body["fact_questions"][0])],
                "save": True,
                "revision": 0,
            },
        )

        assert saved.status_code == 200, saved.text

    def test_a_correction_still_passes(self):
        client, store = catalog_client()
        job = finished(store)
        client.app.state.runner_for = removed

        corrected = correct(client, job, FactAnswer(key=ASKED.key, value="unknown"))

        assert corrected.status_code == 200, corrected.text


class TestTheFirstRunApp:
    def paused_then_narrowed(self, tiers):
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        shown = shown_list(client, paused)
        cell = client.app.state.cell
        cell.current = replace(
            cell.current, answers=replace(cell.current.answers, runner_for=removed)
        )
        return client, paused, shown

    def test_a_resume_is_a_409_that_names_the_framework(self, tiers):
        client, paused, _ = self.paused_then_narrowed(tiers)

        refused = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "revision": 0},
            headers=SAME_ORIGIN,
        )

        assert refused.status_code == 409, refused.text
        assert "does not carry stride" in refused.json()["message"]

    def test_a_save_on_the_paused_job_still_passes(self, tiers):
        client, paused, shown = self.paused_then_narrowed(tiers)
        key = shown["early_questions"][0]["key"]

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
