"""A new analysis carries the answers of an earlier job (ADR 0079).

An owner who analyses a system again answered the same questions again. A
submission now names one earlier job of its owner, and the new job carries
every answer that job holds. Its own questions take each answer they still
ask, as an amended job's questions do (ADR 0072).
"""

from __future__ import annotations

import asyncio

from analysis_service.answer_sets import AnswerSet
from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import MAX_HELD_FACTS, FactAnswer
from analysis_service.jobs import JobRecord
from analysis_service.links import LinkAnswer
from analysis_service.sources import Source
from tests.factories import DESCRIPTION_TEXT, sample_selection
from tests.test_api import auth, submission
from tests.test_links import catalog_client
from tests.test_pause import held, waiting

STORE = "store:orders-db"
GONE = FactAnswer(
    key=UnknownRef(element_id="store:gone", attribute="encryption_at_rest").key,
    value="AES",
)


def _fact(value: str, attribute: str = "encryption_at_rest") -> FactAnswer:
    return FactAnswer(key=(STORE, attribute, "", "", "", ""), value=value)


def _holding(store, **held_answers) -> str:
    """A waiting job of alice's that holds ``held_answers``."""
    job = waiting(store)
    record = asyncio.run(store.get(job))
    asyncio.run(store.save(record.model_copy(update=held_answers)))
    return job


def _submit(client, answers_from, *, questions=True, token="alice-token"):
    return client.post(
        "/v1/jobs",
        json=submission(questions=questions, answers_from=answers_from),
        headers=auth(token),
    )


class TestWhatAJobHolds:
    def test_a_later_answer_lies_over_an_earlier_one(self):
        record = JobRecord.create(
            owner_id="alice",
            sources=[Source.description(DESCRIPTION_TEXT)],
            frameworks=sample_selection(),
            carried=AnswerSet(facts=(_fact("carried"), _fact("kept", "protocol"))),
            answers=AnswerSet(facts=(_fact("answered"),)),
        )
        record.draft = AnswerSet(facts=(_fact("drafted"),))
        record.corrections = [_fact("corrected")]

        held_facts = {fact.key[1]: fact.value for fact in record.held_answers().facts}

        assert held_facts == {"encryption_at_rest": "corrected", "protocol": "kept"}

    def test_it_holds_the_link_answers_too(self):
        link = LinkAnswer(principal="shoppers", element="entity:shopper")
        record = JobRecord.create(
            owner_id="alice",
            sources=[Source.description(DESCRIPTION_TEXT)],
            frameworks=sample_selection(),
            answers=AnswerSet(links=(link,)),
        )
        assert record.held_answers().links == (link,)


class TestTheRoute:
    def test_a_new_job_carries_the_earlier_job_s_answers(self):
        client, store = catalog_client()
        saved = _fact("AES")
        earlier = _holding(store, answers=AnswerSet(facts=(saved,)))

        response = _submit(client, earlier)

        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.carried.facts == (saved,) and child.answers.empty
        assert child.amends is None, "it locks nothing"
        assert asyncio.run(store.resumed_by(earlier, "alice")) is None

    def test_another_owner_s_job_reads_as_missing(self):
        client, store = catalog_client()
        earlier = _holding(store, answers=AnswerSet(facts=(_fact("AES"),)))
        foreign = _submit(client, earlier, token="bob-token")
        missing = _submit(client, "job-missing")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["detail"] == missing.json()["detail"].replace(
            "job-missing", earlier
        )

    def test_a_job_that_does_not_pause_is_refused_what_it_would_never_read(self):
        client, store = catalog_client()
        earlier = _holding(store, answers=AnswerSet(facts=(_fact("AES"),)))
        response = _submit(client, earlier, questions=False)
        assert response.status_code == 400
        assert "turn the questions on" in response.json()["detail"]

    def test_a_job_that_holds_no_answer_is_refused(self):
        client, store = catalog_client()
        response = _submit(client, waiting(store))
        assert response.status_code == 400
        assert "holds no answers" in response.json()["detail"]

    def test_a_set_at_the_held_limit_carries_and_one_over_it_is_refused(self):
        """Each set a job holds is bounded; their union can pass the bound."""
        client, store = catalog_client()

        def facts(start, count):
            return tuple(
                FactAnswer(key=(f"store:s{i}", "protocol", "", "", "", ""), value="x")
                for i in range(start, start + count)
            )

        at = _holding(store, answers=AnswerSet(facts=facts(0, MAX_HELD_FACTS)))
        over = _holding(
            store,
            answers=AnswerSet(facts=facts(0, MAX_HELD_FACTS)),
            carried=AnswerSet(facts=facts(MAX_HELD_FACTS, 1)),
        )

        assert _submit(client, at).status_code == 201
        refused = _submit(client, over)
        assert refused.status_code == 400
        assert f"at most {MAX_HELD_FACTS} fact answers" in refused.json()["detail"]

    def test_the_new_job_s_questions_take_what_they_ask_and_list_the_rest(self):
        client, store = catalog_client()
        parent = waiting(store)
        asked = client.get(f"/v1/jobs/{parent}/questions", headers=auth()).json()
        key = next(
            q["key"] for q in asked["early_questions"] if q["kind"] == "attribute"
        )
        taken = FactAnswer(key=tuple(key), value="none")
        earlier = _holding(store, answers=AnswerSet(facts=(taken, GONE)))
        child_id = _submit(client, earlier).json()["job_id"]
        child = asyncio.run(store.get(child_id))
        asyncio.run(
            store.save(
                child.model_copy(
                    update={"status": "awaiting-answers", "checkpoint": held()}
                )
            )
        )

        read = client.get(f"/v1/jobs/{child_id}/questions", headers=auth()).json()

        assert [a["key"] for a in read["carried_dropped"]] == [list(GONE.key)]
        assert key in [q["key"] for q in read["answered_early"]]
        assert key not in [q["key"] for q in read["early_questions"]]

    def test_an_amendment_carries_what_a_new_analysis_carries(self):
        """Both new jobs read :meth:`JobRecord.held_answers`, so a carried answer
        the waiting job's questions dropped reaches each one, and each new
        job's own questions decide whether to take it."""
        client, store = catalog_client()
        parent = waiting(store)
        asked = client.get(f"/v1/jobs/{parent}/questions", headers=auth()).json()
        key = next(
            q["key"] for q in asked["early_questions"] if q["kind"] == "attribute"
        )
        earlier = _holding(
            store, answers=AnswerSet(facts=(FactAnswer(key=tuple(key), value="none"),))
        )
        child_id = _submit(client, earlier).json()["job_id"]
        child = asyncio.run(store.get(child_id)).model_copy(
            update={"status": "awaiting-answers", "checkpoint": held()}
        )
        child.carried = AnswerSet(facts=(*child.carried.facts, GONE))
        asyncio.run(store.save(child))

        amended = client.post(
            f"/v1/jobs/{child_id}/amendments",
            json={"text": "A CDN sits in front.", "revision": child.revision},
            headers=auth(),
        )
        assert amended.status_code == 201, amended.text
        again = _submit(client, child_id)
        assert again.status_code == 201, again.text

        by_amendment = asyncio.run(store.get(amended.json()["job_id"])).carried
        by_submission = asyncio.run(store.get(again.json()["job_id"])).carried
        assert by_amendment == by_submission == child.held_answers()
        assert GONE in by_amendment.facts
