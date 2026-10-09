"""The answer round's interface: one Answer State in, a saved round or a Resumed Job out.

The ``/v1`` routes, the first-run app and the eval replay each build an
:class:`~analysis_service.answer_round.AnswerState` from their own job record
and map its refusals to their own status codes. These tests drive the state
alone, so each rule fails here with no route between the test and the rule.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from analysis_service.answer_round import (
    AlreadyResumed,
    Answers,
    AnswerState,
    MissingRevision,
    ResumedJob,
    SavedRound,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.answer_sets import NO_ANSWERS
from analysis_service.sources import Source, SourceLimits
from tests.test_pause import held

ROOMY = SourceLimits(max_total_bytes=1_000_000, max_sources=10)


def waiting(**changes) -> AnswerState:
    """A job paused at its first round, with one source."""
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

        outcome = state.answer(Answers(revision=3), limits=ROOMY)

        assert isinstance(outcome, ResumedJob)
        assert outcome.checkpoint is state.checkpoint
        assert outcome.follow_up is False
        assert set(outcome.shown) == {q.key for q in state.questions.early}

    def test_a_save_keeps_the_round_at_the_revision_it_read(self):
        state = waiting()
        key = first_key(state)

        outcome = state.answer(
            Answers(save=True, skips=[key], revision=3), limits=ROOMY
        )

        assert isinstance(outcome, SavedRound)
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

        with pytest.raises(SourcesOverLimit):
            state.answer(
                Answers(save=True, skips=[first_key(state)], revision=3), limits=tight
            )

    def test_no_limits_checks_nothing(self):
        outcome = waiting(sources=()).answer(Answers(revision=3), limits=None)

        assert isinstance(outcome, ResumedJob)


def test_only_a_final_report_takes_corrections():
    with pytest.raises(ValueError, match="only a final report takes corrections"):
        waiting().correct([])
