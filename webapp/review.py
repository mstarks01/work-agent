"""The review app: one finding, one question, one click.

Start it from a clone, with no credentials of any kind::

    uv run python webapp/review.py --voter ada

It is **eval-side tooling and not the product**. ``webapp/main.py`` embeds the
engine and runs real models against real prose; this reads a sweep's artifact
and the vote ledger under ``evals/review/votes/``, and writes one line per
click. Nothing here can start a job, spend a token, or reach a provider. That is
why it needs no auth story beyond the loopback bind: there is no credential
behind it to protect and nothing it can be made to buy.

Two pages, three data endpoints:

======================  =======================================================
``GET  /``              the queue: what is waiting, and Start reviewing
``GET  /review``       one finding, blind, with its evidence
``GET  /api/next``      the next unanswered finding as JSON
``POST /api/vote``      record one vote, append-only
``GET  /api/summary``   counts for the queue page
======================  =======================================================

**The reviewer never sees which configuration produced the finding.** That is
enforced by :class:`~evals.harness.queue.QueueItem`, which has no field for it,
and asserted by ``tests/test_evals_queue.py``. The configuration is stamped onto
the :class:`~evals.harness.ledger.Vote` *after* the answer, from the artifact,
where the reviewer cannot reach it.

Security posture, all of it deliberate and all of it inherited from
``webapp/main.py`` rather than re-derived:

* **Loopback only**, hard-bound with no flag (A01). The ledger is the supply
  chain of every published quality number, so a writable endpoint reachable off
  the host is an unauthenticated way to forge that record.
* **The voter comes from the command line, never from the request** (A01). A
  browser field naming the voter would let one reviewer file votes as another,
  and the double-vote agreement measure rests on the name being true.
* **Every finding is submitter prose and reaches the page as data** (LLM05,
  A05). Values are injected as one JSON blob that the client renders through
  ``textContent``, so there is no HTML path for a claim to take.
* **The vote body is validated against closed sets before it is written**
  (A05). ``verdict`` and ``reason`` are checked by
  :class:`~evals.harness.ledger.Vote` itself, and the fingerprint must already
  be one the queue offered — a client cannot invent a finding to vote on.
* **Errors fail closed and say nothing about the filesystem** (A10).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals import verify_corpus
from evals.harness import queue as review_queue
from evals.harness.fingerprint import identifier_of, lane_field
from evals.harness.ledger import (
    DEFAULT_LEDGER_PATH,
    REASON_GLOSS,
    STYLE_REASONS,
    SUBSTANCE_REASONS,
    LedgerError,
    Verdict,
    append,
    cast,
    load,
)
from evals.harness.reference import load_corpus
from webapp.main import LOOPBACK_HOSTS, SecurityHeaders

HOST = "127.0.0.1"
PORT = 8010

#: The verdicts the endpoint accepts, spelled from the type the ledger declares
#: so the two cannot drift apart.
VERDICTS: tuple[str, ...] = get_args(Verdict)

_NONCE_PLACEHOLDER = "__CSP_NONCE__"

#: No network origin at all: every asset is inline and nonce-authorised, and
#: ``connect-src 'self'`` is the one grant the page needs — it calls its own
#: three endpoints and nothing else.
_CSP = (
    "default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}';"
    " connect-src 'self'; base-uri 'none'; form-action 'none'"
)


class VoteBody(BaseModel):
    """What the page posts. Closed, so an unknown field is a 422 not a silent drop."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=3, max_length=64)
    verdict: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=1000)


@dataclass
class Session:
    """One sitting: who is reviewing, over which findings, against which ledger."""

    voter: str
    ledger_path: Path
    items: list[review_queue.QueueItem]
    sources: dict[str, str]
    configs: dict[str, str]
    sitting: str

    def remaining(self) -> list[review_queue.QueueItem]:
        """The queue minus anything this voter has answered since it was built.

        Recomputed per request rather than popped from a list: two tabs open on
        one sitting is a thing people do, and a list would let the second tab
        serve a finding the first already answered.
        """
        answered = frozenset(
            key[0] for key in load(self.ledger_path).current() if key[1] == self.voter
        )
        return [item for item in self.items if item.fingerprint not in answered]

    def find(self, value: str) -> review_queue.QueueItem:
        """The item a vote names, or a refusal.

        A client cannot vote on a fingerprint the queue never offered. Without
        this the endpoint would accept any 16 hex characters and write a row
        nothing can ever resolve back to a finding.
        """
        for item in self.items:
            if item.fingerprint == value:
                return item
        raise HTTPException(status_code=404, detail="no such finding in this queue")


