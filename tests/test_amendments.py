"""A paused job's description is amended and extracted again (#1542 F, ADR 0072).

The pause could not correct the model: an owner who saw a missing component,
a missing flow or a stated fact read wrongly could only answer the facts the
service offered. An amendment starts a new job from the paused job's sources
with the amendment added. It carries the paused job's answers, and its own
questions take each one they still ask.
"""

from __future__ import annotations

import asyncio

import pytest

from analysis_service.answer_round import (
    AMENDMENT_LABEL,
    AlreadyResumed,
    Answers,
    AnswerState,
    MissingRevision,
    ResumedJob,
    StaleRevision,
)
from analysis_service.answer_sets import AnswerSet
from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import FactAnswer
from analysis_service.jobs import Checkpoint, JobRecord
from analysis_service.pipeline import _seeded_state
from analysis_service.sources import Source, SourceLimits
from tests.factories import DESCRIPTION_TEXT, sample_selection, valid_model
from tests.test_api import auth
from tests.test_links import catalog_client
from tests.test_pause import waiting

ROOM = SourceLimits(max_total_bytes=100_000, max_sources=5)
AMENDMENT = "A nightly batch loader also writes to the orders database."
STORE = "store:orders-db"


def _client():
    client, store = catalog_client()
    client.app.state.limits = ROOM
    return client, store


def _amend(client, job, revision=0, text=AMENDMENT):
    return client.post(
        f"/v1/jobs/{job}/amendments",
        json={"text": text, "revision": revision},
        headers=auth(),
    )


def _state(*, carried=(), facts=(), waiting=True, model=None):
    return AnswerState(
        checkpoint=Checkpoint(system_model=model or valid_model(), assertions=None),
        frameworks={"stride": {}},
        analyses=(),
        waiting=waiting,
        final=False,
        sources=(Source.description(DESCRIPTION_TEXT),),
        answers=AnswerSet(facts=tuple(facts)),
        shown=(),
        skipped=(),
        corrections=(),
        revision=0,
        resumed_by=None,
        carried_in=AnswerSet(facts=tuple(carried)),
    )


def _open_key(state):
    return next(q.key for q in state.questions.early if q.kind == "attribute")


class TestTheRoute:
    def test_an_amendment_starts_a_job_that_extracts_the_description_again(self):
        client, store = _client()
        job = waiting(store)
        saved = FactAnswer(
            key=(STORE, "encryption_at_rest", "", "", "", ""), value="AES"
        )
        parent = asyncio.run(store.get(job))
        asyncio.run(
            store.save(parent.model_copy(update={"answers": AnswerSet(facts=(saved,))}))
        )

        response = _amend(client, job)

        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert [source.label for source in child.sources][-1] == f"{AMENDMENT_LABEL} 1"
        assert child.sources[-1].text == AMENDMENT
        assert child.amends == job and child.ask_questions
        assert child.resumption is None, "it extracts again; it does not resume"
        assert child.carried.facts == (saved,) and child.answers.empty

    def test_the_paused_job_is_held_while_the_amended_job_is_alive(self):
        client, store = _client()
        job = waiting(store)
        child = _amend(client, job).json()["job_id"]
        child_record = asyncio.run(store.get(child))
        asyncio.run(store.save(child_record.model_copy(update={"status": "running"})))

        held_by = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert held_by["resumed_by"] == child
        again = _amend(client, job)
        assert again.status_code == 409
        answered = client.post(
            f"/v1/jobs/{job}/answers", json={"links": [], "revision": 0}, headers=auth()
        )
        assert answered.status_code == 409

    def test_a_failed_amended_job_frees_the_paused_job(self):
        client, store = _client()
        job = waiting(store)
        child = _amend(client, job).json()["job_id"]
        spent = asyncio.run(store.get(child))
        asyncio.run(store.save(spent.model_copy(update={"status": "failed"})))
        assert _amend(client, job).status_code == 201

    def test_an_amendment_names_the_revision_it_read(self):
        client, store = _client()
        job = waiting(store)
        assert _amend(client, job, revision=3).status_code == 409
        missing = client.post(
            f"/v1/jobs/{job}/amendments", json={"text": AMENDMENT}, headers=auth()
        )
        assert missing.status_code == 422

    def test_only_a_waiting_job_takes_an_amendment(self):
        from tests.test_questions import TestTheRoutes

        client, store = _client()
        finished = TestTheRoutes().completed(store)
        response = _amend(client, finished)
        assert response.status_code == 400
        assert "only a job waiting on answers" in response.json()["detail"]

    def test_an_amendment_is_held_to_the_source_limits(self):
        client, store = _client()
        client.app.state.limits = SourceLimits(max_total_bytes=600, max_sources=5)
        job = waiting(store)
        response = _amend(client, job, text="x" * 2_000)
        assert response.status_code in {400, 413}

    def test_the_amended_job_s_extraction_reads_no_carried_answer(self):
        """The carried answers wait for its questions; the extraction and the
        catalog pass never read them."""
        record = JobRecord.create(
            owner_subject="alice",
            sources=[Source.description(DESCRIPTION_TEXT)],
            frameworks=sample_selection(),
            ask_questions=True,
            amends="job-parent",
            carried=AnswerSet(
                facts=(
                    FactAnswer(
                        key=(STORE, "encryption_at_rest", "", "", "", ""), value="x"
                    ),
                )
            ),
        )
        seeded = _seeded_state(record)
        assert all(not value for value in seeded.values())


