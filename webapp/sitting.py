"""The Case Sitting app: read a case, judge its reference sets, record it.

Run it from a clone, with no credentials of any kind::

    uv run python webapp/sitting.py --case 02-iot-fleet-telemetry

It is **eval-side tooling and not the product**, and it is the browser half of
a path that also works entirely from the shell: everything it writes is what
``evals/BLESSING.md`` step 6 asks a person to write by hand, and
``submit sitting`` checks the result the same way either way. It prepares your
working tree, and then offers three ways out — run the command yourself, paste
the text into a pull request you open, or press the button.

**The button opens a pull request, through your own authenticated ``gh``.**
That reverses map #319's "the web app never gains a network write", on the
maintainer's instruction and recorded in ``docs/agents/issue-tracker.md``.
It is not a hosted service: nothing is hosted, no credential is held here, and
the app still binds to loopback. What it does mean is that a request reaching
:func:`create_app`'s submit endpoint can act on GitHub as you, so that one
endpoint carries four controls rather than the app's usual none:

* the **Host** check every loopback app here runs, which is what stops DNS
  rebinding making an attacker's page same-origin with this one;
* **``Sec-Fetch-Site: same-origin``**, the check ``webapp/main.py`` already
  uses on its own write endpoint;
* a **one-time token** minted per process and embedded in the page, so a
  request that never read the page cannot carry it;
* **no request-controlled arguments at all** — the endpoint opens the one
  submission this session prepared, and takes neither a kind nor a path.

**The own list comes first, and the server enforces it.** ``/api/part-two``
refuses until the reader has posted their own threat list, so the recorded
sets are not in the page for a curious reader to find. That is the method's
only rule: a reader who opens the recorded sets first finds them reasonable,
and the sitting measures nothing. It is enforced the way the review app
enforces configuration-blindness — by the payload not carrying it — rather
than by asking.

Security posture, inherited from ``webapp/review.py`` rather than re-derived:

* **Loopback only, and a checked ``Host``** (A01). Binding alone does not stop
  a rebound page in the operator's own browser, and this app writes to the
  corpus.
* **The reviewer comes from the command line, never from the request** (A01).
  A browser field naming the reviewer would let one person file a sitting as
  another, and #320's binding rests on the name being true.
* **Case prose reaches the page as data** (LLM05, A05). The document is
  injected as one JSON blob the client renders through ``textContent``.
* **Every write lands inside the one case directory** the command named
  (A01), plus the debt list. There is no path in the request at all.
* **The submit endpoint is off unless ``gh`` is authenticated** — with no
  login there is nothing to act as, so the button is never offered.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from webapp.main import LOOPBACK_HOSTS, SecurityHeaders

HOST = "127.0.0.1"
PORT = 8020

_NONCE_PLACEHOLDER = "__CSP_NONCE__"
_CSP = (
    "default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}';"
    " connect-src 'self'; base-uri 'none'; form-action 'none'"
)


class OwnList(BaseModel):
    """What the reader saw for themselves, before the sets were shown."""

    model_config = ConfigDict(extra="forbid")

    items: list[str] = Field(default_factory=list, max_length=200)


class Finish(BaseModel):
    """The sitting's result. Marks are prose, so no code reads their values."""

    model_config = ConfigDict(extra="forbid")

    marks: dict[str, str] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=4000)


@dataclass
class Session:
    """One sitting: who is reading, which case, and how far they have got."""

    case_dir: Path
    reviewer: str
    prepared: sittings.Prepared
    root: Path
    own_list: list[str] | None = field(default=None)
    #: Minted per process and embedded in the page. The submit endpoint
    #: requires it, so a request that never read the page cannot carry it —
    #: and reading the page cross-origin is what the Host and Sec-Fetch-Site
    #: checks already refuse.
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    #: Whether a `gh` login is available to act as. With none there is nothing
    #: to submit with, so the button is never offered.
    can_submit: bool = False
    recorded: bool = False

    @property
    def document_name(self) -> str:
        return f"REVIEW-{self.reviewer}.md"