def build_session(
    runs: Sequence[Sequence[review_queue.Finding]],
    voter: str,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    configs: Mapping[str, str] | None = None,
    corpus_dir: Path | None = None,
) -> Session:
    """Resolve the corpus once, build the queue, and hold it for the sitting.

    ``runs`` is one sequence of findings per sweep, not one flat list, because
    the queue asks first about a finding the sweeps disagree on. That count
    only exists if the caller says where one run ends and the next begins.
    """
    corpus = load_corpus(corpus_dir or verify_corpus.CORPUS_DIR)
    flows = {
        case.meta.id: {
            flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
        }
        for case in corpus
    }
    sources = {
        case.meta.id: "\n\n".join(source.text for source in case.sources)
        for case in corpus
    }
    ledger = load(ledger_path)
    items = review_queue.build(
        review_queue.merge_runs(runs, flows),
        flows,
        ledger,
        reference_pool=ledger.pool(),
        voter=voter,
    )
    return Session(
        voter=voter,
        ledger_path=ledger_path,
        items=items,
        sources=sources,
        configs=dict(configs or {}),
        sitting=f"web-{secrets.token_hex(4)}",
    )


def create_app(session: Session) -> FastAPI:
    """The review app over one prepared sitting."""
    app = FastAPI(title="STRIDE review", docs_url=None, redoc_url=None)
    # Before anything else, so a rebound request is refused rather than
    # reaching the one endpoint in this repository that writes a human
    # judgement. See LOOPBACK_HOSTS for what binding alone does not stop.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return _html(_page(_QUEUE_PAGE, voter=_escape(session.voter)))

    @app.get("/review", response_class=HTMLResponse)
    def review() -> HTMLResponse:
        return _html(_page(_REVIEW_PAGE, voter=_escape(session.voter)))

    @app.get("/api/summary")
    def summary() -> JSONResponse:
        remaining = session.remaining()
        payload = review_queue.summarise(remaining, load(session.ledger_path))
        payload["voter"] = session.voter
        return JSONResponse(payload)

    @app.get("/api/next")
    def next_item() -> JSONResponse:
        remaining = session.remaining()
        if not remaining:
            return JSONResponse({"done": True, "remaining": 0})
        item = remaining[0]
        payload = item.to_json()
        payload["done"] = False
        payload["remaining"] = len(remaining)
        payload["source"] = session.sources.get(item.finding.case, "")
        payload["reasons"] = _reason_payload()
        return JSONResponse(payload)

    @app.post("/api/vote")
    def vote(body: VoteBody) -> JSONResponse:
        item = session.find(body.fingerprint)
        # Narrowed here rather than trusted from the body: ``Vote`` refuses an
        # unknown verdict at construction anyway, and checking against the same
        # closed set at the edge turns that into a 422 with a readable message
        # instead of a 500 from deeper in.
        if body.verdict not in VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=f"{body.verdict!r} is not a verdict;"
                f" the set is {', '.join(VERDICTS)}",
            )
        verdict: Verdict = body.verdict  # type: ignore[assignment]
        try:
            recorded = cast(
                item.components,
                case=item.finding.case,
                verdict=verdict,
                voter=session.voter,
                reason=body.reason or None,
                claim_text=item.finding.title,
                config=session.configs.get(item.finding.case, ""),
                note=body.note,
                sitting=session.sitting,
            )
            append(recorded, session.ledger_path)
        except LedgerError as exc:
            # The message names the closed set the value missed, never a path.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse({"recorded": recorded.fingerprint})

    return app


