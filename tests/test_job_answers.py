"""The job answer operations: one store in, a saved round, a started job or a
typed refusal out.

These tests drive :class:`~analysis_service.job_answers.JobAnswers` over an
in-memory store and a stub runner, with no route between the test and the
rule. The ``/v1`` route tests hold the HTTP mapping of each refusal.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from analysis_service.answer_round import (
    AlreadyResumed,
    Answers,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.answer_sets import AnswerSet
from analysis_service.errors import ConfigError
from analysis_service.fact_answers import FactAnswer
from analysis_service.job_answers import (
    DraftSaved,
    JobAnswers,
    NoCatalog,
    NoSuchJob,
    NotAdmitted,
    NotCompleted,
    Refused,
    RoundSaved,
    RunnerUnavailable,
    Started,
)
from analysis_service.jobs import InMemoryJobStore, JobRecord, StubPipelineRunner
from analysis_service.links import LinkAnswer
from analysis_service.sources import SourceLimits
from tests.factories import SEEDING_BUDGET, admit, sample_selection, valid_model
from tests.test_pause import waiting
from tests.test_questions import TestTheRoutes

SUBJECT = "alice"
ROOMY = SourceLimits(max_total_bytes=100_000, max_sources=10)
#: The fact the completed report of :meth:`TestTheRoutes.completed` asks.
ASKED = (valid_model().data_flows[1].id, "encryption_in_transit", "", "", "", "")


class Starts:
    """Records each start the operations ask for."""

    def __init__(self) -> None:
        self.started: list[tuple[JobRecord, object]] = []

    def __call__(self, record: JobRecord, runner: object) -> None:
        self.started.append((record, runner))


def operations(
    store: InMemoryJobStore,
    *,
    ceiling: int = 100,
    catalog: bool = True,
    runner_for=None,
) -> JobAnswers:
    runner = StubPipelineRunner()
    return JobAnswers(
        store=store,
        runner_for=runner_for or (lambda selection, entry: runner),
        limits=ROOMY,
        ceiling=ceiling,
        budget=SEEDING_BUDGET,
        carries_catalog=catalog,
    )


def first_early_key(answers: JobAnswers, job: str) -> tuple[str, ...]:
    payload = asyncio.run(answers.questions(job, SUBJECT))
    return tuple(payload["early_questions"][0]["key"])


class TestReadingAJob:
    def test_a_foreign_or_missing_job_reads_the_same(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        with pytest.raises(NoSuchJob):
            asyncio.run(answers.questions("job-nobody", SUBJECT))
        with pytest.raises(NoSuchJob):
            asyncio.run(answers.questions(job, "mallory"))

    def test_a_running_job_has_no_report_to_ask_about(self):
        store = InMemoryJobStore()
        record = JobRecord.create(
            owner_subject=SUBJECT,
            sources=list(valid_model_sources()),
            frameworks=sample_selection(),
        )
        record.transition("running")
        asyncio.run(admit(store, record))
        with pytest.raises(NotCompleted) as refused:
            asyncio.run(operations(store).questions(record.id, SUBJECT))
        assert refused.value.status == "running"

    def test_a_waiting_job_asks_its_early_questions(self):
        store = InMemoryJobStore()
        job = waiting(store)
        payload = asyncio.run(operations(store).questions(job, SUBJECT))
        assert payload["early_questions"]
        assert payload["revision"] == 0
        assert payload["fact_questions"] == []


class TestAnsweringAWaitingJob:
    def test_a_saved_round_writes_the_answers_and_starts_nothing(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        starts = Starts()
        outcome = asyncio.run(
            answers.answer(
                job,
                SUBJECT,
                Answers(
                    given=AnswerSet(facts=(FactAnswer(key=key, value="unknown"),)),
                    save=True,
                    skips=(),
                    revision=0,
                ),
                start=starts,
            )
        )
        assert outcome == RoundSaved(job_id=job)
        assert starts.started == []
        record = asyncio.run(store.get(job))
        assert record is not None
        assert record.round_revision == 1
        assert [fact.key for fact in record.answers.facts] == [key]

    def test_a_continue_starts_the_resumed_job(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        starts = Starts()
        outcome = asyncio.run(
            answers.answer(
                job,
                SUBJECT,
                Answers(given=AnswerSet(), save=False, skips=(), revision=0),
                start=starts,
            )
        )
        assert isinstance(outcome, Started)
        [(record, _)] = starts.started
        assert record.id == outcome.job_id
        assert record.resumption is not None
        assert record.resumption.parent_id == job
        assert not record.resumption.follow_up
        assert asyncio.run(store.resumed_by(job, SUBJECT)) == record.id

    def test_a_link_answer_needs_a_catalog_before_any_read(self):
        store = InMemoryJobStore()
        answers = operations(store, catalog=False)
        given = AnswerSet(
            links=(LinkAnswer(principal="shopper accounts", element="none"),)
        )
        with pytest.raises(NoCatalog):
            asyncio.run(
                answers.answer(
                    "job-nobody",
                    SUBJECT,
                    Answers(given=given, save=False, skips=(), revision=0),
                    start=Starts(),
                )
            )

    def test_an_empty_save_is_refused_in_the_rules_own_words(self):
        store = InMemoryJobStore()
        job = waiting(store)
        with pytest.raises(Refused, match="answers or skips at least one"):
            asyncio.run(
                operations(store).answer(
                    job,
                    SUBJECT,
                    Answers(given=AnswerSet(), save=True, skips=(), revision=0),
                    start=Starts(),
                )
            )

    def test_an_answer_read_off_an_older_round_is_refused_and_starts_nothing(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        saved = Answers(
            given=AnswerSet(facts=(FactAnswer(key=key, value="unknown"),)),
            save=True,
            skips=(),
            revision=0,
        )
        asyncio.run(answers.answer(job, SUBJECT, saved, start=Starts()))
        starts = Starts()
        with pytest.raises(StaleRevision):
            asyncio.run(
                answers.answer(
                    job,
                    SUBJECT,
                    Answers(given=AnswerSet(), save=False, skips=(), revision=0),
                    start=starts,
                )
            )
        assert starts.started == []
        assert asyncio.run(store.resumed_by(job, SUBJECT)) is None

    def test_a_round_of_skips_alone_is_kept_and_listed(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        outcome = asyncio.run(
            answers.answer(
                job,
                SUBJECT,
                Answers(given=AnswerSet(), save=True, skips=(key,), revision=0),
                start=Starts(),
            )
        )
        assert outcome == RoundSaved(job_id=job)
        payload = asyncio.run(answers.questions(job, SUBJECT))
        assert [tuple(q["key"]) for q in payload["skipped_early"]] == [key]
        assert key not in {tuple(q["key"]) for q in payload["early_questions"]}

    def test_a_save_after_the_start_is_refused(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        asyncio.run(
            answers.answer(
                job,
                SUBJECT,
                Answers(given=AnswerSet(), save=False, skips=(), revision=0),
                start=Starts(),
            )
        )
        with pytest.raises(AlreadyResumed):
            asyncio.run(
                answers.answer(
                    job,
                    SUBJECT,
                    Answers(given=AnswerSet(), save=True, skips=(key,), revision=0),
                    start=Starts(),
                )
            )

    def test_answers_over_the_input_limits_start_nothing(self):
        store = InMemoryJobStore()
        job = waiting(store)
        tight = replace(
            operations(store), limits=SourceLimits(max_total_bytes=10, max_sources=10)
        )
        key = first_early_key(tight, job)
        starts = Starts()
        with pytest.raises(SourcesOverLimit, match="bytes"):
            asyncio.run(
                tight.answer(
                    job,
                    SUBJECT,
                    Answers(
                        given=AnswerSet(facts=(FactAnswer(key=key, value="unknown"),)),
                        save=False,
                        skips=(),
                        revision=0,
                    ),
                    start=starts,
                )
            )
        assert starts.started == []


class TestAnsweringAReport:
    def test_an_answer_starts_the_one_follow_up(self):
        store = InMemoryJobStore()
        job = TestTheRoutes().completed(store)
        starts = Starts()
        outcome = asyncio.run(
            operations(store).answer(
                job,
                SUBJECT,
                Answers(
                    given=AnswerSet(facts=(FactAnswer(key=ASKED, value="TLS 1.3"),)),
                    save=False,
                    skips=(),
                    revision=None,
                ),
                start=starts,
            )
        )
        assert isinstance(outcome, Started)
        [(record, _)] = starts.started
        assert record.resumption is not None and record.resumption.follow_up

    def test_a_draft_is_kept_with_its_next_revision(self):
        store = InMemoryJobStore()
        job = TestTheRoutes().completed(store)
        outcome = asyncio.run(
            operations(store).answer(
                job,
                SUBJECT,
                Answers(
                    given=AnswerSet(facts=(FactAnswer(key=ASKED, value="TLS 1.3"),)),
                    save=True,
                    skips=(),
                    revision=0,
                ),
                start=Starts(),
            )
        )
        assert outcome == DraftSaved(job_id=job, revision=1)
        record = asyncio.run(store.get(job))
        assert record is not None
        assert [fact.key for fact in record.draft.facts] == [ASKED]

    def test_a_correction_needs_a_final_report(self):
        store = InMemoryJobStore()
        job = TestTheRoutes().completed(store)
        with pytest.raises(Refused, match="final"):
            asyncio.run(
                operations(store).correct(
                    job, SUBJECT, [FactAnswer(key=ASKED, value="TLS 1.3")]
                )
            )

    def test_a_fact_the_report_does_not_hold_is_refused(self):
        store = InMemoryJobStore()
        job = TestTheRoutes().completed(store)
        wrong = (ASKED[0], "exposure", "", "", "", "")
        with pytest.raises(Refused, match="exposure"):
            asyncio.run(
                operations(store).answer(
                    job,
                    SUBJECT,
                    Answers(
                        given=AnswerSet(
                            facts=(FactAnswer(key=wrong, value="internal"),)
                        ),
                        save=False,
                        skips=(),
                        revision=None,
                    ),
                    start=Starts(),
                )
            )


class TestAmending:
    def test_an_amendment_starts_a_job_that_asks_again_and_carries_the_answers(
        self,
    ):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        asyncio.run(
            answers.answer(
                job,
                SUBJECT,
                Answers(
                    given=AnswerSet(facts=(FactAnswer(key=key, value="unknown"),)),
                    save=True,
                    skips=(),
                    revision=0,
                ),
                start=Starts(),
            )
        )
        starts = Starts()
        outcome = asyncio.run(
            answers.amend(
                job,
                SUBJECT,
                "The web app also calls a payment gateway.",
                revision=1,
                start=starts,
            )
        )
        assert isinstance(outcome, Started)
        [(record, _)] = starts.started
        assert record.amends == job
        assert record.ask_questions and record.resumption is None
        assert [fact.key for fact in record.carried.facts] == [key]
        assert record.sources[-1].label.startswith("Amendment")

    def test_a_stale_amendment_is_refused(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        asyncio.run(
            answers.answer(
                job,
                SUBJECT,
                Answers(given=AnswerSet(), save=True, skips=(key,), revision=0),
                start=Starts(),
            )
        )
        starts = Starts()
        with pytest.raises(StaleRevision):
            asyncio.run(
                answers.amend(job, SUBJECT, "More text.", revision=0, start=starts)
            )
        assert starts.started == []


class TestTheOneAdmission:
    def test_a_runner_that_cannot_build_holds_no_slot(self):
        store = InMemoryJobStore()

        def unconfigured(selection, entry):
            raise ConfigError("ANALYSIS_TIERS_FILE is unset")

        record = JobRecord.create(
            owner_subject=SUBJECT,
            sources=list(valid_model_sources()),
            frameworks=sample_selection(),
        )
        starts = Starts()
        with pytest.raises(RunnerUnavailable):
            asyncio.run(
                operations(store, runner_for=unconfigured).admit(record, start=starts)
            )
        assert asyncio.run(store.get(record.id)) is None
        assert starts.started == []

    def test_a_subject_at_the_ceiling_is_refused_and_nothing_starts(self):
        store = InMemoryJobStore()
        answers = operations(store, ceiling=1)
        first = JobRecord.create(
            owner_subject=SUBJECT,
            sources=list(valid_model_sources()),
            frameworks=sample_selection(),
        )
        asyncio.run(answers.admit(first, start=Starts()))
        second = JobRecord.create(
            owner_subject=SUBJECT,
            sources=list(valid_model_sources()),
            frameworks=sample_selection(),
        )
        starts = Starts()
        with pytest.raises(NotAdmitted) as refused:
            asyncio.run(answers.admit(second, start=starts))
        assert refused.value.admission.outcome == "at_ceiling"
        assert refused.value.ceiling == 1
        assert starts.started == []


def valid_model_sources():
    from analysis_service.sources import Source
    from tests.factories import DESCRIPTION_TEXT

    return [Source.description(DESCRIPTION_TEXT, label="overview")]
