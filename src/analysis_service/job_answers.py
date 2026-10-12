"""The one writer of a job's answers, and the one starter of the job they start.

Every surface that takes a submission to a job reads the job through here,
builds its **Answer State** from the record, admits the submission, and
either stores a round, a draft or a correction, or creates the **Resumed
Job** and starts it. The Answer State itself writes nothing
(:mod:`analysis_service.answer_round`); this module is what writes, so no
caller stores a round or starts a job by hand.

A refusal here is typed and never a status code. A driver maps each one to
its own transport: the ``/v1`` routes to a problem response, the first-run
app to a page message. The messages name the submitter's own choices, so a
driver may show them.

The start of a job is injected. The one admission looks the runner up
before it reserves a slot, because a graph is built on first use per
selection and a build that raised after the reservation would hold a slot
no task releases. The driver's ``start`` then schedules
:func:`~analysis_service.jobs.execute_job` however its process runs tasks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio.to_thread

from analysis_service import budgets
from analysis_service.answer_round import (
    ANSWER_LIMITS,
    AlreadyResumed,
    Answers,
    AnswerState,
    SavedDraft,
    SavedRound,
)
from analysis_service.assertions import AssertionRecord
from analysis_service.budgets import BudgetPolicy
from analysis_service.certification import CertificationGate, CertifyResult
from analysis_service.claims import (
    FrameworkAnalysis,
    FrameworkName,
    UnknownKey,
    UnknownRef,
)
from analysis_service.errors import ConfigError
from analysis_service.fact_answers import FactAnswer
from analysis_service.fact_writes import LineageConflict
from analysis_service.graph import Entry
from analysis_service.jobs import (
    Admission,
    Checkpoint,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    Resumption,
)
from analysis_service.links import NoCatalogError
from analysis_service.pipeline import entry_of
from analysis_service.questions import question_fallback
from analysis_service.report import Report
from analysis_service.report_conditions import corrected_findings
from analysis_service.sources import SourceLimits
from analysis_service.system_model import SystemModel

logger = logging.getLogger(__name__)

__all__ = [
    "Answerable",
    "Corrected",
    "DraftSaved",
    "JobAnswers",
    "JobDataMissing",
    "NoCatalog",
    "NoSuchJob",
    "NotAdmitted",
    "NotCompleted",
    "Refused",
    "RoundSaved",
    "RunnerFor",
    "RunnerUnavailable",
    "SaveConflict",
    "Start",
    "Started",
    "Withheld",
]

#: Looks a runner up for one framework selection and one graph entry.
RunnerFor = Callable[[Sequence[FrameworkName], Entry], PipelineRunner]
#: Schedules an admitted job's run on its runner. The driver decides how: a
#: request's background task, or a task on the process's event loop.
Start = Callable[[JobRecord, PipelineRunner], None]


class NoSuchJob(LookupError):
    """The subject owns no job with this ID. A foreign job reads the same."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__("job not found")


class JobDataMissing(RuntimeError):
    """A job in a status that promises a field, read without it. This
    service's defect, never the caller's."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__("an internal error occurred")


class NotCompleted(Exception):
    """A read of what the report serves, before the job completed."""

    def __init__(self, status: JobStatus) -> None:
        self.status = status
        super().__init__(
            f"job status is {status!r}; the report exists only once the job is"
            " completed"
        )


class Withheld(Exception):
    """The deployment's gate withholds the job's output.

    Its execution identity is not blessed by the deployment's manifest. The
    result names the unblessed nodes and the unexercised tiers, and never the
    output: enough to act on, nothing that leaks past the gate.
    """

    def __init__(self, result: CertifyResult) -> None:
        self.result = result
        super().__init__(
            "the job's output is withheld: its execution identity is not blessed"
            " by this deployment's manifest"
        )


class NoCatalog(ValueError):
    """A link answer to a deployment that builds no assertion catalog."""

    def __init__(self) -> None:
        super().__init__(
            "this deployment builds no assertion catalog, so nothing would read a"
            " link answer"
        )


class Refused(ValueError):
    """The answer rules refused the submission. The message names the
    submitter's own choices, so a driver may show it.

    ``contradiction`` is set where the answers would leave a capability
    present under an absent ancestor: the part's question key and the
    ancestor's, so a page can offer to answer both in one save.
    """

    def __init__(
        self, message: str, contradiction: tuple[UnknownKey, UnknownKey] | None = None
    ) -> None:
        super().__init__(message)
        self.contradiction = contradiction

    def fields(self) -> dict[str, object]:
        """What a driver adds to the refusal's body beside its message."""
        if self.contradiction is None:
            return {}
        part, ancestor = self.contradiction
        return {"contradiction": {"part": list(part), "ancestor": list(ancestor)}}