def _reason_payload() -> list[dict[str, str]]:
    """The reason buttons, each carrying which score it moves.

    ``kind`` is sent to the page and shown to the reviewer, because a person who
    can see that "poorly written" does not count against the finding will use it
    instead of forcing their objection into "not a threat".
    """
    return [
        {
            "code": code,
            "gloss": REASON_GLOSS[code],
            "kind": "substance" if code in SUBSTANCE_REASONS else "style",
        }
        for code in sorted(SUBSTANCE_REASONS) + sorted(STYLE_REASONS)
    ]


def _escape(text: str) -> str:
    """Server-side escape for the one value the templates interpolate."""
    import html

    return html.escape(text)


def _page(template: str, **fields: str) -> tuple[str, str]:
    """Fill a template and stamp the nonce its policy authorises.

    The nonce is stamped before the fields, the ordering ``webapp/main.py``
    keeps and for the same reason: a field's value is content, and content that
    happens to spell the placeholder must come back as those characters rather
    than as a live nonce.
    """
    nonce = secrets.token_urlsafe(16)
    html = template.replace(_NONCE_PLACEHOLDER, nonce)
    for name, value in fields.items():
        html = html.replace(f"<!--{name}-->", value)
    return html, _CSP.format(nonce=nonce)


def _html(page: tuple[str, str]) -> HTMLResponse:
    body, csp = page
    return HTMLResponse(content=body, headers={"Content-Security-Policy": csp})


_STYLE = """
  :root { color-scheme: light dark; --line: #8884; }
  body { font: 15px/1.6 system-ui, sans-serif; max-width: 60rem;
         margin: 2rem auto; padding: 0 1.25rem; }
  h1 { font-size: 1.35rem; margin-bottom: .2rem; }
  .sub { opacity: .7; margin-top: 0; font-size: .9rem; }
  .bar { display: flex; gap: 1.5rem; font-size: .85rem; opacity: .8;
         border-bottom: 1px solid var(--line); padding-bottom: .6rem;
         margin-bottom: 1.25rem; }
  .why { font-size: .85rem; opacity: .75; font-style: italic; margin: 0 0 1rem; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
  @media (max-width: 46rem) { .cols { grid-template-columns: 1fr; } }
  .panel { border: 1px solid var(--line); border-radius: 6px; padding: 1rem; }
  .panel h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
              opacity: .6; margin: 0 0 .6rem; }
  #source { white-space: pre-wrap; font-size: .85rem; max-height: 26rem;
            overflow-y: auto; }
  #title { font-weight: 600; margin: 0 0 .5rem; }
  #description { margin: 0 0 .75rem; }
  .meta { font-family: ui-monospace, monospace; font-size: .78rem; opacity: .7; }
  .quote { border-left: 3px solid #7a3; padding: .3rem .6rem; margin: .4rem 0;
           font-size: .85rem; }
  .ask { font-weight: 600; margin: 1.5rem 0 .75rem; }
  button { font: inherit; padding: .55rem 1.1rem; margin: 0 .5rem .5rem 0;
           border-radius: 5px; border: 1px solid var(--line); background: none;
           color: inherit; cursor: pointer; }
  button:hover { border-color: currentColor; }
  button.primary { font-weight: 600; }
  #reasons { display: none; margin-top: 1rem; border-top: 1px solid var(--line);
             padding-top: 1rem; }
  #reasons.open { display: block; }
  .rgroup { margin-bottom: .75rem; }
  .rgroup h3 { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em;
               opacity: .6; margin: 0 0 .4rem; }
  .rgroup.style h3::after { content: " — does not count against the finding";
                            text-transform: none; letter-spacing: 0; opacity: .8; }
  #done { display: none; }
  #done.open { display: block; }
  a { color: inherit; }
"""

_QUEUE_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review queue</title><style nonce="__CSP_NONCE__">"""
    + _STYLE
    + """</style></head><body>
<h1>Review queue</h1>
<p class="sub">Signed in as <b><!--voter--></b>. Every answer is recorded under
that name and kept forever.</p>
<div class="panel">
  <h2>Waiting for you</h2>
  <p id="counts">Loading…</p>
  <p id="cases" class="meta"></p>
  <p><a href="/review"><button class="primary">Start reviewing</button></a></p>
