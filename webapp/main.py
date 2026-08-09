"""The lite first-run web app — route step 3.

Start it from a clone, with model auth already configured::

    uv run python webapp/main.py

It embeds :class:`~stride_service.StrideEngine` **in process**. It does not go
through the ``/v1`` HTTP surface, so it needs no bearer token and no CORS — and
it runs real models against real prose, because there is no credential-free
path here. Its whole job is to show a first-time integrator that the engine
works, then get out of the way; the library is what they actually embed.

Two pages, three data endpoints:

======================  ========================================================
``GET  /``              form page: the resolved tiers, a textarea, Analyze
``GET  /report/{run}``  ``report_view.html`` with this run's JSON injected
``GET  /example``       ``examples/orders.md``, for **Load example**
``POST /analyze``       start a run, return its id
``GET  /events/{run}``  server-sent per-node progress
======================  ========================================================

**Deliberately unbloated.** One module, no template engine, no JS framework, no
build step, no CSS framework, no bundler. HTML comes from f-strings and the
only client-side JavaScript is the SSE listener, the Load-example fill, and the
redirect.

Security posture, all of it deliberate:

* **Loopback only.** ``127.0.0.1`` is hard-bound with no flag and no override.
  The no-auth/no-CORS posture is only safe there: anything reachable is an
  unauthenticated proxy to someone else's vendor bill. Remote, authenticated
  access is exactly what ``/v1`` already exists for.
* **No HTTP input touches model identity.** Tier, vendor, model and sampling
  come from ``config/`` alone. A form field selecting a model would be
  unauthenticated control over what runs and what it costs (A01, LLM10).
* **The injection point is the whole trust boundary.** A report carries the
  submitter's own prose, so every ``<`` is escaped to ``\\u003c`` before the
  JSON enters the viewer's ``<script>`` block — otherwise a description
  containing ``</script>`` closes it and the rest parses as HTML (A05 / LLM05).
  See :func:`render_report`.
* **Untrusted text never reaches ``innerHTML``** on any page. It renders as
  ``textContent`` or as constructed DOM nodes, so there is no escape helper to
  forget to call — the discipline had already failed once, unnoticed, in the
  element table's attribute column. This is the primary control; the CSP below
  is defence in depth behind it. The form page is included: a source label and a
  validator message both carry submitter bytes onto it over SSE, and both land
  as text nodes. Server-side, the two f-string pages escape through
  :func:`_escape`, which is quote-safe so that where a value lands is not part
  of whether the escape is adequate.
* **CSRF.** ``POST /analyze`` requires ``Sec-Fetch-Site: same-origin``. The
  header is browser-set and unspoofable from script, and it is checked before
  anything else so a cross-origin caller cannot even start a run.
* **One run at a time** (LLM10). A second submission is refused with a message,
  not queued and held open.
* **A strict nonce CSP on every page.** ``default-src 'none'`` with a
  per-response nonce for each inline block, and no ``'unsafe-inline'`` anywhere
  — which is why the viewer carries no ``style=""`` attributes and sets
  generated colours through ``element.style.*``, a CSSOM write CSP does not
  govern. No page loads an external resource or carries an ``on*=`` handler, so
  ``'none'`` costs nothing. Each policy grants what its own page does and
  nothing else: the report page reaches the network not at all, the form page
  only its own origin (``connect-src 'self'`` for ``/example``, ``/analyze`` and
  ``/events``), and the diagnostic page runs no script, so it is granted no
  ``script-src`` at all. A page and its policy are built together as a
  :class:`RenderedPage` and served through :func:`_html`, so serving HTML
  without its header is unspellable rather than merely discouraged.
* **``nosniff`` and ``no-referrer`` on every response**, HTML or not, applied by
  :class:`SecurityHeaders`. Content sniffing is what would let a browser treat
  ``/example``'s ``text/plain`` prose as something else, and the referrer policy
  keeps a run id out of outbound ``Referer`` headers.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from stride_service import (
    ConfigError,
    EngineDeadlineError,
    EngineInputError,
    PipelineCompleted,
    Source,
    StrideEngine,
    StrideReport,
)
from stride_service.deployment import Deployment
from stride_service.model_tiers import ModelTierConfig
from stride_service.vendors import vendor_for

logger = logging.getLogger("webapp")

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEWER = Path(__file__).resolve().parent / "report_view.html"
SAMPLE = REPO_ROOT / "examples" / "orders.md"

HOST = "127.0.0.1"
PORT = 8000

# The registry is a demo surface, not a job store — /v1 already is one. It holds
# untrusted prose and its report, in memory, per process, never persisted, and
# oldest-first evicted. A restart loses history, which is correct here.
MAX_RUNS = 20

# The template's own script parses this same block. Matched by id rather than
# by position, so the chrome can move without breaking injection; the trailing
# ``[^>]*`` tolerates the nonce attribute the render pass fills in.
_PAYLOAD_BLOCK = re.compile(
    r'(<script type="application/json" id="report"[^>]*>)(.*?)(</script>)',
    re.DOTALL,
)

# Replaced with a fresh per-response nonce on every render. The viewer ships
# with the placeholder in its three inline blocks, so a block added later
# without one simply stops running rather than running unauthorised.
_NONCE_PLACEHOLDER = "__CSP_NONCE__"

# ``default-src 'none'`` and no ``'unsafe-inline'``: the viewer loads nothing
# external, so everything it needs is the nonce. ``base-uri`` and
# ``form-action`` are closed because neither has any use on a report page.
_CSP = (
    "default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
    "base-uri 'none'; form-action 'none'"
)

# The form page's policy differs from the report page's by exactly what the page
# does: it calls ``/example``, ``/analyze`` and ``/events/{run}``, so it needs
# ``connect-src 'self'`` and the report page does not. ``form-action`` stays
# closed even though the page carries a ``<form>`` — the submit handler is
# ``preventDefault()``-ed and posts through fetch, so a navigation away from
# this form is something going wrong.
_FORM_CSP = (
    "default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'"
)

# The diagnostic page runs no script and makes no request: it is one style block
# and static prose. So it is granted neither ``script-src`` nor ``connect-src``
# and both fall through to ``default-src 'none'`` — a page that has nothing to
# authorise should not carry the grant that would authorise one.
_DIAGNOSTIC_CSP = (
    "default-src 'none'; style-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'"
)


class SecurityHeaders:
    """``nosniff`` and ``no-referrer`` on every response, whatever served it.

    Pure ASGI rather than a ``@app.middleware("http")`` function so it cannot
    come between ``/events/{run}`` and its client: this only edits the header
    frame on its way out and never touches the body, so a stream stays a stream.

    Both headers are per *response* rather than per page, which is why they are
    here and the CSP is not. ``nosniff`` matters most for the responses that are
    not HTML — ``/example`` serves prose as ``text/plain``, and content sniffing
    is precisely the mechanism that would let a browser decide otherwise.
    ``no-referrer`` keeps a run id out of the ``Referer`` of anything the report
    page's own links reach.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
            await send(message)

        await self._app(scope, receive, _send)


