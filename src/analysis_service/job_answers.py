"""The one writer of a job's answers, and the one starter of the job they start.

Every surface that takes a submission to a job reads the job through here,
builds its **Answer State** from the record, admits the submission, and
either stores the answers, a draft or a correction, or creates the **Resumed
Job** and starts it. The Answer State itself writes nothing
(:mod:`analysis_service.answer_state`); this module is what writes, so no
caller stores answers or starts a job by hand.

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
from typing import Any, NoReturn

import anyio.to_thread

from analysis_service import budgets
from analysis_service.answer_sets import NO_ANSWERS, AnswerSet, within_held_limits
from analysis_service.answer_state import (
    ANSWER_LIMITS,
    AlreadyResumed,
    Answers,
    AnswerState,
    SavedAnswers,
    SavedDraft,
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
    JOB_ID_PATTERN,
    Admission,
    Checkpoint,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    Resumption,
    same_schema,
)
from analysis_service.links import LinkAnswer, NoCatalogError, with_link_answers
from analysis_service.pipeline import entry_of
from analysis_service.principals import Identity, Principal, log_created
from analysis_service.questions import question_fallback
from analysis_service.report import SCHEMA_VERSION, Report
from analysis_service.report_conditions import corrected_findings
from analysis_service.sources import Source, SourceLimits
from analysis_service.system_model import SystemModel

logger = logging.getLogger(__name__)

__all__ = [
    "Answerable",
    "AnswersSaved",
    "Corrected",
    "DraftSaved",
    "JobAnswers",
    "JobDataMissing",
    "NoCatalog",
    "NoSuchJob",
    "NotAdmitted",
    "NotCompleted",
    "Refused",
    "RunnerFor",
    "RunnerUnavailable",
    "SaveConflict",
    "Start",
    "Started",
    "Submitter",
    "UnreadableSchema",
    "Withheld",
]

#: Looks a runner up for one framework selection and one graph entry.
RunnerFor = Callable[[Sequence[FrameworkName], Entry], PipelineRunner]
#: Schedules an admitted job's run on its runner. The driver decides how: a
#: request's background task, or a task on the process's event loop.
Start = Callable[[JobRecord, PipelineRunner], None]


class NoSuchJob(LookupError):
    """The owner owns no job with this ID. A foreign job reads the same, and so
    does every job where the caller's identity holds no principal."""

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


class UnreadableSchema(Exception):
    """The job's record has a schema version other than this service's (#1532).

    This service cannot parse what such a record holds, and no migration
    rewrites it, so the owner must submit the job again.
    """

    def __init__(self, version: str) -> None:
        super().__init__(
            f"the job was stored under schema version {version}, which this"
            f" service (schema version {SCHEMA_VERSION}) cannot read; submit the"
            " job again"
        )


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
                selection.name: selection.options
                for selection in record.whole_frameworks()
            },
            analyses=self.analyses,
            waiting=record.status == "awaiting-answers",
            final=record.final(),
            sources=record.whole_sources(),
            answers=record.answers,
            shown=record.shown_early,
            skipped=record.skipped_early,
            corrections=record.corrections,
            revision=record.revision,
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
                "revision": record.revision,
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
class AnswersSaved:
    """A waiting job's answers the job keeps. No model ran."""

    job_id: str


@dataclass(frozen=True)
class Started:
    """A job admitted and started: a submission, a Resumed Job or an amended job."""

    job_id: str
    status: JobStatus


@dataclass(frozen=True)
class Submitter:
    """The owner of a new submission: the owner ID its job names.

    ``new`` is the principal admission creates with the job, where the
    identity holds none yet, and ``None`` where it holds one.
    """

    owner_id: str
    new: Principal | None


@dataclass(frozen=True)
class Corrected:
    """A final report's corrections once a submission landed, and the findings
    they reach."""

    job_id: str
    corrections: list[FactAnswer]
    corrected_findings: list[str]
    #: The revision the next correction names.
    revision: int


