"""The lite first-run web app — route step 3.

Start it from a clone::

    uv run python webapp/main.py

Its Settings page sets the models and the keys.

It embeds the service in process: its jobs live in a service job store, and
:mod:`analysis_service.job_answers` admits, stores and starts them for this
app as it does for ``/v1``. The store is the one the service's backend
settings select, and ``memory`` where ``ANALYSIS_JOB_STORE`` is unset. It does
not go through the ``/v1`` HTTP surface, so it needs no bearer token and no
CORS, and it runs real models against real prose, because there is no
credential-free path here.
The local workspace provides description entry, grouped questions, and a bounded
session library of analyses and reports. Templates and native JavaScript keep
the frontend dependency-free; the engine remains the reader of answer rules.

The security posture is deliberate throughout:

* Loopback only. ``127.0.0.1`` is hard-bound, with no flag and no override. The
  no-auth and no-CORS posture is safe only there, because anything reachable is
  an unauthenticated proxy to somebody else's vendor bill. Remote,
  authenticated access is exactly what ``/v1`` already exists for.
* No job input touches model identity. Tier, vendor, model and sampling come
  from the deployment's settings alone. A submission that selected a model
  would be control over what runs and what it costs (A01, LLM10). The Settings
  page changes the settings, and its save runs under the loopback bind, the
  ``Host`` check and the ``Sec-Fetch-Site`` check of every writing route. The
  framework picker is a different thing: it selects which framework analyses the
  text, rather than which model does the analysing. It does change what a
  submission can spend, because each selected framework runs its own nodes. The
  one-run-at-a-time gate and the loopback bind are what bound that.
* The picker is an allowlist, checked server-side (A01). A submitted name must
  be one this install carries, and a submitted option must satisfy that
  package's own options model, both before an engine exists. The form's
  checkboxes decide nothing: the one submission
  (:meth:`~analysis_service.job_answers.JobAnswers.submit`) re-derives the
  selection from the carried list, so an invented name, a repeated one and a
  reordered pair are refused or normalised rather than reaching the graph.
* The injection point is the whole trust boundary. A report carries the
  submitter's own prose, so every ``<`` is escaped to ``\u003c`` before the JSON
  enters the viewer's ``<script>`` block. Otherwise a description containing
  ``</script>`` closes it, and the rest parses as HTML (A05, LLM05). See
  :func:`render_report`.
* Untrusted text never reaches ``innerHTML`` on any page. It renders as
  ``textContent``, or as constructed DOM nodes, so there is no escape helper to
  forget to call. An escape that a caller forgets fails silently, so no page
  uses one. This is the primary control, and the CSP
  below is defence in depth behind it. The form page is included: a source label
  and a validator message both carry submitter bytes onto it over SSE, and both
  land as text nodes. An uploaded transcript or diagram is a file only inside
  the browser: the page checks its size, decodes it as strict UTF-8 and refuses
  a control character before it keeps the text, and ``/analyze`` receives that
  text as a ``transcript`` or ``diagram`` source in the JSON body. No route takes a file, so
  no file name, content type or multipart body reaches the server.
  Server-side, the two f-string pages escape through
  :func:`~webapp.page.escape`, which is quote-safe, so where a value lands is
  not part of whether the escape is adequate.
* CSRF: each ``POST``, ``PUT`` and ``DELETE`` route requires
  ``Sec-Fetch-Site: same-origin`` through :func:`_cross_origin`. The header
  is browser-set and unspoofable from script, and it is checked before anything
  else, so a cross-origin caller cannot even start a run. The check is
  :func:`~webapp.page.is_same_origin`, which the two eval-side apps share
  through :func:`~webapp.page.refuse_cross_origin`. That is one spelling of the
  header name and the accepted value, rather than one per endpoint.
* No page here can be framed. Each policy closes ``frame-ancestors``, which does
  not fall back to ``default-src``, however total the rest of the policy reads.
  It is the control the origin check rests on rather than a second opinion about
  it: a press inside somebody else's frame reaches the app as same-origin,
  because it really does come from the app's own page.
* One run at a time (LLM10). A second submission is refused with a message,
  rather than queued and held open.
* A strict nonce CSP on every page. That is ``default-src 'none'`` with a
  per-response nonce for each inline block, and no ``'unsafe-inline'`` anywhere.
  That is why the viewer carries no ``style=""`` attributes and sets generated
  colours through ``element.style.*``, which is a CSSOM write CSP does not
  govern. No page loads an external resource or carries an ``on*=`` handler, so
  ``'none'`` breaks nothing. Each policy grants what its own page does and
  nothing else: the report page reaches only its own origin, to post to
  ``/answer/{run}`` and ``/correct/{run}``, the form page reaches only its own
  origin, through ``connect-src 'self'`` for ``/example``, ``/analyze``,
  ``/answer/{run}``, ``/amend/{run}``, ``/events/{run}`` and
  ``/workspace/runs``, the Settings page reaches only its own origin, to post
  to ``/settings`` and ``/settings/rows``, and the diagnostic page runs no
  script, so it is granted no ``script-src`` at all. Each page declares what it does as a
  :class:`~webapp.page.Grants`, and the policy follows from the declaration
  rather than from a string this file keeps. A page and its policy are built
  together as a :class:`~webapp.page.RenderedPage` and served through
  :func:`~webapp.page.response`, so serving HTML without its header is
  unspellable rather than merely discouraged.
* ``nosniff``, ``no-referrer`` and ``no-store`` on every response, HTML or not,
  applied by :class:`~webapp.page.SecurityHeaders`. Content sniffing is what
  would let a browser treat ``/example``'s ``text/plain`` prose as something
  else, the referrer policy keeps a run id out of outbound ``Referer`` headers,
  and ``no-store`` keeps a page built by one process out of the cache a later
  process serves into.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import (
    AsyncIterator,
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, TypeVar, get_args
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from analysis_service import (
    ConfigError,
    FrameworkName,
    FrameworkSelection,
    Report,
    Source,
)
from analysis_service.answer_bodies import (
    BodyRefused,
    read_amendment,
    read_answers,
    read_corrections,
)
from analysis_service.answer_forms import (
    answer_choices,
    answer_form,
    answer_limit,
    answer_suggestions,
    facets_json,
)
from analysis_service.answer_shares import SharedAnswer
from analysis_service.answer_state import (
    ANSWER_LIMITS,
    AlreadyResumed,
    AnswerState,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.api import (
    JOB_REFUSAL_STATUS,
    FrameworkRequest,
    JobSubmission,
    max_body_bytes,
    max_control_body_bytes,
)
from analysis_service.budgets import BudgetPolicy
from analysis_service.certification import CertificationGate
from analysis_service.claims import UNIT_VERDICT_WORDS, VERDICT_LABELS, UnknownKey
from analysis_service.config_files import (
    SETTINGS_FILE,
    SettingsFileError,
    settings_dir,
)
from analysis_service.controls import (
    CONTROL_ID,
    CONTROL_ID_PATTERN,
    EVIDENCE_ID_PATTERN,
    MAX_CONTROLS_CEILING,
    Control,
    ControlLimits,
    ControlScope,
    EvidenceRefusal,
    evidence_too_large,
)
from analysis_service.critic import SEVERITY_ORDER
from analysis_service.deployment import Deployment
from analysis_service.diagrams import install_reader, reader_fault
from analysis_service.element_hues import element_hues
from analysis_service.engine import DEFAULT_CALLER
from analysis_service.errors import refusal_text
from analysis_service.fact_answers import (
    FACET_ANSWERS,
    FactAnswer,
    SkipKey,
    answer_facets,
    asked_facets,
    attribute_place,
    fact_label,
    merged_facts,
)
from analysis_service.fixes import fixes_by_framework
from analysis_service.frameworks import PACKAGES, package_for
from analysis_service.job_answers import (
    Answerable,
    DraftSaved,
    JobAnswers,
    NoSuchJob,
    NotAdmitted,
    NotCompleted,
    Refused,
    RunnerFor,
    Start,
    Started,
    Submission,
    UnreadableSchema,
    Withheld,
)
from analysis_service.job_controls import (
    RULING_LISTS,
    ControlScopeEntry,
    SectionEntry,
    controls_section,
    controls_used,
    section_summary,
)
from analysis_service.job_state import (
    EndedJobState,
    JobState,
    StoredRefusal,
    job_state,
)
from analysis_service.jobs import (
    DeleteRefusal,
    Envelope,
    JobEvent,
    JobRecord,
    JobStore,
    PipelineRunner,
    RefusalOutcome,
    StopSignal,
    ended,
    event_frames,
    execute_job,
    parent_of,
    readable,
)
from analysis_service.links import (
    LinkAnswer,
    components,
    fold_principal,
)
from analysis_service.mermaid import styled_mermaid
from analysis_service.model_tiers import ModelTierConfig
from analysis_service.open_facts import (
    gaps,
    reference_labels,
)
from analysis_service.owner_files import (
    NO_TEXT,
    NOT_PLAIN_TEXT,
    NOT_UTF_8,
    OWNER_FILE_ID_PATTERN,
    OwnerFile,
    name_in_use,
    over_limit,
    too_large,
    upload_refusal,
)
from analysis_service.principals import Identity, Principal, log_created
from analysis_service.questions import question_fallback
from analysis_service.report_changes import report_changes
from analysis_service.report_conditions import conditions, corrected_findings
from analysis_service.selection import SelectionError
from analysis_service.settings_variables import deployment_env
from analysis_service.sources import (
    ANSWERS_LABEL,
    FORMATS,
    MAX_LABEL_CHARS,
    MAX_SOURCES_CEILING,
    MAX_SYSTEM_NAME_CHARS,
    OWNER_FILE_ID,
    SourceFormat,
    SourceKind,
    SourceLimits,
    clean_system_name,
)
from analysis_service.store_backends import (
    audited_delete,
    store_lifespan,
)
from analysis_service.system_model import ELEMENT_TYPE_NOUNS
from analysis_service.vendors import (
    CREDENTIAL_MODE_NOTES,
    VendorName,
    missing_sdk,
    vendor_for,
)
from webapp.controls_page import (
    ControlBody,
    ControlEdit,
    ControlSave,
    control_view,
    controls_full,
    page_data,
    save_refusal,
    saved_control,
)
from webapp.page import (
    LOOPBACK_HOSTS,
    WORKSPACE_STYLE,
    Grants,
    RenderedPage,
    SecurityHeaders,
    client_script,
    client_scripts,
    escape,
    is_same_origin,
    render,
    response,
    script_json,
)
from webapp.page_words import (
    FORM_WORDS,
    REPORT_WORDS,
    STAGE_COUNT,
    change_line,
    island_tables,
    progress_line,
)
from webapp.report_export import report_csv, report_markdown
from webapp.settings_save import (
    MAX_SAVE_BYTES,
    Refusal,
    SaveConflict,
    Saved,
    SaveRefused,
    apply_save,
    parse_changes,
    parse_save,
    restart_keys,
)
from webapp.settings_view import (
    build_app_store,
    settings_view,
    store_shown,
    with_store_defaults,
)

logger = logging.getLogger("webapp")

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEWER = Path(__file__).resolve().parent / "report_view.html"
SAMPLE = REPO_ROOT / "examples" / "orders.md"

HOST = "127.0.0.1"
PORT = 8000

#: The most jobs the app holds. :class:`Workspace` says which job it deletes
#: to stay inside the bound.
MAX_RUNS = 20

_STILL_RUNNING = "This analysis is still running. Stop it, then delete it."

_NOT_RUNNING = "This analysis is not running."

#: How long the stop route waits for a stopped run to end. A run ends when
#: its graph's cancellation finishes, so the wait is short.
STOP_WAIT_SECONDS = 10.0

#: The report page loads nothing external. It reaches its own origin to post
#: answers to ``/answer/{run}`` and corrections to ``/correct/{run}``.
_REPORT_GRANTS = Grants(script=True, style=True, connect=True)

#: The form page calls ``/example``, ``/analyze``, ``/answer/{run}``,
#: ``/amend/{run}``, ``/events/{run}`` and ``/workspace/runs``, so it needs its
#: own origin. ``form-action`` stays closed even though the page carries a
#: ``<form>`` — the submit handler is ``preventDefault()``-ed and posts through
#: fetch, so a navigation away from this form is something going wrong.
_FORM_GRANTS = Grants(script=True, style=True, connect=True)

#: The diagnostic page runs no script and makes no request: it is one style
#: block and static prose. A page that has nothing to authorise should not
#: carry the grant that would authorise one.
_DIAGNOSTIC_GRANTS = Grants(style=True)

#: The Settings page draws its table from the data in the page, and posts a
#: save to ``/settings``.
_SETTINGS_GRANTS = Grants(script=True, style=True, connect=True)

#: The Files page calls ``/workspace/files`` to list, add and delete files.
_FILES_GRANTS = Grants(script=True, style=True, connect=True)

#: The Controls page calls ``/workspace/controls`` to list, add, replace and
#: delete Controls.
_CONTROLS_GRANTS = Grants(script=True, style=True, connect=True)

#: The label of each Source that a job of this app gets beside its Owner
#: Files, by kind: the three that the form page makes, which it reads from
#: here, and the answers Source that the service composes. An upload to Files
#: refuses each one as a name, because a job that held both would repeat a
#: label (#2062).
PAGE_SOURCE_LABELS: dict[SourceKind, str] = {
    "description": "Pasted description",
    "transcript": "Uploaded transcript",
    "diagram": "Uploaded diagram",
    "answers": ANSWERS_LABEL,
}
_RESERVED_NAMES = frozenset(PAGE_SOURCE_LABELS.values())

#: The identity of this app's one local operator, who owns every job it holds.
#: It is the caller the engine names for an in-process run, under the reserved
#: local issuer, so no bearer token can name it.
OPERATOR = Identity.local(DEFAULT_CALLER)


class WorkspaceFull(Exception):
    """Every job the workspace holds runs or waits for answers, so none can make
    room."""


@dataclass(frozen=True)
class Settings:
    """What the routes read of the deployment: its runner, and the bounds a
    job is held to."""

    runner_for: RunnerFor
    limits: SourceLimits
    budget: BudgetPolicy
    deadline_seconds: float
    carries_catalog: bool
    #: How many Owner Files the operator keeps in Files (ADR 0118).
    max_owner_files: int
    #: The bounds on the operator's Controls (ADR 0127).
    control_limits: ControlLimits
    gate: CertificationGate | None = None


class Workspace:
    """The jobs this process holds: the store, the tasks that run them, and
    the cap on them.

    The jobs live in a job store, capped at ``max_jobs``. The oldest job
    that waits for no answers is deleted first, and a job that waits for
    answers is never deleted: where every job waits, a new one is refused
    instead. One job runs at a time (LLM10): the store's admission refuses a
    second while one is in flight, with a message rather than a queue. A
    restart keeps the jobs of a durable store and loses those of ``memory``.

    The workspace holds nothing of the Deployment, so it outlives a swap of
    the :class:`Cell`. Each read through the job operations takes the
    :class:`~analysis_service.job_answers.JobAnswers` that the route read
    from the cell.
    """

    def __init__(self, *, store: JobStore, max_jobs: int = MAX_RUNS) -> None:
        self.store = store
        self._max_jobs = max_jobs
        self._runs: dict[str, tuple[asyncio.Task[None], StopSignal]] = {}

    def start(self, deadline_seconds: float, answers: JobAnswers) -> Start:
        """What runs an admitted job on this process's event loop, under
        ``deadline_seconds``. A job keeps the runner, the deadline and the job
        operations that it got at admission, whatever the cell holds later."""

        def run(record: JobRecord, runner: PipelineRunner) -> None:
            stop = StopSignal()
            task = asyncio.create_task(
                self._run(answers, record, runner, deadline_seconds, stop)
            )
            # Held so the task is not garbage-collected mid-flight, and so
            # :meth:`stop` can reach it.
            self._runs[record.id] = (task, stop)
            task.add_done_callback(lambda _: self._runs.pop(record.id, None))

        return run

    async def _run(
        self,
        answers: JobAnswers,
        record: JobRecord,
        runner: PipelineRunner,
        deadline_seconds: float,
        stop: StopSignal,
    ) -> None:
        """Run the job, then continue it where it pauses for its Controls
        alone (ADR 0125). The continue runs in the job's own task, so a read
        of the job can wait for it with :meth:`settled`."""
        await execute_job(
            self.store, runner, record.id, deadline_seconds=deadline_seconds, stop=stop
        )
        if record.continues_at_pause and not stop.requested:
            await self._continue(answers, record, deadline_seconds)

    async def _continue(
        self, answers: JobAnswers, record: JobRecord, deadline_seconds: float
    ) -> None:
        """Continue the paused job ``record`` with no own answers, and store
        the page's words for a refusal, which leaves the job paused."""
        try:
            async with self.make_room(answers, answering=record.id):
                await answers.continue_at_pause(
                    record.id,
                    record.owner_id,
                    start=self.start(deadline_seconds, answers),
                )
        except Exception as refusal:
            words = _refusal_message(refusal)
            if words is None:
                logger.exception("job %s did not continue at its pause", record.id)
                words = _NOT_STARTED
            try:
                kept = await self.store.set_continue_refusal(
                    record.id, record.owner_id, words
                )
            except Exception:
                logger.exception("job %s did not store its refusal", record.id)
            else:
                if not kept:
                    logger.warning(
                        "job %s left the store before its refusal", record.id
                    )

    async def settled(self, job_id: str) -> None:
        """Wait for the task of ``job_id`` to end, where this process runs
        one. The task of a job that pauses for its Controls alone ends after
        the continue."""
        if (held := self._runs.get(job_id)) is not None:
            await asyncio.wait([held[0]])

    async def state(self, record: Envelope) -> JobState:
        """The Job State of ``record``. A job of this process is held while
        its task runs, and that task continues it at its pause (ADR 0125)."""
        return await job_state(self.store, record, held=record.id in self._runs)

    async def stop(self, job_ids: Iterable[str]) -> bool:
        """Stop each run of ``job_ids`` that this process runs, and wait up to
        :data:`STOP_WAIT_SECONDS` for them to end. False where it runs none
        of them (ADR 0109)."""
        tasks = []
        for job_id in job_ids:
            if (held := self._runs.get(job_id)) is not None:
                task, stop = held
                stop.request()
                tasks.append(task)
        if tasks:
            await asyncio.wait(tasks, timeout=STOP_WAIT_SECONDS)
        return bool(tasks)

    async def owner_id(self, answers: JobAnswers, job_id: str) -> str:
        """The operator's owner ID, by :meth:`JobAnswers.owner_id`. Before the
        first submission the operator owns no job, so ``job_id`` reads as
        missing."""
        return await answers.owner_id(OPERATOR, job_id)

    async def owned(self, answers: JobAnswers, job_id: str) -> Envelope | None:
        """The operator's job ``job_id``, as an envelope, or ``None``."""
        try:
            return await answers.owned(job_id, await self.owner_id(answers, job_id))
        except NoSuchJob:
            return None

    async def envelope(self, answers: JobAnswers, job_id: str) -> JobRecord | None:
        """The operator's job ``job_id``, as an envelope that parses, or
        ``None``. Raises :class:`UnreadableSchema` where it does not parse."""
        try:
            return await answers.envelope(job_id, await self.owner_id(answers, job_id))
        except NoSuchJob:
            return None

    async def parsable(self, answers: JobAnswers, job_id: str) -> JobRecord | None:
        """The operator's job ``job_id``, as an envelope, or ``None``. Raises
        :class:`UnreadableSchema` where this app cannot parse the record."""
        try:
            return await answers.parsable(job_id, await self.owner_id(answers, job_id))
        except NoSuchJob:
            return None

    async def jobs(self, answers: JobAnswers) -> list[Envelope]:
        """Every job of the operator, as envelopes, oldest first."""
        owner_id = await answers.held_owner_id(OPERATOR)
        if owner_id is None:
            return []
        return await self.store.owned_jobs(owner_id)

    @asynccontextmanager
    async def make_room(
        self, answers: JobAnswers, *, answering: str | None = None
    ) -> AsyncIterator[None]:
        """Delete the oldest jobs that wait for nothing, once the body admits a
        new job, until that job fits.

        The jobs are chosen on entry and deleted on a clean exit, so a
        submission the body refuses deletes nothing. Each delete goes through
        :func:`~analysis_service.store_backends.audited_delete` with the cause
        ``workspace`` and the time of the choice, so a chosen job that changes
        before the delete stays, and the workspace then holds one job over its
        bound. A job that answers the job ``answering``, paused or finished,
        may hold one place over the bound: that job stays the place to retry
        from until its answer has a report, and it is never deleted to make
        room for its own answer. Raises :class:`WorkspaceFull` where every
        held job runs or waits.
        """
        limit = self._max_jobs + (answering is not None)
        chosen_at = datetime.now(UTC)
        held = await self.jobs(answers)
        removable = [
            job
            for job in held
            if ended(job)
            and job.id != answering
            and not (await self.state(job)).waiting
        ]
        excess = len(held) - limit + 1
        if excess > len(removable):
            raise WorkspaceFull(
                f"{len(held)} analyses run or wait for answers, so none can"
                " make room. Answer or continue one of them before you start"
                " another."
            )
        yield
        for job in removable[: max(excess, 0)]:
            await audited_delete(
                self.store, job.id, job.owner_id, "workspace", updated_before=chosen_at
            )

    async def parent_of(self, record: Envelope) -> str | None:
        """The job this one resumes or amends: :func:`parent_of` over the
        record with its resumption, which an envelope leaves out.

        The resumption is read only where :func:`readable` accepts the
        record. A record of another schema version names only the job it
        amends, and a record whose envelope does not parse names none.
        """
        if readable(record):
            resumption = await self.store.resumption(record.id, record.owner_id)
            return parent_of(record.model_copy(update={"resumption": resumption}))
        return parent_of(record) if isinstance(record, JobRecord) else None

    async def workspace_id(self, record: Envelope) -> str:
        """The analysis this job belongs to: the root of its parent chain.

        A parent deleted since still names the root, so the group holds.
        """
        current = record
        while (parent := await self.parent_of(current)) is not None:
            above = await self.store.owned(parent, current.owner_id)
            if above is None:
                return parent
            current = above
        return current.id

    async def analysis(self, answers: JobAnswers, job_id: str) -> list[Envelope]:
        """Every job of the analysis that holds ``job_id``, oldest first, or
        none where the operator holds no job ``job_id``."""
        record = await self.owned(answers, job_id)
        if record is None:
            return []
        group = await self.workspace_id(record)
        return [
            item
            for item in await self.jobs(answers)
            if await self.workspace_id(item) == group
        ]

    async def snapshots(self, answers: JobAnswers) -> list[dict[str, object]]:
        """Latest retained job per analysis, with its retained reports, newest first."""
        grouped: dict[str, list[Envelope]] = {}
        for record in await self.jobs(answers):
            grouped.setdefault(await self.workspace_id(record), []).append(record)
        snapshots = []
        for records in reversed(list(grouped.values())):
            states = {record.id: await self.state(record) for record in records}
            shown = next(
                (record for record in reversed(records) if states[record.id].waiting),
                records[-1],
            )
            snapshots.append(
                await self.snapshot(answers, shown, states[shown.id], records)
            )
        return snapshots

    async def snapshot(
        self,
        answers: JobAnswers,
        record: Envelope,
        state: JobState,
        related: list[Envelope] | None = None,
    ) -> dict[str, object]:
        """Public workspace metadata; descriptions only on the detail route.
        ``state`` is the Job State of ``record``."""
        if related is None:
            group = await self.workspace_id(record)
            related = [
                item
                for item in await self.jobs(answers)
                if await self.workspace_id(item) == group
            ]
        return {
            "run": record.id,
            "name": _name_of(record),
            "title": record.title,
            "system_name": record.system_name,
            "created_at": min(
                (item.created_at for item in related), default=record.created_at
            ).isoformat(),
            "status": state.page_status,
            # Whether the run stops to ask a person, so the page shows its
            # progress under the Questions tab rather than Reports.
            "pauses": state.asks,
            "frameworks": [selection.name for selection in _selection_of(record)],
            "reports": [
                {
                    "run": item.id,
                    "name": _name_of(item),
                    "generated_at": item.updated_at.isoformat(),
                }
                for item in related
                if served(answers, item)
            ],
        }


