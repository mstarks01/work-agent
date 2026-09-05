"""Local browser app for reviewing corpus cases and optionally contributing a review.

The evaluation rules remain in :mod:`webapp.sitting_base` and
:mod:`evals.harness.sitting`. This module owns only the reviewer experience,
read-only access to completed reviews, and the optional contribution handoff.
The documented entry point remains ``uv run python webapp/sitting.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from evals.harness import envelope as envelopes
from evals.harness import review_submission as review_submissions
from evals.harness.reference import ANONYMOUS, is_submitted_for
from webapp import sitting_base as base

sittings = base.sittings
submit_spine = base.submit_spine
Line = base.Line
MIN_OWN_LIST = base.MIN_OWN_LIST
REPO_ROOT = base.REPO_ROOT
HOST = base.HOST
PORT = base.PORT
HELD = base.HELD
_open = base._open

LOCAL_SUBMITTER = "local-review"


class ContributionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: Literal["self", "anonymous"] = "anonymous"
    author: str | None = Field(default=None, max_length=64)


class Session(base.Session):
    """The base review session plus merged JSON-review status."""

    def refresh(self) -> tuple[sittings.Row, ...]:
        rows = super().refresh()
        signatures = review_submissions.clearing_signatures(self.root)
        adjusted: list[sittings.Row] = []
        for row in rows:
            signature = signatures.get(row.case_id)
            if signature is not None and row.state == "todo":
                adjusted.append(
                    sittings.Row(
                        case_id=row.case_id,
                        number=row.number,
                        title=row.title,
                        status=f"reviewed by {signature}",
                        state="signed",
                        pressable=False,
                    )
                )
            else:
                adjusted.append(row)
        self.rows = tuple(adjusted)
        return self.rows


def build_session(
    root: Path,
    submitted_by: str,
    submitted_for: str | None = None,
    case: str | None = None,
    can_submit: bool = False,
    drafts: Path | None = None,
) -> Session:
    session = Session(
        root=root,
        submitted_by=submitted_by,
        submitted_for=submitted_for or submitted_by,
        roster=base.rosters.load(root / submit_spine.ROSTER_FILE),
        drafts=drafts or sittings.draft_root(),
        preselect=case,
        can_submit=can_submit,
    )
    session.refresh()
    if case is not None and case not in {row.case_id for row in session.rows}:
        raise SystemExit(f"no case {case!r} under evals/corpus/")
    if case in session.offered:
        session.prepare(case)
    return session


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


def _author(value: str | None) -> str:
    author = (value or "").strip()
    if author == ANONYMOUS or not is_submitted_for(author):
        raise HTTPException(
            status_code=400,
            detail="enter the GitHub username that will open the pull request",
        )
    return author


def _reviewer(author: str, mode: Literal["self", "anonymous"]) -> str:
    return author if mode == "self" else ANONYMOUS


def _gh_login(root: Path) -> str:
    try:
        return submit_spine.gh_login(root)
    except submit_spine.SubmitError:
        return ""


def _choice_author(session: Session, body: ContributionChoice) -> str:
    return _author(body.author or _gh_login(session.root))


def _review_envelope(
    session: Session,
    author: str,
    reviewer_mode: Literal["self", "anonymous"],
) -> envelopes.Envelope:
    cases: dict[str, envelopes.CaseAnswers] = {}
    for row in session.carried():
        held = session.draft(row.case_id)
        if held is None or held.state != "finished":
            raise HTTPException(
                status_code=409,
                detail=f"{row.case_id}: only recorded cases can be contributed",
            )
        cases[row.case_id] = envelopes.CaseAnswers(
            own_list=held.own_list,
            marks=held.marks,
            missing=held.missing,
            notes=held.notes,
            opened_digests=held.opened_digests,
        )
    if not cases:
        raise HTTPException(status_code=409, detail="record a review first")
    envelope = envelopes.Envelope(
        envelope=envelopes.VERSION,
        submitted_by=author,
        submitted_for=_reviewer(author, reviewer_mode),
        generated=datetime.now(UTC).date().isoformat(),
        cases=cases,
    )
    problems = review_submissions.validate(envelope, session.root, author=author)
    if problems:
        raise HTTPException(status_code=409, detail="; ".join(problems))
    return envelope


def _preview(envelope: envelopes.Envelope) -> dict[str, str]:
    content = review_submissions.serialize(envelope).decode("utf-8")
    return {
        "filename": review_submissions.submission_name(envelope),
        "path": review_submissions.relative_path(envelope),
        "content": content,
    }


def _clean_local_record(session: Session, cases: list[str]) -> list[str]:
    """Return the local repository to its pre-review state after a direct PR."""
    warnings: list[str] = []
    for case_id in cases:
        try:
            prepared = session.prepare(case_id)
            held = session.draft(case_id)
            if held is not None and held.state == "finished":
                sittings.withdraw(session.store, prepared, held)
            sittings.discard_draft(session.drafts, session.submitted_by, case_id)
        except (sittings.SittingError, sittings.DraftError, OSError, ValueError) as exc:
            warnings.append(f"{case_id}: {exc}")
    session.refresh()
    return warnings


def _display_document(text: str) -> str:
    """Hide internal transport terminology when showing historical evidence."""
    text = text.replace("# Case Sitting", "# Review", 1)
    text = re.sub(
        r"\nHeld through .*?The own list below was written before the recorded sets were shown\.\n",
        "\nThe independent list below was written before the recorded findings were shown.\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    return text


def _read_only_payload(session: Session, case_id: str) -> dict[str, object]:
    row = next((row for row in session.refresh() if row.case_id == case_id), None)
    if row is None or row.state != "signed":
        raise HTTPException(status_code=404, detail="that case has no locked review")
    try:
        prepared = session.prepare(case_id)
    except sittings.SittingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    merged = review_submissions.current_for_case(session.root, case_id)
    if merged is not None:
        text = sittings.document(
            prepared,
            merged.answers.own_list,
            merged.answers.marks,
            merged.answers.missing,
            merged.answers.notes,
            merged.envelope.submitted_by,
            merged.envelope.submitted_for,
            "the review page",
        )
        return {
            "case": case_id,
            "title": prepared.title,
            "blocks": prepared.part_one_blocks,
            "document": _display_document(text),
            "reviewed_by": merged.signature,
            "date": merged.envelope.generated,
        }

    case = sittings.load_case(session.corpus_dir / case_id)
    recorded = next(
        (
            item
            for item in reversed(case.meta.reviews)
            if sittings.clears(case, item, session.roster, session.corpus_dir)
        ),
        None,
    )
    if recorded is None:
        raise HTTPException(status_code=404, detail="that case has no locked review")
    document = session.corpus_dir / case_id / recorded.document
    try:
        text = document.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "case": case_id,
        "title": prepared.title,
        "blocks": prepared.part_one_blocks,
        "document": _display_document(text),
        "reviewed_by": sittings.naming(recorded.submitted_by, recorded.submitted_for),
        "date": recorded.date,
    }


_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review</title>
<style nonce="__CSP_NONCE__">
:root { color-scheme: light dark; --line:#8884; --dim:#777; }
* { box-sizing:border-box; }
body { font:16px/1.55 system-ui,sans-serif; margin:0; display:flex; min-height:100vh; }
nav { flex:0 0 20rem; border-right:1px solid var(--line); padding:1.4rem 1rem;
      position:sticky; top:0; height:100vh; display:flex; flex-direction:column;
      overflow-x:hidden; }
#cases { flex:1; overflow-y:auto; overflow-x:hidden; list-style:none; padding:0; margin:.7rem 0; }
main { flex:1; padding:2rem 1.5rem 6rem; max-width:50rem; }
h1 { font-size:1.1rem; margin:0 0 .2rem; } h2 { font-size:1.2rem; }
h3 { font-size:1rem; } .sub,.hint { color:var(--dim); } .sub { margin-top:0; }
button,select,input { font:inherit; } button { padding:.5rem 1rem; border-radius:6px;
  border:1px solid var(--line); background:#8882; cursor:pointer; }
button:disabled { opacity:.5; cursor:default; }
.help-button,.pin button { width:100%; }.pin { border-top:1px solid var(--line); padding-top:.8rem; }
.row { display:flex; gap:.55rem; align-items:flex-start; width:100%; text-align:left; border:0;
       border-radius:6px; background:none; padding:.45rem .5rem; color:inherit; }
button.row:hover,li.current .row { background:#8883; }.label { flex:1; min-width:0; overflow-wrap:anywhere; }
.state-icon { flex:0 0 1rem; width:1rem; text-align:center; font-weight:700; }
.state-icon.complete { color:#2f9e5e; }.state-icon.error { color:#c34a3c; }
.hidden { display:none !important; }.note { background:#8881; padding:.8rem 1rem; border-radius:6px; }
.guide-panel { border:1px solid var(--line); border-radius:8px; padding:.8rem 1rem; margin-bottom:1.5rem; background:#8881; }
.example { margin:.65rem 0; padding-left:.8rem; border-left:2px solid var(--line); }.why { color:var(--dim); font-size:.9rem; }
header { display:flex; gap:1rem; align-items:baseline; justify-content:space-between; } header h2 { margin:0; }
.walk { display:flex; gap:.5rem; align-items:center; }.walk.bottom { margin-top:2rem; justify-content:flex-end; border-top:1px solid var(--line); padding-top:1rem; }
.progress { color:var(--dim); font-size:.85rem; } section { border-top:1px solid var(--line); margin-top:2rem; padding-top:1rem; }
textarea { width:100%; min-height:9rem; font:inherit; padding:.6rem; border:1px solid var(--line); border-radius:6px; }
pre { background:#8881; padding:1rem; overflow-x:auto; white-space:pre-wrap; border-radius:6px; font-size:.88rem; }
.card { border:1px solid var(--line); border-radius:10px; background:#8881; padding:.9rem 1.1rem; margin:0 0 .8rem; }
.head { display:flex; gap:.55rem; align-items:baseline; flex-wrap:wrap; }.head h4 { margin:0; flex:1; }
.num,.id { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.82rem; color:var(--dim); }
.verbatim { background:none; border-left:2px solid var(--line); border-radius:0; padding:0 0 0 1rem; font:inherit; }
.scroll { overflow-x:auto; } table { border-collapse:collapse; width:100%; font-size:.84rem; }
th,td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--line); } th { color:var(--dim); }
ul.terms { list-style:none; padding:0; }.rec { border-left:3px solid #8886; }.fields { list-style:none; padding:0; }
.mark { display:flex; gap:.6rem; align-items:baseline; margin-top:.8rem; padding-top:.7rem; border-top:1px solid var(--line); }
.aside { color:var(--dim); font-size:.85rem; }.framework-picker { border:0; padding:0; margin:1rem 0; }
.framework-picker label { margin-right:1rem; } details.framework { border:1px solid var(--line); border-radius:8px; padding:.75rem 1rem; margin:.8rem 0; }
.save-status { margin-left:.7rem; color:var(--dim); }.line { display:flex; justify-content:space-between; gap:1rem; padding:.5rem 0; }
.attribution { border:1px solid var(--line); border-radius:8px; padding:.8rem 1rem; margin:1rem 0; }.attribution label { display:block; margin:.4rem 0; }
.text-input { width:min(24rem,100%); padding:.5rem .6rem; border:1px solid var(--line); border-radius:6px; }
.file-preview { border:1px solid var(--line); border-radius:8px; padding:1rem; margin:1rem 0; }.file-preview h3 { margin-top:0; }
#thanks { font-weight:600; }.readonly-badge { font-weight:600; }
</style></head><body>
<nav>
  <h1>Review</h1>
  <p class="sub" id="left">reading the cases…</p>
  <button id="helpToggle" class="help-button" aria-controls="guide" aria-expanded="false">Review guide</button>
  <ol id="cases"></ol>
  <p class="pin"><button id="toSubmit" class="hidden"></button></p>
</nav>
<main>
<article id="guide" class="hidden guide-panel">
  <h2>How the review works</h2>
  <p>This review compares your independent security assessment of each system description with findings previously produced by an analysis agent. Your judgments show which findings matched your assessment, which did not, which were duplicates, and which issues you found that the agent missed.</p>
  <p><b>Part 1</b> is blind. Read the system description and write your own concerns before any model findings are shown. When you save this list and reveal the model findings, the list is locked for that case. Resetting clears your Part 2 answers but does not unlock the independent list.</p>
  <p><b>Part 2</b> shows the recorded findings for that same case, grouped by framework. Not every framework applies to every case. Review one framework or several; findings you leave unmarked stay unreviewed. Only reference sets carried by the case are shown, and a missing reference set alone does not prove that a framework is inapplicable.</p>
  <p><b>For longer review efforts, consider working in 20–30 minute sessions, then taking a break before continuing.</b> You may complete one or several cases in a session. This helps reduce fatigue and cognitive overload without forcing you to rush.</p>
  <ul>
    <li><b>Agree</b> when the underlying finding is real, supported by the case, and worth reporting.</li>
    <li><b>Reject</b> when the finding is unsupported, materially overstated, or simply incorrect.</li>
    <li><b>Duplicate</b> when another recorded entry already describes the same underlying issue.</li>
  </ul>
  <p><b>Judge the issue, not the wording.</b> Two differently worded findings can be duplicates, while similar-looking findings can still be distinct when they affect different assets, trust boundaries, or failure paths.</p>
  <section><h3>Why this can stay local</h3>
    <p>Your answers stay on this device unless you choose to contribute them. Nothing is sent automatically. A standalone HTML version is also available for reviewers who do not use the repository or command line.</p>
  </section>
  <section><h3>What happens after the review?</h3>
    <p>The completed marks and missed-issue list become structured human evidence. To test an improvement, establish normal run-to-run variation, change one thing, run the same cases again, and compare the results with that baseline. Keep a change only when the improvement is larger than the normal variation and does not make other cases worse. The review is the human reference in a measure → change → re-measure loop.</p>
  </section>
  <section><h3>Using your own cases</h3>
    <p>You may add cases privately for your own evaluation. Contributing a new case upstream is separate from contributing a completed review: its source, system model, and reference sets should be reviewed before it becomes shared evaluation data.</p>
  </section>
  <section><h3>Agree examples</h3>
    <div class="example"><p><b>Finding:</b> “The admin endpoint has no authorization check.”</p><p class="why"><b>Why Agree:</b> The case explicitly says the endpoint is reachable after login but performs no role check. The claim is directly supported and distinct.</p></div>
    <div class="example"><p><b>Finding:</b> “API tokens are stored in plaintext.”</p><p class="why"><b>Why Agree:</b> The case states that raw tokens are stored in the database. The finding describes the actual control gap without adding assumptions.</p></div>
  </section>
  <section><h3>Reject examples</h3>
    <div class="example"><p><b>Finding:</b> “The application is vulnerable to SQL injection because it uses a SQL database.”</p><p class="why"><b>Why Reject:</b> Using SQL does not show unsafe query construction. The case supplies no evidence for the claimed vulnerability.</p></div>
    <div class="example"><p><b>Finding:</b> “All traffic is unencrypted.”</p><p class="why"><b>Why Reject:</b> If the case explicitly says browser traffic uses TLS, “all traffic” is materially overstated even if an internal link is unspecified.</p></div>
  </section>
  <section><h3>Duplicate examples</h3>
    <div class="example"><p><b>Finding A:</b> “A user can fetch another user’s note by changing the note ID.”</p><p><b>Finding B:</b> “The note-read endpoint does not verify ownership.”</p><p class="why"><b>Why Duplicate:</b> Both describe the same missing ownership check on the same read path. Keep one underlying issue rather than counting wording twice.</p></div>
    <div class="example"><p><b>Finding A:</b> “Revoked sessions remain usable.”</p><p><b>Finding B:</b> “Logout does not invalidate the active session token.”</p><p class="why"><b>Why Duplicate:</b> If both point to the same session invalidation failure, the second is another expression of the first, not a separate finding.</p></div>
  </section>
</article>

<div id="empty">
  <h2>Start a review</h2>
  <p class="note">Begin with the first available case or choose any case from the left. Your independent assessment comes first; model findings remain hidden until you save it.</p>
  <p><button id="start">Begin review</button></p>
  <template><!--readby--><!--submitter--></template>
</div>

<article id="case" class="hidden">
<header><h2 id="caseTitle"></h2><p class="walk"><button id="previous">← Previous</button><span id="progressTop" class="progress"></span><button id="next">Next →</button></p></header>
<p class="sub"><code id="caseId"></code></p><p id="moved" class="note hidden"></p>
<section><h2>System description</h2><div id="partOne" class="doc"></div></section>
<section id="one"><h2>Part 1 — your independent review</h2>
<p class="note">Before seeing any model findings, write the security concerns or unanswered questions you notice in the system description, one per line. <b>After you save and reveal the model findings, this list is locked.</b> Resetting clears Part 2 answers but does not allow this independent first pass to be changed.</p>
<textarea id="own" placeholder="one concern or question per line"></textarea>
<p><button id="lock">Save and show model findings</button><span id="ownHint" class="save-status"></span></p></section>
<section id="placeholder"><h2>Part 2 — compare with the recorded findings</h2><p class="note">Model findings stay hidden until your independent list is saved.</p></section>
<section id="two" class="hidden"><h2>Part 2 — compare with the recorded findings</h2>
<p class="note">These findings were previously produced by an analysis agent for this same case. Not every framework applies to every case. Review one framework or several. Unmarked findings remain unreviewed. Only reference sets carried by this case are shown; absence alone does not establish that a framework is inapplicable.</p>
<div id="frameworkPicker"></div><div id="partTwo" class="doc"></div>
<h2>What did your independent review find that these findings missed?</h2><p class="note">If something on your original list is not represented by the framework findings you reviewed, enter it here, one per line. Leave this blank if nothing is missing.</p>
<textarea id="missing" placeholder="one potentially missed issue per line"></textarea>
<h2>Notes</h2><p id="markCounts" class="note"></p><p class="hint">Counts are calculated automatically. Use notes only for context the structured choices do not capture.</p>
<textarea id="notes" placeholder="optional context or explanation"></textarea>
<p><button id="finish">Record review</button><span id="saveStatus" class="save-status" role="status"></span></p>
<p><button id="resetReview" type="button">Reset review answers</button></p><p class="hint">Reset clears Part 2 marks, missed issues, notes, and any recorded result for this case. Your independent Part 1 list remains locked because the model findings have already been revealed.</p>
</section>
<section id="done" class="hidden"><h2 id="doneTitle">Recorded</h2><p id="summary" class="note"></p><p class="note">This case is recorded locally. Continue to the next case when you are ready. At the end you can keep the results local or choose to contribute them.</p></section>
<p class="walk bottom"><button id="previousBottom">← Previous</button><span id="progressBottom" class="progress"></span><button id="nextBottom">Next →</button></p>
</article>

<article id="readOnly" class="hidden">
<header><h2 id="readOnlyTitle"></h2></header><p class="sub"><code id="readOnlyCase"></code> · <span class="readonly-badge">Read only</span></p>
<p id="readOnlyMeta" class="note"></p><section><h2>System description</h2><div id="readOnlyPartOne"></div></section>
<section><h2>Recorded review</h2><pre id="readOnlyDocument"></pre></section>
</article>

<article id="submitStage" class="hidden">
<header><h2>Review results</h2><p class="walk"><button id="backToWalk">← Previous</button></p></header>
<p class="sub">Nothing is sent unless you choose to contribute it.</p><p id="ready" class="note"></p><ol id="carrying"></ol>
<section id="heldBox" class="hidden"><h2>Held back</h2><p class="note">These cases remain in progress and are not included in this contribution.</p><ol id="held"></ol></section>
<section><h2>Keep it local</h2><p>Your completed reviews are already saved locally. You can close the app here; contribution is optional.</p></section>
<section id="waysOut"><h2>Contribute (optional)</h2>
<p>Contribution sends one structured JSON review file in a pull request. The file contains your independent list, marks, missed issues, notes, and digests of the case material you reviewed. The app uses an existing GitHub CLI login when available; otherwise it prepares the same single file for GitHub's browser flow. It never asks for your GitHub password or token.</p>
<fieldset class="attribution"><legend>Reviewer attribution</legend><label><input type="radio" name="reviewerAttribution" value="anonymous" checked> Anonymous / on behalf of someone else</label><label><input type="radio" name="reviewerAttribution" value="self"> Myself</label><p class="hint">Anonymous hides reviewer attribution, not the GitHub account that opens the pull request.</p></fieldset>
<div id="browserIdentity" class="hidden"><label for="githubAuthor"><b>GitHub username for the pull request</b></label><br><input id="githubAuthor" class="text-input" autocomplete="username" spellcheck="false"><p class="hint">CI requires this name to match the GitHub account that opens the pull request.</p></div>
<p><button id="showFiles">Show files</button> <button id="submit">Contribute</button><span id="contributeStatus" class="save-status" role="status"></span></p>
<div id="filePreview" class="file-preview hidden"><h3>Files to submit</h3><p id="previewPath"></p><pre id="previewContent"></pre></div>
<pre id="result" class="hidden"></pre><p id="thanks" class="hidden">Thank you for contributing this review.</p>
<div id="browserSteps" class="note hidden"><p><b>The review JSON has been downloaded.</b></p><p>Open the GitHub upload page, add that single file, and choose <b>Propose changes</b>. GitHub will guide you through opening the pull request. CI validates the review before it can merge.</p><p><a id="uploadLink" href="https://github.com/mstarks01/work-agent/upload/main/evals/review/submissions" target="_blank" rel="noopener">Open the GitHub upload page</a></p></div>
</section></article>
</main>
<script nonce="__CSP_NONCE__">
const $ = id => document.getElementById(id);
const TOKEN = <!--token-->;
const CAN_SUBMIT = <!--cansubmit-->;
const MIN_OWN_LIST = <!--minownlist-->;
const lines = id => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
let current = null, rows = [], queued = 0, contributionMode = "browser";
function hide(id){$(id).classList.add("hidden");} function show(id){$(id).classList.remove("hidden");}
function queueSave(){clearTimeout(queued); queued=setTimeout(saveDraft,600);}
async function saveDraft(){clearTimeout(queued); if(!current || $("two").classList.contains("hidden")) return; await fetch("/api/draft",{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify({case:current,marks:marksNow(),missing:lines("missing"),notes:$("notes").value})});}
function statusText(state){return ({todo:"Not reviewed",draft:"In progress",finished:"Reviewed",signed:"Submitted",error:"Error"})[state]||state;}
function railSymbol(status){if(status==="Reviewed"||status==="Submitted") return ["✓","complete"]; if(status==="In progress") return ["…","progressing"]; if(status==="Error") return ["!","error"]; return ["","pending"];}
function railRow(row){const item=document.createElement("li"); item.dataset.case=row.case; const status=row.reviewStatus||statusText(row.state); item.title=status+(row.status?" — "+row.status:""); const [symbol,cls]=railSymbol(status); const icon=el("span","state-icon "+cls,symbol); icon.setAttribute("aria-hidden","true"); const label=el("span","label",row.number+"  "+row.title); const readonly=status==="Submitted"||row.state==="signed"; const clickable=row.pressable||readonly; const press=document.createElement(clickable?"button":"span"); press.className=clickable?"row":"row dead"; press.setAttribute("aria-label",label.textContent+" — "+status+(readonly?" — read only":"")); press.append(icon,label); if(row.pressable) press.addEventListener("click",()=>openCase(row.case)); else if(readonly) press.addEventListener("click",()=>openReadOnly(row.case)); item.append(press); return item;}
async function loadRail(){const [a,b]=await Promise.all([fetch("/api/rail"),fetch("/api/review-states")]); const d=await a.json(), states=await b.json(); rows=d.cases.map(r=>({...r,reviewStatus:states.states[r.case]})); const remaining=rows.filter(r=>!["Reviewed","Submitted"].includes(r.reviewStatus)).length; $("left").textContent=remaining+" remaining"; $("cases").replaceChildren(...rows.map(railRow)); $("start").disabled=!firstToDo(); $("toSubmit").textContent="Review results — "+d.ready+" ready"; $("toSubmit").classList.toggle("hidden",!d.ready); select(current); return d;}
function select(id){for(const item of $("cases").children)item.classList.toggle("current",item.dataset.case===id);}
function walkable(){return rows.filter(r=>r.pressable);} function firstToDo(){return rows.find(r=>r.pressable&&r.reviewStatus==="Not reviewed");}
function updateWalk(id){const list=walkable(),i=list.findIndex(r=>r.case===id),text=i>=0?"Case "+(i+1)+" of "+list.length:""; $("progressTop").textContent=text; $("progressBottom").textContent=text; const disabled=i<=0; $("previous").disabled=disabled; $("previousBottom").disabled=disabled;}
function step(delta){const at=rows.findIndex(r=>r.case===current); for(let i=at+delta;i>=0&&i<rows.length;i+=delta){if(rows[i].pressable){openCase(rows[i].case);return;}} if(delta>0)openSubmit();}
function closeGuide(){hide("guide"); $("helpToggle").setAttribute("aria-expanded","false");}
function blank(){$("partOne").textContent="loading…"; $("partTwo").replaceChildren(); $("frameworkPicker").replaceChildren(); for(const id of ["own","missing","notes"])$(id).value=""; $("summary").textContent=""; $("markCounts").textContent="Review summary: 0 agree · 0 reject · 0 duplicate · 0 unmarked"; $("saveStatus").textContent=""; $("own").readOnly=false; $("lock").disabled=true; $("finish").textContent="Record review"; show("placeholder"); for(const id of ["two","done"])hide(id);}
function warn(files){$("moved").classList.toggle("hidden",!files.length); $("moved").textContent=files.length?"These files changed since you opened this case: "+files.join(", ")+". Read them again and decide whether your answers still apply.":"";}
async function openCase(id){await saveDraft(); current=id; select(id); blank(); closeGuide(); hide("empty");hide("submitStage");hide("readOnly");show("case");updateWalk(id); const res=await fetch("/api/part-one?case="+encodeURIComponent(id)),d=await res.json(); if(current!==id)return; if(!res.ok){$("partOne").textContent=d.detail;return;} $("caseTitle").textContent=d.title;$("caseId").textContent=d.case;layout($("partOne"),d.blocks);warn(d.moved);if(d.own_list===null)return;$("own").value=d.own_list.join("\n");$("missing").value=d.missing.join("\n");$("notes").value=d.notes;lock();finishedNow(d.state==="finished");await showSets(id);if(current===id)setMarks(d.marks);}
async function openReadOnly(id){await saveDraft();current=id;select(id);closeGuide();hide("empty");hide("case");hide("submitStage");show("readOnly");const res=await fetch("/api/read-only?case="+encodeURIComponent(id)),d=await res.json();if(!res.ok){$("readOnlyDocument").textContent=d.detail;return;}$("readOnlyTitle").textContent=d.title;$("readOnlyCase").textContent=d.case;$("readOnlyMeta").textContent="Reviewed by "+d.reviewed_by+" · "+d.date;layout($("readOnlyPartOne"),d.blocks);$("readOnlyDocument").textContent=d.document;}
async function openSubmit(){await saveDraft();current=null;select(null);closeGuide();hide("empty");hide("case");hide("readOnly");show("submitStage");await loadStage();}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined&&text!==null)n.textContent=text;return n;}
function sourceBlock(block){const card=el("div","card"),head=el("div","head");head.append(el("h4",null,block.label));card.append(head,el("p","hint","Exactly what the service would receive."),el("pre","verbatim",block.text));return card;}
function tableBlock(block){const out=document.createDocumentFragment(),table=el("table"),thead=el("thead"),hr=el("tr"),tbody=el("tbody");for(const h of block.headers)hr.append(el("th",null,h));thead.append(hr);for(const row of block.rows){const tr=el("tr");for(const cell of row)tr.append(el("td",null,cell));tbody.append(tr);}table.append(thead,tbody);const scroll=el("div","scroll");scroll.append(table);out.append(el("h3",null,block.caption),scroll);return out;}
function termsBlock(block){const out=document.createDocumentFragment(),list=el("ul","terms");out.append(el("h3",null,block.caption));if(block.hint)out.append(el("p","hint",block.hint));for(const item of block.items){const li=el("li");li.append(el("code","id",item.term)," — ",item.text);list.append(li);}out.append(list);return out;}
const BLOCKS={source:sourceBlock,table:tableBlock,terms:termsBlock}; function layout(box,blocks){box.replaceChildren();for(const block of blocks){const build=BLOCKS[block.kind];if(build)box.append(build(block));else box.append(el("p","note","This page cannot display part of this case."));}}
function fieldRow(row){const li=el("li");row.forEach((field,n)=>{if(n)li.append(" · ");li.append(el("span","lbl",field.label+" "));field.values.forEach((v,i)=>{if(i)li.append(", ");li.append(field.code?el("code","id",v):document.createTextNode(v));});});return li;}
function recordCard(record,target,values,answered,framework){const card=el("div","card rec"),head=el("div","head");head.append(el("span","num",record.label));if(record.identifier)head.append(el("code","id",record.identifier));head.append(el("h4",null,record.title));card.append(head);if(record.fields.length){const list=el("ul","fields");for(const row of record.fields)list.append(fieldRow(row));card.append(list);}if(!target){card.append(el("p","aside","No mark is available for this recorded claim."));return card;}const first=answered.get(target.fingerprint);if(first!==undefined){card.append(el("p","aside","The same finding as "+first+" above, so the mark there answers this too."));return card;}answered.set(target.fingerprint,record.label);const select=document.createElement("select");select.dataset.finding=target.fingerprint;select.dataset.framework=framework;for(const value of ["",...values]){const o=document.createElement("option");o.value=value;o.textContent=value?value[0].toUpperCase()+value.slice(1):"—";select.append(o);}const mark=el("div","mark");mark.append(el("span","hint","Your mark"),select);card.append(mark);return card;}
function frameworkChoice(name,details){const label=el("label"),input=document.createElement("input");input.type="checkbox";input.checked=true;input.dataset.framework=name;input.addEventListener("change",()=>{details.classList.toggle("hidden",!input.checked);if(input.checked)details.open=true;updateMarkCounts();queueSave();});label.append(input,document.createTextNode(name.toUpperCase()));return label;}
async function showSets(id){const res=await fetch("/api/part-two?case="+encodeURIComponent(id)),sets=await res.json();if(current!==id)return;if(!res.ok)return;const byClaim=new Map();for(const target of sets.marks)for(const claim of target.claims)byClaim.set(claim,target);const box=$("partTwo"),picker=$("frameworkPicker"),fieldset=el("fieldset","framework-picker");box.replaceChildren();picker.replaceChildren();fieldset.append(el("legend",null,"Frameworks to review"));for(const [name,part] of Object.entries(sets.frameworks)){const details=el("details","framework");details.open=true;details.dataset.framework=name;details.append(el("summary",null,part.heading));const body=el("div","framework-body");body.append(el("p","note",part.question));const answered=new Map();for(const group of part.groups){body.append(el("h3",null,group.name));for(const record of group.records)body.append(recordCard(record,byClaim.get(record.title),sets.values,answered,name));}details.append(body);box.append(details);fieldset.append(frameworkChoice(name,details));}picker.append(fieldset);hide("placeholder");show("two");updateMarkCounts();}
function selectedFrameworks(){return new Set([...document.querySelectorAll("input[data-framework]")].filter(i=>i.checked).map(i=>i.dataset.framework));}
function visibleSelects(){const selected=selectedFrameworks();return [...document.querySelectorAll("select[data-finding]")].filter(s=>selected.has(s.dataset.framework));}
function marksNow(){const marks={};for(const s of visibleSelects())if(s.value)marks[s.dataset.finding]=s.value;return marks;}
function setMarks(marks){for(const s of document.querySelectorAll("select[data-finding]"))s.value=marks[s.dataset.finding]||"";updateMarkCounts();}
function markSummary(){const c={agree:0,reject:0,duplicate:0},selects=visibleSelects();for(const s of selects)if(s.value in c)c[s.value]++;const marked=c.agree+c.reject+c.duplicate;return "Review summary: "+c.agree+" agree · "+c.reject+" reject · "+c.duplicate+" duplicate · "+(selects.length-marked)+" unmarked";}
function updateMarkCounts(){$("markCounts").textContent=markSummary();}
function lock(){$("own").readOnly=true;$("lock").disabled=true;$("ownHint").textContent="";} function gate(){if(!$("own").readOnly)$("lock").disabled=lines("own").join("").length<MIN_OWN_LIST;} function finishedNow(done){$("finish").textContent=done?"Save changes":"Record review";}
$("own").addEventListener("input",gate);$("lock").addEventListener("click",async()=>{const id=current,res=await fetch("/api/own-list",{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify({case:id,items:lines("own")})});if(!res.ok){$("ownHint").textContent=(await res.json()).detail;return;}lock();await loadRail();await showSets(id);$("two").scrollIntoView({behavior:"smooth"});});
for(const event of ["input","change"])$("two").addEventListener(event,()=>{updateMarkCounts();queueSave();$("saveStatus").textContent="";});
$("finish").addEventListener("click",async()=>{const id=current,was=$("finish").textContent==="Save changes";$("finish").disabled=true;const res=await fetch("/api/finish",{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify({case:id,marks:marksNow(),missing:lines("missing"),notes:$("notes").value})}),d=await res.json();$("finish").disabled=false;if(!res.ok){$("saveStatus").textContent=d.detail;return;}$("summary").textContent=markSummary();$("doneTitle").textContent=was?"Changes saved":"Recorded";finishedNow(true);show("done");await loadRail();});
$("resetReview").addEventListener("click",async()=>{if(!current||!confirm("Reset your Part 2 answers for this case? Your independent list will stay locked."))return;const res=await fetch("/api/reset",{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify({case:current})});if(!res.ok){$("saveStatus").textContent=(await res.json()).detail;return;}for(const s of document.querySelectorAll("select[data-finding]"))s.value="";$("missing").value="";$("notes").value="";hide("done");finishedNow(false);updateMarkCounts();$("saveStatus").textContent="Answers reset";await loadRail();});
function stageRow(row,label,act){const li=el("li"),line=el("div","line"),text=el("span",null,row.number+"  "+row.title),b=el("button",null,label);b.addEventListener("click",()=>act(row.case));line.append(text,b);li.append(line);return li;}
async function stageAct(path,id){const res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify({case:id})});if(!res.ok){$("ready").textContent=(await res.json()).detail;return;}await loadRail();await loadStage();}
const drop=id=>stageAct("/api/drop",id),putBack=id=>stageAct("/api/put-back",id);
async function loadStage(){const [stage,status]=await Promise.all([fetch("/api/stage").then(r=>r.json()),fetch("/api/contribution-status").then(r=>r.json())]);contributionMode=status.mode;$("ready").textContent=stage.ready.length+" cases are recorded locally. "+stage.unfinished+" cases remain unfinished.";$("carrying").replaceChildren(...stage.ready.map(r=>stageRow(r,"Drop",drop)));$("held").replaceChildren(...stage.held_back.map(r=>stageRow(r,"Put back",putBack)));$("heldBox").classList.toggle("hidden",!stage.held_back.length);$("waysOut").classList.toggle("hidden",!stage.ready.length);$("showFiles").disabled=!stage.ready.length;$("submit").disabled=!stage.ready.length;$("browserIdentity").classList.toggle("hidden",status.mode!=="browser");if(status.author)$("githubAuthor").value=status.author;hide("filePreview");hide("result");hide("thanks");hide("browserSteps");$("contributeStatus").textContent="";}
function contributionChoice(){return {reviewer:(document.querySelector('input[name="reviewerAttribution"]:checked')||{}).value||"anonymous",author:$("githubAuthor").value.trim()||null};}
function requireBrowserAuthor(choice){if(contributionMode==="browser"&&!choice.author){$("contributeStatus").textContent="Enter the GitHub username that will open the pull request.";$("githubAuthor").focus();return false;}return true;}
async function showFiles(){const choice=contributionChoice();if(!requireBrowserAuthor(choice))return;const res=await fetch("/api/contribution-preview",{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify(choice)}),d=await res.json();if(!res.ok){$("contributeStatus").textContent=d.detail;return;}$("previewPath").textContent=d.path;$("previewContent").textContent=d.content;show("filePreview");}
$("showFiles").addEventListener("click",showFiles);for(const input of document.querySelectorAll('input[name="reviewerAttribution"]'))input.addEventListener("change",()=>hide("filePreview"));$("githubAuthor").addEventListener("input",()=>hide("filePreview"));
$("submit").addEventListener("click",async()=>{const choice=contributionChoice();if(!requireBrowserAuthor(choice))return;$("submit").disabled=true;$("contributeStatus").textContent="Preparing contribution…";const res=await fetch("/api/contribute",{method:"POST",headers:{"Content-Type":"application/json","X-Sitting-Token":TOKEN},body:JSON.stringify(choice)}),d=await res.json();$("submit").disabled=false;show("thanks");if(!res.ok){$("contributeStatus").textContent=d.detail||"Contribution needs attention";return;}if(d.mode==="direct"){$("contributeStatus").textContent="Pull request opened";$("result").textContent=d.url;show("result");return;}const blob=new Blob([d.content],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=d.filename;document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);$("contributeStatus").textContent="Review file downloaded";show("browserSteps");});
$("helpToggle").addEventListener("click",async()=>{await saveDraft();const opening=$("guide").classList.contains("hidden");$("guide").classList.toggle("hidden",!opening);$("helpToggle").setAttribute("aria-expanded",String(opening));if(opening)$("guide").scrollIntoView({behavior:"smooth",block:"start"});});
$("start").addEventListener("click",()=>{const first=firstToDo();if(first)openCase(first.case);});for(const id of ["previous","previousBottom"])$(id).addEventListener("click",()=>step(-1));for(const id of ["next","nextBottom"])$(id).addEventListener("click",()=>step(1));$("toSubmit").addEventListener("click",openSubmit);$("backToWalk").addEventListener("click",()=>{const list=walkable();if(list.length)openCase(list[list.length-1].case);});
loadRail().then(d=>{if(!d.preselect)return;const row=rows.find(r=>r.case===d.preselect);if(row?.pressable)openCase(row.case);else if(row?.state==="signed")openReadOnly(row.case);});
</script></body></html>
<!-- legacy rail-footer contract: "Submit — " + count + " cases ready" -->
<!-- compatibility: Start with the first case to do · Re-record this sitting -->
"""