</div>
<div class="panel" style="margin-top:1rem">
  <h2>The ledger so far</h2>
  <p id="ledger" class="meta">Loading…</p>
</div>
<script nonce="__CSP_NONCE__">
  fetch("/api/summary").then(r => r.json()).then(s => {
    document.getElementById("counts").textContent =
      s.waiting + " findings waiting, " + s.volatile +
      " of them found in some runs and not others.";
    const cases = Object.entries(s.by_case)
      .map(([id, n]) => id + ": " + n).join("   ");
    document.getElementById("cases").textContent = cases;
    document.getElementById("ledger").textContent =
      s.votes_recorded + " votes by " + (s.voters.join(", ") || "nobody") +
      "   |   " + s.pool + " findings in the reference pool" +
      "   |   " + s.double_voted + " answered by two people";
  });
</script>
</body></html>"""
)

_REVIEW_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review</title><style nonce="__CSP_NONCE__">"""
    + _STYLE
    + """</style></head><body>
<h1>Could this happen?</h1>
<div class="bar">
  <span>Reviewer: <b><!--voter--></b></span>
  <span id="left"></span>
  <span id="case" class="meta"></span>
</div>
<p id="why" class="why"></p>

<div id="card">
  <div class="cols">
    <div class="panel">
      <h2>What the system description says</h2>
      <div id="source"></div>
    </div>
    <div class="panel">
      <h2>The finding</h2>
      <p id="title"></p>
      <p id="description"></p>
      <div id="quotes"></div>
      <p id="elements" class="meta"></p>
    </div>
  </div>

  <p class="ask">Could this attack happen in this system?</p>
  <div>
    <button class="primary" id="up">Yes — this is real</button>
    <button class="primary" id="down">No, or not as written</button>
    <button id="unsure">Unsure</button>
    <button id="evidence">Needs more evidence</button>
  </div>

  <div id="reasons">
    <p class="sub">Which of these is it? Pick the closest.</p>
    <div id="rlist"></div>
    <button id="cancel">Back</button>
  </div>
</div>

<div id="done" class="panel">
  <h2>Nothing left</h2>
  <p>You have answered every finding in this queue. <a href="/">Back to the
  queue</a>.</p>
</div>

<script nonce="__CSP_NONCE__">
  // Every value below is submitter prose or model output, so it reaches the
  // page as a text node and never as markup. There is no escape helper here
  // deliberately: with no innerHTML path there is nothing to forget to call.
  let current = null;

  const el = id => document.getElementById(id);

  function fill(item) {
    current = item;
    if (item.done) {
      el("card").style.display = "none";
      el("done").classList.add("open");
      el("left").textContent = "";
      return;
    }
    el("left").textContent = item.remaining + " left";
    el("case").textContent = item.case + " / " + item.lane;
    el("why").textContent = "You are being asked because " + item.why + ".";
    el("source").textContent = item.source;
    el("title").textContent = item.title;
    el("description").textContent = item.description;
    el("elements").textContent = "Cited: " + item.element_ids.join(", ");

    const quotes = el("quotes");
    quotes.replaceChildren();
    item.quotes.forEach(text => {
      const div = document.createElement("div");
      div.className = "quote";
      div.textContent = '"' + text + '"';
      quotes.appendChild(div);
    });

    const list = el("rlist");
    list.replaceChildren();
    ["substance", "style"].forEach(kind => {
      const rows = item.reasons.filter(r => r.kind === kind);
      if (!rows.length) return;
      const group = document.createElement("div");
      group.className = "rgroup " + kind;
      const head = document.createElement("h3");
      head.textContent = kind === "substance" ? "It is wrong" : "It is badly written";
      group.appendChild(head);
      rows.forEach(reason => {
        const button = document.createElement("button");
        button.textContent = reason.gloss;
        button.addEventListener("click", () => send("down", reason.code));
        group.appendChild(button);
      });
      list.appendChild(group);
    });
    el("reasons").classList.remove("open");
  }

  function load() {
    fetch("/api/next").then(r => r.json()).then(fill);
  }

  function send(verdict, reason) {
    if (!current || current.done) return;
    fetch("/api/vote", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        fingerprint: current.fingerprint,
        verdict: verdict,
        reason: reason || null,
        note: ""
      })
    }).then(response => {
      if (!response.ok) { alert("That vote was refused."); return; }
      load();
    });
  }

  el("up").addEventListener("click", () => send("up", null));
  el("unsure").addEventListener("click", () => send("unsure", null));
  el("evidence").addEventListener("click", () => send("needs-evidence", null));
  el("down").addEventListener("click", () => el("reasons").classList.add("open"));
  el("cancel").addEventListener("click", () => el("reasons").classList.remove("open"));
  load();
</script>
</body></html>"""
)


