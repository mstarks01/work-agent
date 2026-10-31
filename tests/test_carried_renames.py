"""The owner maps a carried answer's element to an element of the new model (ADR 0116)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from analysis_service.answer_bodies import read_answers
from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import Answers
from analysis_service.carried_renames import ElementRename, renamed
from analysis_service.fact_answers import FactAnswer
from evals.harness.early_rounds import paused_state
from tests.test_answer_shares import MODEL, STORE
from tests.test_api import auth, submission
from tests.test_links import catalog_client
from tests.test_pause import waiting

OLD_STORE = "store:orders-database"
OLD_FLOW = "flow:process:web-app>store:orders-database>store-order"
ENCRYPTED = FactAnswer(
    key=(OLD_STORE, "encryption_at_rest", "", "", "", ""), value="AES-256"
)
AUTHENTICATED = FactAnswer(
    key=(OLD_FLOW, "authentication", "", "", "", ""), value="mutual TLS"
)
TO_STORE = ElementRename(earlier=OLD_STORE, element="store:orders-db")


def _state(*facts: FactAnswer, renames: tuple[ElementRename, ...] = ()):
    state = paused_state(MODEL, {"stride": {}}, (), ())
    return replace(state, carried_in=AnswerSet(facts=facts), renames=renames)


def _saved(state, renames):
    return state.answer(
        Answers(save=True, revision=state.revision, renames=renames), limits=None
    )


class TestTheRename:
    def test_a_missing_element_is_listed_with_the_elements_of_its_type(self):
        listed = _state(ENCRYPTED).carried.dropped_json(MODEL)

        (dropped,) = listed["carried_dropped"]
        assert dropped["reason"] == "missing-element"
        (choice,) = listed["rename_choices"]
        assert choice["earlier"] == OLD_STORE
        assert {one["element"] for one in choice["choices"]} == {
            e.id for e in MODEL.data_stores
        }

    def test_a_rename_carries_the_answer_and_its_flows(self):
        """The renamed store's flow ID names the store, so the flow's answer
        moves with it, and nothing guesses from the similar name."""
        before = _state(ENCRYPTED, AUTHENTICATED)
        assert before.carried.taken.empty

        saved = _saved(before, (TO_STORE,))
        after = _state(ENCRYPTED, AUTHENTICATED, renames=saved.renames)

        assert {a.key[0] for a in after.carried.taken.facts} == {
            "store:orders-db",
            STORE,
        }
        assert after.carried.dropped_json(MODEL)["carried_renames"] == [
            {"earlier": OLD_STORE, "element": "store:orders-db", "name": "Orders DB"}
        ]

    def test_a_save_without_renames_keeps_the_ones_the_job_holds(self):
        state = _state(ENCRYPTED, renames=(TO_STORE,))
        asked = state.questions.early[1]
        given = AnswerSet(facts=(FactAnswer(key=asked.key, value="password"),))
        saved = state.answer(
            Answers(given=given, save=True, revision=0, presented=(asked.key,)),
            limits=None,
        )
        assert saved.renames == (TO_STORE,)

    @pytest.mark.parametrize(
        ("rename", "refusal"),
        [
            (ElementRename(earlier=OLD_STORE, element="process:web-app"), "two types"),
            (
                ElementRename(earlier=OLD_STORE, element="store:nowhere"),
                "not an element",
            ),
            (
                ElementRename(earlier="store:orders-db", element="store:orders-db"),
                "carried answer names",
            ),
        ],
    )
    def test_a_rename_the_job_cannot_hold_is_refused(self, rename, refusal):
        with pytest.raises(ValueError, match=refusal):
            _saved(_state(ENCRYPTED), (rename,))

    def test_an_element_is_renamed_once(self):
        twice = (TO_STORE, ElementRename(earlier=OLD_STORE, element="store:orders-db"))
        with pytest.raises(ValueError, match="renamed once"):
            _saved(_state(ENCRYPTED), twice)

    def test_only_a_save_to_a_waiting_job_sends_renames(self):
        state = _state(ENCRYPTED)
        with pytest.raises(ValueError, match="only a save"):
            state.answer(Answers(revision=0, renames=(TO_STORE,)), limits=None)

    def test_a_renamed_answer_never_lies_over_a_carried_one(self):
        own = FactAnswer(key=("store:orders-db", *ENCRYPTED.key[1:]), value="none")
        held = renamed(AnswerSet(facts=(ENCRYPTED, own)), (TO_STORE,))
        assert held.facts == (own,)


def test_the_body_reads_renames_and_left_out_keeps_them():
    body = {"save": True, "revision": 0}
    assert read_answers(body).renames is None
    sent = read_answers(body | {"renames": [TO_STORE.model_dump()]})
    assert sent.renames == (TO_STORE,)


class TestTheRoute:
    def _carrying(self, store):
        job = waiting(store)
        record = asyncio.run(store.get(job))
        asyncio.run(
            store.save(
                record.model_copy(update={"carried": AnswerSet(facts=(ENCRYPTED,))})
            )
        )
        return job

    def test_a_saved_rename_survives_the_store_and_reaches_the_next_job(self):
        client, store = catalog_client()
        job = self._carrying(store)

        saved = client.post(
            f"/v1/jobs/{job}/answers",
            json={"save": True, "revision": 0, "renames": [TO_STORE.model_dump()]},
            headers=auth(),
        )
        read = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()

        assert saved.status_code < 300, saved.text
        assert read["carried_renames"][0]["earlier"] == OLD_STORE
        assert read["carried_dropped"] == []
        held = asyncio.run(store.get(job)).held_answers()
        assert [a.key[0] for a in held.facts] == ["store:orders-db"]

    def test_another_owner_cannot_rename(self):
        client, store = catalog_client()
        job = self._carrying(store)
        refused = client.post(
            f"/v1/jobs/{job}/answers",
            json={"save": True, "revision": 0, "renames": [TO_STORE.model_dump()]},
            headers=auth("bob-token"),
        )
        assert refused.status_code == 404
        assert asyncio.run(store.get(job)).carried_renames == []

    def test_deleting_the_earlier_job_keeps_what_the_new_one_carried(self):
        client, store = catalog_client()
        earlier = waiting(store)
        record = asyncio.run(store.get(earlier))
        asyncio.run(
            store.save(
                record.model_copy(update={"answers": AnswerSet(facts=(ENCRYPTED,))})
            )
        )
        child = client.post(
            "/v1/jobs",
            json=submission(questions=True, answers_from=earlier),
            headers=auth(),
        ).json()["job_id"]
        assert client.delete(f"/v1/jobs/{earlier}", headers=auth()).status_code == 204

        assert asyncio.run(store.get(child)).carried.facts == (ENCRYPTED,)
