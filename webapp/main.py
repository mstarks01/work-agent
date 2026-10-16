"""The lite first-run web app — route step 3.

Start it from a clone, with model auth already configured::

    uv run python webapp/main.py

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
* No HTTP input touches model identity. Tier, vendor, model and sampling come
  from ``config/`` alone. A form field that selected a model would be
  unauthenticated control over what runs and what it costs (A01, LLM10). The
  framework picker is a different thing: it selects which framework analyses the
  text, rather than which model does the analysing. It does change what a
  submission can spend, because each selected framework runs its own nodes. The
  one-run-at-a-time gate and the loopback bind are what bound that.
* The picker is an allowlist, checked server-side (A01). A submitted name must
  be one this install carries, and a submitted option must satisfy that
  package's own options model, both before an engine exists. The form's
  checkboxes decide nothing: :func:`_selection` re-derives the selection from
  the carried list, so an invented name, a repeated one and a reordered pair are
  refused or normalised rather than reaching the graph.
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
  land as text nodes. Server-side, the two f-string pages escape through
  :func:`~webapp.page.escape`, which is quote-safe, so where a value lands is
  not part of whether the escape is adequate.
* CSRF: ``POST /analyze`` and ``POST /answer/{run}`` require
  ``Sec-Fetch-Site: same-origin``. The header
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
  ``'none'`` costs nothing. Each policy grants what its own page does and
  nothing else: the report page reaches only its own origin, to post to
  ``/answer/{run}`` and ``/correct/{run}``, the form page reaches only its own
  origin, through ``connect-src 'self'`` for ``/example``, ``/analyze``,
  ``/answer/{run}``, ``/amend/{run}``, ``/events/{run}`` and
  ``/workspace/runs``, and the diagnostic page runs no script, so it is granted no
  ``script-src`` at all. Each page declares what it does as a
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
    Awaitable,
    Callable,
    Collection,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Annotated, get_args

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from pydantic import Field, TypeAdapter, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from analysis_service import (
    ConfigError,
    FrameworkName,
    FrameworkSelection,
    Report,
    Source,
    budgets,
)
from analysis_service.answer_forms import (
    answer_choices,
    answer_form,
    answer_limit,
    answer_suggestions,
    facets_json,
)
from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import (
    ANSWER_LIMITS,
    MAX_SKIPS,
    AlreadyResumed,
    Answers,
    AnswerState,
    QuestionSet,
    Revision,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.budgets import BudgetPolicy
from analysis_service.certification import CertificationGate
from analysis_service.claims import UnknownKey
from analysis_service.critic import SEVERITY_ORDER
from analysis_service.deployment import Deployment
from analysis_service.engine import DEFAULT_CALLER
from analysis_service.fact_answers import (
    MAX_FACT_ANSWERS,
    MAX_HELD_FACTS,
    FactAnswer,
    SkipKey,
    answer_facets,
    attribute_place,
    fact_label,
    merged_facts,
)
from analysis_service.frameworks import PACKAGES, package_for
from analysis_service.job_answers import (
    DraftSaved,
    JobAnswers,
    JobDataMissing,
    NoCatalog,
    NoSuchJob,
    NotAdmitted,
    NotCompleted,
    Refused,
    RunnerFor,
    RunnerUnavailable,
    SaveConflict,
    Started,
    UnreadableSchema,
    Withheld,
)
from analysis_service.jobs import (
    TERMINAL_STATUSES,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    JobStore,
    PipelineRunner,
    event_frames,
    execute_job,
    parent_of,
    same_schema,
)
from analysis_service.links import (
    MAX_LINK_ANSWERS,
    LinkAnswer,
    NoCatalogError,
    components,
    refuse_answers_source,
)
from analysis_service.model_tiers import ModelTierConfig
from analysis_service.open_facts import open_facts_by_framework, reference_labels
from analysis_service.principals import Identity
from analysis_service.questions import question_fallback
from analysis_service.report_changes import report_changes
from analysis_service.report_conditions import conditions, corrected_findings
from analysis_service.selection import SelectionError, resolve_selection
from analysis_service.sources import (
    ANSWERS_LABEL,
    MAX_SYSTEM_NAME_CHARS,
    SourceLimits,
    clean_system_name,
)
from analysis_service.store_backends import audited_delete, build_store, store_lifespan
from analysis_service.vendors import (
    CREDENTIAL_MODE_NOTES,
    VendorName,
    missing_sdk,
    vendor_for,
)
from webapp.page import (
    LOOPBACK_HOSTS,
    Grants,
    RenderedPage,
    SecurityHeaders,
    client_script,
    escape,
    is_same_origin,
    render,
    response,
    script_json,
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


#: The identity of this app's one local operator, who owns every job it holds.
#: It is the caller the engine names for an in-process run, under the reserved
#: local issuer, so no bearer token can name it.
OPERATOR = Identity.local(DEFAULT_CALLER)


def store_note(store: JobStore) -> str:
    """The sentence that tells a user whether a restart keeps their runs.

    The startup line and the form page both show it, so the two cannot
    disagree about the store.
    """
    if isinstance(store, InMemoryJobStore):
        return (
            "This app keeps your runs in memory, so a restart clears them."
            " To keep them, set a sql store as docs/Web-App.md states."
        )
    return "This app keeps your runs in a durable store, so a restart keeps them."


class WorkspaceFull(Exception):
    """Every job the workspace holds waits for answers, so none can make room."""


@dataclass(frozen=True)
class Settings:
    """What the routes read of the deployment: its runner, and the bounds a
    job is held to."""

    runner_for: RunnerFor
    limits: SourceLimits
    budget: BudgetPolicy
    deadline_seconds: float
    carries_catalog: bool
    gate: CertificationGate | None = None


class Workspace:
    """The jobs this process holds, and what runs them.

    The jobs live in a job store, capped at ``max_jobs``. The oldest job
    that waits for no answers is deleted first, and a job that waits for
    answers is never deleted: where every job waits, a new one is refused
    instead. One job runs at a time (LLM10): the store's admission refuses a
    second while one is in flight, with a message rather than a queue. A
    restart keeps the jobs of a durable store and loses those of ``memory``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        store: JobStore | None = None,
        max_jobs: int = MAX_RUNS,
    ) -> None:
        self.store: JobStore = InMemoryJobStore() if store is None else store
        self.answers = JobAnswers(
            store=self.store,
            runner_for=settings.runner_for,
            limits=settings.limits,
            ceiling=1,
            budget=settings.budget,
            carries_catalog=settings.carries_catalog,
            gate=settings.gate,
        )
        self._deadline = settings.deadline_seconds
        self._max_jobs = max_jobs
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, record: JobRecord, runner: PipelineRunner) -> None:
        """Run an admitted job on this process's event loop."""
        task = asyncio.create_task(
            execute_job(self.store, runner, record.id, deadline_seconds=self._deadline)
        )
        # Held so the task is not garbage-collected mid-flight.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def owner_id(self, job_id: str) -> str:
        """The operator's owner ID, by :meth:`JobAnswers.owner_id`. Before the
        first submission the operator owns no job, so ``job_id`` reads as
        missing."""
        return await self.answers.owner_id(OPERATOR, job_id)

    async def owned(self, job_id: str) -> JobRecord | None:
        """The operator's job ``job_id``, as an envelope, or ``None``."""
        try:
            return await self.answers.owned(job_id, await self.owner_id(job_id))
        except NoSuchJob:
            return None

    async def parsable(self, job_id: str) -> JobRecord | None:
        """The operator's job ``job_id``, as an envelope, or ``None``. Raises
        :class:`UnreadableSchema` where this app cannot parse the record."""
        try:
            return await self.answers.parsable(job_id, await self.owner_id(job_id))
        except NoSuchJob:
            return None

    async def jobs(self) -> list[JobRecord]:
        """Every job of the operator, as envelopes, oldest first."""
        owner_id = await self.answers.held_owner_id(OPERATOR)
        if owner_id is None:
            return []
        return await self.store.owned_jobs(owner_id)

    async def waiting(self, record: JobRecord) -> bool:
        """True while the job waits for answers.

        A job whose resumed job has not reached a report still waits: that
        job can fail, and this one is then the only place its answers resume.
        """
        if record.status != "awaiting-answers":
            return False
        locker = await self.store.resumed_by(record.id, record.owner_id)
        if locker is None:
            return True
        child = await self.store.owned(locker, record.owner_id)
        return child is None or child.status != "completed"

    @asynccontextmanager
    async def make_room(self, *, answering: str | None = None) -> AsyncIterator[None]:
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
        held job waits.
        """
        limit = self._max_jobs + (answering is not None)
        chosen_at = datetime.now(UTC)
        held = await self.jobs()
        removable = [
            job
            for job in held
            if job.status in TERMINAL_STATUSES
            and job.id != answering
            and not await self.waiting(job)
        ]
        excess = len(held) - limit + 1
        if excess > len(removable):
            raise WorkspaceFull(
                f"{len(held)} analyses wait for answers. Answer or continue"
                " one of them before you start another."
            )
        yield
        for job in removable[: max(excess, 0)]:
            await audited_delete(
                self.store, job.id, job.owner_id, "workspace", updated_before=chosen_at
            )

    async def parent_of(self, record: JobRecord) -> str | None:
        """The job this one resumes or amends: :func:`parent_of` over the
        record with its resumption, which an envelope leaves out.

        The resumption is read only where :func:`same_schema` accepts the
        record. A record of another schema version names only the job it
        amends.
        """
        if not same_schema(record.schema_version):
            return parent_of(record)
        resumption = await self.store.resumption(record.id, record.owner_id)
        return parent_of(record.model_copy(update={"resumption": resumption}))

    async def workspace_id(self, record: JobRecord) -> str:
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

    async def snapshots(self) -> list[dict[str, object]]:
        """Latest retained job per analysis, with its retained reports, newest first."""
        grouped: dict[str, list[JobRecord]] = {}
        for record in await self.jobs():
            grouped.setdefault(await self.workspace_id(record), []).append(record)
        snapshots = []
        for records in reversed(list(grouped.values())):
            shown = records[-1]
            for record in reversed(records):
                if await self.waiting(record):
                    shown = record
                    break
            snapshots.append(await self.snapshot(shown, records))
        return snapshots

    async def snapshot(
        self, record: JobRecord, related: list[JobRecord] | None = None
    ) -> dict[str, object]:
        """Public workspace metadata; descriptions only on the detail route."""
        if related is None:
            group = await self.workspace_id(record)
            related = [
                item
                for item in await self.jobs()
                if await self.workspace_id(item) == group
            ]
        return {
            "run": record.id,
            "name": _name_of(record),
            "status": _page_status(record.status),
            # Whether the run stops for answers, so the page shows its
            # progress under the Questions tab rather than Reports.
            "pauses": record.pauses(),
            "frameworks": [selection.name for selection in record.frameworks or ()],
            "reports": [
                {
                    "run": item.id,
                    "url": f"/report/{item.id}",
                    "name": _name_of(item),
                    "generated_at": item.updated_at.isoformat(),
                }
                for item in related
                if item.status == "completed"
            ],
        }


DEFAULT_NAME = "Your system"

#: The page's statement of :func:`~analysis_service.sources.clean_system_name`.
#: A fixed sentence, so a refusal never echoes the name it refused.
_NAME_REFUSED = (
    f"Use an analysis name of at most {MAX_SYSTEM_NAME_CHARS} characters, on one"
    " line, with no control or formatting characters."
)


def _name_of(record: JobRecord) -> str:
    return record.system_name or DEFAULT_NAME


def _page_status(status: JobStatus) -> JobStatus:
    """A job's status as the pages read it: a queued job is running, and a
    rejected one stopped."""
    if status == "queued":
        return "running"
    return "failed" if status == "rejected" else status


@dataclass(frozen=True)
class Startup:
    """What starting up produced: what the app can offer, or the failure.

    The app serves either way. If construction raised, every route that would
    run a model is replaced by the diagnostic page, so no analysis can run on a
    model nobody chose — fail-closed, but explained.

    ``frameworks`` is what this install carries, which is what the picker
    offers and what :func:`_selection` allow-lists against. It is not itself a
    selection and it is never used as one: a submission names its own, and the
    form ships with every box unticked.
    """

    settings: Settings | None
    frameworks: tuple[FrameworkName, ...]
    tiers: ModelTierConfig | None
    error: ConfigError | None

    @property
    def ok(self) -> bool:
        return self.settings is not None


def build_startup() -> Startup:
    """Resolve the config and prove the credentials, or convert the failure to a page.

    Two stages, and the split is the whole content of the diagnostic. Resolving
    the :class:`~analysis_service.deployment.Deployment` reads the config files;
    building a runner off it resolves the vendor's credentials and runs every
    tier's ``(vendor, model, sampling)`` through LiteLLM's own check. So a
    *config* failure leaves no tiers to report, while a *credential* failure
    can still name the vendor the config selected — which is the case a first
    run overwhelmingly hits. One read either way: the tiers the page prints are
    the tiers the runner was built from, not a second load of the same file.

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
        deployment = Deployment.from_env(os.environ)
        deployment.runner(deployment.frameworks)
    except ConfigError as exc:
        logger.error("config error at startup: %s", exc)
        tiers = deployment.tiers if deployment is not None else None
        return Startup(settings=None, frameworks=(), tiers=tiers, error=exc)
    return Startup(
        settings=Settings(
            runner_for=deployment.runner,
            limits=deployment.resilience.source_limits(),
            budget=deployment.resilience.budget_policy(),
            deadline_seconds=deployment.job_deadline_seconds(),
            carries_catalog=deployment.carries_catalog,
            gate=deployment.gate,
        ),
        frameworks=deployment.frameworks,
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
    which unit is a package's answer (``unit_of``) and what a unit says is a
    package's answer (``text_of_unit``); the report page holds neither and must
    not learn them. So the server pairs them and the page renders what it is
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
    report: Report, state: AnswerState, previous: Report | None = None
) -> RenderedPage:
    """``report_view.html``, carrying this run's report.

    ``previous`` is the report a follow-up's answers came from. The page then
    shows how each finding moved since it.

    ``state`` is the run's :class:`~analysis_service.answer_state.AnswerState`:
    its answers, whether a follow-up wrote its report, what the pause showed,
    what its owner corrected since and the run its follow-up started. It
    decides what the page still asks and how it labels each question, from
    the same Question Set the answer route admits against.

    The template is a self-contained renderer for the report schema — no build
    step, no framework, its own inline CSS and JS. This fills its one payload
    placeholder and its per-response CSP nonce, and serves the result.

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
        script=client_script("answer_editor.js")
        + "\n"
        + client_script("report_view.js"),
        answer_limits=script_json(dict(ANSWER_LIMITS)),
        report=script_json(report.model_dump(mode="json")),
        units=script_json(unit_rows(report)),
        open_facts=script_json(
            open_facts_by_framework(report.analyses, report.system_model)
        ),
        fact_questions=script_json(asked["fact_questions"]),
        question_fallback=script_json(question_fallback(report.analyses).to_json()),
        link_questions=script_json(asked["link_questions"]),
        final=script_json(asked["final"]),
        resumed_by=script_json(asked["resumed_by"]),
        corrections=script_json(
            _corrections_payload(
                report, state.answers.facts, state.corrections, state.revision
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
                    change.to_json() for change in report_changes(previous, report)
                ]
            }
            if previous is not None
            else {}
        ),
        severity_order=script_json(list(SEVERITY_ORDER)),
        lanes=script_json(
            {name: package.id_rule.lane_field for name, package in PACKAGES.items()}
        ),
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
            finding: [{"label": label, "status": status} for _, label, status in rows]
            for finding, rows in conditions(
                report.analyses, report.system_model, answered, shown, skipped
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
            "label": fact_label(answer.key, model),
            "form": answer_form(answer.key, model, catalog),
            "choices": list(answer_choices(answer.key, model, catalog)),
            "facets": facets_json(answer_facets(answer.key)),
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
) -> dict[str, object]:
    """A final report's answers as it is corrected in, what corrections reach,
    and the revision a correction names (ADR 0090).

    Each answer carries its current value with every correction in.
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
        "revision": revision,
    }