@dataclass
class Run:
    """One in-flight or finished analysis."""

    id: str
    events: asyncio.Queue[tuple[str, str]] = field(default_factory=asyncio.Queue)
    report: StrideReport | None = None
    task: asyncio.Task | None = None


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

    def claim(self) -> Run | None:
        """Start a run, or ``None`` if one is already going."""
        if self._busy:
            return None
        self._busy = True
        run = Run(id=secrets.token_urlsafe(16))
        self._runs[run.id] = run
        while len(self._runs) > self._max_runs:
            self._runs.pop(next(iter(self._runs)))
        return run

    def release(self) -> None:
        self._busy = False

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)


@dataclass(frozen=True)
class Startup:
    """What building the engine produced: a working pair, or the failure.

    The app serves either way. If construction raised, every route that would
    run a model is replaced by the diagnostic page, so no analysis can run on a
    model nobody chose — fail-closed, but explained.
    """

    engine: StrideEngine | None
    tiers: ModelTierConfig | None
    error: ConfigError | None

    @property
    def ok(self) -> bool:
        return self.engine is not None


def build_startup(env: Mapping[str, str] | None = None) -> Startup:
    """Build the engine once, converting a config failure into a page.

    Two stages, and the split is the whole content of the diagnostic. Resolving
    the :class:`~stride_service.deployment.Deployment` reads the config files;
    building the engine off it resolves the vendor's credentials and runs every
    tier's ``(vendor, model, sampling)`` through LiteLLM's own check. So a
    *config* failure leaves no tiers to report, while a *credential* failure
    can still name the vendor the config selected — which is the case a first
    run overwhelmingly hits. One read either way: the tiers the page prints are
    the tiers the engine was built from, not a second load of the same file.
    """
    env = os.environ if env is None else env
    deployment = None
    try:
        deployment = Deployment.from_env(env)
        engine = StrideEngine.from_deployment(deployment)
    except ConfigError as exc:
        logger.error("config error at startup: %s", exc)
        tiers = deployment.tiers if deployment is not None else None
        return Startup(engine=None, tiers=tiers, error=exc)
    return Startup(engine=engine, tiers=deployment.tiers, error=None)


