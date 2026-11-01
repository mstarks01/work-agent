"""The job answer operations: one store in, saved answers, a started job or a
typed refusal out.

These tests drive :class:`~analysis_service.job_answers.JobAnswers` over an
in-memory store and a stub runner, with no route between the test and the
rule. The ``/v1`` route tests hold the HTTP mapping of each refusal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from uuid import UUID

import pytest

from analysis_service import budgets
from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import (
    AlreadyResumed,
    Answers,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.claims import FrameworkName, UnknownRef
from analysis_service.errors import ConfigError
from analysis_service.fact_answers import FactAnswer
from analysis_service.fact_writes import LineageConflict, check_fact_answers
from analysis_service.job_answers import (
    OWNER_FILE_GONE,
    AnswersSaved,
    DraftSaved,
    JobAnswers,
    NoCatalog,
    NoSuchJob,
    NotAdmitted,
    NotCompleted,
    Refused,
    RunnerUnavailable,
    Started,
    Submission,
)
from analysis_service.jobs import InMemoryJobStore, JobRecord, StubPipelineRunner
from analysis_service.links import LinkAnswer
from analysis_service.owner_files import OwnerFile
from analysis_service.report import FrameworkSelection
from analysis_service.selection import SelectionError
from analysis_service.sources import ANSWERS_LABEL, Source, SourceLimits
from tests.factories import (
    SEEDING_BUDGET,
    admit,
    identity_of,
    new_principal,
    sample_selection,
    valid_model,
)
from tests.test_pause import waiting
from tests.test_questions import TestTheRoutes

OWNER = "alice"
ROOMY = SourceLimits(max_total_bytes=100_000, max_sources=10)
#: What the deployment under test carries, in its config order.
CARRIED: tuple[FrameworkName, ...] = ("stride", "asvs")
#: The complete selection of each carried package, as a submission lists it.
PICKED = (
    FrameworkSelection(name="stride"),
    FrameworkSelection(name="asvs", options={"level": 2}),
)
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
        frameworks=CARRIED,
        carries_catalog=catalog,
    )


def first_early_key(answers: JobAnswers, job: str) -> tuple[str, ...]:
    payload = asyncio.run(answers.questions(job, OWNER))
    return tuple(payload["early_questions"][0]["key"])


class TestReadingAJob:
    def test_a_foreign_or_missing_job_reads_the_same(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        with pytest.raises(NoSuchJob):
            asyncio.run(answers.questions("job-nobody", OWNER))
        with pytest.raises(NoSuchJob):
            asyncio.run(answers.questions(job, "mallory"))

    def test_an_identity_with_no_principal_owns_no_job_and_gets_none(self):
        store = InMemoryJobStore()
        with pytest.raises(NoSuchJob):
            asyncio.run(operations(store).owner_id(identity_of("nobody"), "job-x"))
        assert asyncio.run(store.principal(identity_of("nobody"))) is None

    def test_a_principal_names_its_owner_id(self):
        store = InMemoryJobStore()
        job = waiting(store)
        owner_id = asyncio.run(operations(store).owner_id(identity_of(OWNER), job))
        assert owner_id == OWNER

    def test_a_running_job_has_no_report_to_ask_about(self):
        store = InMemoryJobStore()
        record = JobRecord.create(
            owner_id=OWNER,
            sources=list(valid_model_sources()),
            frameworks=sample_selection(),
        )
        record.transition("running")
        asyncio.run(admit(store, record))
        with pytest.raises(NotCompleted) as refused:
            asyncio.run(operations(store).questions(record.id, OWNER))
        assert refused.value.status == "running"

    def test_a_waiting_job_asks_its_early_questions(self):
        store = InMemoryJobStore()
        job = waiting(store)
        payload = asyncio.run(operations(store).questions(job, OWNER))
        assert payload["early_questions"]
        assert payload["revision"] == 0
        assert payload["fact_questions"] == []


class TestAnsweringAWaitingJob:
    def test_a_save_writes_the_answers_and_starts_nothing(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        starts = Starts()
        outcome = asyncio.run(
            answers.answer(
                job,
                OWNER,
                Answers(
                    given=AnswerSet(facts=(FactAnswer(key=key, value="unknown"),)),
                    save=True,
                    skips=(),
                    revision=0,
                ),
                start=starts,
            )
        )
        assert outcome == AnswersSaved(job_id=job)
        assert starts.started == []
        record = asyncio.run(store.get(job))
        assert record is not None
        assert record.revision == 1
        assert [fact.key for fact in record.answers.facts] == [key]

    def test_a_continue_starts_the_resumed_job(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        starts = Starts()
        outcome = asyncio.run(
            answers.answer(
                job,
                OWNER,
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
        assert asyncio.run(store.resumed_by(job, OWNER)) == record.id

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
                    OWNER,
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
                    OWNER,
                    Answers(given=AnswerSet(), save=True, skips=(), revision=0),
                    start=Starts(),
                )
            )

    def test_an_answer_read_off_an_older_revision_is_refused_and_starts_nothing(self):
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
        asyncio.run(answers.answer(job, OWNER, saved, start=Starts()))
        starts = Starts()
        with pytest.raises(StaleRevision):
            asyncio.run(
                answers.answer(
                    job,
                    OWNER,
                    Answers(given=AnswerSet(), save=False, skips=(), revision=0),
                    start=starts,
                )
            )
        assert starts.started == []
        assert asyncio.run(store.resumed_by(job, OWNER)) is None

    def test_a_save_of_skips_alone_is_kept_and_listed(self):
        store = InMemoryJobStore()
        job = waiting(store)
        answers = operations(store)
        key = first_early_key(answers, job)
        outcome = asyncio.run(
            answers.answer(
                job,
                OWNER,
                Answers(
                    given=AnswerSet(),
                    save=True,
                    skips=(key,),
                    presented=(key,),
                    revision=0,
                ),
                start=Starts(),
            )
        )
        assert outcome == AnswersSaved(job_id=job)
        payload = asyncio.run(answers.questions(job, OWNER))
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
                OWNER,
                Answers(given=AnswerSet(), save=False, skips=(), revision=0),
                start=Starts(),
            )
        )
        with pytest.raises(AlreadyResumed):
            asyncio.run(
                answers.answer(
                    job,
                    OWNER,
                    Answers(
                        given=AnswerSet(),
                        save=True,
                        skips=(key,),
                        presented=(key,),
                        revision=0,
                    ),
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
                    OWNER,
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
                OWNER,
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
                OWNER,
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
                    job, OWNER, [FactAnswer(key=ASKED, value="TLS 1.3")], revision=0
                )
            )

    def test_a_fact_the_report_does_not_hold_is_refused(self):
        store = InMemoryJobStore()
        job = TestTheRoutes().completed(store)
        wrong = (ASKED[0], "exposure", "", "", "", "")
        with pytest.raises(Refused, match=r"facts\[0\]: no attribute"):
            asyncio.run(
                operations(store).answer(
                    job,
                    OWNER,
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
                OWNER,
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
                OWNER,
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
                OWNER,
                Answers(
                    given=AnswerSet(),
                    save=True,
                    skips=(key,),
                    presented=(key,),
                    revision=0,
                ),
                start=Starts(),
            )
        )
        starts = Starts()
        with pytest.raises(StaleRevision):
            asyncio.run(
                answers.amend(job, OWNER, "More text.", revision=0, start=starts)
            )
        assert starts.started == []


def submission(**changes) -> Submission:
    """A complete submission of every carried framework, in config order."""
    return Submission(
        **{"sources": valid_model_sources(), "frameworks": PICKED} | changes
    )


class TestSubmitting:
    """The one submission of a new job, for both HTTP surfaces (ADR 0077)."""

    def submit(self, answers: JobAnswers, submission: Submission) -> JobRecord:
        starts = Starts()
        started = asyncio.run(
            answers.submit(identity_of(OWNER), submission, start=starts)
        )
        assert isinstance(started, Started)
        assert [record.id for record, _ in starts.started] == [started.job_id]
        record = asyncio.run(answers.store.get(started.job_id))
        assert record is not None
        return record

    @pytest.mark.parametrize("picked", [PICKED, tuple(reversed(PICKED))])
    def test_the_blocks_follow_the_config_order_whatever_order_was_sent(self, picked):
        """ADR 0102: the report's blocks are in ``config/frameworks.toml`` order."""
        record = self.submit(
            operations(InMemoryJobStore()), submission(frameworks=picked)
        )
        assert record.selection() == CARRIED
        assert record.frameworks == list(PICKED)

    def test_a_selection_the_install_cannot_run_is_refused_before_an_owner_exists(
        self,
    ):
        store = InMemoryJobStore()
        twice = (PICKED[0], PICKED[0])
        with pytest.raises(SelectionError, match="repeats stride"):
            self.submit(operations(store), submission(frameworks=twice))
        assert asyncio.run(store.principal(identity_of(OWNER))) is None

    def test_an_answers_source_of_the_callers_own_is_refused(self):
        forged = Source(kind="answers", label=ANSWERS_LABEL, text="shoppers: web-app")
        with pytest.raises(Refused, match="composed by this service"):
            self.submit(
                operations(InMemoryJobStore()),
                submission(sources=[*valid_model_sources(), forged]),
            )

    def test_a_link_answer_needs_a_catalog(self):
        link = LinkAnswer(principal="shoppers", element="entity:shopper")
        with pytest.raises(NoCatalog):
            self.submit(
                operations(InMemoryJobStore(), catalog=False),
                submission(links=[link]),
            )

    def test_the_link_answers_ride_on_the_record_and_in_its_sources(self):
        link = LinkAnswer(principal="shoppers", element="entity:shopper")
        record = self.submit(operations(InMemoryJobStore()), submission(links=[link]))
        assert record.answers.links == (link,)
        assert [source.kind for source in record.sources] == ["description", "answers"]
        assert record.reserved_tokens == budgets.estimate(
            record.sources, record.frameworks
        )

    @pytest.mark.parametrize(
        ("sources", "rung"),
        [
            pytest.param([], "empty", id="empty"),
            pytest.param(
                [Source.description("x", label=f"Doc {n}") for n in range(11)],
                "count",
                id="count",
            ),
        ],
    )
    def test_sources_over_the_limits_are_refused_by_rung(self, sources, rung):
        with pytest.raises(SourcesOverLimit) as refused:
            self.submit(operations(InMemoryJobStore()), submission(sources=sources))
        assert refused.value.breach.rung == rung

    def test_a_refused_submission_leaves_no_principal(self):
        store = InMemoryJobStore()
        with pytest.raises(NotAdmitted):
            self.submit(operations(store, ceiling=0), submission())
        assert asyncio.run(store.principal(identity_of(OWNER))) is None

    def test_the_first_submission_creates_the_principal_and_logs_the_sub_once(
        self, caplog
    ):
        store = InMemoryJobStore()
        answers = operations(store)
        with caplog.at_level(logging.INFO):
            first = self.submit(answers, submission())
            second = self.submit(answers, submission())
        principal = asyncio.run(store.principal(identity_of(OWNER)))
        assert principal is not None
        assert UUID(principal.owner_id).version == 4
        assert {first.owner_id, second.owner_id} == {principal.owner_id}
        assert caplog.text.count(OWNER) == 1
        assert principal.owner_id in caplog.text

    def test_a_new_job_carries_the_earlier_jobs_answers(self):
        """ADR 0079: the new job keeps them apart from its own answers."""
        store = InMemoryJobStore()
        answers = operations(store)
        saved = FactAnswer(
            key=("store:orders-db", "encryption_at_rest", "", "", "", ""), value="AES"
        )
        earlier = waiting(store, owner=OWNER)
        record = asyncio.run(store.get(earlier))
        asyncio.run(
            store.save(record.model_copy(update={"answers": AnswerSet(facts=(saved,))}))
        )

        child = self.submit(answers, submission(questions=True, answers_from=earlier))

        assert child.carried.facts == (saved,) and child.answers.empty
        assert child.amends is None, "it locks nothing"
        assert asyncio.run(store.resumed_by(earlier, OWNER)) is None

    def test_carried_answers_need_the_questions_on(self):
        store = InMemoryJobStore()
        earlier = waiting(store, owner=OWNER)
        with pytest.raises(Refused, match="turn the questions on"):
            self.submit(operations(store), submission(answers_from=earlier))


