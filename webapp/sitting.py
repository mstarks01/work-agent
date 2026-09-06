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
from evals.harness.reference import (
    ANONYMOUS,
    CorpusError,
    corpus_refusal,
    is_submitted_for,
)
from webapp import sitting_base as base
from webapp.page import client_script

sittings = base.sittings
submit_spine = base.submit_spine
Line = base.Line
MIN_OWN_LIST = base.MIN_OWN_LIST
REPO_ROOT = base.REPO_ROOT
HOST = base.HOST
PORT = base.PORT
HELD = base.HELD

LOCAL_SUBMITTER = "local-review"


class ContributionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: Literal["self", "anonymous"] = "anonymous"
    author: str | None = Field(default=None, max_length=64)


build_session = base.build_session
Session = base.Session


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
    if merged is None:
        raise HTTPException(status_code=404, detail="that case has no locked review")

    # Rendered from the submission rather than read from a committed file: the
    # submission is the whole record, so the document a reader sees here and
    # the document the reader wrote are one derivation of one set of answers.
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
const TOKEN = <!--token-->;
const MIN_OWN_LIST = <!--minownlist-->;
</script>
<script nonce="__CSP_NONCE__"><!--script--></script></body></html>
<!-- legacy rail-footer contract: "Submit — " + count + " cases ready" -->
<!-- compatibility: Start with the first case to do · Re-record this sitting -->
"""


def create_app(session: Session) -> FastAPI:
    app = base.create_app(session, _PAGE, client_script("sitting.js"))
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
        base.require_token(request, session)
        prepared = base.open_case(session, body.case)
        held = base.held_draft(session, body.case)
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
        base.save_draft(session, held)
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
        base.require_token(request, session)
        author = _choice_author(session, body)
        return JSONResponse(_preview(_review_envelope(session, author, body.reviewer)))

    @app.post("/api/contribute")
    def contribute(request: Request, body: ContributionChoice) -> JSONResponse:
        base.refuse_cross_origin(request)
        base.require_token(request, session)
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
        session = build_session(root, submitted_by, submitted_for, args.case)
    except CorpusError as exc:
        print(corpus_refusal(exc), file=sys.stderr)
        return 1

    import uvicorn

    print(f"{len(session.offered)} cases available for review")
    print(f"open http://{HOST}:{PORT}/")
    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