@dataclass(frozen=True)
class RenderedPage:
    """A page and the CSP header that authorises exactly its own nonce.

    The two travel together so a caller cannot serve the page without the
    policy: a nonce nothing authorises would leave the page's own blocks
    blocked, and a policy without its page is meaningless. Every HTML response
    this app serves is one of these — the three pages differ in what their
    policy grants, never in whether they carry one.
    """

    html: str
    csp: str


def render_report(report: StrideReport) -> RenderedPage:
    """``report_view.html``, carrying this run's report.

    The template is a self-contained renderer for the report schema — no build
    step, no framework, its own inline CSS and JS. This substitutes the contents
    of its JSON block and its per-response CSP nonce, and serves the result.

    **The escape is the contract.** The report carries the submitter's own prose,
    so a description containing ``</script>`` would close the block and
    everything after it would parse as HTML — a stored-input XSS on the one page
    the first-run route sends people to. Escaping every ``<`` to ``\\u003c``
    keeps the payload inert while remaining valid JSON, and the substitution
    goes through a function rather than a replacement string so that those
    backslashes are not themselves re-interpreted.

    **The nonce is stamped before the payload is injected**, never after, so a
    submitter who writes the placeholder into their own prose gets it back
    verbatim as data instead of having it substituted.
    """
    nonce = secrets.token_urlsafe(16)
    template = VIEWER.read_text(encoding="utf-8").replace(_NONCE_PLACEHOLDER, nonce)

    payload = json.dumps(report.model_dump(mode="json")).replace("<", "\\u003c")
    rendered, count = _PAYLOAD_BLOCK.subn(
        lambda match: f"{match.group(1)}\n{payload}\n{match.group(3)}",
        template,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"{VIEWER} has no <script id='report'> block to inject into")
    return RenderedPage(html=rendered, csp=_CSP.format(nonce=nonce))


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
    app = FastAPI(title="STRIDE first run", docs_url=None, redoc_url=None)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    async def form_page() -> Response:
        if not state.ok:
            return _html(diagnostic_page(state), status_code=503)
        return _html(_page(_FORM_PAGE, _FORM_CSP, tiers=_tier_lines(state.tiers)))

    @app.get("/example", response_class=PlainTextResponse)
    async def example() -> Response:
        """The sample description, read from the file the examples also run."""
        return PlainTextResponse(SAMPLE.read_text(encoding="utf-8"))

    @app.post("/analyze")
    async def analyze(request: Request) -> Response:
        if request.headers.get("sec-fetch-site") != "same-origin":
            logger.warning("refused a POST /analyze that was not same-origin")
            return JSONResponse(
                {"message": "This request did not come from the app's own page."},
                status_code=403,
            )
        if not state.ok or state.engine is None:
            return JSONResponse({"message": str(state.error)}, status_code=503)

        try:
            body = await request.json()
            sources = [Source.model_validate(source) for source in body["sources"]]
        except (ValidationError, ValueError, KeyError, TypeError):
            return JSONResponse(
                {"message": "Expected a JSON body with a 'sources' list."},
                status_code=400,
            )

        run = analyses.claim()
        if run is None:
            return JSONResponse(
                {"message": "An analysis is already running. Wait for it to finish."},
                status_code=409,
            )
        # Held on the run so the task is not garbage-collected mid-flight.
        run.task = asyncio.create_task(_drive(state.engine, analyses, run, sources))
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
        return _html(render_report(run.report))

    return app