class RunnerUnavailable(Exception):
    """The deployment cannot build a runner now: its configuration is incomplete.

    The cause names environment variables, so it goes to the log and never
    to a caller.
    """

    def __init__(self) -> None:
        super().__init__(
            "this deployment cannot run an analysis now: its configuration is"
            " incomplete"
        )


class NotAdmitted(Exception):
    """The store refused the job a slot or a share of the budget."""

    def __init__(self, admission: Admission, ceiling: int) -> None:
        self.admission = admission
        self.ceiling = ceiling
        super().__init__(f"job not admitted: {admission.outcome}")


class SaveConflict(Exception):
    """A save landed on a job that moved: another save, a start, or a status
    change came first. Read the questions again."""


@dataclass(frozen=True)
class Answerable:
    """What one job asks about, read once from the store.

    A job waiting on answers holds its checkpoint and has no findings yet; a
    finished job answers from its report. ``resumed_by`` names the job this
    one's answers started while it locks this one.
    """

    record: JobRecord
    model: SystemModel
    assertions: AssertionRecord | None
    analyses: list[FrameworkAnalysis]
    resumed_by: str | None
    #: The finished job's report, the gate's verdict already read; a waiting
    #: job has none.
    report: Report | None = None

    def state(self) -> AnswerState:
        """The job's **Answer State**: everything its questions and answers read."""
        record = self.record
        return AnswerState(
            checkpoint=Checkpoint(system_model=self.model, assertions=self.assertions),
            frameworks={
                selection.name: selection.options for selection in record.frameworks
            },
            analyses=self.analyses,
            waiting=record.status == "awaiting-answers",
            final=record.final(),
            sources=record.sources,
            answers=record.answers,
            shown=record.shown_early,
            skipped=record.skipped_early,
            corrections=record.corrections,
            revision=record.round_revision,
            resumed_by=self.resumed_by,
            draft=record.draft,
            carried_in=record.carried,
        )

    def payload(self) -> dict[str, Any]:
        """Every question the job asks, with its drafts, corrections and limits."""
        record = self.record
        state = self.state()
        return (
            state.questions.to_json()
            | state.carried.dropped_json(self.model)
            | {
                "fallback": question_fallback(self.analyses).to_json(),
                "revision": record.round_revision,
                "draft_links": [
                    link.model_dump(mode="json") for link in record.draft.links
                ],
                "draft_facts": [
                    fact.model_dump(mode="json") for fact in record.draft.facts
                ],
                "answer_limits": dict(ANSWER_LIMITS),
                "corrections": [
                    fact.model_dump(mode="json") for fact in record.corrections
                ],
                "corrected_findings": list(
                    corrected_findings(
                        self.analyses, record.answers.facts, record.corrections
                    )
                ),
            }
        )


@dataclass(frozen=True)
class DraftSaved:
    """A finished report's draft the job keeps. No model ran. ``revision`` is
    the revision the draft's next save names."""

    job_id: str
    revision: int


@dataclass(frozen=True)
class RoundSaved:
    """A waiting job's round the job keeps. No model ran."""

    job_id: str


@dataclass(frozen=True)
class Started:
    """A job admitted and started: a submission, a Resumed Job or an amended job."""

    job_id: str
    status: JobStatus


@dataclass(frozen=True)
class Corrected:
    """A final report's corrections once a submission landed, and the findings
    they reach."""

    job_id: str
    corrections: list[FactAnswer]
    corrected_findings: list[str]