class TestTheCarriedAnswers:
    def test_a_carried_answer_its_questions_still_ask_is_taken(self):
        key = _open_key(_state())
        answer = FactAnswer(key=key, value="none")
        state = _state(carried=[answer])
        assert state.carried.taken.facts == (answer,) and state.carried.dropped.empty
        assert key not in {q.key for q in state.questions.early}
        assert key in {q.key for q, _ in state.questions.answered_early}

    def test_a_carried_answer_about_a_missing_part_is_dropped(self):
        gone = FactAnswer(
            key=UnknownRef(element_id="store:gone", attribute="encryption_at_rest").key,
            value="AES",
        )
        state = _state(carried=[gone])
        assert state.carried.dropped.facts == (gone,)
        assert state.held.facts == ()

    def test_a_carried_answer_about_a_fact_the_model_now_states_is_dropped(self):
        key = _open_key(_state())
        element_id, attribute, *_ = key
        model = valid_model()
        setattr(model.get(element_id), attribute, "stated by the amendment")
        state = _state(carried=[FactAnswer(key=key, value="none")], model=model)
        assert [a.key for a in state.carried.dropped.facts] == [key]

    def test_an_answer_the_new_rounds_gave_replaces_the_carried_one(self):
        key = _open_key(_state())
        carried = FactAnswer(key=key, value="none")
        later = FactAnswer(key=key, value="unknown")
        state = _state(carried=[carried], facts=[later])
        assert state.held.facts == (later,)

    def test_a_continue_carries_the_taken_answers_into_the_analysis(self):
        key = _open_key(_state())
        carried = FactAnswer(key=key, value="none")
        resumed = _state(carried=[carried]).answer(Answers(revision=0), limits=ROOM)
        assert isinstance(resumed, ResumedJob)
        assert carried in resumed.answers.facts


class TestTheAmendRule:
    def test_it_names_its_revision(self):
        with pytest.raises(MissingRevision):
            _state().amend(AMENDMENT, revision=None, limits=ROOM)
        with pytest.raises(StaleRevision):
            _state().amend(AMENDMENT, revision=1, limits=ROOM)

    def test_a_held_job_takes_no_amendment(self):
        from dataclasses import replace

        with pytest.raises(AlreadyResumed):
            replace(_state(), resumed_by="job-x").amend(
                AMENDMENT, revision=0, limits=ROOM
            )

    def test_amendments_are_numbered_and_drop_the_answers_source(self):
        state = _state()
        first = state.amend(AMENDMENT, revision=0, limits=ROOM)
        from dataclasses import replace

        again = replace(
            state,
            sources=(
                *first.sources,
                Source(kind="answers", label="Your answers", text="answer lines"),
            ),
        ).amend("Another fact.", revision=0, limits=ROOM)
        assert [s.label for s in again.sources][-2:] == [
            f"{AMENDMENT_LABEL} 1",
            f"{AMENDMENT_LABEL} 2",
        ]
        assert all(source.kind != "answers" for source in again.sources)

    def test_a_caller_s_own_label_never_collides_with_an_amendment_s(self):
        """A caller names its own sources, so a label is no count of amendments."""
        from dataclasses import replace

        named = Source.description("The caller's notes.", label=f"{AMENDMENT_LABEL} 2")
        state = replace(_state(), sources=(*_state().sources, named))
        amended = state.amend(AMENDMENT, revision=0, limits=ROOM)
        labels = [source.label for source in amended.sources]
        assert len(labels) == len(set(labels)), labels