def served(answers: JobAnswers, record: Envelope) -> bool:
    """True where this app can read the job's record and
    :meth:`JobAnswers.servable` lets its report be served. Each view that
    links to a report or shows one asks it. The report page refuses a record
    that :func:`readable` refuses, so no view links to its report."""
    if not readable(record):
        return False
    try:
        answers.servable(record)
    except (NotCompleted, Withheld):
        return False
    return True


@dataclass(frozen=True)
class Current:
    """What the routes run on now: the job operations of one Deployment on
    the running store, the job deadline, and what the pages show of it.

    ``answers`` holds the runner, the bounds, the carried frameworks that the
    picker offers, and the certification gate. The Deployment stays in memory
    through ``answers.runner_for`` while a request or a job holds it.
    """

    answers: JobAnswers
    deadline_seconds: float
    max_owner_files: int
    control_limits: ControlLimits


class CellEmpty(Exception):
    """The cell holds no :class:`Current`; ``response`` is the route's answer."""

    def __init__(self, response: Response) -> None:
        super().__init__()
        self.response = response


@dataclass
class Cell:
    """Holds the :class:`Current` (ADR 0093).

    ``current`` is ``None`` where the Deployment did not build, and the
    diagnostic page then serves. A swap replaces ``current`` in one
    assignment. Each route reads it one time, at its start, and uses only
    that value, so one request never reads two Deployments. A route that
    needs a :class:`Current` reads it with :meth:`read`.
    """

    current: Current | None

    def read(self, empty: Callable[[], Response]) -> Current:
        """The current value, or :class:`CellEmpty` with ``empty()``."""
        current = self.current
        if current is None:
            raise CellEmpty(empty())
        return current