def _html(page: RenderedPage, status_code: int = 200) -> HTMLResponse:
    """The only way this app serves HTML, so no page can be served bare.

    A page and its policy are built together and travel together; taking a
    :class:`RenderedPage` rather than a string is what makes "serve the HTML,
    forget the header" unspellable rather than merely discouraged.
    """
    return HTMLResponse(
        page.html,
        status_code=status_code,
        headers={"Content-Security-Policy": page.csp},
    )


async def _drive(
    engine: StrideEngine, analyses: Analyses, run: Run, sources: list[Source]
) -> None:
    """Run one analysis to a terminal state, narrating it onto the run's queue.

    Only a completed run reaches the viewer, because the viewer renders
    reports. A rejection, a bad submission and an internal failure all land
    back on the form page, where the submitted text is still in the textarea.
    """
    try:
        outcome = await engine.analyze(
            sources, system_name="Your system", on_node=_ticker(run)
        )
    except EngineInputError as exc:
        # Raised before any model ran — no sources, too many, or more bytes
        # than this deployment allows. The message is about the caller's input
        # and is safe to show.
        await _emit(run, "failed", {"message": str(exc)})
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
            await _emit(run, "done", {"url": f"/report/{run.id}"})
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
        f'<div class="tier"><b>{_escape(tier)}</b> → '
        f"{_escape(selection.vendor)} / {_escape(selection.model)}</div>"
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
    return _page(
        _DIAGNOSTIC_PAGE,
        _DIAGNOSTIC_CSP,
        message=_escape(str(state.error)),
        vendors=_vendor_sections(state.tiers),
    )


def _vendor_sections(
    tiers: ModelTierConfig | None, env: Mapping[str, str] | None = None
) -> str:
    """Each selected vendor's required variables, marked set or unset.

    ``required_env_vars`` comes from the same registry entry that performs the
    check, so this cannot drift from what actually failed — and it lists the
    vendor's *whole* set, because ``Vendor._require`` raises on the first
    missing one and a reader would otherwise discover them one restart at a
    time.
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
    for vendor in dict.fromkeys(sel.vendor for sel in tiers.tiers.values()):
        items = "\n".join(
            _env_var_item(var, bool(env.get(var, "").strip()))
            for var in vendor_for(vendor).required_env_vars
        )
        sections.append(f"<h3>{_escape(vendor)}</h3>\n<ul>{items}</ul>")
    return "\n".join(sections)


def _env_var_item(var: str, is_set: bool) -> str:
    """One variable's row — presence only, never the value (OWASP A09)."""
    css_class, label = ("set", "set") if is_set else ("not", "NOT SET")
    return (
        f'<li><code>{_escape(var)}</code> <span class="{css_class}">{label}</span></li>'
    )


def _escape(text: str) -> str:
    """Server-side escape for the two f-string pages.

    :func:`html.escape` rather than a hand-rolled three-character replace,
    because the hand-rolled one covered ``&<>`` and not quotes: every call site
    here lands in element text position, so it was correct, and it would have
    stopped being correct the first time someone interpolated into an attribute
    value. The stdlib version is right in both positions, so where a value goes
    is no longer part of whether the escape is adequate.
    """
    return html.escape(text)