@dataclass(frozen=True)
class JobAnswers:
    """One job store's answer operations, under one deployment's bounds.

    ``runner_for`` looks a runner up by selection and entry. ``limits`` bounds
    what a job may carry, ``ceiling`` how many jobs one subject may have in
    flight, and ``budget`` the tokens a window admits. ``carries_catalog``
    says whether a job here builds an assertion catalog, the only reader of
    a link answer. ``gate`` is the certification gate that withholds an
    unblessed report; ``None`` where the runner is a stand-in and nothing was
    certified.
    """

    store: JobStore
    runner_for: RunnerFor
    limits: SourceLimits
    ceiling: int
    budget: BudgetPolicy
    carries_catalog: bool
    gate: CertificationGate | None = None

    def withheld(self, record: JobRecord) -> CertifyResult | None:
        """The certification that withholds this job's output, or ``None``.

        Withholding rather than failing is deliberate: a ``failed`` job
        carries no report, and the fingerprints that prove the drift live in
        the report, so failing would destroy the evidence an operator needs.
        """
        result = record.certification
        if self.gate is None or result is None or not self.gate.withholds(result):
            return None
        return result

    def servable(self, record: JobRecord) -> None:
        """Raise where this job's report may not be served.

        **The one reader of "may this report be served".** The report route
        and every read derived from the report ask it, so a report the gate
        withholds cannot reach a caller through a view of it.
        """
        if record.status != "completed":
            raise NotCompleted(record.status)
        result = self.withheld(record)
        if result is not None:
            raise Withheld(result)

    async def owned(self, job_id: str, subject: str) -> JobRecord:
        """The job the subject owns, without its heavy fields, or :class:`NoSuchJob`."""
        record = await self.store.owned(job_id, subject)
        if record is None:
            raise NoSuchJob(job_id)
        return record

    async def answerable(self, job_id: str, subject: str) -> Answerable:
        """What this caller may ask or answer about, read once.

        **The one reader of "what does this job ask about".** Each heavy field
        is read by name, once: the envelope carries neither, so a status read
        never pays for them.
        """
        record = await self.owned(job_id, subject)
        store = self.store
        if record.status == "awaiting-answers":
            # The questions are read off what the head built, so they answer
            # to the head's verdict as a report answers to its run's.
            result = self.withheld(record)
            if result is not None:
                raise Withheld(result)
            held = await store.checkpoint(job_id, subject)
            if held is None:
                logger.error("waiting job %s holds no checkpoint", record.id)
                raise JobDataMissing(record.id)
            resumed_by = await store.resumed_by(job_id, subject)
            return Answerable(
                record, held.system_model, held.assertions, [], resumed_by
            )
        self.servable(record)
        report = await store.report(job_id, subject)
        if report is None:
            logger.error("completed job %s has no report attached", record.id)
            raise JobDataMissing(record.id)
        # The envelope leaves the resumption out, and it says whether the
        # report is final: a report its follow-up wrote asks nothing (ADR 0054).
        record.resumption = await store.resumption(job_id, subject)
        resumed_by = await store.resumed_by(job_id, subject)
        return Answerable(
            record,
            report.system_model,
            report.assertions,
            list(report.analyses),
            resumed_by,
            report,
        )

    async def questions(self, job_id: str, subject: str) -> dict[str, Any]:
        """Every question the job asks the submitter, derived at read time.

        The derivation grows with the report, and a read carries no admission
        bound, so it runs off the event loop.
        """
        answerable = await self.answerable(job_id, subject)
        return await anyio.to_thread.run_sync(answerable.payload)

    async def answer(
        self, job_id: str, subject: str, answers: Answers, *, start: Start
    ) -> DraftSaved | RoundSaved | Started:
        """Keep a round or a draft, or start the Resumed Job the answers start.

        A save keeps a waiting job's round or a finished report's draft, and
        starts nothing. A send without ``save`` starts the Resumed Job, which
        is admitted as a new job against the ceiling and the budget, because
        its lanes and critics spend model calls.
        """
        if answers.given.links and not self.carries_catalog:
            raise NoCatalog()
        answerable = await self.answerable(job_id, subject)
        parent = answerable.record
        state = answerable.state()
        outcome = await anyio.to_thread.run_sync(
            partial(self._refusing, state.answer, answers, limits=self.limits)
        )
        if isinstance(outcome, SavedDraft):
            if not await self.store.save_draft(
                parent.id, subject, outcome.draft, outcome.revision
            ):
                raise SaveConflict(
                    "the report's follow-up has started, or another save landed"
                    " first; read the questions again"
                )
            # A draft runs nothing and spends no follow-up (ADR 0070).
            return DraftSaved(job_id=parent.id, revision=outcome.revision + 1)
        if isinstance(outcome, SavedRound):
            if not await self.store.save_round(
                parent.id,
                subject,
                outcome.answers,
                outcome.shown,
                outcome.skipped,
                outcome.revision,
            ):
                raise SaveConflict(
                    "the job no longer waits on answers, or another save landed"
                    " first; read the questions again"
                )
            # A saved round never starts the analysis: a continue does.
            return RoundSaved(job_id=parent.id)
        record = JobRecord.create(
            owner_subject=subject,
            sources=outcome.sources,
            frameworks=parent.frameworks,
            system_name=parent.system_name,
            answers=outcome.answers,
            shown_early=outcome.shown,
            skipped_early=list(outcome.skipped),
            resumption=Resumption(
                parent_id=parent.id,
                checkpoint=outcome.checkpoint,
                certification=parent.certification,
                follow_up=outcome.follow_up,
            ),
            parent_revision=parent.round_revision,
            reserved_tokens=budgets.estimate(outcome.sources, parent.frameworks),
        )
        return await self.admit(record, start=start)

    async def amend(
        self,
        job_id: str,
        subject: str,
        text: str,
        *,
        revision: int | None,
        start: Start,
    ) -> Started:
        """Start the job that extracts a paused job's description again (ADR 0072).

        The new job reads the paused job's sources with the amendment added,
        pauses again, and carries the paused job's answers, which its own
        questions take where they still ask them.
        """
        answerable = await self.answerable(job_id, subject)
        parent = answerable.record
        state = answerable.state()
        amended = await anyio.to_thread.run_sync(
            partial(
                self._refusing, state.amend, text, revision=revision, limits=self.limits
            )
        )
        record = JobRecord.create(
            owner_subject=subject,
            sources=amended.sources,
            frameworks=parent.frameworks,
            system_name=parent.system_name,
            ask_questions=True,
            amends=parent.id,
            parent_revision=parent.round_revision,
            carried=amended.answers,
            reserved_tokens=budgets.estimate(amended.sources, parent.frameworks),
        )
        return await self.admit(record, start=start)

    async def correct(
        self, job_id: str, subject: str, facts: Sequence[FactAnswer]
    ) -> Corrected:
        """Correct a final report's answers, and run nothing (ADR 0054).

        The corrections are kept beside the report; the report is not
        rewritten.
        """
        answerable = await self.answerable(job_id, subject)
        parent = answerable.record
        state = answerable.state()
        corrections = await anyio.to_thread.run_sync(
            partial(self._refusing, state.correct, facts)
        )
        if not await self.store.save_corrections(parent.id, subject, corrections):
            raise SaveConflict("the job's report is no longer final")
        return Corrected(
            job_id=parent.id,
            corrections=corrections,
            corrected_findings=list(
                corrected_findings(
                    answerable.analyses, parent.answers.facts, corrections
                )
            ),
        )

    async def admit(self, record: JobRecord, *, start: Start) -> Started:
        """Admit one new job against the ceiling and the budget, and start it.

        **The one admission.** A submission, a Resumed Job and an amended job
        are all new jobs: each takes an in-flight slot and a share of the
        token budget, and each is refused the same way when it cannot have
        one. Every refusal is logged, because it is the only place these
        bounds are observable from outside the caller they refused, and the
        log carries the deployment-wide figure a response withholds.
        """
        try:
            runner = self.runner_for(record.selection(), entry_of(record))
        except ConfigError as exc:
            logger.error("cannot build a runner for job %s: %s", record.id, exc)
            raise RunnerUnavailable() from None
        admission = await self.store.reserve(
            record, ceiling=self.ceiling, budget=self.budget
        )
        if admission.outcome != "admitted":
            logger.warning(
                "subject %s refused (%s): %d in flight of %d, %d tokens of %d"
                " in this subject's window, %d of %d across the deployment",
                record.owner_subject,
                admission.outcome,
                admission.active,
                self.ceiling,
                admission.subject_tokens,
                self.budget.max_tokens_per_window,
                admission.global_tokens,
                self.budget.global_max_tokens_per_window,
            )
            raise NotAdmitted(admission, self.ceiling)
        start(record, runner)
        return Started(job_id=record.id, status=record.status)

    @staticmethod
    def _refusing(rule: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one answer rule, and raise its ``ValueError`` as a :class:`Refused`.

        The refusals a driver maps on their own pass through unchanged:
        :class:`~analysis_service.answer_round.AlreadyResumed` and
        :class:`~analysis_service.links.NoCatalogError` are conflicts, and
        the others are not ``ValueError`` at all. The ``ValueError`` that
        names the submitter's own choices is re-typed, so a driver maps it
        without catching every ``ValueError`` in reach.
        """
        try:
            return rule(*args, **kwargs)
        except (AlreadyResumed, NoCatalogError):
            raise
        except LineageConflict as exc:
            contradiction = (
                UnknownRef(capability=exc.part).key,
                UnknownRef(capability=exc.ancestor).key,
            )
            raise Refused(str(exc), contradiction) from exc
        except ValueError as exc:
            raise Refused(str(exc)) from exc