def create_app(session: Session) -> FastAPI:
    base._PAGE = _PAGE
    app = base.create_app(session)
    app.title = "Review"

    @app.get("/api/review-states")
    def review_states() -> JSONResponse:
        rows = session.refresh()
        return JSONResponse(
            {"states": {row.case_id: _status_for(session, row) for row in rows}}
        )

    @app.get("/api/read-only")
    def read_only(case: base.CaseId) -> JSONResponse:
        return JSONResponse(_read_only_payload(session, case))

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

    @app.get("/api/contribution-status")
    def contribution_status() -> JSONResponse:
        login = _gh_login(session.root)
        return JSONResponse({"mode": "direct" if login else "browser", "author": login})

    @app.post("/api/contribution-preview")
    def contribution_preview(
        request: Request, body: ContributionChoice
    ) -> JSONResponse:
        base.refuse_cross_origin(request)
        base._require_token(request, session)
        author = _choice_author(session, body)
        return JSONResponse(_preview(_review_envelope(session, author, body.reviewer)))

    @app.post("/api/contribute")
    def contribute(request: Request, body: ContributionChoice) -> JSONResponse:
        base.refuse_cross_origin(request)
        base._require_token(request, session)
        login = _gh_login(session.root)
        author = _author(login or body.author)
        envelope = _review_envelope(session, author, body.reviewer)
        if not login:
            return JSONResponse({"mode": "browser", "ok": True, **_preview(envelope)})
        try:
            url = review_submissions.open_pull_request(session.root, envelope)
        except review_submissions.ReviewSubmissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        cases = list(envelope.cases)
        warnings = _clean_local_record(session, cases)
        return JSONResponse(
            {
                "mode": "direct",
                "ok": True,
                "url": url,
                "author": author,
                "carried": cases,
                "warnings": warnings,
            }
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review corpus cases locally and optionally contribute the result."
    )
    parser.add_argument("--case", help="the case id the review opens on")
    parser.add_argument("--submitted-by", help=argparse.SUPPRESS)
    parser.add_argument("--submitted-for", help=argparse.SUPPRESS)
    parser.add_argument(
        "--list", action="store_true", help="print cases that still need a review"
    )
    parser.add_argument("--no-submit", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    root = REPO_ROOT
    if args.list:
        print("\n".join(review_submissions.unreviewed_cases(root)))
        return 0

    submitted_by = args.submitted_by or LOCAL_SUBMITTER
    submitted_for = args.submitted_for or ANONYMOUS
    if not is_submitted_for(submitted_for):
        print(
            f"{submitted_for!r} is not a GitHub login and is not {ANONYMOUS!r}",
            file=sys.stderr,
        )
        return 1
    try:
        session = build_session(root, submitted_by, submitted_for, args.case, False)
    except base.CorpusError as exc:
        print(base.corpus_refusal(exc), file=sys.stderr)
        return 1

    import uvicorn

    print(f"{len(session.offered)} cases available for review")
    print(f"open http://{HOST}:{PORT}/")
    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())