class TestTheOneAdmission:
    def test_a_runner_that_cannot_build_holds_no_slot(self):
        store = InMemoryJobStore()

        def unconfigured(selection, entry):
            raise ConfigError("ANALYSIS_TIERS_FILE is unset")

        record = JobRecord.create(
            owner_id=OWNER,
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

    def test_an_owner_at_the_ceiling_is_refused_and_nothing_starts(self):
        store = InMemoryJobStore()
        answers = operations(store, ceiling=1)
        submitter = asyncio.run(answers.submitter(identity_of(OWNER)))
        first = JobRecord.create(
            owner_id=submitter.owner_id,
            sources=list(valid_model_sources()),
            frameworks=sample_selection(),
        )
        asyncio.run(answers.admit(first, start=Starts(), principal=submitter.new))
        second = JobRecord.create(
            owner_id=submitter.owner_id,
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


class TestAContradictionIsNamed:
    """A refused lineage names the part and the ancestor, so a page can offer
    to answer both in one save (#1632)."""

    PART = UnknownRef(capability="oauth-client").key
    ANCESTOR = UnknownRef(capability="oauth").key

    def test_the_rule_names_the_part_and_the_ancestor(self):
        answers = [
            FactAnswer(key=self.ANCESTOR, value="no"),
            FactAnswer(key=self.PART, value="yes"),
        ]
        with pytest.raises(LineageConflict) as refused:
            check_fact_answers(answers, valid_model(), None)
        assert (refused.value.part, refused.value.ancestor) == ("oauth-client", "oauth")

    def test_the_refusal_carries_the_two_question_keys(self):
        def rule():
            raise LineageConflict("oauth-client", "oauth")

        with pytest.raises(Refused) as refused:
            JobAnswers._refusing(rule)
        assert refused.value.contradiction == (self.PART, self.ANCESTOR)
        assert "answer both" in str(refused.value)

    def test_another_refusal_adds_no_field(self):
        assert Refused("refused").fields() == {}

    def test_both_drivers_add_the_same_field_to_the_body(self):
        from analysis_service import api
        from webapp import main

        refused = Refused("refused", (self.PART, self.ANCESTOR))
        page = asyncio.run(main._refused(400, None, refused))
        problem = asyncio.run(api._refusal_response(400, None, refused))
        named = {"part": list(self.PART), "ancestor": list(self.ANCESTOR)}
        assert json.loads(page.body) == {"message": "refused", "contradiction": named}
        assert json.loads(problem.body)["contradiction"] == named
        assert json.loads(problem.body)["detail"] == "refused"


def held_file(
    store,
    name: str = "Kickoff call",
    text: str = "Ana: the API uses mTLS.",
    owner: str = OWNER,
) -> OwnerFile:
    """An Owner File of ``owner`` in ``store``, with a principal where none is held."""
    file = OwnerFile.create(owner_id=owner, name=name, kind="transcript", text=text)
    principal = asyncio.run(new_principal(store, owner))
    assert (
        asyncio.run(store.add_file(file, text, limit=100, principal=principal))
        == "added"
    )
    return file


class TestOwnerFileSources:
    """ADR 0119: a job copies each Owner File it names as a Source."""

    submit = TestSubmitting.submit

    def test_each_named_file_is_a_source_after_the_body_and_before_the_answers(self):
        store = InMemoryJobStore()
        file = held_file(store)
        link = LinkAnswer(principal="shoppers", element="entity:shopper")
        record = self.submit(
            operations(store), submission(owner_files=[file.id], links=[link])
        )
        assert [source.kind for source in record.sources] == [
            "description",
            "transcript",
            "answers",
        ]
        copy = record.sources[1]
        assert (copy.label, copy.text, copy.owner_file_id) == (
            file.name,
            "Ana: the API uses mTLS.",
            file.id,
        )
        assert copy.digest() == file.sha256
        assert record.sources[0].owner_file_id is None

    def test_a_file_alone_makes_a_job(self):
        store = InMemoryJobStore()
        file = held_file(store)
        record = self.submit(
            operations(store), submission(sources=[], owner_files=[file.id])
        )
        assert [source.owner_file_id for source in record.sources] == [file.id]

    def test_a_deleted_file_and_another_owners_file_get_one_refusal(self):
        store = InMemoryJobStore()
        gone = held_file(store)
        asyncio.run(store.delete_file(gone.id, OWNER))
        foreign = held_file(store, owner="bob")
        refusals = []
        for file in (gone, foreign):
            with pytest.raises(Refused) as refused:
                self.submit(operations(store), submission(owner_files=[file.id]))
            refusals.append(str(refused.value))
        assert refusals == [OWNER_FILE_GONE] * 2
        assert foreign.id not in OWNER_FILE_GONE

    def test_more_files_than_the_source_limit_are_refused_before_a_read(self):
        store = InMemoryJobStore()
        named = ["file-" + f"{n:032x}" for n in range(ROOMY.max_sources + 1)]
        with pytest.raises(SourcesOverLimit) as refused:
            self.submit(operations(store), submission(owner_files=named))
        assert refused.value.breach.rung == "count"

    def test_a_repeated_file_is_refused(self):
        store = InMemoryJobStore()
        file = held_file(store)
        with pytest.raises(Refused, match="twice"):
            self.submit(operations(store), submission(owner_files=[file.id, file.id]))

    def test_a_file_named_by_an_identity_with_no_principal_creates_none(self):
        store = InMemoryJobStore()
        with pytest.raises(Refused):
            self.submit(operations(store), submission(owner_files=["file-" + "a" * 32]))
        assert asyncio.run(store.principal(identity_of(OWNER))) is None

    def test_a_copy_counts_toward_the_byte_limit(self):
        store = InMemoryJobStore()
        file = held_file(store, text="x" * ROOMY.max_total_bytes)
        with pytest.raises(SourcesOverLimit) as refused:
            self.submit(operations(store), submission(owner_files=[file.id]))
        assert refused.value.breach.rung == "total"

    def test_a_copy_whose_name_is_a_body_label_is_a_repeated_label(self):
        store = InMemoryJobStore()
        file = held_file(store, name=valid_model_sources()[0].label)
        with pytest.raises(SourcesOverLimit) as refused:
            self.submit(operations(store), submission(owner_files=[file.id]))
        assert refused.value.breach.rung == "duplicate-label"

    def test_a_callers_owner_file_id_is_refused(self):
        forged = Source(
            kind="transcript",
            label="Forged",
            text="x",
            owner_file_id="file-" + "a" * 32,
        )
        with pytest.raises(Refused, match="composed by this service"):
            self.submit(
                operations(InMemoryJobStore()),
                submission(sources=[*valid_model_sources(), forged]),
            )

    def test_an_amended_job_keeps_the_copy_after_the_file_is_deleted(self):
        store = InMemoryJobStore()
        file = held_file(store)
        copy = Source(
            kind=file.kind,
            label=file.name,
            text="Ana: the API uses mTLS.",
            owner_file_id=file.id,
        )
        job = waiting(store)
        record = asyncio.run(store.get(job))
        asyncio.run(
            store.save(record.model_copy(update={"sources": [*record.sources, copy]}))
        )
        asyncio.run(store.delete_file(file.id, OWNER))
        starts = Starts()
        asyncio.run(
            operations(store).amend(
                job, OWNER, "Also a gateway.", revision=0, start=starts
            )
        )
        [(amended, _)] = starts.started
        assert copy in amended.sources