def _draft_payload(state: AnswerState) -> dict[str, object]:
    """A report's follow-up draft and the revision a save names (ADR 0070)."""
    return {
        "draft": {
            "links": [link.model_dump(mode="json") for link in state.draft.links],
            "facts": [fact.model_dump(mode="json") for fact in state.draft.facts],
        },
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
                "principal": link.principal,
                "options": options,
                "answer": link.model_dump(mode="json"),
            }
            for link in links
        ],
    }


#: Each typed refusal of the job operations and of the answer rules, with the
#: status the page is answered under. The message is the refusal's own, which
#: names the submitter's choices and never this deployment's configuration.
_REFUSAL_STATUS: dict[type[Exception], int] = {
    NoSuchJob: 404,
    NotCompleted: 409,
    Withheld: 409,
    NoCatalog: 400,
    NoCatalogError: 409,
    Refused: 400,
    SaveConflict: 409,
    UnreadableSchema: 409,
    WorkspaceFull: 409,
    RunnerUnavailable: 503,
    JobDataMissing: 500,
}

#: What the page says where a save in another tab moved the answers first.
_STALE_MESSAGE = (
    "Your saved answers changed in another tab or window. Reload this page to see them."
)

#: What the page is told for each admission refusal, keyed by outcome. An
#: outcome missing here is this app's defect: ``duplicate`` cannot follow from
#: a page, because ``JobRecord.create`` mints the ID.
_ADMISSION_MESSAGES: dict[str, str] = {
    "at_ceiling": "An analysis is already running. Wait for it to finish.",
    "over_rate": "This workspace started too many analyses in the current"
    " window. Wait for the window to roll past the earlier ones.",
    "over_owner_budget": "This workspace has reached its consumption limit"
    " for the current window. Retry later, or submit less text.",
    "over_global_budget": "This workspace has reached its consumption limit"
    " for the current window. Retry later, or submit less text.",
    "resumed_already": "These answers already started an analysis. Open its"
    " report, or answer again only if it fails.",
    "parent_moved": _STALE_MESSAGE,
    "parent_missing": "This analysis was deleted.",
    "principal_exists": "Another analysis started at the same moment. Start"
    " this one again.",
}


