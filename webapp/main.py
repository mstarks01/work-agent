"""The lite first-run web app — route step 3.

Start it from a clone, with model auth already configured::

    uv run python webapp/main.py

It embeds :class:`~analysis_service.Engine` in process. It does not go through
the ``/v1`` HTTP surface, so it needs no bearer token and no CORS, and it runs
real models against real prose, because there is no credential-free path here.
Its whole job is to show a first-time integrator that the engine works, and then
get out of the way. The library is what they actually embed.

Two pages, three data endpoints:

======================  ========================================================
``GET  /``              form page: the tiers, the framework picker, a textarea
``GET  /report/{run}``  ``report_view.html`` with this run's JSON injected
``GET  /example``       ``examples/orders.md``, for **Load example**
``POST /analyze``       start a run on a selection, return its id
``POST /answer/{run}``  answer a run's questions, start the resumed run
``GET  /events/{run}``  server-sent per-node progress
======================  ========================================================

It is deliberately unbloated: one module, no template engine, no JS framework,
no build step, no CSS framework, no bundler. HTML comes from f-strings, and the
only client-side JavaScript is the SSE listener, the Load-example fill, the
picker's show-and-hide, and the redirect.

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
  forget to call. The discipline had already failed once, unnoticed, in the
  element table's attribute column. This is the primary control, and the CSP
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
  nothing else: the report page reaches only its own origin, to post answers to
  ``/answer/{run}``, the form page reaches only its own origin, through
  ``connect-src 'self'`` for ``/example``, ``/analyze``, ``/answer/{run}`` and
  ``/events``, and the diagnostic page runs no script, so it is granted no
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
import secrets
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
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
    Engine,
    EngineDeadlineError,
    EngineInputError,
    FrameworkName,
    FrameworkSelection,
    PipelineCompleted,
    Report,
    Source,
)
from analysis_service.answer_forms import (
    answer_choices,
    answer_form,
    answer_limit,
    answer_suggestions,
    facets_json,
)
from analysis_service.answer_round import (
    MAX_SKIPS,
    AlreadyResumed,
    Answers,
    AnswerState,
    QuestionSet,
    SavedRound,
    SkipKey,
    SourcesOverLimit,
    StaleRevision,
)
from analysis_service.claims import UnknownKey
from analysis_service.deployment import Deployment
from analysis_service.early_questions import EarlyQuestion
from analysis_service.fact_answers import (
    MAX_FACT_ANSWERS,
    FactAnswer,
    answer_facets,
    fact_label,
    merged_facts,
)
from analysis_service.frameworks import PACKAGES, package_for
from analysis_service.jobs import (
    Checkpoint,
    JobStatus,
    PipelineAwaiting,
    PipelineOutcome,
    holds_its_parent,
)
from analysis_service.links import (
    MAX_LINK_ANSWERS,
    LinkAnswer,
    LinkQuestion,
    components,
)
from analysis_service.model_tiers import ModelTierConfig
from analysis_service.open_facts import open_facts_by_framework, reference_labels
from analysis_service.questions import question_fallback
from analysis_service.report_changes import report_changes
from analysis_service.report_conditions import conditions, corrected_findings
from analysis_service.selection import SelectionError, resolve_selection
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import SystemModel
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

# The registry is a demo surface, not a job store — /v1 already is one. It holds
# untrusted prose and its report, in memory, per process, never persisted, and
# oldest-first evicted. A run that waits for answers is never evicted: a new run
# is refused instead. A restart loses history, which is correct here.
MAX_RUNS = 20

#: The report page loads nothing external. It reaches its own origin for one
#: thing: posting answers to its questions to ``/answer/{run}``.
_REPORT_GRANTS = Grants(script=True, style=True, connect=True)

#: The form page calls ``/example``, ``/analyze`` and ``/events/{run}``, so it
#: needs its own origin and the report page does not. ``form-action`` stays
#: closed even though the page carries a ``<form>`` — the submit handler is
#: ``preventDefault()``-ed and posts through fetch, so a navigation away from
#: this form is something going wrong.
_FORM_GRANTS = Grants(script=True, style=True, connect=True)

#: The diagnostic page runs no script and makes no request: it is one style
#: block and static prose. A page that has nothing to authorise should not
#: carry the grant that would authorise one.
_DIAGNOSTIC_GRANTS = Grants(style=True)


@dataclass
class Run:
    """One in-flight or finished analysis.

    ``engine``, ``sources`` and ``links`` are what the run was given, and
    ``checkpoint`` is the model and catalog it reached: a paused run's, or a
    finished report's. Together they are what an answer resumes from.
    """

    id: str
    events: asyncio.Queue[tuple[str, str]] = field(default_factory=asyncio.Queue)
    report: Report | None = None
    task: asyncio.Task | None = None
    engine: Engine | None = None
    sources: list[Source] = field(default_factory=list)
    links: list[LinkAnswer] = field(default_factory=list)
    facts: list[FactAnswer] = field(default_factory=list)
    checkpoint: Checkpoint | None = None
    #: True for a run the report's follow-up started: its report is final.
    final: bool = False
    #: Every early question the pause showed.
    shown: list[UnknownKey] = field(default_factory=list)
    #: A final report's corrections, kept beside it; no model reads them.
    corrections: list[FactAnswer] = field(default_factory=list)
    #: Every early question and link question the submitter skipped for now.
    skipped: list[SkipKey] = field(default_factory=list)
    #: How many rounds a paused run has saved. A page sends the revision it
    #: read, so a page left open on an earlier round cannot write over a later one.
    revision: int = 0
    #: The run a submitter's answers started from this one, if any.
    resumed_by: Run | None = None
    #: The report this run's answers came from, for a follow-up: what its own
    #: report is compared with.
    previous: Report | None = None

    @property
    def waiting(self) -> bool:
        """True while the run waits for answers before its analysis.

        A run whose resumed run has not reached a report still waits: that
        run can fail, and this one is then the only place its answers resume.
        """
        resumed = self.resumed_by is not None and self.resumed_by.report is not None
        return self.checkpoint is not None and self.report is None and not resumed

    @property
    def holding(self) -> Run | None:
        """The run these answers started, where it holds this one, or ``None``."""
        return self.resumed_by if self.resumed else None

    @property
    def resumed(self) -> bool:
        """True where a run this one's answers started is running or has a report.

        Such a run takes no more answers, as a ``/v1`` job takes one resumed
        job. A resumed run that failed read nothing into a report, so this one
        takes answers again.
        """
        run = self.resumed_by
        return run is not None and holds_its_parent(run.status)

    @property
    def status(self) -> JobStatus:
        """The run's state as a ``/v1`` job's status reads it.

        A run with a report is ``completed``, one still going is ``running``,
        and one that stopped at its checkpoint is ``awaiting-answers``. A run
        that ended with neither read nothing into a report, whether it failed
        or was rejected, and reads as ``failed``.
        """
        if self.report is not None:
            return "completed"
        if self.task is None or not self.task.done():
            return "running"
        return "awaiting-answers" if self.checkpoint is not None else "failed"

    def state(self) -> AnswerState:
        """What this run's questions and answers read, from its engine and checkpoint."""
        if self.engine is None or self.checkpoint is None:
            raise RuntimeError(f"run {self.id} has reached no checkpoint")
        holding = self.holding
        return AnswerState(
            checkpoint=self.checkpoint,
            frameworks=self.engine.framework_options,
            analyses=() if self.report is None else self.report.analyses,
            waiting=self.report is None,
            final=self.final,
            sources=self.sources,
            links=self.links,
            facts=self.facts,
            shown=self.shown,
            skipped=self.skipped,
            corrections=self.corrections,
            revision=self.revision,
            resumed_by=None if holding is None else holding.id,
        )


