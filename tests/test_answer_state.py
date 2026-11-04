"""The answer interface: one Answer State in, saved answers or a Resumed Job out.

The ``/v1`` routes, the first-run app and the eval replay each build an
:class:`~analysis_service.answer_state.AnswerState` from their own job record
and map its refusals to their own status codes. These tests drive the state
alone, so each rule fails here with no route between the test and the rule.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from analysis_service.answer_sets import NO_ANSWERS, AnswerSet, SentAnswers
from analysis_service.answer_state import (
    AlreadyResumed,
    Answers,
    AnswerState,
    MissingRevision,
    ResumedJob,
    SavedAnswers,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.controls import Control, ControlDocument
from analysis_service.fact_answers import FactAnswer
from analysis_service.job_controls import CopiedControl
from analysis_service.sources import Source, SourceLimits
from tests.test_controls import fact
from tests.test_pause import held

ROOMY = SourceLimits(max_total_bytes=1_000_000, max_sources=10)


def waiting(**changes) -> AnswerState:
    """A job paused at its list, with one source."""
    state = AnswerState(
        checkpoint=held(),
        frameworks={"stride": {}},
        analyses=(),
        waiting=True,
        final=False,
        sources=(Source(kind="description", label="overview", text="A web app."),),
        answers=NO_ANSWERS,
        shown=(),
        skipped=(),
        corrections=(),
        revision=3,
        resumed_by=None,
    )
    return replace(state, **changes)


def first_key(state: AnswerState):
    early = state.questions.early
    assert early, "the held model asks no early question"
    return early[0].key


class TestTheRevision:
    def test_a_waiting_job_requires_one(self):
        with pytest.raises(MissingRevision, match="send the revision"):
            waiting().answer(Answers(), limits=ROOMY)

    def test_an_earlier_revision_is_stale(self):
        with pytest.raises(StaleRevision):
            waiting().answer(Answers(revision=2), limits=ROOMY)

    def test_a_finished_job_checks_a_revision_where_one_is_sent(self):
        """A finished report's saves carry a revision (ADR 0070), so a run that
        sends one is checked against it, and a run that sends none is not."""
        finished = waiting(waiting=False, revision=0)

        with pytest.raises(StaleRevision):
            finished.answer(Answers(revision=7), limits=ROOMY)
        with pytest.raises(ValueError, match="no answers were sent"):
            finished.answer(Answers(), limits=ROOMY)

    def test_a_finished_job_s_save_requires_a_revision(self):
        finished = waiting(waiting=False, revision=0)

        with pytest.raises(MissingRevision):
            finished.answer(Answers(save=True), limits=ROOMY)


class TestTheOutcome:
    def test_a_continue_starts_a_resumed_job_from_the_checkpoint(self):
        state = waiting()
        shown = [q.key for q in state.questions.early]

        outcome = state.answer(Answers(revision=3, presented=shown), limits=ROOMY)

        assert isinstance(outcome, ResumedJob)
        assert outcome.checkpoint is state.checkpoint
        assert outcome.follow_up is False
        assert set(outcome.shown) == set(shown)

    def test_a_save_keeps_the_answers_at_the_revision_it_read(self):
        state = waiting()
        key = first_key(state)

        outcome = state.answer(
            Answers(save=True, skips=[key], presented=[key], revision=3), limits=ROOMY
        )

        assert isinstance(outcome, SavedAnswers)
        assert outcome.revision == 3
        assert outcome.skipped == (key,)

    def test_a_job_its_answers_started_takes_no_more(self):
        with pytest.raises(AlreadyResumed):
            waiting(resumed_by="job-1").answer(Answers(revision=3), limits=ROOMY)


class TestTheSourceLimits:
    def test_a_start_over_the_limits_is_refused(self):
        tight = SourceLimits(max_total_bytes=1, max_sources=10)

        with pytest.raises(SourcesOverLimit) as refused:
            waiting().answer(Answers(revision=3), limits=tight)

        assert refused.value.breach.rung == "total"

    def test_a_save_over_the_limits_is_refused_too(self):
        state = waiting()
        tight = SourceLimits(max_total_bytes=1, max_sources=10)

        key = first_key(state)
        with pytest.raises(SourcesOverLimit):
            state.answer(
                Answers(save=True, skips=[key], presented=[key], revision=3),
                limits=tight,
            )

    def test_no_limits_checks_nothing(self):
        outcome = waiting(sources=()).answer(Answers(revision=3), limits=None)

        assert isinstance(outcome, ResumedJob)


def test_only_a_final_report_takes_corrections():
    state = waiting()
    with pytest.raises(ValueError, match="only a final report takes corrections"):
        state.correct([], revision=state.revision)


def _copied(document: dict) -> CopiedControl:
    control = Control.create(
        owner_id="alice", document=ControlDocument.model_validate(document)
    )
    return CopiedControl.of(control, {})


ENCRYPTED = {
    "id": f"control-{'a' * 32}",
    "name": "Encrypted",
    "scope": {"type": "store", "name": "Orders DB", "aliases": []},
    "facts": [fact()],
    "rows": [],
    "evidence": [],
}
DB_KEY = ("store:orders-db", "encryption_at_rest", "", "", "", "")
TLS_KEY = (
    "flow:process:web-app>store:orders-db>store-order",
    "encryption_in_transit",
    "",
    "",
    "",
    "",
)


class TestTheRung:
    """``fact_origin`` names the rung of each held answer (ADR 0125)."""

    def answer(self, key, value: str = "yes") -> FactAnswer:
        return FactAnswer(key=key, value=value)

    def test_each_rung_is_named(self):
        state = waiting(
            controls=(_copied(ENCRYPTED),),
            corrections=(self.answer(("", "", "", "", "", "oauth")),),
            draft=SentAnswers(facts=(self.answer(("", "", "", "", "", "cors")),)),
            answers=AnswerSet(facts=(self.answer(("", "", "", "", "", "cookies")),)),
            carried_in=AnswerSet(facts=(self.answer(TLS_KEY, "TLS 1.3"),)),
        )
        rungs = {
            key[-1] or key[1]: origin.rung
            for key in (
                ("", "", "", "", "", "oauth"),
                ("", "", "", "", "", "cors"),
                ("", "", "", "", "", "cookies"),
                DB_KEY,
                TLS_KEY,
            )
            if (origin := state.fact_origin(key)) is not None
        }
        assert rungs == {
            "oauth": "correction",
            "cors": "draft",
            "cookies": "answer",
            "encryption_at_rest": "control",
            "encryption_in_transit": "carried",
        }
        assert state.fact_origin(DB_KEY).name == "Encrypted"
        assert state.fact_origin(("", "", "", "", "", "websocket")) is None

    def test_an_own_answer_lies_over_a_control_and_a_control_over_a_carried(self):
        state = waiting(
            controls=(_copied(ENCRYPTED),),
            carried_in=AnswerSet(facts=(self.answer(DB_KEY, "KMS"),)),
        )
        assert state.fact_origin(DB_KEY).rung == "control"
        assert state.held.facts == (self.answer(DB_KEY, "AES-256"),)
        own = replace(state, answers=AnswerSet(facts=(self.answer(DB_KEY, "CMEK"),)))
        assert own.fact_origin(DB_KEY).rung == "answer"
        assert own.held.facts == (self.answer(DB_KEY, "CMEK"),)