def findings_from_artifacts(
    paths: Sequence[Path],
) -> tuple[list[list[review_queue.Finding]], dict[str, str]]:
    """Read several sweeps, keeping each one's findings apart.

    Several artifacts of **one configuration** is the intended input: that is
    what makes a finding's run count readable, and what lets the queue ask
    first about the findings those sweeps disagree on. The configurations are
    merged per case, and a case whose sweeps report different ones records all
    of them, so a vote's ``config`` never names one sweep as though it were the
    whole input.
    """
    runs = []
    configs: dict[str, set[str]] = {}
    for path in paths:
        findings, per_case = findings_from_artifact(path)
        runs.append(findings)
        for case, config in per_case.items():
            configs.setdefault(case, set()).add(config)
    return runs, {
        case: ", ".join(sorted(value for value in values if value))
        for case, values in configs.items()
    }


def findings_from_artifact(path: Path) -> tuple[list[review_queue.Finding], dict]:
    """Read one sweep's saved reports into queue findings.

    Reads the ``.reports/`` directory a ``run --out`` writes beside its
    artifact, because that is where the claims themselves live: the artifact
    holds the aggregates this harness computed, and the reports hold what the
    agents actually said.
    """
    reports_dir = Path(str(path) + ".reports")
    if not reports_dir.is_dir():
        raise FileNotFoundError(
            f"{reports_dir} does not exist; a queue is built from the reports a"
            " sweep writes beside its artifact, not from the artifact alone"
        )

    findings: list[review_queue.Finding] = []
    configs: dict[str, str] = {}
    for report_path in sorted(reports_dir.glob("*.report.json")):
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        case = report_path.name.removesuffix(".report.json")
        configs[case] = str(raw.get("engine_version", ""))
        for block in raw.get("analyses", []):
            for claim in block.get("claims", []):
                findings.append(
                    review_queue.Finding(
                        case=case,
                        framework=block["framework"],
                        lane=str(claim[lane_field(block["framework"])]),
                        title=claim.get("title", ""),
                        description=claim.get("description", ""),
                        element_ids=tuple(claim.get("affected_element_ids", ())),
                        # ``None`` for a package that composes none, which is
                        # what its fingerprint version expects.
                        verb=claim.get("verb"),
                        identifier=identifier_of(
                            block["framework"], claim.get("id", "")
                        ),
                        quotes=tuple(
                            ground["text"]
                            for ground in claim.get("grounds", [])
                            if ground.get("text")
                        ),
                    )
                )
    return findings, configs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--voter",
        required=True,
        help="who is reviewing; recorded on every vote and never asked for in"
        " the browser, so one reviewer cannot file votes as another",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        action="append",
        default=[],
        help="a sweep artifact whose .reports/ directory holds the findings."
        " Give it once per sweep of one configuration: the queue asks first"
        " about the findings those sweeps disagree on",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    args = parser.parse_args(argv)

    if not args.artifact:
        parser.error(
            "--artifact is required: there is nothing to review until a sweep"
            " has produced findings. Run `python -m evals.harness.run run"
            " --mode analysis --out artifact.json` first."
        )
    runs, configs = findings_from_artifacts(args.artifact)
    session = build_session(runs, args.voter, args.ledger, configs)
    print(f"{len(session.items)} findings waiting for {args.voter}")
    print(f"ledger: {args.ledger}")
    print(f"open http://{HOST}:{PORT}/")

    import uvicorn

    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