class RegistryFull(Exception):
    """Every run the registry holds waits for answers, so none can make room."""


class Analyses:
    """The bounded run registry, plus the one-run-at-a-time gate.

    The gate is a flag rather than an :class:`asyncio.Semaphore` because a
    refused submission must be *refused*, not held open until the running one
    finishes (LLM10). Check-and-set is safe unsynchronised: asyncio is
    single-threaded and there is no ``await`` between the two.
    """

    def __init__(self, max_runs: int = MAX_RUNS) -> None:
        self._runs: dict[str, Run] = {}
        self._max_runs = max_runs
        self._busy = False

    def claim(self, answering: Run | None = None) -> Run | None:
        """Start a run, or ``None`` if one is already going.

        A full registry removes its oldest runs that wait for no answers. Where
        every run waits for answers, it raises :class:`RegistryFull` rather
        than remove one. A run that resumes ``answering`` may hold one place
        over the bound: ``answering`` stays the place to retry from until that
        run has a report, and a resumed run never pauses, so at most one such
        place is held.
        """
        if self._busy:
            return None
        limit = self._max_runs + (answering is not None)
        removable = iter([key for key, held in self._runs.items() if not held.waiting])
        while len(self._runs) >= limit:
            key = next(removable, None)
            if key is None:
                raise RegistryFull(
                    f"{len(self._runs)} analyses wait for answers. Answer or"
                    " continue one of them before you start another."
                )
            del self._runs[key]
        self._busy = True
        run = Run(id=secrets.token_urlsafe(16))
        self._runs[run.id] = run
        return run

    def release(self) -> None:
        self._busy = False

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)