def create_app(session: Session) -> FastAPI:
    """The sitting app over one prepared case."""
    app = FastAPI(title="STRIDE case sitting", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)
    app.add_middleware(SecurityHeaders)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return _html(
            _page(
                _PAGE,
                case=_escape(session.prepared.case_id),
                title=_escape(session.prepared.title),
                reviewer=_escape(session.reviewer),
                token=_escape(session.token),
                cansubmit="yes" if session.can_submit else "no",
            )
        )

    @app.get("/api/part-one")
    def part_one() -> JSONResponse:
        return JSONResponse(
            {
                "case": session.prepared.case_id,
                "title": session.prepared.title,
                "reviewer": session.reviewer,
                "body": session.prepared.part_one,
                "files": session.prepared.files,
            }
        )

    @app.post("/api/own-list")
    def own_list(body: OwnList) -> JSONResponse:
        # Recorded before part two is reachable. An empty list is allowed —
        # "I saw nothing" is an answer — but it has to be given.
        session.own_list = [item.strip() for item in body.items if item.strip()]
        return JSONResponse({"accepted": len(session.own_list)})

    @app.get("/api/part-two")
    def part_two() -> JSONResponse:
        if session.own_list is None:
            # The method's one rule, enforced rather than requested.
            raise HTTPException(
                status_code=409,
                detail="write your own list first; the recorded sets are not"
                " served until it is in, because a reader who sees them first"
                " finds them reasonable and the sitting measures nothing",
            )
        return JSONResponse({"frameworks": session.prepared.part_two})

    @app.post("/api/finish")
    def finish(body: Finish) -> JSONResponse:
        if session.own_list is None:
            raise HTTPException(status_code=409, detail="no own list was written")
        try:
            text = sittings.document(
                session.prepared, session.own_list, body.marks, body.missing, body.notes
            )
            (session.case_dir / session.document_name).write_text(
                text, encoding="utf-8"
            )
            read = sittings.read_records(session.case_dir, session.prepared.files)
            sittings.record(
                session.case_dir,
                session.reviewer,
                read,
                session.document_name,
                body.notes,
            )
            cleared = sittings.clear_debt(session.root, session.prepared.case_id)
        except sittings.SittingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.recorded = True
        return JSONResponse(
            {
                "written": [
                    f"evals/corpus/{session.prepared.case_id}/{session.document_name}",
                    f"evals/corpus/{session.prepared.case_id}/case.json",
                    *([sittings.DEBT_FILE] if cleared else []),
                ],
                "command": "python -m evals.harness.run submit sitting",
                "paste": _paste(session, len(read)),
                "can_submit": session.can_submit,
            }
        )

    @app.post("/api/submit")
    def open_the_pr(request: Request) -> JSONResponse:
        """Open the pull request, through the operator's own `gh`.

        The four controls this endpoint carries are in the module docstring,
        and they are all here rather than spread about because this is the
        only place in any app in this repository that can act on GitHub as
        the person running it.

        It takes no arguments. The submission is whatever this session already
        recorded into the working tree, so there is nothing in the request for
        an attacker to steer — and nothing to steer it with, since a request
        that did not read the page has no token.
        """
        if not session.can_submit:
            raise HTTPException(
                status_code=409,
                detail="no authenticated gh login, so there is nothing to"
                " submit as. Run the printed command yourself.",
            )
        if request.headers.get("sec-fetch-site") != "same-origin":
            raise HTTPException(
                status_code=403, detail="this request did not come from the app's page"
            )
        if not secrets.compare_digest(
            request.headers.get("x-sitting-token", ""), session.token
        ):
            raise HTTPException(status_code=403, detail="wrong or missing page token")
        if not session.recorded:
            raise HTTPException(
                status_code=409, detail="record the sitting before submitting it"
            )
        outcome = submit_spine.submission(session.root, "sitting")
        return JSONResponse(outcome.to_json(), status_code=200 if outcome.ok else 409)

    return app