@dataclass(frozen=True)
class JobAnswers:
    """One job store's answer operations, under one deployment's bounds.

    ``runner_for`` looks a runner up by selection and entry. ``limits`` bounds
    what a job may carry, ``ceiling`` how many jobs one owner may have in
    flight, and ``budget`` the tokens a window admits. ``carries_catalog``
    says whether a job here builds an assertion catalog. A link answer places
    a principal in that catalog, so a deployment without one refuses it
    (:class:`NoCatalog`); the answer's line also goes into the job's answers
    Source (:func:`~analysis_service.links.with_link_answers`). ``gate`` is the certification gate that withholds an
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

    async def owned(self, job_id: str, owner_id: str) -> JobRecord:
        """The job the owner owns, without its heavy fields, or :class:`NoSuchJob`.

        **The one check of a requested job ID, and every route reads a job
        through it.** An ID of another shape than
        :data:`~analysis_service.jobs.JOB_ID_PATTERN` reads as missing, and the
        store never sees it (#1528).
        """
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise NoSuchJob(job_id)
        record = await self.store.owned(job_id, owner_id)
        if record is None:
            raise NoSuchJob(job_id)
        return record

    async def missing(self, record: JobRecord, part: str) -> NoReturn:
        """Raise for the ``part`` of ``record`` that a read after :meth:`owned`
        did not find.

        **The one reader of "why is a part of an owned job missing".** A
        delete can remove the job after the first read, and then the job
        reads as missing: :class:`NoSuchJob`. Else the job's status promises
        the part, and :class:`JobDataMissing` reports this service's defect.
        """
        if await self.store.owned(record.id, record.owner_id) is None:
            raise NoSuchJob(record.id)
        logger.error("%s job %s holds no %s", record.status, record.id, part)
        raise JobDataMissing(record.id)

    async def parsable(self, job_id: str, owner_id: str) -> JobRecord:
        """The owned job, where this service can parse what its record holds.

        **The gate before every read that parses a stored record.** It raises
        :class:`UnreadableSchema` where :func:`~analysis_service.jobs.same_schema`
        refuses the record's version. Ownership is checked first, so a foreign
        job reads as missing and never as unreadable.
        """
        record = await self.owned(job_id, owner_id)
        if not same_schema(record.schema_version):
            raise UnreadableSchema(record.schema_version)
        return record

    async def answerable(self, job_id: str, owner_id: str) -> Answerable:
        """What this caller may ask or answer about, read once.

        **The one reader of "what does this job ask about".** Each part that
        the envelope leaves out is read by name, once, after :meth:`parsable`.
        """
        record = await self.parsable(job_id, owner_id)
        store = self.store
        # The envelope leaves the sources out, and the answers compose with them.
        sources = await store.sources(job_id, owner_id)
        if sources is None:
            await self.missing(record, "sources")
        record.sources = sources
        if record.status == "awaiting-answers":
            # The questions are read off what the head built, so they answer
            # to the head's verdict as a report answers to its run's.
            result = self.withheld(record)
            if result is not None:
                raise Withheld(result)
            held = await store.checkpoint(job_id, owner_id)
            if held is None:
                await self.missing(record, "checkpoint")
            resumed_by = await store.resumed_by(job_id, owner_id)
            return Answerable(
                record, held.system_model, held.assertions, [], resumed_by
            )
        self.servable(record)
        report = await store.report(job_id, owner_id)
        if report is None:
            await self.missing(record, "report")
        # The envelope leaves the resumption out, and it says whether the
        # report is final: a report its follow-up wrote asks nothing (ADR 0054).
        record.resumption = await store.resumption(job_id, owner_id)
        resumed_by = await store.resumed_by(job_id, owner_id)
        return Answerable(
            record,
            report.system_model,
            report.assertions,
            list(report.analyses),
            resumed_by,
            report,
        )

    async def questions(self, job_id: str, owner_id: str) -> dict[str, Any]:
        """Every question the job asks the submitter, derived at read time.

        The derivation grows with the report, and a read carries no admission
        bound, so it runs off the event loop.
        """
        answerable = await self.answerable(job_id, owner_id)
        return await anyio.to_thread.run_sync(answerable.payload)

    def submitted_sources(
        self, sources: Sequence[Source], links: Sequence[LinkAnswer]
    ) -> list[Source]:
        """A new job's sources with its link answers composed in, or a refusal.

        The same :class:`NoCatalog` refusal :meth:`answer` makes, and the
        ``ValueError`` of :func:`~analysis_service.links.with_link_answers`.
        The answers go only onto a job that has sources of its own: answers
        alone would carry the body past the ladder's empty-sources rung.
        """
        if links and not self.carries_catalog:
            raise NoCatalog()
        if not sources:
            return list(sources)
        return with_link_answers(sources, links)

    async def answer(
        self, job_id: str, owner_id: str, answers: Answers, *, start: Start
    ) -> DraftSaved | AnswersSaved | Started:
        """Keep the answers or a draft, or start the Resumed Job the answers start.

        A save keeps a waiting job's answers or a finished report's draft, and
        starts nothing. A send without ``save`` starts the Resumed Job, which
        is admitted as a new job against the ceiling and the budget, because
        its lanes and critics spend model calls.
        """
        if answers.given.links and not self.carries_catalog:
            raise NoCatalog()
        answerable = await self.answerable(job_id, owner_id)
        parent = answerable.record
        state = answerable.state()
        outcome = await anyio.to_thread.run_sync(
            partial(self._refusing, state.answer, answers, limits=self.limits)
        )
        if isinstance(outcome, SavedDraft):
            if not await self.store.save_draft(
                parent.id, owner_id, outcome.draft, outcome.revision
            ):
                raise SaveConflict(
                    "the report's follow-up has started, or another save landed"
                    " first; read the questions again"
                )
            # A draft runs nothing and spends no follow-up (ADR 0070).
            return DraftSaved(job_id=parent.id, revision=outcome.revision + 1)
        if isinstance(outcome, SavedAnswers):
            if not await self.store.save_answers(
                parent.id,
                owner_id,
                outcome.answers,
                outcome.shown,
                outcome.skipped,
                outcome.revision,
            ):
                raise SaveConflict(
                    "the job no longer waits on answers, or another save landed"
                    " first; read the questions again"
                )
            # A save never starts the analysis: a continue does.
            return AnswersSaved(job_id=parent.id)
        record = JobRecord.create(
            owner_id=owner_id,
            sources=outcome.sources,
            frameworks=parent.whole_frameworks(),
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
            parent_revision=parent.revision,
            reserved_tokens=budgets.estimate(
                outcome.sources, parent.whole_frameworks()
            ),
        )
        return await self.admit(record, start=start)

    async def amend(
        self,
        job_id: str,
        owner_id: str,
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
        answerable = await self.answerable(job_id, owner_id)
        parent = answerable.record
        state = answerable.state()
        amended = await anyio.to_thread.run_sync(
            partial(
                self._refusing, state.amend, text, revision=revision, limits=self.limits
            )
        )
        record = JobRecord.create(
            owner_id=owner_id,
            sources=amended.sources,
            frameworks=parent.whole_frameworks(),
            system_name=parent.system_name,
            ask_questions=True,
            amends=parent.id,
            parent_revision=parent.revision,
            carried=parent.held_answers(),
            reserved_tokens=budgets.estimate(
                amended.sources, parent.whole_frameworks()
            ),
        )
        return await self.admit(record, start=start)

    async def carried_from(
        self, job_id: str | None, owner_id: str, *, asks: bool
    ) -> AnswerSet:
        """The answers a new job carries from an earlier job of this owner (ADR 0079).

        ``None`` names no job and carries nothing. Another owner's job reads
        as missing, as every read does. A job that does not pause would never
        read what it carries, so it is refused one; so is a job that holds no
        answer, or more than one job may hold.
        """
        if job_id is None:
            return NO_ANSWERS
        if not asks:
            raise Refused(
                "answers from an earlier analysis are offered as questions;"
                " turn the questions on to carry them"
            )
        earlier = await self.parsable(job_id, owner_id)
        held = earlier.held_answers()
        if held.empty:
            raise Refused("the earlier analysis holds no answers to carry")
        return self._refusing(within_held_limits, held)

    async def correct(
        self,
        job_id: str,
        owner_id: str,
        facts: Sequence[FactAnswer],
        *,
        revision: int | None,
    ) -> Corrected:
        """Correct a final report's answers, and run nothing (ADR 0054).

        The corrections are kept beside the report; the report is not
        rewritten. ``revision`` is the revision the correction read, so of
        two corrections that read one revision only the first lands (ADR 0090).
        """
        answerable = await self.answerable(job_id, owner_id)
        parent = answerable.record
        state = answerable.state()
        corrections = await anyio.to_thread.run_sync(
            partial(self._refusing, state.correct, facts, revision=revision)
        )
        if not await self.store.save_corrections(
            parent.id, owner_id, corrections, parent.revision
        ):
            raise SaveConflict(
                "the job's report is no longer final, or another correction"
                " landed first; read the questions again"
            )
        return Corrected(
            job_id=parent.id,
            revision=parent.revision + 1,
            corrections=corrections,
            corrected_findings=list(
                corrected_findings(
                    answerable.analyses, parent.answers.facts, corrections
                )
            ),
        )

    async def held_owner_id(self, identity: Identity) -> str | None:
        """The owner ID of ``identity``'s principal, or ``None`` where it holds
        none.

        **The one read of a caller's owner ID.** A read creates no principal.
        """
        held = await self.store.principal(identity)
        return None if held is None else held.owner_id

    async def owner_id(self, identity: Identity, job_id: str) -> str:
        """The owner ID of ``identity``'s principal, for a read of ``job_id``.

        Where ``identity`` holds no principal, it owns no job, so ``job_id``
        raises :class:`NoSuchJob`.
        """
        owner_id = await self.held_owner_id(identity)
        if owner_id is None:
            raise NoSuchJob(job_id)
        return owner_id

    async def submitter(self, identity: Identity) -> Submitter:
        """The owner a submission by ``identity`` names, read once.

        An identity with no principal gets a new one here, which nothing
        keeps until :meth:`admit` admits its first job.
        """
        held = await self.store.principal(identity)
        if held is not None:
            return Submitter(owner_id=held.owner_id, new=None)
        new = Principal.new(identity)
        return Submitter(owner_id=new.owner_id, new=new)

    async def admit(
        self,
        record: JobRecord,
        *,
        start: Start,
        principal: Principal | None = None,
    ) -> Started:
        """Admit one new job against the ceiling and the budget, and start it.

        **The one admission.** A submission, a Resumed Job and an amended job
        are all new jobs: each takes an in-flight slot and a share of the
        token budget, and each is refused the same way when it cannot have
        one. Every refusal is logged, because it is the only place these
        bounds are observable from outside the caller they refused, and the
        log carries the deployment-wide figure a response withholds.

        ``principal`` is the new principal of a first submission
        (:attr:`Submitter.new`). The store creates it with the job, and this
        method writes the one log line that records it.
        """
        try:
            runner = self.runner_for(record.selection(), entry_of(record))
        except ConfigError as exc:
            logger.error("cannot build a runner for job %s: %s", record.id, exc)
            raise RunnerUnavailable() from None
        admission = await self.store.reserve(
            record, ceiling=self.ceiling, budget=self.budget, principal=principal
        )
        if admission.outcome != "admitted":
            logger.warning(
                "owner %s refused (%s): %d in flight of %d, %d tokens of %d"
                " in this owner's window, %d of %d across the deployment",
                record.owner_id,
                admission.outcome,
                admission.active,
                self.ceiling,
                admission.owner_tokens,
                self.budget.max_tokens_per_window,
                admission.global_tokens,
                self.budget.global_max_tokens_per_window,
            )
            raise NotAdmitted(admission, self.ceiling)
        if principal is not None:
            log_created(principal)
        start(record, runner)
        return Started(job_id=record.id, status=record.status)

    @staticmethod
    def _refusing(rule: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one answer rule, and raise its ``ValueError`` as a :class:`Refused`.

        The refusals a driver maps on their own pass through unchanged:
        :class:`~analysis_service.answer_state.AlreadyResumed` and
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