#: Builds the engine for one submission's selection. A factory rather than a
#: built engine, because an engine is built *for* a selection and the selection
#: is now the submitter's: the options a package needs ride on it, and they are
#: known only once a form has been filled in.
EngineFactory = Callable[[Sequence[FrameworkSelection]], Engine]


@dataclass(frozen=True)
class Startup:
    """What starting up produced: what the app can offer, or the failure.

    The app serves either way. If construction raised, every route that would
    run a model is replaced by the diagnostic page, so no analysis can run on a
    model nobody chose — fail-closed, but explained.

    ``frameworks`` is what this install carries, which is what the picker
    offers and what :func:`_selection` allow-lists against. It is not itself a
    selection and it is never used as one: a submission names its own, and the
    form ships with every box ticked rather than with a default nobody chose.
    """

    engine_for: EngineFactory | None
    frameworks: tuple[FrameworkName, ...]
    tiers: ModelTierConfig | None
    error: ConfigError | None

    @property
    def ok(self) -> bool:
        return self.engine_for is not None


def build_startup(env: Mapping[str, str] | None = None) -> Startup:
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
    thing. The probe names the whole carried set for a second reason: that is
    the selection the form ships ticked, so the most common submission finds
    its graph already composed.
    :meth:`~analysis_service.deployment.Deployment.runner` memoizes per
    selection, so a narrower pick composes its own graph once and no
    submission binds the adapters twice.
    """
    env = os.environ if env is None else env
    deployment = None
    try:
        deployment = Deployment.from_env(env)
        deployment.runner(deployment.frameworks)
    except ConfigError as exc:
        logger.error("config error at startup: %s", exc)
        tiers = deployment.tiers if deployment is not None else None
        return Startup(engine_for=None, frameworks=(), tiers=tiers, error=exc)
    return Startup(
        engine_for=partial(Engine.from_deployment, deployment),
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

    ``state`` is the run's :class:`~analysis_service.answer_round.AnswerState`:
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
        script=client_script("report_view.js"),
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
            _corrections_payload(report, state.facts, state.corrections)
            if state.final
            else {}
        ),
        earlier=script_json(
            _earlier_payload(report, state.facts, state.links, state.questions.asked)
            if not state.final and state.resumed_by is None
            else {}
        ),
        provenance=script_json(_provenance_payload(report, state.facts, state.shown)),
        changes=script_json(
            {
                "findings": [
                    change.to_json() for change in report_changes(previous, report)
                ]
            }
            if previous is not None
            else {}
        ),
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
    report: Report, answered: Sequence[FactAnswer], shown: Sequence[UnknownKey]
) -> dict[str, object]:
    """What the page needs to tell the owner's answers from the sources, and to
    say why each conditional finding is still open (#1289, PR 4)."""
    return {
        "answers_label": ANSWERS_LABEL,
        "answered_attributes": [
            list(answer.key[:2])
            for answer in answered
            if answer.kind == "attribute" and answer.known
        ],
        "conditions": {
            finding: [{"label": label, "status": status} for _, label, status in rows]
            for finding, rows in conditions(
                report.analyses, report.system_model, answered, shown
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
    report: Report, answered: Sequence[FactAnswer], corrections: Sequence[FactAnswer]
) -> dict[str, object]:
    """A final report's answers as it is corrected in, and what corrections reach.

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
    once.
    """
    options = list(components(report.system_model))
    return {
        "answers": _answer_rows(
            report, [answer for answer in answered if answer.key not in asked]
        ),
        "links": [
            {
                "principal": link.principal,
                "options": options,
                "answer": link.model_dump(mode="json"),
            }
            for link in links
        ],
    }


def create_app(
    startup: Startup | None = None, analyses: Analyses | None = None
) -> FastAPI:
    """The ASGI app.

    Both collaborators are injectable so the offline lane can drive the app
    without credentials: ``startup`` supplies a stub engine, and ``analyses``
    lets a test hold the run gate to observe a refusal deterministically.
    """
    state = build_startup() if startup is None else startup
    analyses = Analyses() if analyses is None else analyses
    app = FastAPI(title="First run", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    async def form_page() -> Response:
        if not state.ok:
            return response(diagnostic_page(state), status_code=503)
        return response(
            render(
                _FORM_PAGE,
                _FORM_GRANTS,
                script=client_script("first_run.js"),
                tiers=_tier_lines(state.tiers),
                frameworks=_framework_fields(state.frameworks),
                questions=_QUESTIONS_FIELD,
            )
        )

    @app.get("/example", response_class=PlainTextResponse)
    async def example() -> Response:
        """The sample description, read from the file the examples also run."""
        return PlainTextResponse(SAMPLE.read_text(encoding="utf-8"))

    @app.post("/analyze")
    async def analyze(request: Request) -> Response:
        if not is_same_origin(request):
            logger.warning("refused a POST /analyze that was not same-origin")
            return JSONResponse(
                {"message": "This request did not come from the app's own page."},
                status_code=403,
            )
        if not state.ok or state.engine_for is None:
            return JSONResponse({"message": str(state.error)}, status_code=503)

        try:
            body = await request.json()
            sources = [Source.model_validate(source) for source in body["sources"]]
            selection = _selection(state.frameworks, body["frameworks"])
            ask_questions = body.get("questions") is True
        except SelectionError as exc:
            # Names the framework and the field it wanted, or the name the
            # install does not carry, and nothing about this deployment. Safe
            # to show, and the only way a submitter learns what to change.
            return JSONResponse({"message": str(exc)}, status_code=400)
        except (ValidationError, ValueError, KeyError, TypeError):
            return JSONResponse(
                {
                    "message": "Expected a JSON body with a 'sources' list and a"
                    " 'frameworks' list naming frameworks this install carries."
                },
                status_code=400,
            )

        # Built here rather than at startup, and before the gate is claimed: an
        # engine is built for one selection, and its constructor is where a
        # package's options model refuses a submission missing a value it needs.
        # Refusing before the gate is claimed keeps a bad option from locking
        # out the next submitter, and refusing before any node runs is what
        # stops the failure landing after the run has been paid for.
        try:
            engine = state.engine_for(selection)
        except EngineInputError as exc:
            # Names the framework and the field it wanted, and nothing about
            # this deployment. Safe to show, and it is the only way a submitter
            # learns which option they left out.
            return JSONResponse({"message": str(exc)}, status_code=400)
        except ConfigError as exc:
            # The names passed the allow-list, so this is not the caller's: the
            # config or the credentials went bad after startup proved them. Log
            # it and answer as the diagnostic would.
            logger.error("could not build an engine for %s: %s", selection, exc)
            return JSONResponse({"message": str(exc)}, status_code=503)

        try:
            run = analyses.claim()
        except RegistryFull as exc:
            return JSONResponse({"message": str(exc)}, status_code=409)
        if run is None:
            return JSONResponse(
                {"message": "An analysis is already running. Wait for it to finish."},
                status_code=409,
            )
        run.engine, run.sources = engine, sources
        start = partial(
            engine.analyze,
            sources,
            system_name="Your system",
            ask_questions=ask_questions,
        )
        # Held on the run so the task is not garbage-collected mid-flight.
        run.task = asyncio.create_task(_drive(analyses, run, start))
        return JSONResponse({"run": run.id})

    @app.post("/answer/{run_id}")
    async def answer(run_id: str, request: Request) -> Response:
        """Answer a run's questions, and start the run that resumes from it.

        The run answered is a paused one or a finished one. The new run starts
        at ``prepare`` from what that run reached, so nothing is extracted
        again. An empty answer list continues a paused run without answers; a
        finished run has nothing to continue.
        """
        if not is_same_origin(request):
            logger.warning("refused a POST /answer that was not same-origin")
            return JSONResponse(
                {"message": "This request did not come from the app's own page."},
                status_code=403,
            )
        parent = analyses.get(run_id)
        if parent is None or parent.checkpoint is None or parent.engine is None:
            return JSONResponse(
                {"message": "That run asks no question, or no longer exists."},
                status_code=404,
            )
        state = parent.state()
        try:
            body = await request.json()
            raw = body["links"]
            if not isinstance(raw, list) or len(raw) > MAX_LINK_ANSWERS:
                raise ValueError
            links = [LinkAnswer.model_validate(link) for link in raw]
            raw_facts = body.get("facts", [])
            if not isinstance(raw_facts, list) or len(raw_facts) > MAX_FACT_ANSWERS:
                raise TypeError
            facts = [_fact_answer(fact, state.questions) for fact in raw_facts]
            save = body.get("save", False)
            if not isinstance(save, bool):
                raise TypeError
            skips = _skips(body.get("skip", []))
            revision = body.get("revision")
            if revision is not None and (
                not isinstance(revision, int) or isinstance(revision, bool)
            ):
                raise TypeError
        except RefusedAnswer as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        except (ValidationError, ValueError, KeyError, TypeError):
            return JSONResponse(
                {
                    "message": "Expected a JSON body with a 'links' list and a"
                    " 'facts' list of answers this report asked for."
                },
                status_code=400,
            )
        try:
            outcome = state.answer(
                Answers(
                    links=links, facts=facts, save=save, skips=skips, revision=revision
                ),
                limits=parent.engine.limits,
            )
        except StaleRevision:
            return JSONResponse(
                {
                    "message": "Your saved answers changed in another tab or"
                    " window. Reload this page to see them."
                },
                status_code=409,
            )
        except AlreadyResumed:
            return JSONResponse(
                {
                    "message": "These answers already started an analysis. Open"
                    " its report, or answer again only if it fails."
                },
                status_code=409,
            )
        except SourcesOverLimit as exc:
            return JSONResponse({"message": exc.breach.message}, status_code=400)
        except ValueError as exc:
            # The answer rules' own refusals name the submitter's choices, so
            # they are safe to show.
            return JSONResponse({"message": str(exc)}, status_code=400)
        if isinstance(outcome, SavedRound):
            # A saved round runs no model: the answers go onto the paused run,
            # and the next round is read off the model with them in. Where
            # none is left, the page says so and waits for its start button.
            parent.links, parent.facts = outcome.links, outcome.facts
            parent.shown = list(outcome.shown)
            parent.skipped = list(outcome.skipped)
            parent.revision += 1
            return JSONResponse(paused_payload(parent, parent.state().questions))
        try:
            run = analyses.claim(answering=parent)
        except RegistryFull as exc:
            return JSONResponse({"message": str(exc)}, status_code=409)
        if run is None:
            return JSONResponse(
                {"message": "An analysis is already running. Wait for it to finish."},
                status_code=409,
            )
        parent.resumed_by = run
        run.previous = parent.report
        run.engine, run.sources = parent.engine, parent.sources
        run.links, run.facts = outcome.links, outcome.facts
        run.final = outcome.follow_up
        run.shown = list(outcome.shown)
        start = partial(parent.engine.resume, outcome, system_name="Your system")
        run.task = asyncio.create_task(_drive(analyses, run, start))
        return JSONResponse({"run": run.id})

    @app.get("/events/{run_id}")
    async def events(run_id: str) -> Response:
        run = analyses.get(run_id)
        if run is None:
            return PlainTextResponse("no such run", status_code=404)
        return StreamingResponse(
            _stream(run),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/report/{run_id}", response_class=HTMLResponse)
    async def report_page(run_id: str) -> Response:
        run = analyses.get(run_id)
        if run is None or run.report is None:
            return PlainTextResponse("no such report", status_code=404)
        return response(render_report(run.report, run.state(), run.previous))

    @app.post("/correct/{run_id}")
    async def correct(run_id: str, request: Request) -> Response:
        """Correct a final report's answers, and run nothing (ADR 0054)."""
        if not is_same_origin(request):
            logger.warning("refused a POST /correct that was not same-origin")
            return JSONResponse(
                {"message": "This request did not come from the app's own page."},
                status_code=403,
            )
        run = analyses.get(run_id)
        if run is None or run.report is None or run.engine is None:
            return JSONResponse(
                {"message": "That report no longer exists."}, status_code=404
            )
        state = run.state()
        try:
            body = await request.json()
            raw = body["facts"]
            if not isinstance(raw, list) or len(raw) > MAX_FACT_ANSWERS:
                raise TypeError
            facts = [_fact_answer(fact, state.questions) for fact in raw]
        except RefusedAnswer as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        except (ValidationError, ValueError, KeyError, TypeError):
            return JSONResponse(
                {"message": "Expected a JSON body with a 'facts' list of corrections."},
                status_code=400,
            )
        try:
            run.corrections = state.correct(facts)
        except ValueError as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        return JSONResponse({"run": run.id})

    return app


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

    Every box ships ticked. That is the form's starting state, not a default
    the app invented: a submission still has to say what it selected, and the
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
            f' value="{escape(name)}" checked> <b>{escape(name)}</b></label>'
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


async def _drive(
    analyses: Analyses,
    run: Run,
    start: Callable[..., Awaitable[PipelineOutcome]],
) -> None:
    """Run one analysis to a terminal state, narrating it onto the run's queue.

    ``start`` is the engine call, a fresh analysis or a resumption, still
    waiting for its ``on_node``. Only a completed run reaches the viewer,
    because the viewer renders reports. A paused run sends its questions to the
    form page. A rejection, a bad submission and an internal failure all land
    back on the form page, where the submitted text is still in the textarea.
    """
    try:
        outcome = await start(on_node=_ticker(run))
    except EngineInputError as exc:
        # Raised before any model ran — no sources, too many, or more bytes
        # than this deployment allows. The message is about the caller's input
        # and is safe to show.
        await _emit(run, "failed", {"message": str(exc)})
    except ConfigError as exc:
        # A resumed run builds its graph on first use, so a credential that
        # went bad after startup raises here. The message names a setting and
        # never its value, and the start route and the diagnostic page show
        # it to the same local operator.
        logger.error("run %s could not build its runner: %s", run.id, exc)
        await _emit(
            run,
            "failed",
            {"message": f"This app's configuration is incomplete: {exc}"},
        )
    except EngineDeadlineError as exc:
        # Distinct from the generic failure for the reason
        # ``jobs.DEADLINE_FAILURE_MESSAGE`` gives: a deadline is an operational
        # fact about this deployment's bounds, and saying so beats sending the
        # submitter to retry an identical description against an identical
        # budget. It names no node and no model — those are in the server log.
        logger.warning("run %s hit the deployment's time budget", run.id)
        await _emit(run, "failed", {"message": str(exc)})
    except Exception:
        # The traceback goes to the server log and never to the browser (A10).
        logger.exception("run %s failed in the pipeline", run.id)
        await _emit(
            run, "failed", {"message": "The analysis failed. Check the server log."}
        )
    else:
        if isinstance(outcome, PipelineCompleted):
            run.report = outcome.report
            # Every finished report can be answered: its open facts need no
            # catalog, and its link questions need the one it carries.
            run.checkpoint = Checkpoint(
                system_model=outcome.report.system_model,
                assertions=outcome.report.assertions,
            )
            await _emit(run, "done", {"url": f"/report/{run.id}"})
        elif isinstance(outcome, PipelineAwaiting):
            run.checkpoint = outcome.checkpoint
            await _emit(run, "questions", paused_payload(run, run.state().questions))
        else:
            await _emit(
                run,
                "rejected",
                {
                    "issues": [
                        {"code": issue.code, "message": issue.message}
                        for issue in outcome.issues
                    ]
                },
            )
    finally:
        analyses.release()
        await run.events.put(("", ""))  # sentinel: closes the SSE stream


def _element_names(questions: QuestionSet) -> dict[str, str]:
    return {element.id: element.name for element in questions.model.elements()}


def _link_row(question: LinkQuestion, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "principal": question.principal,
        "rows": question.rows,
        "options": [
            {"id": option, "name": names.get(option, "")} for option in question.options
        ],
    }


def _early_row(question: EarlyQuestion, model: SystemModel) -> dict[str, object]:
    """The question as the form page shows it: each choice's element name, and
    the words of the description the question's element was read from."""
    element = model.get(question.key[0])
    return {
        **question.to_json(),
        "choices": [
            {"id": choice, "name": getattr(model.get(choice), "name", "")}
            for choice in question.choices
        ],
        "excerpt": "" if element is None else element.source_excerpt,
    }


_SKIPS: TypeAdapter[list[SkipKey]] = TypeAdapter(
    Annotated[list[SkipKey], Field(max_length=MAX_SKIPS)]
)


def _skips(raw: object) -> list[SkipKey]:
    """The keys a save skips (:data:`SkipKey`), or a ``TypeError``."""
    try:
        return _SKIPS.validate_python(raw)
    except ValidationError as exc:
        raise TypeError from exc


class RefusedAnswer(Exception):
    """One fact answer the page sent does not parse; the message names its question."""


def _fact_answer(raw: object, questions: QuestionSet) -> FactAnswer:
    """The answer, or a :class:`RefusedAnswer` that names the question it answers.

    The message is the validator's own, which names the submitter's choices,
    so it is safe to show.
    """
    try:
        return FactAnswer.model_validate(raw)
    except ValidationError as error:
        reason = error.errors()[0]["msg"].removeprefix("Value error, ")
        known = questions.asked | {q.key for q, _ in questions.answered_early}
        key = raw.get("key") if isinstance(raw, dict) else None
        parts = tuple(key) if isinstance(key, list) else ()
        label = (
            fact_label(parts, questions.model)
            if all(isinstance(part, str) for part in parts) and parts in known
            else None
        )
        lead = "An answer" if label is None else f'The answer to "{label}"'
        raise RefusedAnswer(f"{lead} was refused: {reason}") from None


def question_rows(questions: QuestionSet) -> list[dict[str, object]]:
    """A run's link questions, with each option's element name beside it.

    The form page has no report to look names up in, so they travel with the
    questions. Every string is untrusted and lands on the page as text.
    """
    names = _element_names(questions)
    return [_link_row(question, names) for question in questions.links]


def early_rows(questions: QuestionSet) -> list[dict[str, object]]:
    """A paused run's open facts, ranked before any finding, with choice names.

    Every string is untrusted and lands on the page as text.
    """
    return [_early_row(question, questions.model) for question in questions.early]


def paused_payload(run: Run, questions: QuestionSet) -> dict[str, object]:
    """What the form page shows for one round at the pause (ADR 0053).

    This round's questions, how many each kind has left, and every earlier
    answer with its question, so the page can show it and take a new one.
    """
    names = _element_names(questions)
    return {
        "run": run.id,
        "revision": run.revision,
        "questions": question_rows(questions),
        "facts": early_rows(questions),
        "remaining": dict(questions.remaining),
        "stop": questions.stop,
        "withheld": questions.withheld,
        "skipped": [
            _early_row(question, questions.model) for question in questions.skipped
        ],
        "answered": [
            _early_row(question, questions.model)
            | {"answer": answer.model_dump(mode="json")}
            for question, answer in questions.answered_early
        ],
        "skipped_links": [
            _link_row(question, names) for question in questions.skipped_links
        ],
        "answered_links": [
            _link_row(question, names) | {"answer": answer.model_dump(mode="json")}
            for question, answer in questions.answered_links
        ],
    }


def _ticker(run: Run):
    """The ``on_node`` callback: one SSE tick per completed node."""

    async def on_node(node: str) -> None:
        # Node names go out verbatim as graph.py emits them. A prettifying
        # table here would be a second place for them to drift from the graph.
        await run.events.put(("node", json.dumps({"node": node})))

    return on_node


async def _emit(run: Run, event: str, data: dict) -> None:
    await run.events.put((event, json.dumps(data)))


async def _stream(run: Run) -> AsyncIterator[str]:
    """Drain the run's queue as server-sent events until the sentinel."""
    while True:
        event, data = await run.events.get()
        if not event:
            return
        yield f"event: {event}\ndata: {data}\n\n"


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

_FORM_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Analyze a system — first run</title><style nonce="__CSP_NONCE__">"""
    + _STYLE
    + """</style></head>
<body>
<h1>Analyze a system</h1>
<p class="sub">Running in process, on real models.</p>
<!--tiers-->
<form id="analyze">
  <fieldset id="frameworks">
    <legend>Frameworks</legend>
    <!--frameworks-->
  </fieldset>
  <p><textarea id="description" name="description"
     placeholder="Describe your system..."></textarea></p>
  <!--questions-->
  <p>
    <button type="submit" id="go">Analyze</button>
    <button type="button" id="load">Load example</button>
  </p>
</form>
<div id="problem" class="problem" hidden></div>
<p id="status" hidden><span class="spinner" aria-hidden="true"></span>
  <span id="status-text" role="status"></span></p>
<ul id="ticks" hidden></ul>
<div id="asked" hidden>
  <h2>Your system model is ready. The threat analysis has not started.</h2>
  <p class="sub">The service read your description, built a model of your system
  and checked it. It stopped before the threat analysis so that you can add
  facts your description does not state. These rounds are free: no analysis
  runs until you start it. The questions come a few at a time,
  the most useful first. Answer what you can. <b>Save and show more</b> keeps
  your answers, and a question you left blank comes back.
  <b>Skip the rest and show more</b> sets the blank ones aside, under
  <b>Skipped for now</b>, where you can still answer them. A skipped question
  is not an answer: the analysis treats it as open. Choose <b>Start the
  analysis</b> at any time. A save never starts the analysis: only the start
  button does. The analysis reads your answers. After it, the report may offer
  one optional follow-up.</p>
  <div id="questions"></div>
  <details id="earlier" hidden></details>
  <details id="skipped" hidden></details>
  <p><button type="button" id="save">Save and show more</button>
  <button type="button" id="skip">Skip the rest and show more</button>
  <button type="button" id="continue">Start the analysis</button></p>
  <div id="answer-problem" class="problem" role="alert" hidden></div>
</div>
<script nonce="__CSP_NONCE__"><!--script--></script>
</body></html>
"""
)

#: The question toggle. Every install can pause: one with no catalog asks its
#: early questions and no link question.
_QUESTIONS_FIELD = """<p><label><input type="checkbox" id="ask" name="ask">
    Ask me questions before the analysis runs, and wait for my answers</label></p>
<ol class="sub">
  <li><b>Facts.</b> With the box ticked, the app asks what your description
  leaves out, a few questions at a time. These rounds are free: no analysis runs until you
  start it.</li>
  <li><b>Analysis.</b> The threat analysis runs once, in a few minutes.</li>
  <li><b>Follow-up, optional.</b> The report may ask about facts its findings
  depend on. Answering runs the analysis once more, and that report is
  final.</li>
</ol>"""

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