def _paste(session: Session, files_read: int) -> str:
    """The copy-paste alternative, for somebody not opening the PR from here."""
    return (
        f"Sitting: {session.prepared.case_id} by {session.reviewer}\n\n"
        f"Held over {files_read} file(s) — the sources, the model and every"
        " declared framework's reference set. My own threat list was written"
        " before the recorded sets were opened; the filled document is"
        f" committed as `{session.document_name}`.\n\n"
        "The `reviews` entry in `case.json` records the digest of each file as"
        " it stands in this PR, so a later edit to any of them re-opens the"
        " debt."
    )


def _escape(text: str) -> str:
    import html

    return html.escape(text)


def _page(template: str, **fields: str) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(16)
    page = template.replace(_NONCE_PLACEHOLDER, nonce)
    for name, value in fields.items():
        page = page.replace(f"<!--{name}-->", value)
    return page, _CSP.format(nonce=nonce)


def _html(page: tuple[str, str]) -> HTMLResponse:
    body, csp = page
    return HTMLResponse(content=body, headers={"Content-Security-Policy": csp})


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Case sitting — <!--case--></title>
<style nonce="__CSP_NONCE__">
  :root { color-scheme: light dark; --line: #8884; }
  body { font: 16px/1.55 system-ui, sans-serif; margin: 0 auto; max-width: 46rem;
         padding: 2rem 1rem 6rem; }
  h1 { font-size: 1.3rem; margin-bottom: .2rem; }
  .sub { color: #7a7a7a; margin-top: 0; }
  pre { background: #8881; padding: 1rem; overflow-x: auto; white-space: pre-wrap;
        border-radius: 6px; font-size: .88rem; }
  textarea { width: 100%; min-height: 9rem; font: inherit; padding: .6rem;
             border: 1px solid var(--line); border-radius: 6px; }
  button { font: inherit; padding: .5rem 1rem; border-radius: 6px;
           border: 1px solid var(--line); background: #8882; cursor: pointer; }
  section { border-top: 1px solid var(--line); margin-top: 2rem; padding-top: 1rem; }
  .hidden { display: none; }
  .note { background: #8881; padding: .8rem 1rem; border-radius: 6px; }
</style></head>
<body>
<h1>Case sitting — <!--title--></h1>
<p class="sub"><code><!--case--></code>, read by <!--reviewer--></p>

<section id="one">
  <h2>Part 1 — the system</h2>
  <pre id="partOne">loading…</pre>
  <h2>Your list, written first</h2>
  <p class="note">Write what could go wrong: an attack, a missing control, a
  question the text does not answer. One per line. The recorded sets are not
  in this page until you submit this — that is the whole method.</p>
  <textarea id="own" placeholder="one per line"></textarea>
  <p><button id="lock">Save my list and show the recorded sets</button></p>
</section>

<section id="two" class="hidden">
  <h2>Part 2 — what is recorded</h2>
  <pre id="partTwo"></pre>
  <h2>On your list and not on theirs</h2>
  <p class="note">The finding this sitting exists for. One per line.</p>
  <textarea id="missing" placeholder="one per line"></textarea>
  <h2>Notes</h2>
  <textarea id="notes" placeholder="counts, and anything you changed"></textarea>
  <p><button id="finish">Record the sitting</button></p>
</section>

<section id="done" class="hidden">
  <h2>Recorded</h2>
  <p>Written into your working tree:</p>
  <pre id="written"></pre>
  <p>Open the pull request:</p>
  <pre id="command"></pre>
  <p>Or paste this into one you open yourself:</p>
  <pre id="paste"></pre>
  <div id="submitBox" class="hidden">
    <p class="note">Or let this open it for you, through the
    <code>gh</code> you are already signed in to. It runs the same checks
    first, pushes to your fork, and opens the pull request as you.</p>
    <p><button id="submit">Open the pull request as <!--reviewer--></button></p>
    <pre id="result" class="hidden"></pre>
  </div>
</section>

<script nonce="__CSP_NONCE__">
const $ = (id) => document.getElementById(id);
const lines = (id) => $(id).value.split("\\n").map(s => s.trim()).filter(Boolean);

fetch("/api/part-one").then(r => r.json()).then(d => {
  $("partOne").textContent = d.body;
});

$("lock").addEventListener("click", async () => {
  await fetch("/api/own-list", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({items: lines("own")}),
  });
  $("own").readOnly = true;
  $("lock").disabled = true;
  const sets = await (await fetch("/api/part-two")).json();
  $("partTwo").textContent = Object.entries(sets.frameworks)
    .map(([name, body]) => body).join("\\n\\n");
  $("two").classList.remove("hidden");
  $("two").scrollIntoView({behavior: "smooth"});
});

const TOKEN = "<!--token-->";
const CAN_SUBMIT = "<!--cansubmit-->" === "yes";

$("finish").addEventListener("click", async () => {
  const res = await fetch("/api/finish", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({marks: {}, missing: lines("missing"), notes: $("notes").value}),
  });
  const d = await res.json();
  if (!res.ok) { $("written").textContent = d.detail; $("done").classList.remove("hidden"); return; }
  $("written").textContent = d.written.join("\\n");
  $("command").textContent = d.command;
  $("paste").textContent = d.paste;
  $("two").classList.add("hidden");
  $("done").classList.remove("hidden");
  if (CAN_SUBMIT && d.can_submit) $("submitBox").classList.remove("hidden");
});

$("submit").addEventListener("click", async () => {
  $("submit").disabled = true;
  $("result").classList.remove("hidden");
  $("result").textContent = "running the checks…";
  const res = await fetch("/api/submit", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
  });
  const d = await res.json();
  if (d.detail) { $("result").textContent = d.detail; $("submit").disabled = false; return; }
  const lines = (d.checks || []).map(c => (c.passed ? "ok   " : "FAIL ") + c.name
    + (c.problems.length ? "\n       " + c.problems.join("\n       ") : ""));
  if (d.ok) { lines.push("", d.url, "", d.closing); }
  else { lines.push("", d.error || "nothing opened; fix the failures above."); $("submit").disabled = false; }
  $("result").textContent = lines.join("\n");
});
</script>
</body></html>
"""


def build_session(
    case_id: str, reviewer: str, root: Path, can_submit: bool = False
) -> Session:
    case_dir = root / "evals" / "corpus" / case_id
    if not (case_dir / "case.json").is_file():
        raise SystemExit(f"no case {case_id!r} under evals/corpus/")
    return Session(
        case_dir=case_dir,
        reviewer=reviewer,
        prepared=sittings.prepare(case_dir),
        root=root,
        can_submit=can_submit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="the case id to sit with")
    parser.add_argument(
        "--reviewer",
        help="your GitHub login. Read from the authenticated `gh` when omitted,"
        " because it is the name the record carries either way.",
    )
    parser.add_argument("--list", action="store_true", help="print the cases in debt")
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="hide the button that opens the pull request, leaving the printed"
        " command and the paste text as the only ways out",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if args.list:
        print("\n".join(sittings.cases_in_debt(root)))
        return 0

    # One read of the login, answering two questions: what name the record
    # carries, and whether there is an account to open a pull request as. A
    # tree with no `gh` still works — it just ends at the command rather than
    # the button.
    try:
        login = submit_spine.gh_login(root)
    except submit_spine.SubmitError:
        login = ""

    reviewer = args.reviewer or login
    if not reviewer:
        print(
            "cannot read your gh login, so pass --reviewer with the login the"
            " record should carry",
            file=sys.stderr,
        )
        return 1

    # Only ever as yourself. A submission opened under a name `gh` does not
    # hold would fail #320's binding in CI, so offering the button would be
    # inviting a red PR.
    can_submit = bool(login) and login == reviewer and not args.no_submit
    session = build_session(args.case, reviewer, root, can_submit)
    import uvicorn

    print(f"sitting with {args.case} as {reviewer}")
    if can_submit:
        print("the page can open the pull request as you; --no-submit hides it")
    print(f"open http://{HOST}:{PORT}/")
    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