DEFAULT_NAME = "Your system"

#: The form page's note for a store that a restart clears. A durable store
#: shows no note.
_MEMORY_STORE_NOTE = (
    "This app keeps your runs in memory, so a restart clears them."
    " To keep them, reset the job store on the Settings page."
)

#: The page's statement of :func:`~analysis_service.sources.clean_system_name`.
#: A fixed sentence, so a refusal never echoes the name it refused.
_NAME_REFUSED = (
    f"Use an analysis name of at most {MAX_SYSTEM_NAME_CHARS} characters, on one"
    " line, with no control or formatting characters."
)


def _as_v1(field: str) -> Any:
    """The field ``POST /v1/jobs`` declares, its bounds and strictness with it.

    Typed ``Any`` as :func:`pydantic.Field` is, so a model can take it as a
    default.
    """
    return JobSubmission.model_fields[field]


class _Submission(BaseModel):
    """The body of ``POST /analyze``, and the one statement of the keys it takes.

    Each field that ``POST /v1/jobs`` also takes is the field
    :class:`~analysis_service.api.JobSubmission` declares, so the two entry
    points refuse the same values with the same sentence. ``name`` is the
    form's ``system_name``; the route reads it with
    :func:`~analysis_service.sources.clean_system_name`.
    """

    model_config = ConfigDict(extra="forbid")

    sources: list[Source] = _as_v1("sources")
    frameworks: list[FrameworkRequest] = _as_v1("frameworks")
    questions: bool = _as_v1("questions")
    answers_from: str | None = _as_v1("answers_from")
    name: str | None = None
    #: The Owner Files whose text the new job copies (ADR 0119). Only this
    #: route takes them: ``POST /v1/jobs`` has no Files.
    #: :meth:`~analysis_service.job_answers.JobAnswers.owner_file_sources`
    #: refuses more of them than the deployment's source limit.
    owner_files: list[Annotated[str, Field(pattern=OWNER_FILE_ID)]] = Field(
        default_factory=list, max_length=MAX_SOURCES_CEILING
    )
    #: The Controls the new job copies (ADR 0127). Only this route takes
    #: them: ``POST /v1/jobs`` has no Controls.
    controls: list[Annotated[str, Field(pattern=CONTROL_ID)]] = Field(
        default_factory=list, max_length=MAX_CONTROLS_CEILING
    )


class _AliasBody(BaseModel):
    """The body of ``POST /workspace/controls/{id}/aliases``: the element
    name that "keep this name" appends to the Control's scope (ADR 0124)."""

    model_config = ConfigDict(extra="forbid")

    alias: str


#: The ``control_scopes`` key of an ``/answer`` body (ADR 0124). Only this
#: route takes it: the ``/v1`` answer body refuses the key.
_CONTROL_SCOPES: TypeAdapter[list[ControlScopeEntry]] = TypeAdapter(
    list[ControlScopeEntry]
)


def _control_scopes(raw: object) -> list[ControlScopeEntry] | None:
    """Take ``control_scopes`` out of an ``/answer`` body, so the rest of the
    body is the one :func:`read_answers` reads. ``None`` where the body does
    not carry the key. Raises :class:`ValidationError` for a value of the
    wrong shape."""
    if not isinstance(raw, dict) or "control_scopes" not in raw:
        return None
    return _CONTROL_SCOPES.validate_python(raw.pop("control_scopes"))