def _message(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


async def _refused(status: int, request: Request, exc: Exception) -> JSONResponse:
    fields = exc.fields() if isinstance(exc, Refused) else {}
    return JSONResponse({"message": str(exc), **fields}, status_code=status)


async def _not_admitted(request: Request, exc: NotAdmitted) -> JSONResponse:
    outcome = exc.admission.outcome
    if outcome in _ADMISSION_MESSAGES:
        return _message(409, _ADMISSION_MESSAGES[outcome])
    logger.error("job was not admitted: %s", outcome)
    return _message(500, "The analysis could not be started. Check the server log.")


async def _already_resumed(request: Request, exc: AlreadyResumed) -> JSONResponse:
    return _message(409, _ADMISSION_MESSAGES["resumed_already"])


async def _stale(request: Request, exc: StaleRevision) -> JSONResponse:
    return _message(409, _STALE_MESSAGE)


async def _over_limit(request: Request, exc: SourcesOverLimit) -> JSONResponse:
    return _message(400, exc.breach.message)


def create_app(
    startup: Startup | None = None, workspace: Workspace | None = None
) -> FastAPI:
    """The ASGI app.

    Both collaborators are injectable so the offline lane can drive the app
    without credentials: ``startup`` supplies a stub runner, and ``workspace``
    lets a test bound the jobs held or hand the app a store of its own.

    Without a ``workspace``, the app builds its store with
    :func:`~analysis_service.store_backends.build_store` from the service's backend
    settings, and an unusable setting raises. Where ``ANALYSIS_JOB_STORE`` is
    unset, the store is ``memory``, so a first run needs no setting. The app
    logs :func:`store_note` for the store it built. At startup, before the app takes a request, the app runs
    :func:`~analysis_service.store_backends.store_lifespan` over the store, as the
    service does.
    """
    state = build_startup() if startup is None else startup
    if workspace is None and state.settings is not None:
        store = build_store({"ANALYSIS_JOB_STORE": "memory", **os.environ})
        logger.info(store_note(store))
        workspace = Workspace(state.settings, store=store)
    app = FastAPI(
        title="Work Agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lambda app: (
            nullcontext() if workspace is None else store_lifespan(workspace.store)
        ),
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)
    for refusal, status in _REFUSAL_STATUS.items():
        app.add_exception_handler(refusal, partial(_refused, status))
    app.exception_handler(NotAdmitted)(_not_admitted)
    app.exception_handler(AlreadyResumed)(_already_resumed)
    app.exception_handler(StaleRevision)(_stale)
    app.exception_handler(SourcesOverLimit)(_over_limit)

    def unavailable() -> JSONResponse:
        """The app came up without a runner: the diagnostic page says why."""
        return _message(503, str(state.error))

    def no_such_run() -> JSONResponse:
        return _message(404, "This analysis is no longer available.")

    @app.get("/", response_class=HTMLResponse)
    async def form_page() -> Response:
        if not state.ok:
            return response(diagnostic_page(state), status_code=503)
        return response(
            render(
                _FORM_PAGE,
                _FORM_GRANTS,
                script="\n".join(
                    client_script(name)
                    for name in ("workspace.js", "answer_editor.js", "first_run.js")
                ),
                answer_limits=script_json(dict(ANSWER_LIMITS)),
                workspace_style=(
                    Path(__file__).parent / "static" / "workspace.css"
                ).read_text(encoding="utf-8"),
                tiers=_tier_lines(state.tiers),
                frameworks=_framework_fields(state.frameworks),
                questions=_QUESTIONS_FIELD,
                name_limit=str(MAX_SYSTEM_NAME_CHARS),
                store_note=escape(store_note(workspace.store)) if workspace else "",
            )
        )

    @app.get("/example", response_class=PlainTextResponse)
    async def example() -> Response:
        """The sample description, read from the file the examples also run."""
        return PlainTextResponse(SAMPLE.read_text(encoding="utf-8"))

    @app.get("/workspace/runs")
    async def workspace_runs() -> Response:
        if workspace is None:
            return JSONResponse([])
        return JSONResponse(await workspace.snapshots())

    @app.get("/workspace/runs/{run_id}")
    async def workspace_run(run_id: str) -> Response:
        """One analysis as the page reopens it: its latest job, with its
        questions where it waits, and its failure where it stopped."""
        if workspace is None:
            return no_such_run()
        store = workspace.store
        record = await workspace.owned(run_id)
        if record is None:
            return no_such_run()
        owner_id = record.owner_id
        while (locker := await store.resumed_by(record.id, owner_id)) is not None:
            locked = await store.owned(locker, owner_id)
            if locked is None:
                break
            record = locked
        record = await workspace.parsable(record.id)
        if record is None:
            return no_such_run()
        payload = await workspace.snapshot(record)
        payload["completed_steps"] = [
            event.node for event in record.events if event.kind == "node"
        ]
        payload["selection"] = [
            {"name": selection.name, "options": dict(selection.options)}
            for selection in record.whole_frameworks()
        ]
        sources = await store.sources(record.id, owner_id)
        if sources is None:
            return no_such_run()
        payload["description"] = "\n\n".join(
            source.text for source in sources if source.kind != "answers"
        )
        payload["error"] = record.error
        payload["issues"] = [
            {"code": issue.code, "message": issue.message}
            for issue in record.validation_issues
        ]
        if record.status == "awaiting-answers":
            payload["questions"] = {
                "run": record.id
            } | await workspace.answers.questions(record.id, owner_id)
        return JSONResponse(payload)

    @app.post("/analyze")
    async def analyze(request: Request) -> Response:
        if not is_same_origin(request):
            logger.warning("refused a POST /analyze that was not same-origin")
            return _message(403, "This request did not come from the app's own page.")
        if workspace is None or state.settings is None:
            return unavailable()

        try:
            body = await request.json()
            sources = [Source.model_validate(source) for source in body["sources"]]
            refuse_answers_source(sources)
            selection = _selection(state.frameworks, body["frameworks"])
            ask_questions = body.get("questions") is True
            answers_from = body.get("answers_from")
            if answers_from is not None and not isinstance(answers_from, str):
                raise TypeError
            name = body.get("name")
            if not isinstance(name, str | None):
                return _message(400, _NAME_REFUSED)
            try:
                name = clean_system_name(name)
            except ValueError:
                return _message(400, _NAME_REFUSED)
        except SelectionError as exc:
            # Names the framework and the field it wanted, or the name the
            # install does not carry, and nothing about this deployment. Safe
            # to show, and the only way a submitter learns what to change.
            return _message(400, str(exc))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _message(
                400,
                "Expected a JSON body with a 'sources' list and a 'frameworks'"
                " list naming frameworks this install carries.",
            )
        breach = state.settings.limits.breach(sources)
        if breach is not None:
            return _message(400, breach.message)
        submitter = await workspace.answers.submitter(OPERATOR)
        carried = await workspace.answers.carried_from(
            answers_from, submitter.owner_id, asks=ask_questions
        )
        record = JobRecord.create(
            owner_id=submitter.owner_id,
            sources=sources,
            frameworks=selection,
            system_name=name,
            carried=carried,
            ask_questions=ask_questions,
            reserved_tokens=budgets.estimate(sources, selection),
        )
        async with workspace.make_room():
            started = await workspace.answers.admit(
                record, start=workspace.start, principal=submitter.new
            )
        return JSONResponse({"run": started.job_id})

    @app.post("/answer/{run_id}")
    async def answer(run_id: str, request: Request) -> Response:
        """Answer a run's questions, and start the run that resumes from it.

        The run answered is a paused one or a finished one. The new run starts
        at ``prepare`` from what that run reached, so nothing is extracted
        again. An empty answer list continues a paused run without answers; a
        finished run has nothing to continue. A save answers with the
        new revision.
        """
        if not is_same_origin(request):
            logger.warning("refused a POST /answer that was not same-origin")
            return _message(403, "This request did not come from the app's own page.")
        if workspace is None:
            return unavailable()
        try:
            body = await request.json()
        except ValueError:
            body = None  # refused below, as every other malformed body is
        questions = _questions_of(workspace, run_id)
        try:
            raw = body["links"]
            raw_facts = body.get("facts", [])
            if (
                isinstance(raw, list)
                and isinstance(raw_facts, list)
                and (len(raw) > MAX_LINK_ANSWERS or len(raw_facts) > MAX_FACT_ANSWERS)
            ):
                return _message(
                    400,
                    f"One request takes at most {MAX_FACT_ANSWERS} answers and"
                    f" {MAX_LINK_ANSWERS} element choices. Save your answers in"
                    " parts, then run the follow-up.",
                )
            if not isinstance(raw, list):
                raise TypeError
            links = [LinkAnswer.model_validate(link) for link in raw]
            if not isinstance(raw_facts, list):
                raise TypeError
            facts = [await _fact_answer(fact, questions) for fact in raw_facts]
            save = body.get("save", False)
            if not isinstance(save, bool):
                raise TypeError
            skips = _skips(body.get("skip", []))
            presented = _presented(body.get("presented", []))
            revision = body.get("revision")
            if revision is not None:
                revision = _revision(revision)
        except InvalidFactAnswer as exc:
            return _message(400, str(exc))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _message(
                400,
                "Expected a JSON body with a 'links' list and a 'facts' list of"
                " answers this report asked for.",
            )
        owner_id = await workspace.owner_id(run_id)
        # A save starts no job, so it needs no room.
        room = nullcontext() if save else workspace.make_room(answering=run_id)
        async with room:
            outcome = await workspace.answers.answer(
                run_id,
                owner_id,
                Answers(
                    given=AnswerSet(links=tuple(links), facts=tuple(facts)),
                    save=save,
                    skips=skips,
                    presented=presented,
                    revision=revision,
                ),
                start=workspace.start,
            )
        if isinstance(outcome, Started):
            return JSONResponse({"run": outcome.job_id})
        if isinstance(outcome, DraftSaved):
            # A draft runs nothing and spends no follow-up (ADR 0070).
            return JSONResponse({"saved": True, "revision": outcome.revision})
        # A save runs no model: the answers go onto the paused run, and the
        # list is read again off the model with them in.
        return JSONResponse(
            {"run": run_id} | await workspace.answers.questions(run_id, owner_id)
        )

    @app.post("/amend/{run_id}")
    async def amend(run_id: str, request: Request) -> Response:
        """Extract a paused run's description again with an amendment (ADR 0072).

        The new run reads the paused run's sources with the amendment added,
        pauses again, and carries the paused run's answers.
        """
        if not is_same_origin(request):
            logger.warning("refused a POST /amend that was not same-origin")
            return _message(403, "This request did not come from the app's own page.")
        if workspace is None:
            return unavailable()
        try:
            body = await request.json()
            text = body["text"]
            revision = _revision(body["revision"])
            if not isinstance(text, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return _message(400, "Expected a JSON body with a 'text' and a 'revision'.")
        owner_id = await workspace.owner_id(run_id)
        async with workspace.make_room(answering=run_id):
            started = await workspace.answers.amend(
                run_id, owner_id, text, revision=revision, start=workspace.start
            )
        return JSONResponse({"run": started.job_id})

    @app.get("/events/{run_id}")
    async def events(run_id: str) -> Response:
        """The job's status and node events, as the store records them, until
        the job reaches a terminal status."""
        if workspace is None or await workspace.owned(run_id) is None:
            return PlainTextResponse("no such run", status_code=404)
        return StreamingResponse(
            _stream(workspace, run_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/report/{run_id}", response_class=HTMLResponse)
    async def report_page(run_id: str) -> Response:
        if workspace is None:
            return PlainTextResponse("no such report", status_code=404)
        try:
            owner_id = await workspace.owner_id(run_id)
            answerable = await workspace.answers.answerable(run_id, owner_id)
        except (NoSuchJob, NotCompleted, Withheld):
            return PlainTextResponse("no such report", status_code=404)
        report = answerable.report
        if report is None:
            return PlainTextResponse("no such report", status_code=404)
        # The page compares with the report before it where the store holds
        # that report under this service's schema version, and else shows
        # none, as for a parent that is gone.
        resumption = answerable.record.resumption
        parent = (
            None if resumption is None else await workspace.owned(resumption.parent_id)
        )
        previous = None
        if parent is not None and same_schema(parent.schema_version):
            previous = await workspace.store.report(parent.id, owner_id)
        return response(render_report(report, answerable.state(), previous))

    @app.post("/correct/{run_id}")
    async def correct(run_id: str, request: Request) -> Response:
        """Correct a final report's answers, and run nothing (ADR 0054)."""
        if not is_same_origin(request):
            logger.warning("refused a POST /correct that was not same-origin")
            return _message(403, "This request did not come from the app's own page.")
        if workspace is None:
            return unavailable()
        questions = _questions_of(workspace, run_id)
        try:
            body = await request.json()
            raw = body["facts"]
            if not isinstance(raw, list) or len(raw) > MAX_FACT_ANSWERS:
                raise TypeError
            facts = [await _fact_answer(fact, questions) for fact in raw]
            revision = _revision(body["revision"])
        except InvalidFactAnswer as exc:
            return _message(400, str(exc))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _message(
                400,
                "Expected a JSON body with a 'facts' list of corrections and a"
                " 'revision'.",
            )
        corrected = await workspace.answers.correct(
            run_id, await workspace.owner_id(run_id), facts, revision=revision
        )
        return JSONResponse({"run": corrected.job_id})

    return app


_REVISION: TypeAdapter[int] = TypeAdapter(Revision)


def _revision(value: object) -> int:
    """The revision a page sent, or :class:`ValidationError`.

    Read by :data:`~analysis_service.answer_state.Revision`, the type the HTTP
    models declare, so the page and the route refuse the same values.
    """
    return _REVISION.validate_python(value)


def _selection(
    carried: Sequence[FrameworkName], requested: object
) -> list[FrameworkSelection]:
    """One submission's selection: checked by the shared reader, in carried order.

    **The checkboxes decide nothing.** What arrives is a list of names and
    options from a page the submitter controls, so it is held to
    :func:`~analysis_service.selection.resolve_selection` — the one reader the
    HTTP route and the engine share — and then ordered by ``carried`` rather
    than by what the page posted, so the block order of every report is
    ``config/frameworks.toml`` order. A name sent twice is refused, as the
    route refuses it: collapsing it would answer a submission with a selection
    nobody made.

    Raises ``TypeError`` for a body whose ``frameworks`` is not a list,
    ``ValidationError`` for an entry that is not a
    :class:`~analysis_service.report.FrameworkSelection`, and
    :class:`~analysis_service.selection.SelectionError` — a ``ValueError`` — for
    a selection the install cannot run. The route answers all three as one 400.
    """
    if not isinstance(requested, list):
        raise TypeError("'frameworks' must be a list of framework selections")
    picked = {
        selection.name: selection
        for selection in resolve_selection(
            carried, [FrameworkSelection.model_validate(entry) for entry in requested]
        )
    }
    return [picked[name] for name in carried if name in picked]


def _framework_fields(frameworks: Sequence[FrameworkName]) -> str:
    """The picker: a checkbox per carried framework, and a control per option.

    **Nothing here names a framework or an option.** The rows come from what
    this install carries and the controls from each package's own options
    model, so a package registered later gets its controls without an edit
    here — and a package that needs no options gets a bare checkbox, because
    an empty options model has no fields to walk.

    Every box starts unticked so the submitter chooses the analyses to run. The
    :func:`_selection` allow-list is what rules on the answer.
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


_SKIPS: TypeAdapter[list[SkipKey]] = TypeAdapter(
    Annotated[list[SkipKey], Field(max_length=MAX_SKIPS)]
)
_PRESENTED: TypeAdapter[list[UnknownKey]] = TypeAdapter(
    Annotated[list[UnknownKey], Field(max_length=MAX_HELD_FACTS)]
)


def _presented(raw: object) -> list[UnknownKey]:
    """The early questions the page says it showed, or a ``TypeError``."""
    try:
        return _PRESENTED.validate_python(raw)
    except ValidationError as exc:
        raise TypeError from exc


def _skips(raw: object) -> list[SkipKey]:
    """The keys a save skips (:data:`SkipKey`), or a ``TypeError``."""
    try:
        return _SKIPS.validate_python(raw)
    except ValidationError as exc:
        raise TypeError from exc


class InvalidFactAnswer(Exception):
    """One fact answer the page sent does not parse; the message names its question."""


def _questions_of(
    workspace: Workspace, run_id: str
) -> Callable[[], Awaitable[QuestionSet]]:
    """The run's Question Set, read only where a refusal needs a label."""

    async def read() -> QuestionSet:
        answerable = await workspace.answers.answerable(
            run_id, await workspace.owner_id(run_id)
        )
        return answerable.state().questions

    return read


async def _fact_answer(
    raw: object, questions: Callable[[], Awaitable[QuestionSet]]
) -> FactAnswer:
    """The answer, or an :class:`InvalidFactAnswer` that names the question it answers.

    The message is the validator's own, which names the submitter's choices,
    so it is safe to show. The questions are read only for that message.
    """
    try:
        return FactAnswer.model_validate(raw)
    except ValidationError as error:
        reason = error.errors()[0]["msg"].removeprefix("Value error, ")
        key = raw.get("key") if isinstance(raw, dict) else None
        parts = tuple(key) if isinstance(key, list) else ()
        label = None
        if all(isinstance(part, str) for part in parts):
            try:
                asked = await questions()
            except (NoSuchJob, NotCompleted, Withheld):
                asked = None
            if asked is not None:
                known = asked.asked | {q.key for q, _ in asked.answered_early}
                if parts in known:
                    label = fact_label(parts, asked.model)
        lead = "An answer" if label is None else f'The answer to "{label}"'
        raise InvalidFactAnswer(f"{lead} was refused: {reason}") from None


def _frame(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream(workspace: Workspace, run_id: str) -> AsyncIterator[str]:
    """Every event the store records for the job, as it records them, then
    one frame for how it ended.

    The store's event log is the one record of the run. The page acts on its
    end, so the last frame carries what the page shows for it: the questions
    a pause asks, where a report is, why a description was refused, or why a
    run failed.
    """
    async for frame in event_frames(workspace.store, run_id):
        yield frame
    record = await workspace.owned(run_id)
    if record is None:
        return
    status = record.status
    if status == "awaiting-answers":
        try:
            payload = await workspace.answers.questions(run_id, record.owner_id)
        except tuple(_REFUSAL_STATUS) as refusal:
            # The response has started, so a refusal is a frame, never a status.
            yield _frame("failed", {"message": str(refusal)})
            return
        yield _frame("questions", {"run": run_id} | payload)
    elif status == "completed":
        yield _frame("done", {"url": f"/report/{run_id}"})
    elif status == "rejected":
        issues = [
            {"code": issue.code, "message": issue.message}
            for issue in record.validation_issues
        ]
        yield _frame("rejected", {"issues": issues})
    else:
        yield _frame("failed", {"message": record.error or "The analysis failed."})


def _tier_lines(tiers: ModelTierConfig | None) -> str:
    """One compact read-only line per tier — config-time selection, and only that.

    Not credential status (the page rendering at all already proves that check
    passed), and not sampling (config a first-run reader has no basis to judge).
    The *served* build that actually answered is per-response provenance, which
    the report itself carries and the viewer already renders; this does not
    duplicate it.
    """
    if tiers is None:
        return ""
    return "\n".join(
        f'<div class="tier"><b>{escape(tier)}</b> → '
        f"{escape(selection.vendor)} / {escape(selection.model)}</div>"
        for tier, selection in tiers.tiers.items()
    )


def diagnostic_page(state: Startup) -> RenderedPage:
    """The fail-closed page that replaces the form when config is unusable.

    Carries the raised message (safe by construction — these errors name the
    variable, never its value), the vendor the config selects, and that vendor's
    **full** required set with the unset ones marked. Presence only.

    There is no retry button, deliberately: an environment variable cannot
    change inside a running process, so a retry would appear to work for a
    ``model_tiers.toml`` edit and silently do nothing for the credential case —
    which is overwhelmingly the common one at this point on the route. One
    instruction that is always right beats one that is conditionally right.
    """
    return render(
        _DIAGNOSTIC_PAGE,
        _DIAGNOSTIC_GRANTS,
        message=escape(str(state.error)),
        vendors=_vendor_sections(state.tiers),
    )


def _vendor_sections(
    tiers: ModelTierConfig | None, env: Mapping[str, str] | None = None
) -> str:
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
    needed, and the page is the one surface where being wrong about that costs
    an operator a detour. ``ModelTierConfig.bound_vendors`` is the reader the
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
    env = os.environ if env is None else env
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
  h1 { font-size: 1.4rem; margin-bottom: .25rem; }
  h2 { font-size: 1.05rem; margin-top: 1.75rem; }
  h3 { font-size: .9rem; font-family: ui-monospace, monospace; margin-bottom: .3rem; }
  .sub { opacity: .7; margin-top: 0; }
  .tier { font-family: ui-monospace, monospace; font-size: .85rem; opacity: .8; }
  textarea { width: 100%; min-height: 15rem; font: 13px/1.5 ui-monospace, monospace;
             padding: .75rem; box-sizing: border-box; }
  button { font: inherit; padding: .5rem 1rem; margin-right: .5rem; }
  .problem { border-left: 3px solid #c00; padding: .5rem .75rem; margin: 1rem 0; }
  #ticks { font-family: ui-monospace, monospace; font-size: .85rem; }
  #ticks li { opacity: .55; }
  .not { color: #c00; font-weight: 600; }
  .set { opacity: .6; }
  .pick { margin: .15rem 0; }
  .pick b { font-family: ui-monospace, monospace; font-weight: 600; }
  .opts { margin-left: .75rem; font-size: .85rem; opacity: .8; }
  .opts[hidden] { display: none; }
  .spinner { display: inline-block; width: .9em; height: .9em; border-radius: 50%;
             border: 2px solid currentColor; border-right-color: transparent;
             vertical-align: -.1em; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
  .hint { font-size: .85rem; opacity: .7; }
  /* A facet table can be wider than a phone: it scrolls in its own box. */
  table { display: block; max-width: 100%; overflow-x: auto; }
"""

_FORM_PAGE = (Path(__file__).parent / "workspace.html").read_text(encoding="utf-8")

#: The question toggle. Every install can pause: one with no catalog asks its
#: early questions and no link question. The page lists the earlier analyses
#: whose answers a new one can carry (ADR 0079), and shows the list only where
#: there is one.
_QUESTIONS_FIELD = """<p class="ask-option"><label><input type="checkbox" id="ask" name="ask" checked>
    Ask me questions to make the report better.</label></p>
<div id="answers-from-field" hidden><label class="field-label" for="answers-from">Reuse answers from</label>
    <select id="answers-from"><option value="">No earlier analysis</option></select></div>"""

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
<p>Set the missing variables, then <b>restart the app</b>:
<code>uv run python webapp/main.py</code>. There is no retry button — a process
cannot pick up an environment variable that changed after it started.</p>
<p>The full setup is <code>docs/First-Run.md</code> step 2; the vendor tables are
in <code>docs/Configuration.md</code>, under "Models and vendors".</p>
</body></html>
"""
)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"First-run app on http://{HOST}:{PORT}")
    # Loopback is hard-bound: no flag, no env override. The no-auth posture is
    # only safe here.
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