def _page(template: str, csp: str, **fields: str) -> RenderedPage:
    """Fill a page template, and stamp the nonce its policy authorises.

    Substitution is by explicit token replacement rather than ``str.format``:
    the templates contain CSS and JavaScript, which are full of braces that
    ``format`` would try to interpret as fields.

    **The nonce is stamped before the fields are filled**, never after, the same
    ordering :func:`render_report` keeps and for the same reason: a field's value
    is content, and content that happens to spell the placeholder must come back
    as those characters rather than as a live nonce.
    """
    nonce = secrets.token_urlsafe(16)
    html = template.replace(_NONCE_PLACEHOLDER, nonce)
    for name, value in fields.items():
        html = html.replace(f"<!--{name}-->", value)
    return RenderedPage(html=html, csp=csp.format(nonce=nonce))


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
"""

_FORM_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>STRIDE — first run</title><style nonce="__CSP_NONCE__">"""
    + _STYLE
    + """</style></head>
<body>
<h1>STRIDE threat model</h1>
<p class="sub">Running in process, on real models.</p>
<!--tiers-->
<form id="analyze">
  <p><textarea id="description" name="description"
     placeholder="Describe your system..."></textarea></p>
  <p>
    <button type="submit" id="go">Analyze</button>
    <button type="button" id="load">Load example</button>
  </p>
</form>
<div id="problem" class="problem" hidden></div>
<ul id="ticks" hidden></ul>
<script nonce="__CSP_NONCE__">
  const form = document.getElementById("analyze");
  const box = document.getElementById("description");
  const ticks = document.getElementById("ticks");
  const problem = document.getElementById("problem");
  const go = document.getElementById("go");

  document.getElementById("load").addEventListener("click", async () => {
    box.value = await (await fetch("/example")).text();
  });

  // Nodes and strings, never markup. replaceChildren() inserts a string as a
  // text node, so a source label or a validator message that spells markup
  // shows the characters the submitter typed. Same rule as the report viewer,
  // and for the same reason: with no escape helper on the page there is none
  // to forget, and forgetting shows junk on screen instead of running.
  const fail = (...content) => {
    problem.replaceChildren(...content);
    problem.hidden = false;
    ticks.hidden = true;
    go.disabled = false;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.hidden = true;
    ticks.replaceChildren();
    go.disabled = true;

    const started = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sources: [
          { kind: "description", label: "Pasted description", text: box.value },
        ],
      }),
    });
    if (!started.ok) {
      fail((await started.json()).message);
      return;
    }

    ticks.hidden = false;
    const stream = new EventSource("/events/" + (await started.json()).run);
    stream.addEventListener("node", (event) => {
      const item = document.createElement("li");
      item.textContent = JSON.parse(event.data).node;
      ticks.append(item);
    });
    stream.addEventListener("done", (event) => {
      stream.close();
      location.href = JSON.parse(event.data).url;
    });
    stream.addEventListener("rejected", (event) => {
      stream.close();
      const lead = document.createElement("b");
      lead.textContent = "That description could not be modelled.";
      const list = document.createElement("ul");
      for (const issue of JSON.parse(event.data).issues) {
        const item = document.createElement("li");
        const code = document.createElement("code");
        code.textContent = issue.code;
        item.append(code, " " + issue.message);
        list.append(item);
      }
      fail(lead, list);
    });
    stream.addEventListener("failed", (event) => {
      stream.close();
      fail(JSON.parse(event.data).message);
    });
  });
</script>
</body></html>
"""
)

_DIAGNOSTIC_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>STRIDE — configuration problem</title><style nonce="__CSP_NONCE__">"""
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
    print(f"STRIDE first-run app on http://{HOST}:{PORT}")
    # Loopback is hard-bound: no flag, no env override. The no-auth posture is
    # only safe here.
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