class _FileUpload(BaseModel):
    """The body of ``POST /workspace/files``: an upload to Files.

    The browser reads the file and sends its text, so no route takes a file.
    :func:`~analysis_service.owner_files.upload_refusal` checks the content.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: SourceKind
    format: SourceFormat
    text: str


def _file_view(file: OwnerFile) -> dict[str, object]:
    """What the pages show of an Owner File: no owner ID and no text."""
    return {
        "id": file.id,
        "name": file.name,
        "kind": file.kind,
        "format": file.format,
        "size_bytes": file.size_bytes,
        "created_at": file.created_at.isoformat(),
    }


#: The answer to a delete of a file the operator does not hold.
_NO_SUCH_FILE = "This file is no longer in Files."
_NO_SUCH_CONTROL = "This Control is no longer in Controls."
_NO_SUCH_EVIDENCE = "This evidence file is no longer in the Control."

_CONTROL_STALE = "This Control changed in another tab or window after you opened it."

_CONTROL_ALIAS_IN_USE = (
    "Another Control protects a scope with one of these names. Remove the alias,"
    " or change the name."
)

_CONTROL_BODY = "Expected a JSON object with a name, a scope, facts, rows and evidence."


class _UnknownKey(Exception):
    """A body carries a key its route does not read."""


def _body_of(raw: object, model: type[BaseModel]) -> dict[str, object]:
    """``raw`` as a JSON object that holds only the keys ``model`` declares.

    Each route names the model of its body, and the /v1 models refuse any
    other key (``extra="forbid"``), so both entry points refuse the same
    bodies. The refusal names the keys the route takes, never the one sent.
    Raises ``TypeError`` for a body that is not an object.
    """
    if not isinstance(raw, dict):
        raise TypeError
    if not raw.keys() <= model.model_fields.keys():
        raise _UnknownKey(
            "This request takes only these fields: "
            + ", ".join(model.model_fields)
            + "."
        )
    return raw


def _name_of(record: Envelope) -> str:
    """The name each workspace view shows. The report shows its own
    ``system_name`` and never the title (ADR 0106)."""
    return record.title or record.system_name or DEFAULT_NAME


def _selection_of(record: Envelope) -> list[FrameworkSelection]:
    """The selection the record holds: none on an envelope that leaves it out."""
    if isinstance(record, JobRecord) and record.frameworks is not None:
        return record.frameworks
    return []


@dataclass(frozen=True)
class Startup:
    """What starting up produced: what the app can offer, or the failure.

    The app serves either way. If construction raised, the cell is empty and
    every route that would run a model is replaced by the diagnostic page, so
    no analysis can run on a model nobody chose — fail-closed, but explained.

    ``carried_frameworks`` is what this install carries, which is what the picker
    offers and what the one submission allow-lists against. It is not itself a
    selection and it is never used as one: a submission names its own, and the
    form ships with every box unticked.
    """

    settings: Settings | None
    carried_frameworks: tuple[FrameworkName, ...]
    tiers: ModelTierConfig | None
    error: ConfigError | None

    def current(self, store: JobStore) -> Current | None:
        """What the routes run on, with the job operations on ``store``, or
        ``None`` where the startup failed."""
        settings = self.settings
        if settings is None:
            return None
        return Current(
            answers=JobAnswers(
                store=store,
                runner_for=settings.runner_for,
                limits=settings.limits,
                ceiling=1,
                budget=settings.budget,
                carried_frameworks=self.carried_frameworks,
                carries_catalog=settings.carries_catalog,
                gate=settings.gate,
            ),
            deadline_seconds=settings.deadline_seconds,
            max_owner_files=settings.max_owner_files,
            control_limits=settings.control_limits,
        )


def build_startup(env: Mapping[str, str]) -> Startup:
    """Resolve the config from ``env`` and prove the credentials, or convert
    the failure to a page.

    Two stages, and the split is the whole content of the diagnostic. Resolving
    the :class:`~analysis_service.deployment.Deployment` reads the config files;
    building a runner off it resolves the vendor's credentials and runs every
    tier's ``(vendor, model, sampling)`` through LiteLLM's own check. So a
    *config* failure leaves no tiers to report, while a *credential* failure
    can still name the vendor the config selected — which is the case a first
    run overwhelmingly hits. One read either way: the tiers the diagnostic page
    reads are the tiers the runner was built from, not a second load of the same file.

    **Building the runner is the credential check, because binding the tier
    adapters is what resolves the credentials.** Binding does not depend on
    which frameworks a submission picks, so any selection proves the same
    thing, and the probe names the whole carried set.
    :meth:`~analysis_service.deployment.Deployment.runner` memoizes per
    selection, so each other pick composes its own graph once and no
    submission binds the adapters twice.

    The bounds a job is held to are the deployment's own: the same source
    limits, budget and deadline ``/v1`` enforces.
    """
    deployment = None
    try:
        deployment = Deployment.from_env(env)
        deployment.runner(deployment.carried_frameworks)
    except ConfigError as exc:
        logger.error("the Deployment did not build: %s", exc)
        tiers = deployment.tiers if deployment is not None else None
        return Startup(settings=None, carried_frameworks=(), tiers=tiers, error=exc)
    return Startup(
        settings=Settings(
            runner_for=deployment.runner,
            limits=deployment.resilience.source_limits(),
            budget=deployment.resilience.budget_policy(),
            max_owner_files=deployment.resilience.max_owner_files,
            control_limits=deployment.resilience.control_limits(),
            deadline_seconds=deployment.job_deadline_seconds(),
            carries_catalog=deployment.carries_catalog,
            gate=deployment.gate,
        ),
        carried_frameworks=deployment.carried_frameworks,
        tiers=deployment.tiers,
        error=None,
    )


def _unit_order(unit: str) -> tuple[int | str, ...]:
    """A unit identifier as a sort key, so ``V6.2.10`` follows ``V6.2.9``.

    Numeric where a part is a number and textual where it is not, because the
    shape of a unit belongs to the framework rather than to this page: ASVS's
    ``V<chapter>.<section>.<requirement>`` sorts into the standard's own order,
    and a package numbering its units another way still sorts stably.

    ``isdecimal`` rather than ``isdigit``: ``int`` accepts exactly the decimal
    digits, and ``isdigit`` also says yes to a superscript, which ``int``
    refuses. A unit read from a file is the shape nobody listed.
    """
    return tuple(
        int(part) if part.isdecimal() else part for part in unit.lstrip("Vv").split(".")
    )


def unit_rows(report: Report) -> dict[str, list[dict[str, str]]]:
    """Per framework, the units its block answers for, with each one's own text.

    **The join is here because the knowledge is here.** Which claim rules on
    which unit is a package's answer (the record hook ``unit_of``) and what a
    unit says is a package's answer (``text_of_unit``); the report page holds
    neither and must not learn them. So the server pairs them and the page renders what it is
    handed.

    A framework whose claims are an open set contributes an empty list: no
    claim names a unit and no unit has text, so the page renders no table for
    it rather than a table of nothing.

    The rows carry the unit, its text and the claim ruling on it. Everything
    else a row shows — the state, the reason, the evidence a unit still needs —
    the page reads off ``scope`` and the claim, which it already holds.
    """
    rows: dict[str, list[dict[str, str]]] = {}
    for block in report.analyses:
        record = package_for(block.framework).record
        by_unit = {
            unit: claim.id
            for claim in block.all_claims()
            if (unit := record.unit_of(claim))
        }
        units = sorted(
            {entry.unit for entry in block.scope} | set(by_unit), key=_unit_order
        )
        rows[block.framework] = [
            {
                "unit": unit,
                "text": record.text_of_unit(unit),
                "claim_id": by_unit.get(unit, ""),
            }
            for unit in units
            if record.text_of_unit(unit) or unit in by_unit
        ]
    return rows


def render_report(
    report: Report,
    state: AnswerState,
    previous: Report | None,
    parent: str | None,
    controls: Sequence[SectionEntry],
) -> RenderedPage:
    """``report_view.html``, carrying this run's report.

    ``previous`` is the report a follow-up's answers came from. The page then
    shows how each finding moved since it. ``parent`` is the ID of the run
    whose answers started this one, or ``None``.

    ``state`` is the run's :class:`~analysis_service.answer_state.AnswerState`:
    its answers, whether a follow-up wrote its report, what the pause showed,
    what its owner corrected since and the run its follow-up started. It
    decides what the page still asks and how it labels each question, from
    the same Question Set the answer route admits against.

    The template is a self-contained renderer for the report schema — no build
    step, no framework, its own inline CSS and JS. It sits in the workspace
    layout, so it carries the workspace styles and the sidebar script too. This
    fills its payload placeholders and its per-response CSP nonce, and serves
    the result.

    **The escape is the contract.** The report carries the submitter's own
    prose, so a description containing ``</script>`` would close the JSON block
    and everything after it would parse as HTML — a stored-input XSS on the one
    page the first-run route sends people to. The payload goes through
    :func:`~webapp.page.script_json`, the same function every other value
    landing in a script block goes through, rather than through a rule this
    file keeps for itself.

    A viewer that lost its payload placeholder raises at
    :func:`~webapp.page.render` rather than serving a report of nothing.
    """
    asked = state.questions.to_json()
    return render(
        VIEWER.read_text(encoding="utf-8"),
        _REPORT_GRANTS,
        script=client_scripts(
            "sidebar.js",
            "service_answer.js",
            "answer_editor.js",
            "question_list.js",
            "report_view.js",
        ),
        workspace_style=WORKSPACE_STYLE,
        sidebar=_SIDEBAR,
        theme=_THEME,
        answer_limits=script_json(dict(ANSWER_LIMITS)),
        facet_answers=script_json(list(FACET_ANSWERS)),
        report=script_json(report.model_dump(mode="json")),
        controls=script_json([entry.model_dump(mode="json") for entry in controls]),
        controls_summary=script_json(section_summary(controls)),
        ruling_lists=script_json(dict(RULING_LISTS)),
        units=script_json(unit_rows(report)),
        fixes=script_json(
            fixes_by_framework(
                report.analyses,
                report.system_model,
                report.assertions.catalog if report.assertions else None,
            )
        ),
        fact_questions=script_json(asked["fact_questions"]),
        question_fallback=script_json(question_fallback(report.analyses).to_json()),
        link_questions=script_json(asked["link_questions"]),
        final=script_json(asked["final"]),
        asks=script_json(state.asks),
        resumed_by=script_json(asked["resumed_by"]),
        parent_run=script_json(parent),
        corrections=script_json(
            _corrections_payload(
                report,
                state.answers.facts,
                state.corrections,
                state.revision,
                state.answers.shares,
            )
            if state.final
            else {}
        ),
        earlier=script_json(
            _earlier_payload(
                report, state.answers.facts, state.answers.links, state.questions.asked
            )
            | _draft_payload(state)
            if not state.final and state.resumed_by is None
            else {}
        ),
        provenance=script_json(
            _provenance_payload(report, state.answers.facts, state.shown, state.skipped)
        ),
        changes=script_json(
            {
                "findings": [
                    change.to_json() | {"line": change_line(change)}
                    for change in report_changes(previous, report)
                ]
            }
            if previous is not None
            else {}
        ),
        severity_order=script_json(list(SEVERITY_ORDER)),
        verdict_labels=script_json(dict(VERDICT_LABELS)),
        unit_verdict_words=script_json(dict(UNIT_VERDICT_WORDS)),
        report_words=script_json(island_tables(REPORT_WORDS)),
        element_type_nouns=script_json(dict(ELEMENT_TYPE_NOUNS)),
        assumed_zones=script_json(sorted(report.system_model.assumed_zone_elements())),
        mermaid=script_json(styled_mermaid(report.system_model)),
        element_hues=script_json(element_hues(report.system_model)),
        lanes=script_json(
            {name: package.id_rule.lane_field for name, package in PACKAGES.items()}
        ),
        gaps=script_json([gap.to_json() for gap in gaps(report.system_model)]),
        names=script_json(
            reference_labels(
                report.system_model,
                report.assertions.catalog if report.assertions else None,
            )
        ),
    )


def _provenance_payload(
    report: Report,
    answered: Sequence[FactAnswer],
    shown: Sequence[UnknownKey],
    skipped: Sequence[SkipKey],
) -> dict[str, object]:
    """What the page needs to tell the owner's answers from the sources, and to
    say why each conditional finding is still open (#1289, PR 4)."""
    return {
        "answers_label": ANSWERS_LABEL,
        "answered_attributes": [
            list(attribute_place(answer.key))
            for answer in answered
            if answer.kind == "attribute" and answer.known
        ],
        "conditions": {
            finding: [
                {"key": list(key), "label": label, "status": status}
                for key, label, status in rows
            ]
            for finding, rows in conditions(
                report.analyses,
                report.system_model,
                answered,
                shown,
                skipped,
                report.assertions.catalog if report.assertions else None,
            ).items()
        },
    }


def _answer_rows(
    report: Report, answers: Sequence[FactAnswer]
) -> list[dict[str, object]]:
    """Each answer with its label and the form, choices and limit it is changed in.

    Read by the same helpers that build a question.
    """
    model = report.system_model
    catalog = report.assertions.catalog if report.assertions else None
    return [
        {
            "key": list(answer.key),
            "label": fact_label(answer.key, model, catalog),
            "form": answer_form(answer.key, model, catalog),
            "choices": list(answer_choices(answer.key, model, catalog)),
            "facets": facets_json(answer_facets(answer.key)),
            "asks": list(asked_facets(answer.key)),
            "suggestions": list(answer_suggestions(answer.key)),
            "max_length": answer_limit(answer.key, model),
            "answer": answer.model_dump(mode="json"),
        }
        for answer in answers
    ]


def _corrections_payload(
    report: Report,
    answered: Sequence[FactAnswer],
    corrections: Sequence[FactAnswer],
    revision: int,
    shares: Sequence[SharedAnswer],
) -> dict[str, object]:
    """A final report's answers as it is corrected in, what corrections reach,
    the shares its run read, and the revision a correction names (ADR 0090,
    ADR 0114).

    Each answer carries its current value with every correction in. A
    corrected answer is its element's own, whatever share covered the element.
    """
    corrected = {fact.key for fact in corrections}
    answers = merged_facts(answered, corrections)
    rows = _answer_rows(report, answers)
    return {
        "answers": [
            row | {"corrected": answer.key in corrected}
            for row, answer in zip(rows, answers, strict=True)
        ],
        "findings": list(corrected_findings(report.analyses, answered, corrections)),
        "shares": [share.model_dump(mode="json") for share in shares],
        "revision": revision,
    }


def _draft_payload(state: AnswerState) -> dict[str, object]:
    """A report's follow-up draft and the revision a save names (ADR 0070)."""
    return {
        "draft": {
            "links": [
                link.model_dump(mode="json") | {"key": fold_principal(link.principal)}
                for link in state.draft.links
            ],
            "facts": [fact.model_dump(mode="json") for fact in state.draft.facts],
            "shares": [share.model_dump(mode="json") for share in state.draft.shares],
        },
        # The shares the report's run read, which a follow-up share of the
        # same question replaces (ADR 0114).
        "shares": [share.model_dump(mode="json") for share in state.answers.shares],
        "revision": state.revision,
    }


def _earlier_payload(
    report: Report,
    answered: Sequence[FactAnswer],
    links: Sequence[LinkAnswer],
    asked: Collection[UnknownKey],
) -> dict[str, object]:
    """The answers a first report read, so its follow-up can take new ones.

    The follow-up admits a new answer to an earlier fact or link
    (:func:`~analysis_service.links.check_answers`), so the page offers it.
    A fact the follow-up still ``asked``, such as a facet answer with facets
    left out, is left to its question, because one submission answers a fact
    once. Its answer is ``retained``, so that question starts from it.
    """
    options = list(components(report.system_model))
    return {
        "answers": _answer_rows(
            report, [answer for answer in answered if answer.key not in asked]
        ),
        "retained": [
            answer.model_dump(mode="json") for answer in answered if answer.key in asked
        ],
        "links": [
            {
                "key": fold_principal(link.principal),
                "principal": link.principal,
                "options": options,
                "answer": link.model_dump(mode="json"),
            }
            for link in links
        ],
    }


#: Each typed refusal of the job operations and of the answer rules, with the
#: status the page is answered under: the ``/v1`` table, and the refusals that
#: only this app answers by a table. The message is the refusal's own, which
#: names the submitter's choices and the frameworks the form offers, and never a
#: setting of this deployment.
_REFUSAL_STATUS: dict[type[Exception], int] = {
    **JOB_REFUSAL_STATUS,
    Withheld: 409,
    WorkspaceFull: 409,
}

#: What the page says where a save in another tab moved the answers first.
_STALE_MESSAGE = (
    "Your saved answers changed in another tab or window. Reload this page to see them."
)

#: What the page is told for each admission refusal, keyed by outcome. An
#: outcome missing here is this app's defect: ``duplicate`` cannot follow from
#: a page, because ``JobRecord.create`` mints the ID.
_ADMISSION_MESSAGES: dict[RefusalOutcome, str] = {
    "at_ceiling": "An analysis is already running. Wait for it to finish.",
    "over_rate": "This workspace started too many analyses in the current"
    " window. Wait for the window to roll past the earlier ones.",
    "over_owner_budget": "This workspace has reached its consumption limit"
    " for the current window. Retry later, or submit less text.",
    "over_global_budget": "This workspace has reached its consumption limit"
    " for the current window. Retry later, or submit less text.",
    "resumed_already": "These answers already started an analysis. Open its"
    " report, or answer again only if it fails.",
    "parent_stale": _STALE_MESSAGE,
    "parent_missing": "This analysis was deleted.",
    "principal_exists": "Another analysis started at the same moment. Start"
    " this one again.",
}


#: The key of each refusal that ``text_file.js`` and ``files.js`` show for a
#: file they check before the server does. ``at_limit`` is the refusal that a
#: page shows where its list holds the most items it can keep.
FileRefusal = Literal["too_large", "not_utf_8", "not_plain_text", "no_text", "at_limit"]


#: The model of a Control save's body.
_ControlBodyT = TypeVar("_ControlBodyT", bound=ControlBody)


class FilesLimits(TypedDict):
    """The limits that ``files.js`` reads."""

    max_owner_files: int
    max_bytes: int
    name_chars: int


def file_refusals(max_bytes: int, max_files: int) -> dict[FileRefusal, str]:
    """Each :data:`FileRefusal` in the server's words (:func:`upload_refusal`)."""
    return {
        "too_large": too_large(max_bytes),
        "not_utf_8": NOT_UTF_8,
        "not_plain_text": NOT_PLAIN_TEXT,
        "no_text": NO_TEXT,
        "at_limit": over_limit(max_files),
    }


def evidence_refusals(limits: ControlLimits) -> dict[FileRefusal, str]:
    """Each :data:`FileRefusal` of the Controls page. The file refusals are
    those of an evidence file, in the words of
    :func:`~analysis_service.controls.control_refusal`, and ``at_limit`` is
    the refusal of a new Control where the owner holds the most Controls."""
    return {
        "too_large": evidence_too_large(limits.max_evidence_bytes),
        "not_utf_8": NOT_UTF_8,
        "not_plain_text": NOT_PLAIN_TEXT,
        "no_text": NO_TEXT,
        "at_limit": controls_full(limits),
    }


def _section_of(record: JobRecord, report: Report) -> list[SectionEntry]:
    """The report's Controls section, from the job's copy and the report's
    rulings (ADR 0127). ``record`` holds the copy that
    :meth:`~analysis_service.job_answers.JobAnswers.answerable` read."""
    return controls_section(
        record.controls,
        record.control_scopes,
        record.control_facts,
        report.system_model,
        report.analyses,
    )


def _name_taken(name: str) -> str:
    """The refusal of a Control save whose name the owner already uses."""
    return (
        f"You already have a Control named “{name}”. Give this Control a"
        " different name."
    )


def _message(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def _cross_origin(request: Request) -> JSONResponse | None:
    """The ``403`` of a write route for a request that
    :func:`~webapp.page.is_same_origin` refuses, or ``None`` where the request
    came from the app's own page.

    **The one same-origin gate of this app's write routes.** Each ``POST``,
    ``PUT`` and ``DELETE`` route asks it before it does anything else.
    """
    if is_same_origin(request):
        return None
    # repr of the path, never the path: it is the decoded text of a request
    # that another site can send, so an escape sequence there would reach the
    # operator's terminal.
    logger.warning(
        "refused a %s %r that was not same-origin", request.method, request.url.path
    )
    return _message(403, "This request did not come from the app's own page.")


async def _refused(status: int, request: Request, exc: Exception) -> JSONResponse:
    fields = exc.fields() if isinstance(exc, Refused) else {}
    words = _refusal_message(exc) or _NOT_STARTED
    return JSONResponse({"message": words, **fields}, status_code=status)


#: What the page says where this app cannot start a job and the reason is in
#: the server log only.
_NOT_STARTED = "The analysis could not be started. Check the server log."

#: What the page says for a job that waits to continue at its pause where no
#: task of this process continues it, no refusal is stored and no Resumed Job
#: exists (ADR 0125): the app restarted, a stop came after the pause, or the
#: last continue ended without a report.
_NOT_CONTINUED = (
    "The app did not continue this job, for example after a restart or a stop."
    " Continue it."
)


def _refusal_key(state: JobState) -> dict[str, str]:
    """The ``refusal`` key of the questions of a job: why it waits at its
    pause. Empty for each job that does not wait there for a continue."""
    refusal = state.refusal if isinstance(state, EndedJobState) else None
    match refusal:
        case StoredRefusal(words=words):
            return {"refusal": words}
        case "not-continued":
            return {"refusal": _NOT_CONTINUED}
        case None:
            return {}


def _refusal_message(refusal: Exception) -> str | None:
    """What the page says for a typed refusal of the job operations, or
    ``None`` for any other exception. Each handler of these refusals and the
    continue at a pause (ADR 0125) take the words from here."""
    if isinstance(refusal, NotAdmitted):
        # An outcome missing from the table gets no words: it is this app's
        # defect, and the server log names it.
        outcome = refusal.admission.outcome
        if outcome not in _ADMISSION_MESSAGES:
            return None
        return _ADMISSION_MESSAGES[outcome]
    if isinstance(refusal, AlreadyResumed):
        return _ADMISSION_MESSAGES["resumed_already"]
    if isinstance(refusal, StaleRevision):
        return _STALE_MESSAGE
    if isinstance(refusal, SourcesOverLimit):
        return refusal.breach.message
    if isinstance(refusal, tuple(_REFUSAL_STATUS)):
        return str(refusal)
    return None


async def _not_admitted(request: Request, exc: NotAdmitted) -> JSONResponse:
    words = _refusal_message(exc)
    if words is not None:
        return _message(409, words)
    logger.error("job was not admitted: %s", exc.admission.outcome)
    return _message(500, _NOT_STARTED)


async def _worded(status: int, request: Request, exc: Exception) -> JSONResponse:
    """The page's words for ``exc``, under ``status``."""
    return _message(status, _refusal_message(exc) or _NOT_STARTED)


async def _cell_empty(request: Request, exc: CellEmpty) -> Response:
    return exc.response


def _refused_save(found: Sequence[Refusal]) -> JSONResponse:
    return JSONResponse(
        {
            "message": "The settings were not saved.",
            "errors": [{"key": item.key, "message": item.message} for item in found],
        },
        status_code=422,
    )


class _BodyUnread(Exception):
    """A write route's body that :func:`_json_body` does not read."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def _body_unread(request: Request, exc: _BodyUnread) -> JSONResponse:
    return _message(exc.status, str(exc))


async def _json_body(request: Request, limit: int) -> object:
    """The request's body as JSON, or ``None`` for a body that is not JSON.

    **The one reader of a write route's body.** It refuses a media type other
    than ``application/json`` with a 415 and a body longer than ``limit``
    bytes with a 413, and it stops the read at the first byte over the limit.
    Each refusal raises :class:`_BodyUnread`, which the app answers. ``None``
    is a body that each route's own reader refuses as it refuses every other
    body that is not an object, so a page gets one refusal wording.
    """
    media_type = request.headers.get("content-type", "").partition(";")[0]
    if media_type.strip().lower() != "application/json":
        raise _BodyUnread(415, "This request takes a JSON body.")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise _BodyUnread(413, f"This request is at most {limit:,} bytes.")
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        return None


async def _job_body(request: Request, answers: JobAnswers) -> object:
    """A job route's body, read by :func:`_json_body` under the deployment's
    raw-body guard, :func:`~analysis_service.api.max_body_bytes`."""
    return await _json_body(request, max_body_bytes(answers.limits))


_RELOAD_MESSAGE = (
    "settings.env changed after this page loaded. Reload the page, then make"
    " the change again."
)


def create_app(
    startup: Startup | None = None,
    workspace: Workspace | None = None,
    env: Mapping[str, str] | None = None,
    build: Callable[[Mapping[str, str]], Startup] = build_startup,
) -> FastAPI:
    """The ASGI app.

    The collaborators are injectable so the offline lane can drive the app
    without credentials: ``startup`` supplies a stub runner, ``workspace``
    lets a test bound the jobs held or hand the app a store of its own, and
    ``env`` replaces the process environment, and ``build`` replaces
    :func:`build_startup` for the startup and for each save.

    The app reads one mapping:
    :func:`~analysis_service.settings_variables.deployment_env` adds the lines
    of ``settings.env`` in the settings directory to the process environment
    (ADR 0092). Where it refuses the file, the cell stays empty and the
    diagnostic page names the refusal, and the store reads the process
    environment alone.

    The app builds the store and the workspace first, from that mapping,
    before the Deployment and without it (ADR 0093). Without a
    ``workspace``, it builds its store with
    :func:`~webapp.settings_view.build_app_store` from the service's backend
    settings, and an unusable setting raises, also where the Deployment then
    fails to build. Where ``ANALYSIS_JOB_STORE`` is unset, the store is
    ``sql`` on SQLite in the data directory, which the app makes and
    migrates, so a first run needs no setting and keeps its runs
    (ADR 0111). The app logs the store class it built. At startup, before the app
    takes a request, the app always runs
    :func:`~analysis_service.store_backends.store_lifespan` over the store, as
    the service does.

    The app holds what the routes run on in one :class:`Cell`, as
    ``app.state.cell``. A save on the Settings page that passes replaces it
    (ADR 0093).
    """
    process_env = os.environ if env is None else env
    try:
        env = deployment_env(process_env)
    except SettingsFileError as exc:
        logger.error("settings.env was refused at startup: %s", exc)
        env = process_env
        startup = Startup(settings=None, carried_frameworks=(), tiers=None, error=exc)
    if workspace is None:
        store = build_app_store(env)
        logger.info("job store: %s", type(store).__name__)
        workspace = Workspace(store=store)
    state = build(env) if startup is None else startup
    cell = Cell(state.current(workspace.store))
    save_lock = asyncio.Lock()
    app = FastAPI(
        title="Work Agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lambda app: store_lifespan(workspace.store, env),
    )
    app.state.cell = cell
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)
    for refusal, status in _REFUSAL_STATUS.items():
        app.add_exception_handler(refusal, partial(_refused, status))
    app.exception_handler(NotAdmitted)(_not_admitted)
    app.add_exception_handler(AlreadyResumed, partial(_worded, 409))
    app.add_exception_handler(StaleRevision, partial(_worded, 409))
    app.add_exception_handler(SourcesOverLimit, partial(_worded, 400))
    app.exception_handler(CellEmpty)(_cell_empty)
    app.exception_handler(_BodyUnread)(_body_unread)

    def unavailable() -> JSONResponse:
        """The app came up without a runner: the diagnostic page says why."""
        return _message(503, str(state.error))

    def no_such_run() -> JSONResponse:
        return _message(404, "This analysis is no longer available.")

    @app.get("/", response_class=HTMLResponse)
    async def form_page() -> Response:
        current = cell.read(
            lambda: response(diagnostic_page(state, env), status_code=503)
        )
        return response(
            render(
                _FORM_PAGE,
                _FORM_GRANTS,
                script=client_scripts(
                    "sidebar.js",
                    "service_answer.js",
                    "workspace.js",
                    "answer_editor.js",
                    "question_list.js",
                    "text_file.js",
                    "form_controls.js",
                    "first_run.js",
                ),
                answer_limits=script_json(dict(ANSWER_LIMITS)),
                facet_answers=script_json(list(FACET_ANSWERS)),
                form_words=script_json(island_tables(FORM_WORDS)),
                stage_count=str(STAGE_COUNT),
                workspace_style=WORKSPACE_STYLE,
                sidebar=_SIDEBAR,
                theme=_THEME,
                frameworks=_framework_fields(current.answers.carried_frameworks),
                questions=_QUESTIONS_FIELD,
                name_limit=str(MAX_SYSTEM_NAME_CHARS),
                source_limit=str(current.answers.limits.max_total_bytes),
                source_formats=_SOURCE_FORMATS,
                file_refusals=script_json(
                    file_refusals(
                        current.answers.limits.max_total_bytes,
                        current.max_owner_files,
                    )
                ),
                upload_accept=_UPLOAD_ACCEPT,
                page_labels=script_json(PAGE_SOURCE_LABELS),
                element_type_nouns=script_json(dict(ELEMENT_TYPE_NOUNS)),
                store_note=""
                if workspace.store.durable
                else escape(_MEMORY_STORE_NOTE),
            )
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page() -> Response:
        """The Settings page. It serves also where the Deployment did not
        build, with the startup's error in a banner."""
        error = None if cell.current is not None else state.error
        return response(
            render(
                _SETTINGS_PAGE,
                _SETTINGS_GRANTS,
                script=client_scripts("sidebar.js", "service_answer.js", "settings.js"),
                settings=script_json(settings_view(process_env, error)),
                workspace_style=WORKSPACE_STYLE,
                sidebar=_SIDEBAR,
                theme=_THEME,
            )
        )

    @app.post("/settings")
    async def save_settings(request: Request) -> Response:
        """Check a save, write it and swap the cell (ADR 0092, ADR 0093).

        :func:`~webapp.settings_save.apply_save` runs the save under the lock
        and swaps the cell to the Deployment that its check built. A refused
        save is a 422 that holds one message for each refusal, a revision
        that moved is a 409, and a write that fails is a 500; each of them
        changes nothing. The response holds the new page data and the keys
        of each store setting that takes effect after a restart.
        """
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        try:
            save = parse_save(await _json_body(request, MAX_SAVE_BYTES))
        except ValueError:
            return _message(
                400,
                "Expected a JSON object with a 'revision', a 'changes' object and"
                " a 'remove' list.",
            )
        path = settings_dir(process_env) / SETTINGS_FILE

        def swap(built: Startup) -> None:
            cell.current = built.current(workspace.store)

        try:
            outcome = await apply_save(save_lock, path, save, process_env, build, swap)
        except OSError as exc:
            logger.error("the settings could not be written: %s", exc)
            return _message(500, f"The settings could not be written: {exc.strerror}")
        match outcome:
            case SaveConflict():
                return _message(409, _RELOAD_MESSAGE)
            case SaveRefused(found):
                return _refused_save(found)
            case Saved(saved_env):
                logger.info(
                    "settings saved: %s", ", ".join([*save.changes, *save.remove])
                )
                return JSONResponse(
                    {
                        "settings": settings_view(process_env, None),
                        "restart": restart_keys(
                            with_store_defaults(env), with_store_defaults(saved_env)
                        ),
                    }
                )

    @app.post("/settings/rows")
    async def settings_rows(request: Request) -> Response:
        """The keys of the store rows that the Settings page shows with its
        unsaved changes applied (:func:`~webapp.settings_view.store_shown`).
        It writes nothing. The page sends no secret, and the route reads none.
        """
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        try:
            changes = parse_changes(await _json_body(request, MAX_SAVE_BYTES))
        except ValueError:
            return _message(400, "Expected a JSON object with a 'changes' object.")
        return JSONResponse({"shown": store_shown(process_env, changes)})

    @app.get("/example", response_class=PlainTextResponse)
    async def example() -> Response:
        """The sample description, read from the file the examples also run."""
        return PlainTextResponse(SAMPLE.read_text(encoding="utf-8"))

    @app.get("/files", response_class=HTMLResponse)
    async def files_page() -> Response:
        """The Files section: the operator's Owner Files (ADR 0118)."""
        current = cell.read(
            lambda: response(diagnostic_page(state, env), status_code=503)
        )
        limits: FilesLimits = {
            "max_owner_files": current.max_owner_files,
            "max_bytes": current.answers.limits.max_total_bytes,
            "name_chars": MAX_LABEL_CHARS,
        }
        return response(
            render(
                _FILES_PAGE,
                _FILES_GRANTS,
                script=client_scripts(
                    "sidebar.js", "text_file.js", "service_answer.js", "files.js"
                ),
                workspace_style=WORKSPACE_STYLE,
                sidebar=_SIDEBAR,
                theme=_THEME,
                source_formats=_SOURCE_FORMATS,
                file_refusals=script_json(
                    file_refusals(limits["max_bytes"], limits["max_owner_files"])
                ),
                upload_accept=_UPLOAD_ACCEPT,
                files_limits=script_json(limits),
            )
        )

    @app.get("/workspace/files")
    async def workspace_files() -> Response:
        """The operator's Owner Files, the newest first, with no text."""
        held = await workspace.store.principal(OPERATOR)
        files = [] if held is None else await workspace.store.owned_files(held.owner_id)
        return JSONResponse([_file_view(file) for file in files])

    @app.post("/workspace/files")
    async def upload_file(request: Request) -> Response:
        """Keep an uploaded file in Files. The first upload of an operator
        who holds no principal creates it, as a first job does (ADR 0064)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        raw = await _job_body(request, current.answers)
        try:
            body = _body_of(raw, _FileUpload)
            upload = _FileUpload.model_validate(body)
        except _UnknownKey as exc:
            return _message(400, str(exc))
        except TypeError:
            return _message(
                400, "Expected a JSON object with a name, a kind, a format and a text."
            )
        except ValueError as exc:
            return _message(400, refusal_text(exc))
        refusal = upload_refusal(
            upload.name,
            upload.kind,
            upload.format,
            upload.text,
            max_bytes=current.answers.limits.max_total_bytes,
            page_labels=_RESERVED_NAMES,
        )
        if refusal is not None:
            return _message(400, refusal)
        held = await workspace.store.principal(OPERATOR)
        principal = held or Principal.new(OPERATOR)
        new = None if held is not None else principal
        file = OwnerFile.create(
            owner_id=principal.owner_id,
            name=upload.name,
            kind=upload.kind,
            text=upload.text,
        )
        outcome = await workspace.store.add_file(
            file, upload.text, limit=current.max_owner_files, principal=new
        )
        match outcome:
            case "added":
                if new is not None:
                    log_created(new)
                return JSONResponse(_file_view(file), status_code=201)
            case "name_in_use":
                return _message(409, name_in_use(upload.name))
            case "over_limit":
                return _message(409, over_limit(current.max_owner_files))
            case "principal_exists":
                return _message(
                    409, "Another upload started at the same time. Try again."
                )

    @app.delete("/workspace/files/{file_id}")
    async def delete_owner_file(file_id: str, request: Request) -> Response:
        """Delete an Owner File and keep nothing. Each job that used it keeps
        its own copy (ADR 0119)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        if not OWNER_FILE_ID_PATTERN.fullmatch(file_id):
            return _message(404, _NO_SUCH_FILE)
        held = await workspace.store.principal(OPERATOR)
        if held is None or not await workspace.store.delete_file(
            file_id, held.owner_id
        ):
            return _message(404, _NO_SUCH_FILE)
        return Response(status_code=204)

    @app.get("/controls", response_class=HTMLResponse)
    async def controls_page() -> Response:
        """The Controls page: the operator's Controls (ADR 0127)."""
        current = cell.read(
            lambda: response(diagnostic_page(state, env), status_code=503)
        )
        limits = current.control_limits
        return response(
            render(
                _CONTROLS_PAGE,
                _CONTROLS_GRANTS,
                script=client_scripts(
                    "sidebar.js", "text_file.js", "service_answer.js", "controls.js"
                ),
                workspace_style=WORKSPACE_STYLE,
                sidebar=_SIDEBAR,
                theme=_THEME,
                source_formats=_SOURCE_FORMATS,
                file_refusals=script_json(evidence_refusals(limits)),
                controls_page=script_json(page_data(limits)),
            )
        )

    @app.get("/workspace/controls")
    async def workspace_controls() -> Response:
        """The operator's Controls, the latest save first, with no evidence
        text."""
        held = await workspace.store.principal(OPERATOR)
        controls = (
            [] if held is None else await workspace.store.list_controls(held.owner_id)
        )
        return JSONResponse([control_view(control) for control in controls])

    async def control_body(
        request: Request, current: Current, model: type[_ControlBodyT]
    ) -> _ControlBodyT | JSONResponse:
        """The body of a Control save, or the ``400`` that refuses it."""
        raw = await _json_body(request, max_control_body_bytes(current.control_limits))
        try:
            return model.model_validate(_body_of(raw, model))
        except _UnknownKey as exc:
            return _message(400, str(exc))
        except TypeError:
            return _message(400, _CONTROL_BODY)
        except ValueError as exc:
            return _message(400, save_refusal(exc))

    @app.post("/workspace/controls")
    async def add_control(request: Request) -> Response:
        """Keep a new Control. The first save of an operator who holds no
        principal creates it, as a first job does (ADR 0064)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        save = await control_body(request, current, ControlSave)
        if isinstance(save, JSONResponse):
            return save
        held = await workspace.store.principal(OPERATOR)
        principal = held or Principal.new(OPERATOR)
        new = None if held is not None else principal
        try:
            control, texts = saved_control(save, owner_id=principal.owner_id)
        except ValueError as exc:
            return _message(400, save_refusal(exc))
        limits = current.control_limits
        outcome = await workspace.store.add_control(
            control, texts, limits=limits, principal=new
        )
        match outcome:
            case "added":
                if new is not None:
                    log_created(new)
                return JSONResponse(control_view(control), status_code=201)
            case EvidenceRefusal(message=message):
                return _message(400, message)
            case "name_in_use":
                return _message(409, _name_taken(save.name))
            case "alias_in_use":
                return _message(409, _CONTROL_ALIAS_IN_USE)
            case "over_limit":
                return _message(409, controls_full(limits))
            case "principal_exists":
                return _message(
                    409, "Another save started at the same time. Try again."
                )

    def stale_control(latest: Control) -> JSONResponse:
        """The ``409`` of an edit that read an older version. It carries the
        version the store holds now, so the page can show what changed and
        keep the owner's draft. The route checks the version before the
        evidence list, because a stale draft can name a file that the newer
        version removed; the store checks it again under its lock."""
        return JSONResponse(
            {"message": _CONTROL_STALE, "current": control_view(latest)},
            status_code=409,
        )

    @app.put("/workspace/controls/{control_id}")
    async def replace_control(control_id: str, request: Request) -> Response:
        """Replace the operator's Control in place, and keep no earlier
        version. The edit names the ``updated_at`` it read (ADR 0127)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        edit = await control_body(request, current, ControlEdit)
        if isinstance(edit, JSONResponse):
            return edit
        if not CONTROL_ID_PATTERN.fullmatch(control_id):
            return _message(404, _NO_SUCH_CONTROL)
        held = await workspace.store.principal(OPERATOR)
        stored = (
            None
            if held is None
            else await workspace.store.get_control(control_id, held.owner_id)
        )
        if stored is None:
            return _message(404, _NO_SUCH_CONTROL)
        if stored.updated_at != edit.updated_at:
            return stale_control(stored)
        try:
            control, texts = saved_control(
                edit,
                owner_id=stored.owner_id,
                control_id=control_id,
                held=stored.document.evidence,
            )
        except ValueError as exc:
            return _message(400, save_refusal(exc))
        outcome = await workspace.store.replace_control(
            control,
            texts,
            limits=current.control_limits,
            expected_updated_at=edit.updated_at,
        )
        match outcome:
            case "replaced":
                return JSONResponse(control_view(control))
            case EvidenceRefusal(message=message):
                return _message(400, message)
            case "absent":
                return _message(404, _NO_SUCH_CONTROL)
            case "stale":
                latest = await workspace.store.get_control(control_id, stored.owner_id)
                if latest is None:
                    return _message(404, _NO_SUCH_CONTROL)
                return stale_control(latest)
            case "name_in_use":
                return _message(409, _name_taken(edit.name))
            case "alias_in_use":
                return _message(409, _CONTROL_ALIAS_IN_USE)

    @app.delete("/workspace/controls/{control_id}")
    async def delete_control(control_id: str, request: Request) -> Response:
        """Delete the operator's Control and its evidence, and keep nothing.
        Each job that applied it keeps its own copy (ADR 0127)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        if not CONTROL_ID_PATTERN.fullmatch(control_id):
            return _message(404, _NO_SUCH_CONTROL)
        held = await workspace.store.principal(OPERATOR)
        if held is None or not await workspace.store.delete_control(
            control_id, held.owner_id
        ):
            return _message(404, _NO_SUCH_CONTROL)
        return Response(status_code=204)

    @app.get("/workspace/controls/{control_id}/evidence/{evidence_id}")
    async def control_evidence(control_id: str, evidence_id: str) -> Response:
        """The text of one evidence file of the operator's Control, as an
        attachment under the file's own name: only a text that matches the
        digest the Control lists."""
        if not (
            CONTROL_ID_PATTERN.fullmatch(control_id)
            and EVIDENCE_ID_PATTERN.fullmatch(evidence_id)
        ):
            return _message(404, _NO_SUCH_EVIDENCE)
        held = await workspace.store.principal(OPERATOR)
        stored = (
            None
            if held is None
            else await workspace.store.get_control(control_id, held.owner_id)
        )
        # read_evidence answers the text only, so the name comes from the
        # document of the same Control.
        file = None if stored is None else stored.document.file(evidence_id)
        if held is None or file is None:
            return _message(404, _NO_SUCH_EVIDENCE)
        text = await workspace.store.read_evidence(
            control_id, evidence_id, held.owner_id
        )
        if text is None:
            return _message(404, _NO_SUCH_EVIDENCE)
        # RFC 6266: the plain name is a fixed fallback, and filename* carries
        # the owner's name percent-encoded, so only letters, digits and
        # "_.-~" reach the header as themselves.
        disposition = (
            'attachment; filename="evidence.txt";'
            f" filename*=UTF-8''{quote(file.name, safe='')}"
        )
        return PlainTextResponse(text, headers={"Content-Disposition": disposition})

    @app.post("/workspace/controls/{control_id}/aliases")
    async def keep_this_name(control_id: str, request: Request) -> Response:
        """Append a name to the scope of the operator's Control as an alias:
        "keep this name" (ADR 0124). Only the owner's click writes an alias;
        the service never writes one of its own."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        raw = await _job_body(request, current.answers)
        try:
            alias = _AliasBody.model_validate(_body_of(raw, _AliasBody)).alias
        except _UnknownKey as exc:
            return _message(400, str(exc))
        except TypeError:
            return _message(400, "Expected a JSON object with an alias.")
        except ValueError as exc:
            return _message(400, save_refusal(exc))
        if not CONTROL_ID_PATTERN.fullmatch(control_id):
            return _message(404, _NO_SUCH_CONTROL)
        held = await workspace.store.principal(OPERATOR)
        control = (
            None
            if held is None
            else await workspace.store.get_control(control_id, held.owner_id)
        )
        if control is None:
            return _message(404, _NO_SUCH_CONTROL)
        document = control.document
        scope = document.scope
        try:
            widened = ControlScope.model_validate(
                scope.model_dump() | {"aliases": [*scope.aliases, alias]}
            )
        except ValueError as exc:
            return _message(400, save_refusal(exc))
        outcome = await workspace.store.replace_control(
            Control.create(
                owner_id=control.owner_id,
                document=document.model_copy(update={"scope": widened}),
            ),
            {},
            limits=current.control_limits,
            expected_updated_at=control.updated_at,
        )
        match outcome:
            case "replaced":
                return JSONResponse({"aliases": widened.aliases})
            case "absent":
                return _message(404, _NO_SUCH_CONTROL)
            case "stale":
                return _message(
                    409, "This Control changed at the same time. Try again."
                )
            case "alias_in_use":
                return _message(
                    409, f"Another Control's scope already holds “{alias}”."
                )
            case "name_in_use":
                return _message(409, "Another Control took this name. Try again.")
            case EvidenceRefusal(message=message):
                return _message(409, message)

    @app.get("/workspace/runs")
    async def workspace_runs() -> Response:
        current = cell.read(lambda: JSONResponse([]))
        return JSONResponse(await workspace.snapshots(current.answers))

    @app.get("/workspace/runs/{run_id}")
    async def workspace_run(run_id: str) -> Response:
        """One analysis as the page reopens it: its latest job, with its
        questions where it waits, and its failure where it stopped."""
        current = cell.read(no_such_run)
        answers = current.answers
        store = workspace.store
        record = await workspace.owned(answers, run_id)
        if record is None:
            return no_such_run()
        owner_id = record.owner_id
        while (locker := await store.resumed_by(record.id, owner_id)) is not None:
            locked = await store.owned(locker, owner_id)
            if locked is None:
                break
            record = locked
        record = await workspace.parsable(answers, record.id)
        if record is None:
            return no_such_run()
        state = await workspace.state(record)
        payload = await workspace.snapshot(answers, record, state)
        if state.page_status == "running":
            # A job that runs is a job of this process, so each node of its
            # events is a node of a graph of its selection.
            line = progress_line(record, state)
            payload["progress"] = line(
                [event for event in record.events if event.kind == "node"]
            )
        payload["selection"] = [
            {"name": selection.name, "options": dict(selection.options)}
            for selection in record.whole_frameworks()
        ]
        sources = await store.sources(record.id, owner_id)
        if sources is None:
            await answers.missing(record, "sources")
        # The text of an Owner File's copy stays out of the description; the
        # page lists each copy under "Files used" (ADR 0119).
        payload["description"] = "\n\n".join(
            source.text
            for source in sources
            if source.kind != "answers" and source.owner_file_id is None
        )
        payload["owner_files"] = [
            {"id": source.owner_file_id, "label": source.label, "kind": source.kind}
            for source in sources
            if source.owner_file_id is not None
        ]
        # Read from the job's copy, so a later edit or delete of a Control
        # changes nothing here (ADR 0127).
        copies = await store.controls(record.id, owner_id)
        if copies is None:
            await answers.missing(record, "controls")
        # A waiting job's questions and its Controls' model come from one
        # read. A job that serves no model leaves its Controls pending.
        asked = None
        if record.status == "awaiting-answers":
            asked = await answers.answerable(
                record.id, owner_id, sources=sources, controls=copies
            )
        elif copies:
            with suppress(NotCompleted, Withheld):
                asked = await answers.answerable(
                    record.id, owner_id, sources=sources, controls=copies
                )
        payload["controls_used"] = controls_used(
            copies, record.control_scopes, None if asked is None else asked.model
        )
        # While the service continues a job at its pause, the page shows the
        # continue rather than the questions (ADR 0125).
        payload["continuing"] = state.continuing
        if payload["continuing"]:
            asked = None
        payload["error"] = record.error
        payload["issues"] = [
            {"code": issue.code, "message": issue.message}
            for issue in record.validation_issues
        ]
        if asked is not None and record.status == "awaiting-answers":
            payload["questions"] = (
                {"run": record.id}
                | await answers.payload_of(asked)
                | _refusal_key(state)
            )
        return JSONResponse(payload)

    @app.delete("/workspace/runs/{run_id}")
    async def delete_analysis(run_id: str, request: Request) -> Response:
        """Delete every job of the analysis, the newest first, and keep each
        job's usage row (ADR 0066). Refused while a job of it has not ended.
        Where the store keeps a job partway, the ``409`` lists in ``deleted``
        the IDs of the jobs that the route deleted first (ADR 0107)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        answers = cell.read(unavailable).answers
        group = await workspace.analysis(answers, run_id)
        if not group:
            return no_such_run()
        if not all(ended(job) for job in group):
            return _message(409, _STILL_RUNNING)
        deleted: list[str] = []
        for job in reversed(group):
            removed = await audited_delete(
                workspace.store, job.id, job.owner_id, "owner"
            )
            if not isinstance(removed, DeleteRefusal):
                deleted.append(job.id)
            elif removed.reason != "absent":
                # A job of the group started after the check above. The jobs
                # deleted before it stay deleted (ADR 0107).
                message = _STILL_RUNNING
                if deleted:
                    message += f" The app deleted {len(deleted)} of its jobs first."
                return JSONResponse(
                    {"message": message, "deleted": deleted}, status_code=409
                )
        return Response(status_code=204)

    @app.post("/workspace/runs/{run_id}/stop")
    async def stop_analysis(run_id: str, request: Request) -> Response:
        """Stop the run of the analysis that has not ended (ADR 0109). The
        route answers ``204`` once the run ended, ``202`` where it still
        ends, and ``409`` where no run of the analysis runs."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        answers = cell.read(unavailable).answers
        group = await workspace.analysis(answers, run_id)
        if not group:
            return no_such_run()
        if not await workspace.stop(job.id for job in group if not ended(job)):
            return _message(409, _NOT_RUNNING)
        group = await workspace.analysis(answers, run_id)
        return Response(status_code=204 if all(map(ended, group)) else 202)

    @app.put("/workspace/runs/{run_id}/title")
    async def rename_analysis(run_id: str, request: Request) -> Response:
        """Write the title on every job of the analysis (ADR 0106). A blank
        title removes it, and the list shows the system name again."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        answers = cell.read(unavailable).answers
        body = await _job_body(request, answers)
        sent = body.get("title") if isinstance(body, dict) else None
        if not isinstance(sent, str):
            return _message(400, _NAME_REFUSED)
        try:
            title = clean_system_name(sent)
        except ValueError as refused:
            # The reasons of the rule name a bound or a character class, never
            # the text it refused.
            return _message(422, f"The title {refused}.")
        group = await workspace.analysis(answers, run_id)
        if not group:
            return no_such_run()
        for job in group:
            await workspace.store.set_title(job.id, job.owner_id, title)
        return JSONResponse({"title": title})

    @app.post("/analyze")
    async def analyze(request: Request) -> Response:
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        answers = current.answers
        raw = await _job_body(request, answers)
        try:
            body = _body_of(raw, _Submission)
        except _UnknownKey as exc:
            return _message(400, str(exc))
        except TypeError:
            return _message(
                400,
                "Expected a JSON body with a 'sources' list and a 'frameworks'"
                " list naming frameworks this install carries.",
            )
        try:
            submission = _Submission.model_validate(body)
        except ValueError as exc:
            # The sentence /v1 answers with: each field and rule a schema
            # refusal names, and never a value the submitter sent.
            return _message(400, refusal_text(exc))
        try:
            name = clean_system_name(submission.name)
        except ValueError:
            return _message(400, _NAME_REFUSED)
        # The steps are the one submission's (:meth:`JobAnswers.submit`); this
        # route maps a selection refusal to the page's status, and a handler
        # answers each other refusal. The room is made around the whole
        # submission, so a refused body deletes nothing.
        try:
            async with workspace.make_room(answers):
                started = await answers.submit(
                    OPERATOR,
                    Submission(
                        sources=submission.sources,
                        frameworks=submission.frameworks,
                        system_name=name,
                        questions=submission.questions,
                        answers_from=submission.answers_from,
                        owner_files=submission.owner_files,
                        controls=submission.controls,
                    ),
                    start=workspace.start(current.deadline_seconds, answers),
                )
        except SelectionError as exc:
            # The framework or option the refusal names, never a value sent.
            return _message(400, refusal_text(exc))
        return JSONResponse({"run": started.job_id})

    @app.post("/answer/{run_id}")
    async def answer(run_id: str, request: Request) -> Response:
        """Answer a run's questions, and start the run that resumes from it.

        The run answered is a paused one or a finished one. The new run starts
        at ``prepare`` from what that run reached, so nothing is extracted
        again. An empty answer list continues a paused run without answers; a
        finished run has nothing to continue. A save answers with the
        new revision. ``control_scopes`` states the resolution of each Control
        a paused run copied (ADR 0124).
        """
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        answers = current.answers
        raw = await _job_body(request, answers)
        try:
            control_scopes = _control_scopes(raw)
        except ValidationError as refused:
            return _message(400, refusal_text(refused))
        try:
            submitted = read_answers(raw)
        except BodyRefused as refused:
            message = await _refusal_words(refused, workspace, answers, run_id)
            if refused.over_a_bound:
                message += ". Save your answers in parts, then run the follow-up."
            return _message(400, message)
        owner_id = await workspace.owner_id(answers, run_id)
        # A save starts no job, so it needs no room.
        room = (
            nullcontext()
            if submitted.save
            else workspace.make_room(answers, answering=run_id)
        )
        async with room:
            outcome = await answers.answer(
                run_id,
                owner_id,
                submitted,
                start=workspace.start(current.deadline_seconds, answers),
                control_scopes=control_scopes,
            )
        if isinstance(outcome, Started):
            return JSONResponse({"run": outcome.job_id})
        if isinstance(outcome, DraftSaved):
            # A draft runs nothing and uses no follow-up (ADR 0070).
            return JSONResponse({"saved": True, "revision": outcome.revision})
        # A save runs no model: the answers go onto the paused run, and the
        # list is read again off the model with them in.
        return JSONResponse({"run": run_id} | await answers.questions(run_id, owner_id))

    @app.post("/amend/{run_id}")
    async def amend(run_id: str, request: Request) -> Response:
        """Extract a paused run's description again with an amendment (ADR 0072).

        The new run reads the paused run's sources with the amendment added,
        pauses again, and carries the paused run's answers.
        """
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        answers = current.answers
        try:
            amendment = read_amendment(await _job_body(request, answers))
        except BodyRefused as refused:
            return _message(400, str(refused))
        owner_id = await workspace.owner_id(answers, run_id)
        async with workspace.make_room(answers, answering=run_id):
            started = await answers.amend(
                run_id,
                owner_id,
                amendment.text,
                revision=amendment.revision,
                start=workspace.start(current.deadline_seconds, answers),
            )
        return JSONResponse({"run": started.job_id})

    @app.get("/events/{run_id}")
    async def events(run_id: str) -> Response:
        """The job's status events, as the store records them, until the job
        reaches a terminal status, with the progress line after each node
        event of a job that has not ended."""

        def no_stream() -> Response:
            return PlainTextResponse("no such run", status_code=404)

        current = cell.read(no_stream)
        record = await workspace.envelope(current.answers, run_id)
        if record is None:
            return no_stream()
        return StreamingResponse(
            _stream(
                workspace,
                current.answers,
                run_id,
                # A job that ended before this request can be a record of an
                # earlier build, with node names that no graph holds.
                None
                if ended(record)
                else progress_line(record, await workspace.state(record)),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def report_of(run_id: str) -> tuple[Answerable, Report] | None:
        """The run's answerable state and its report, or ``None`` where the
        app serves no report of it."""
        current = cell.read(
            lambda: PlainTextResponse("no such report", status_code=404)
        )
        answers = current.answers
        try:
            owner_id = await workspace.owner_id(answers, run_id)
            answerable = await answers.answerable(run_id, owner_id)
        except (NoSuchJob, NotCompleted, Withheld):
            return None
        if answerable.report is None:
            return None
        return answerable, answerable.report

    @app.get("/report/{run_id}", response_class=HTMLResponse)
    async def report_page(run_id: str) -> Response:
        served_report = await report_of(run_id)
        if served_report is None:
            return PlainTextResponse("no such report", status_code=404)
        answerable, report = served_report
        answers = cell.read(unavailable).answers
        owner_id = answerable.record.owner_id
        # The page compares with the report before it where the store holds
        # that report under this service's schema version and
        # :meth:`JobAnswers.servable` lets it be served, and else shows none,
        # as for a parent that is gone.
        resumption = answerable.record.resumption
        parent = (
            None
            if resumption is None
            else await workspace.owned(answers, resumption.parent_id)
        )
        previous = None
        if parent is not None and served(answers, parent):
            previous = await workspace.store.report(parent.id, owner_id)
        return response(
            render_report(
                report,
                answerable.state(),
                previous,
                None if resumption is None else resumption.parent_id,
                _section_of(answerable.record, report),
            )
        )

    @app.get("/report/{run_id}/export.md")
    async def report_markdown_export(run_id: str) -> Response:
        """The report as one Markdown file, with its Controls section."""
        served_report = await report_of(run_id)
        if served_report is None:
            return PlainTextResponse("no such report", status_code=404)
        answerable, report = served_report
        text = report_markdown(
            _name_of(answerable.record),
            report,
            _section_of(answerable.record, report),
        )
        return PlainTextResponse(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="report.md"'},
        )

    @app.get("/report/{run_id}/export.json")
    async def report_json_export(run_id: str) -> Response:
        """The report and its Controls section as one JSON object."""
        served_report = await report_of(run_id)
        if served_report is None:
            return PlainTextResponse("no such report", status_code=404)
        answerable, report = served_report
        section = _section_of(answerable.record, report)
        return JSONResponse(
            {
                "report": report.model_dump(mode="json"),
                "controls": [entry.model_dump(mode="json") for entry in section],
            },
            headers={"Content-Disposition": 'attachment; filename="report.json"'},
        )

    @app.get("/report/{run_id}/export.csv")
    async def report_csv_export(run_id: str) -> Response:
        """The report's findings as one CSV file, a row for each."""
        served_report = await report_of(run_id)
        if served_report is None:
            return PlainTextResponse("no such report", status_code=404)
        answerable, report = served_report
        text = report_csv(report, _section_of(answerable.record, report))
        # The byte order mark tells a spreadsheet that the file is UTF-8.
        return Response(
            "\ufeff" + text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="report.csv"'},
        )

    @app.post("/correct/{run_id}")
    async def correct(run_id: str, request: Request) -> Response:
        """Correct a final report's answers, and run nothing (ADR 0054)."""
        if (forbidden := _cross_origin(request)) is not None:
            return forbidden
        current = cell.read(unavailable)
        answers = current.answers
        try:
            corrections = read_corrections(await _job_body(request, answers))
        except BodyRefused as refused:
            return _message(
                400, await _refusal_words(refused, workspace, answers, run_id)
            )
        corrected = await answers.correct(
            run_id,
            await workspace.owner_id(answers, run_id),
            corrections.facts,
            revision=corrections.revision,
        )
        return JSONResponse({"run": corrected.job_id})

    return app


def _framework_fields(frameworks: Sequence[FrameworkName]) -> str:
    """The picker: a checkbox per carried framework, and a control per option.

    **Nothing here names a framework or an option.** The rows come from what
    this install carries and the controls from each package's own options
    model, so a package registered later gets its controls without an edit
    here — and a package that needs no options gets a bare checkbox, because
    an empty options model has no fields to walk.

    Every box starts unticked so the submitter chooses the analyses to run. The
    :func:`~analysis_service.selection.resolve_selection` allow-list is what
    rules on the answer, in :meth:`~analysis_service.job_answers.JobAnswers.submit`.
    """
    rows = []
    for name in frameworks:
        options = "".join(
            _option_control(name, field, annotation)
            for field, annotation in _option_fields(name)
        )
        rows.append(
            f'<div class="pick"><label><input type="checkbox" name="framework"'
            f' value="{escape(name)}"> <b>{escape(name)}</b></label>'
            f'<span class="opts">{options}</span></div>'
        )
    return "\n".join(rows)


def _option_fields(name: FrameworkName) -> list[tuple[str, object]]:
    """One ``(field, annotation)`` per option the named package declares."""
    return [
        (field, spec.annotation)
        for field, spec in package_for(name).options.model_fields.items()
    ]


def _option_control(name: FrameworkName, field: str, annotation: object) -> str:
    """One option's ``<select>``, its choices read off the field's own type.

    **A closed set is the only option this app can render.** A package declares
    its options as a Pydantic model and this walks the declaration, so the
    choices offered and the choices accepted are one source rather than two
    that drift. A field annotated as anything but a ``Literal`` raises rather
    than rendering a control that cannot be filled in — the same fail-loud
    :func:`render_report` takes when its template has no block to inject into.

    Each choice carries its **JSON** in ``value``, and the page parses it back
    before posting. A level is ``Literal[1, 2, 3]``, so a control posting the
    string ``"1"`` would hand the options model a value of the wrong type and
    turn a filled-in form into a 400.
    """
    choices = get_args(annotation)
    if not choices:
        raise RuntimeError(
            f"framework {name!r} declares option {field!r} as {annotation!r},"
            " which is not a closed set of choices this form can offer"
        )
    rendered = "".join(
        f'<option value="{escape(json.dumps(choice))}">{escape(str(choice))}</option>'
        for choice in choices
    )
    return (
        f'<label> {escape(field)} <select data-framework="{escape(name)}"'
        f' data-option="{escape(field)}">{rendered}</select></label>'
    )


async def _refusal_words(
    refused: BodyRefused, workspace: Workspace, answers: JobAnswers, run_id: str
) -> str:
    """The page's words for a refused body.

    A refusal that names a question of the run names it by its label, which
    means something on a page where its six-part key does not; the words
    are the rule's own, which name the submitter's choices, so they are safe
    to show. The run is read only for that label. Every other refusal is the
    reader's own sentence.
    """
    if refused.question is None:
        return str(refused)
    try:
        answerable = await answers.answerable(
            run_id, await workspace.owner_id(answers, run_id)
        )
    except (NoSuchJob, NotCompleted, Withheld):
        return str(refused)
    label = answerable.state().questions.label(refused.question)
    if label is None:
        return str(refused)
    return f'The answer to "{label}" was refused: {refused.reason}'


#: How a run's stream ends: the event of its last frame. ``first_run.js``
#: listens for each one.
EndFrame = Literal[
    "failed", "questions", "done", "rejected", "stopped", "continuing", "continued"
]


def _frame(event: EndFrame, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream(
    workspace: Workspace,
    answers: JobAnswers,
    run_id: str,
    progress: Callable[[Sequence[JobEvent]], dict[str, object]] | None,
) -> AsyncIterator[str]:
    """Every status event the store records for the job, as it records them,
    then one frame for how it ended. Where ``progress`` is given, each node
    event gets a frame too: the progress line after that node, and no node
    name. The first frame is then the progress line before any node, so the
    bar of a Resumed Job starts at its lowest stage
    (:func:`webapp.page_words.lowest_stage`).

    The store's event log is the one record of the run. The page acts on its
    end, so the last frame carries what the page shows for it: the questions
    a pause asks, where a report is, why a report is withheld, why a
    description was refused, or why a run failed.
    """

    def frame(event: JobEvent) -> str | None:
        if event.node is None:
            return event.sse_frame()
        if progress is None:
            return None
        return event.framed(json.dumps(progress([event])))

    if progress is not None:
        yield f"event: node\ndata: {json.dumps(progress([]))}\n\n"
    async for written in event_frames(workspace.store, run_id, frame=frame):
        yield written
    try:
        record = await workspace.envelope(answers, run_id)
    except UnreadableSchema as refusal:
        yield _frame("failed", {"message": str(refusal)})
        return
    if record is None:
        return
    if record.waits_to_continue():
        # The service continues the job at once (ADR 0125). The page follows
        # the Resumed Job, or shows the pause with the refusal. A job that no
        # task of this process continues stays paused.
        if (await workspace.state(record)).continuing:
            yield _frame("continuing", {})
            await workspace.settled(run_id)
        child = await workspace.store.resumed_by(run_id, record.owner_id)
        if child is not None:
            yield _frame("continued", {"run": child})
            return
    status = record.status
    if status == "awaiting-answers":
        try:
            payload = await answers.questions(run_id, record.owner_id)
        except tuple(_REFUSAL_STATUS) as refusal:
            # The response has started, so a refusal is a frame, never a status.
            yield _frame("failed", {"message": str(refusal)})
            return
        # Read after the continue ends, which stores a refusal or a Resumed Job.
        state = await workspace.state(record)
        yield _frame("questions", {"run": run_id} | payload | _refusal_key(state))
    elif status == "completed":
        # The envelope read above refuses an unreadable record. The frame
        # names why a report is withheld, which :func:`served` does not give.
        try:
            answers.servable(record)
        except Withheld as refusal:
            yield _frame("failed", {"message": str(refusal)})
            return
        yield _frame("done", {})
    elif status == "rejected":
        issues = [
            {"code": issue.code, "message": issue.message}
            for issue in record.validation_issues
        ]
        yield _frame("rejected", {"issues": issues})
    elif status == "stopped":
        yield _frame("stopped", {})
    else:
        yield _frame("failed", {"message": record.error or "The analysis failed."})


def diagnostic_page(state: Startup, env: Mapping[str, str]) -> RenderedPage:
    """The fail-closed page that replaces the form when config is unusable.

    Carries the raised message (safe by construction — these errors name the
    variable, never its value), the vendor the config selects, and that vendor's
    **full** required set with the unset ones marked. Presence only.

    There is no retry button, deliberately: an environment variable cannot
    change inside a running process. The page sends the user to the Settings
    page, whose save that passes fills the cell, and names a restart for a
    variable of the process environment.
    """
    return render(
        _DIAGNOSTIC_PAGE,
        _DIAGNOSTIC_GRANTS,
        message=escape(str(state.error)),
        vendors=_vendor_sections(state.tiers, env),
    )


def _vendor_sections(tiers: ModelTierConfig | None, env: Mapping[str, str]) -> str:
    """Each bound vendor's declared mode and required variables, set or unset.

    ``required_env_vars`` comes from the same registry entry that performs the
    check, so this cannot drift from what actually failed — and it lists the
    vendor's *whole* set, because ``Vendor._require`` raises on the first
    missing one and a reader would otherwise discover them one restart at a
    time.

    **Bound to the tiers a node points at.** A tier no node points at builds no
    adapter and needs no credential, so listing every tier would show a
    deployment whose unused ``review`` tier names a second vendor that vendor's
    variables marked NOT SET and its client library to install. None of it is
    needed, and the page is the one surface where being wrong about that sends
    an operator on a detour. ``ModelTierConfig.bound_vendors`` is the reader the
    build uses, so the two cannot disagree again.

    The mode is **reported, never resolved**. Under a mode that passes no
    credential material, the only way to find out whether an identity exists is
    to ask for one — which is a network call on page render, and it reaches the
    instance metadata service. So this page says what the deployment declared
    and what the platform has to supply, and leaves the answer to a run.

    The client library a vendor's provider needs is reported beside its
    variables, from the same table the build-time gate reads. Without that row
    the page would mark every variable "set" while the run still failed at bind
    time, which is exactly the drift this section exists not to have.
    """
    if tiers is None:
        # Covers both "the file is broken" and the ordinary first-run case where
        # it is fine but selects nothing. Which variables to set is a question
        # only a chosen vendor can answer, and guessing one here would reinstate
        # the privileged default the config deliberately does not ship.
        return (
            "<p>No vendor is selected yet, so there is nothing to report "
            "required variables for — which ones you need depends on which "
            "vendor you pick. Resolve the error above first.</p>"
        )
    sections = []
    for vendor in tiers.bound_vendors:
        mode = tiers.credential_mode(vendor)
        items = "\n".join(
            _env_var_item(var, bool(env.get(var, "").strip()))
            for var in vendor_for(vendor).required_env_vars(mode)
        )
        sections.append(
            f"<h3>{escape(vendor)}</h3>\n"
            f"<p>Credential mode: <code>{escape(mode.value)}</code>. "
            f"{escape(CREDENTIAL_MODE_NOTES[mode])}</p>\n"
            f"<ul>{items}{_sdk_item(vendor)}</ul>"
        )
    return "\n".join(sections)


def _sdk_item(vendor: VendorName) -> str:
    """One row for the client library this vendor needs, or nothing.

    A vendor whose provider needs no library gets no row: an empty statement
    reads as a missing one, and the section is a list of what an operator has
    to arrange.
    """
    sdk = vendor_for(vendor).sdk
    if sdk is None:
        return ""
    installed = missing_sdk(vendor) is None
    css_class, label = ("set", "installed") if installed else ("not", "NOT INSTALLED")
    return (
        f"<li><code>{escape(sdk.module)}</code> "
        f'<span class="{css_class}">{label}</span> — '
        f"<code>pip install analysis-service[{escape(sdk.extra)}]</code></li>"
    )


def prepare_diagram_reader() -> None:
    """Install the diagram reader's packages where the reader cannot read a
    valid flowchart, then read one again (ADR 0122).

    Where the second reading also fails, it logs a warning that names the
    fault, and it returns, so the app still starts: a job with no diagram
    does not need the reader.
    """
    if reader_fault() is None:
        return
    logger.info("installing the diagram reader's packages")
    install_reader()
    if fault := reader_fault():
        logger.warning("the diagram reader cannot read a diagram Source: %s", fault)


def _env_var_item(var: str, is_set: bool) -> str:
    """One variable's row — presence only, never the value (OWASP A09)."""
    css_class, label = ("set", "set") if is_set else ("not", "NOT SET")
    return (
        f'<li><code>{escape(var)}</code> <span class="{css_class}">{label}</span></li>'
    )


_STYLE = """
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 system-ui, sans-serif; max-width: 46rem;
         margin: 3rem auto; padding: 0 1.25rem; }
  h1 { font-size: 20px; margin-bottom: .25rem; }
  h2 { font-size: 17px; margin-top: 1.75rem; }
  h3 { font-size: 15px; font-family: ui-monospace, monospace; margin-bottom: .3rem; }
  .sub { opacity: .7; margin-top: 0; }
  .problem { border-left: 3px solid #c00; padding: .5rem .75rem; margin: 1rem 0; }
  .not { color: #c00; font-weight: 600; }
  .set { opacity: .6; }
"""

_FORM_PAGE = (Path(__file__).parent / "workspace.html").read_text(encoding="utf-8")
_SETTINGS_PAGE = (Path(__file__).parent / "settings.html").read_text(encoding="utf-8")
_FILES_PAGE = (Path(__file__).parent / "files.html").read_text(encoding="utf-8")
_CONTROLS_PAGE = (Path(__file__).parent / "controls.html").read_text(encoding="utf-8")
#: The format table as each page that uploads reads it, and the ``accept``
#: attribute of its file field: both from :data:`FORMATS` (ADR 0120).
_SOURCE_FORMATS = script_json({name: asdict(row) for name, row in FORMATS.items()})
_UPLOAD_ACCEPT = escape(
    ",".join(extension for row in FORMATS.values() for extension in row.extensions)
)
#: The sidebar of every workspace page: the form, Settings and the report.
#: Static markup, so it takes no escape.
_SIDEBAR = (Path(__file__).parent / "sidebar.html").read_text(encoding="utf-8")
#: The script each sidebar page runs in its head, to set the theme the reader
#: chose before the page draws.
_THEME = client_script("theme.js")

#: The question toggle. Every install can pause: one with no catalog asks its
#: early questions and no link question.
_QUESTIONS_FIELD = """<p class="ask-option"><label><input type="checkbox" id="ask" name="ask" checked>
    Ask me questions to make the report better.</label></p>"""

_DIAGNOSTIC_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>First run — configuration problem</title><style nonce="__CSP_NONCE__">"""
    + _STYLE
    + """</style></head>
<body>
<h1>The engine could not start</h1>
<p class="sub">No analysis can run until this is fixed, so the form is not shown.</p>
<div class="problem"><code><!--message--></code></div>
<h2>What this vendor needs</h2>
<!--vendors-->
<p>Correct the setting on the <a href="/settings">Settings page</a>. A save
that passes starts the engine with no restart. A variable of the process
environment changes only at a restart: set it, then <b>restart the app</b> with
<code>uv run python webapp/main.py</code>.</p>
<p>The full setup is <code>docs/First-Run.md</code> step 2; the vendor tables are
in <code>docs/Configuration.md</code>, under "Models and vendors".</p>
</body></html>
"""
)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"First-run app on http://{HOST}:{PORT}")
    prepare_diagram_reader()
    # Loopback is hard-bound: no flag, no env override. The no-auth posture is
    # only safe here.
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
