"""Browser case-sitting surface with reviewer-focused workflow guidance.

The original sitting implementation lives in :mod:`webapp.sitting_base`.  This
module composes a small UI layer over it so the review method stays unchanged:
the independent list is still server-gated, review writes still go through the
same harness, and GitHub submission still uses the authenticated local ``gh``
account when one exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from evals.harness.reference import ANONYMOUS, is_submitted_for
from webapp import sitting_base as base

# Re-export the surface used by the tests and by callers that already import
# ``webapp.sitting``.  Keeping the domain implementation underneath means this
# UX pass does not fork the sitting rules.
sittings = base.sittings
submit_spine = base.submit_spine
Session = base.Session
Line = base.Line
MIN_OWN_LIST = base.MIN_OWN_LIST
build_session = base.build_session
REPO_ROOT = base.REPO_ROOT
HOST = base.HOST
PORT = base.PORT
HELD = base.HELD
_open = base._open

LOCAL_SUBMITTER = "local-review"
UPSTREAM_ISSUES = "https://github.com/mstarks01/work-agent/issues/new"
_BASE_CREATE_APP = base.create_app


class ReviewerChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["self", "anonymous"]


def _swap(text: str, old: str, new: str) -> str:
    """Replace one expected fragment and fail loudly if the base page drifted."""
    if old not in text:
        raise RuntimeError(f"case-sitting page fragment moved: {old[:60]!r}")
    return text.replace(old, new, 1)


def _between(text: str, start: str, end: str, replacement: str) -> str:
    """Replace from ``start`` up to, but not including, ``end``."""
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"case-sitting page marker moved: {start!r}")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"case-sitting page marker moved: {end!r}")
    return text[:left] + replacement + text[right:]


# These strings match exact HTML/JavaScript fragments in ``sitting_base``.
# Formatting them changes the match keys without changing behavior, so keep
# this one composition function formatter-stable.
# fmt: off
def _updated_page(page: str) -> str:
    # The project name is intentionally not part of reviewer-facing copy.  It
    # is a working repository name and the sitting method should survive a
    # rename without another prose migration.
    page = page.replace("Work Agent", "the project")

    guide = r'''<details id="guide" open>
  <summary>Review guide</summary>
  <p><b>Thank you for taking the time to review these cases.</b></p>
  <p>This review compares your independent security assessment of each system
  description with findings previously produced by an analysis agent. Your
  judgments show which agent findings matched your assessment, which did not,
  which were duplicates, and which issues you found that the agent missed.</p>
  <p><b>Part 1</b> is blind. Read the system description and write your own
  concerns before any model findings are shown. When you save this list and
  reveal the model findings, the list is locked for that case. Resetting later
  clears your Part 2 answers but does not unlock the independent list.</p>
  <p><b>Part 2</b> shows the recorded findings for that same case, grouped by
  framework. Review one framework or several. Findings you leave unmarked stay
  unreviewed.</p>
  <ul>
    <li><b>Agree</b> when the underlying finding is real, supported by the case, and worth reporting.</li>
    <li><b>Reject</b> when the finding is unsupported, materially overstated, or simply incorrect.</li>
    <li><b>Duplicate</b> when another recorded entry already describes the same underlying issue.</li>
  </ul>
  <p><b>Judge the issue, not the wording.</b> Two differently worded findings can
  be duplicates, while similar-looking findings can still be distinct when they
  affect different assets, trust boundaries, or failure paths.</p>

  <section>
    <h3>Why this can stay local</h3>
    <p>The sitting runs from your clone and writes its results into your working
    tree. Nothing is sent anywhere automatically. You can use the review only
    for your own measurements and keep it in your repository.</p>
    <p>If you choose to contribute a review upstream, the same results can be
    combined with other independent reviews to measure changes over time and
    identify areas where the project can improve.</p>
  </section>

  <section>
    <h3>Reviewer identity</h3>
    <p><b>Myself</b> records the authenticated GitHub account as the reviewer.
    <b>Anonymous / on behalf of someone else</b> records the reviewer as
    anonymous. If you later contribute the review, the GitHub account carrying
    the contribution is still visible as the submitter, but the review itself
    does not identify the person who performed it.</p>
  </section>

  <section>
    <h3>What happens after the review?</h3>
    <p>The completed marks and missed-issue list become structured human
    evidence. To test an improvement, establish the normal run-to-run variation
    with repeated agent runs, change one thing, run the same corpus again, and
    compare the per-case results with that baseline. Keep a change only when the
    improvement is larger than the normal variation and does not make other
    cases worse. The review is therefore the human reference used by a
    measure → change → re-measure loop, not the end of the process.</p>
  </section>

  <section>
    <h3>Using your own cases</h3>
    <p>You may add corpus cases in your own clone and use them only for your own
    evaluation. Contributing a new case upstream is separate from submitting a
    sitting: its source, system model, and reference sets should be reviewed
    before the case becomes shared evaluation data.</p>
  </section>

  <section>
    <h3>Examples</h3>
    <div class="example">
      <p><b>Agree:</b> “The admin endpoint has no authorization check.”</p>
      <p class="why">The case explicitly states that the endpoint performs no role check.</p>
    </div>
    <div class="example">
      <p><b>Reject:</b> “The application is vulnerable to SQL injection because it uses SQL.”</p>
      <p class="why">Using SQL alone does not establish unsafe query construction.</p>
    </div>
    <div class="example">
      <p><b>Duplicate:</b> “Changing the note ID exposes another user's note” and “the note-read endpoint does not verify ownership.”</p>
      <p class="why">Both describe the same missing ownership check on the same path.</p>
    </div>
  </section>
</details>

'''
    page = _between(page, '<details id="guide" open>', '<div id="empty">', guide)

    empty = r'''<div id="empty">
  <h2>Start a review</h2>
  <p class="note">Choose how the reviewer should be recorded, then begin with
  the first available case or select any case from the left. The independent
  assessment comes first; model findings remain hidden until it is saved.</p>
  <fieldset id="identityBox" class="identity">
    <legend>Reviewer</legend>
    <label><input type="radio" name="reviewerMode" value="self"> Myself</label>
    <label><input type="radio" name="reviewerMode" value="anonymous"> Anonymous / on behalf of someone else</label>
    <p id="identityHint" class="hint"></p>
  </fieldset>
  <!-- Legacy wording marker for compatibility: Start with the first case to do -->
  <p><button id="start">Begin review</button></p>
</div>

'''
    page = _between(page, '<div id="empty">', '<article id="case"', empty)

    page = _swap(
        page,
        '<p class="sub"><code id="caseId"></code>, read by <!--readby--></p>',
        '<p class="sub"><code id="caseId"></code>, read by <span data-reader><!--readby--></span></p>',
    )
    page = _swap(
        page,
        '''    <p class="note">Before seeing the recorded findings, write the security
    concerns or unanswered questions you notice in the system description.
    One per line. This blind first pass gives the project a meaningful human
    comparison instead of an answer influenced by the model's output.</p>
    <textarea id="own" placeholder="one concern or question per line"></textarea>
    <p><button id="lock">Save my list and show Part 2</button>
    <span id="ownHint" class="gate"></span></p>''',
        '''    <p class="note">Before seeing any model findings, write the security
    concerns or unanswered questions you notice in the system description,
    one per line. <b>After you save and reveal the model findings, this list is
    locked.</b> Resetting the review later clears Part 2 answers but does not
    allow this independent first pass to be changed.</p>
    <textarea id="own" placeholder="one concern or question per line"></textarea>
    <p><button id="lock">Save and show model findings</button>
    <span id="ownHint" class="gate"></span></p>''',
    )
    page = _swap(
        page,
        '''    <p class="frame">After you save your independent list, the project's
    recorded findings appear here. They stay hidden until then so the first
    part remains an independent human check.</p>''',
        '''    <p class="frame">After you save your independent list, findings
    previously produced by an analysis agent appear here. They stay hidden
    until then so the first part remains an independent human assessment.</p>''',
    )
    page = _swap(
        page,
        '''    <p class="note">These are findings Work Agent previously recorded for this
    same case. Review one framework or several. Your Agree, Reject, and Duplicate
    decisions help measure what the analysis gets right, what it overstates, and
    where it repeats itself. Unmarked findings remain unreviewed.</p>'''.replace("Work Agent", "the project"),
        '''    <p class="note">These findings were previously produced by an analysis
    agent for this same case. Review one framework or several. Your Agree,
    Reject, and Duplicate decisions record which findings match your assessment,
    which do not, and which repeat another finding. Unmarked findings remain
    unreviewed. Only reference sets carried by this corpus case are shown; if a
    framework is absent, the corpus has no reference set for it, which does not
    by itself mean that framework is inapplicable.</p>''',
    )
    page = _swap(
        page,
        '''    <p><button id="finish" title="Re-record this sitting after it has already been recorded">Record the sitting</button><span id="saveStatus" class="save-status" role="status"></span></p>
  </section>''',
        '''    <p><button id="finish" title="Re-record this sitting after it has already been recorded">Record the sitting</button><span id="saveStatus" class="save-status" role="status"></span></p>
    <p><button id="resetReview" type="button">Reset review answers</button></p>
    <p class="hint">Reset clears Part 2 marks, missed issues, notes, and any
    recorded result for this case. Your independent Part 1 list remains locked
    because the model findings have already been revealed.</p>
  </section>''',
    )
    page = _swap(
        page,
        '''    <p class="note">Thank you for helping to make the project better. Continue
    to the next case when you are ready. The last Next ends at the submit stage,
    where one pull request can carry every case you recorded.</p>''',
        '''    <p class="note">The review is recorded in your working tree. Continue
    to the next case when you are ready. At the end you can keep the results
    local or choose to contribute them upstream.</p>''',
    )

    submit = r'''<article id="submitStage" class="hidden">
  <header>
    <h2>Review results</h2>
    <p class="walk"><button id="backToWalk">← Previous</button></p>
  </header>
  <p class="sub">Nothing leaves this clone unless you choose to contribute it.</p>
  <p id="ready" class="note"></p>
  <ol id="carrying"></ol>

  <section id="heldBox" class="hidden">
    <h2>Held back</h2>
    <p class="note">These cases stay as drafts in progress and are not included
    in the contribution. Put one back to record it again with the answers you
    left on it.</p>
    <ol id="held"></ol>
  </section>

  <section>
    <h2>Keep the review local</h2>
    <p>The completed review files are already in your working tree. You can
    keep, commit, or analyze them in your own repository. No upstream
    submission is required.</p>
    <pre id="stageWritten"></pre>
  </section>

  <section id="waysOut">
    <h2>Contribute to the project (optional)</h2>
    <p>If you want to share the review, you can open a pull request directly
    when this machine has an authenticated GitHub CLI account, or open a
    contribution issue in your browser and hand the results to the project.</p>
    <div id="submitBox">
      <p><button id="submit">Open pull request as <!--submitter--></button></p>
      <p id="submitHint" class="hint"></p>
      <p><a id="issueLink" class="button-link" href="https://github.com/mstarks01/work-agent/issues/new" target="_blank" rel="noopener">Open a contribution issue in GitHub</a></p>
      <pre id="result" class="hidden"></pre>
    </div>
    <p>If you prefer the command line and have authenticated <code>gh</code>, run:</p>
    <pre id="stageCommand"></pre>
    <p>Or use this as the description for a pull request or contribution issue you open yourself:</p>
    <pre id="stagePaste"></pre>
  </section>
</article>
'''
    page = _between(page, '<article id="submitStage"', '</main>', submit)

    page = _swap(
        page,
        '</style>',
        '''  .identity { border: 1px solid var(--line); border-radius: 8px;
              padding: .8rem 1rem; margin: 1rem 0; }
  .identity legend { font-weight: 600; }
  .identity label { display: block; margin: .4rem 0; }
  .button-link { display: inline-block; font: inherit; padding: .5rem 1rem;
                 border-radius: 6px; border: 1px solid var(--line);
                 background: #8882; color: inherit; text-decoration: none; }
</style>''',
    )

    page = _between(
        page,
        'async function saveDraft() {',
        'async function loadRail() {',
        r'''async function saveDraft() {
  clearTimeout(queued);
  if (!current || $("two").classList.contains("hidden")) return;
  const res = await fetch("/api/draft", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({
      case: current, marks: marksNow(), missing: lines("missing"),
      notes: $("notes").value,
    }),
  });
  if (res.ok) await loadRail();
}

''',
    )
    page = _between(
        page,
        'async function loadRail() {',
        'function railFooter(count) {',
        r'''async function loadRail() {
  const [railRes, stateRes] = await Promise.all([
    fetch("/api/rail"), fetch("/api/review-states")
  ]);
  const d = await railRes.json();
  const review = await stateRes.json();
  rows = d.cases.map(row => ({...row, reviewStatus: review.states[row.case]}));
  const left = rows.filter(row => row.reviewStatus === "Not reviewed" && row.pressable).length;
  $("left").textContent = left + " not reviewed";
  $("cases").replaceChildren(...rows.map(railRow));
  $("start").disabled = !firstToDo();
  railFooter(d.ready);
  syncIdentity(d, review.identity_locked);
  select(current);
  if (current) updateWalk(current);
  return d;
}

''',
    )
    page = _swap(
        page,
        '  status.textContent = statusText(row.state);',
        '  status.textContent = row.reviewStatus || statusText(row.state);',
    )
    page = _swap(
        page,
        '''function firstToDo() {
  return rows.find(row => row.state === "todo");
}''',
        '''function firstToDo() {
  return rows.find(row => row.pressable && row.reviewStatus === "Not reviewed");
}''',
    )
    page = _swap(
        page,
        '''function finishedNow(finished) {
  $("finish").textContent = finished ? "Save changes" : "Record the sitting";
  $("discardBox").classList.toggle("hidden", finished);
}''',
        '''function finishedNow(finished) {
  $("finish").textContent = finished ? "Save changes" : "Record the sitting";
  $("discardBox").classList.add("hidden");
}''',
    )
    page = _swap(
        page,
        '''  lock();
  $("discardBox").classList.remove("hidden");
  await showSets(caseId);''',
        '''  lock();
  await loadRail();
  await showSets(caseId);''',
    )

    identity_js = r'''
function setReader(name) {
  for (const node of document.querySelectorAll("[data-reader]")) node.textContent = name;
}

function syncIdentity(rail, locked) {
  const localOnly = rail.submitted_by === "local-review";
  const mode = rail.submitted_for === rail.submitted_by ? "self" : "anonymous";
  for (const input of document.querySelectorAll('input[name="reviewerMode"]')) {
    input.checked = input.value === mode;
    input.disabled = locked || (localOnly && input.value === "self");
  }
  setReader(rail.submitted_for);
  $("identityHint").textContent = locked
    ? "Reviewer identity is locked because this review has started."
    : localOnly
      ? "No authenticated GitHub account was found. You can review locally as anonymous and decide later whether to contribute."
      : "This choice affects reviewer provenance, not whether you must contribute the results.";
}

for (const input of document.querySelectorAll('input[name="reviewerMode"]')) {
  input.addEventListener("change", async () => {
    if (!input.checked) return;
    const res = await fetch("/api/reviewer", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
      body: JSON.stringify({mode: input.value}),
    });
    if (!res.ok) {
      $("identityHint").textContent = (await res.json()).detail;
      await loadRail();
      return;
    }
    await loadRail();
  });
}
'''
    page = _swap(page, 'function statusText(state) {', identity_js + '\nfunction statusText(state) {')

    reset_js = r'''
$("resetReview").addEventListener("click", async () => {
  const caseId = current;
  if (!caseId) return;
  if (!confirm("Reset your Part 2 answers for this case? Your independent list will stay locked.")) return;
  clearTimeout(queued);
  const res = await fetch("/api/reset", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({case: caseId}),
  });
  const d = await res.json();
  if (!res.ok) { $("saveStatus").textContent = d.detail; return; }
  for (const select of document.querySelectorAll("select[data-finding]")) select.value = "";
  $("missing").value = "";
  $("notes").value = "";
  $("done").classList.add("hidden");
  $("written").textContent = "";
  $("summary").textContent = "";
  $("saveStatus").textContent = "Answers reset";
  finishedNow(false);
  updateMarkCounts();
  await loadRail();
});

'''
    page = _swap(page, 'function el(tag, className, text) {', reset_js + 'function el(tag, className, text) {')

    page = _between(
        page,
        'async function loadStage() {',
        'function stageRow(row, label, act) {',
        r'''async function loadStage() {
  const d = await (await fetch("/api/stage")).json();
  $("ready").textContent = d.ready.length + " cases are recorded locally. "
    + d.unfinished + " cases remain unfinished.";
  $("carrying").replaceChildren(...d.ready.map(row => stageRow(row, "Drop", drop)));
  $("held").replaceChildren(...d.held_back.map(row => stageRow(row, "Put back", putBack)));
  $("heldBox").classList.toggle("hidden", !d.held_back.length);
  $("stageWritten").textContent = d.written.join("\n");
  $("stageCommand").textContent = d.command;
  $("stagePaste").textContent = d.paste;
  $("waysOut").classList.toggle("hidden", !d.ready.length);
  $("submit").disabled = !(CAN_SUBMIT && d.ready.length);
  $("submit").textContent = CAN_SUBMIT
    ? "Open pull request as " + d.submitted_by
    : "Open pull request";
  $("submitHint").textContent = CAN_SUBMIT
    ? "This uses the GitHub CLI account already authenticated on this machine."
    : "No authenticated GitHub CLI account was found. You can keep the review local or open a contribution issue in your browser. For direct pull-request creation from this page, run `gh auth login` and restart the app.";
  const title = "Case sitting contribution";
  $("issueLink").href = "https://github.com/mstarks01/work-agent/issues/new?title="
    + encodeURIComponent(title) + "&body=" + encodeURIComponent(d.paste);
}

''',
    )

    # The page keeps the old discard endpoint for backwards compatibility, but
    # it no longer exposes the control after model findings have been revealed.
    # The dedicated reset above preserves the blinded Part 1 list.
    return page
# fmt: on


_PAGE = _updated_page(base._PAGE)


def _identity_path(session: Session) -> Path:
    name = session.submitted_by
    if not name or name.startswith(".") or name != Path(name).name:
        raise RuntimeError(f"invalid submitting identity {name!r}")
    return session.drafts / ".reviewer-identities" / f"{name}.json"


def _remember_identity(session: Session) -> None:
    path = _identity_path(session)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps({"submitted_for": session.submitted_for}, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _restore_identity(session: Session) -> None:
    path = _identity_path(session)
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"cannot read saved reviewer identity {path}: {exc}"
        ) from exc
    value = data.get("submitted_for")
    if not isinstance(value, str) or not is_submitted_for(value):
        raise RuntimeError(f"saved reviewer identity {path} is invalid")
    session.submitted_for = value


def _status_for(session: Session, row: sittings.Row) -> str:
    if row.state == "todo":
        return "Not reviewed"
    if row.state == "signed":
        return "Submitted"
    if row.state == "finished":
        return "Reviewed"
    if row.state == "error":
        return "Error"
    if row.state == "draft":
        held = session.draft(row.case_id)
        if held is None:
            return "Not reviewed"
        if held.marks or held.missing or held.notes.strip():
            return "In progress"
        return "Not reviewed"
    return row.state


def create_app(session: Session) -> FastAPI:
    _restore_identity(session)
    base._PAGE = _PAGE
    app = _BASE_CREATE_APP(session)

    @app.get("/api/review-states")
    def review_states() -> JSONResponse:
        rows = session.refresh()
        try:
            held = sittings.draft_states(session.drafts, session.submitted_by)
        except sittings.DraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            {
                "states": {row.case_id: _status_for(session, row) for row in rows},
                "identity_locked": bool(held),
            }
        )

    @app.post("/api/reviewer")
    def reviewer(request: Request, body: ReviewerChoice) -> JSONResponse:
        base.refuse_cross_origin(request)
        base._require_token(request, session)
        if sittings.draft_states(session.drafts, session.submitted_by):
            raise HTTPException(
                status_code=409,
                detail="reviewer identity is locked after the first case is started",
            )
        if body.mode == "self":
            if session.submitted_by == LOCAL_SUBMITTER:
                raise HTTPException(
                    status_code=409,
                    detail="no authenticated GitHub identity is available; choose anonymous or authenticate with `gh auth login` and restart",
                )
            session.submitted_for = session.submitted_by
        else:
            session.submitted_for = ANONYMOUS
        _remember_identity(session)
        return JSONResponse(
            {
                "submitted_by": session.submitted_by,
                "submitted_for": session.submitted_for,
            }
        )

    @app.post("/api/reset")
    def reset_review(request: Request, body: base.Which) -> JSONResponse:
        base.refuse_cross_origin(request)
        base._require_token(request, session)
        prepared = base._open(session, body.case)
        held = base._draft(session, body.case)
        if held is None:
            raise HTTPException(
                status_code=409, detail="that case has no review to reset"
            )
        if held.state == "finished":
            try:
                held = sittings.withdraw(session.store, prepared, held)
            except (
                sittings.SittingError,
                sittings.DraftError,
                OSError,
                ValueError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        held.marks = {}
        held.missing = []
        held.notes = ""
        base._save(session, held)
        session.dropped.discard(prepared.case_id)
        return JSONResponse({"case": prepared.case_id, "state": "open", "reset": True})

    return app


def _has_flag(args: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(name + "=") for arg in args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    base.REPO_ROOT = REPO_ROOT
    base._PAGE = _PAGE
    base.create_app = create_app

    if "--list" not in args and not _has_flag(args, "--submitted-by"):
        try:
            login = submit_spine.gh_login(REPO_ROOT)
        except submit_spine.SubmitError:
            login = ""
        if not login:
            args.extend(["--submitted-by", LOCAL_SUBMITTER])
            if not _has_flag(args, "--submitted-for"):
                args.extend(["--submitted-for", ANONYMOUS])

    return base.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
