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
from pathlib import Path
from typing import Literal, NamedTuple, get_args

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals import review_submission as review_submissions
from evals.harness import envelope as envelopes
from evals.harness import submit as submit_spine
from evals.harness.reference import (
    ANONYMOUS,
    CorpusError,
    corpus_refusal,
    is_submitted_for,
)
from webapp import sitting_base as base
from webapp.page import client_script

sittings = base.sittings
REPO_ROOT = base.REPO_ROOT
HOST = base.HOST
PORT = base.PORT

LOCAL_SUBMITTER = "local-review"

#: Whether a submission names the reviewer or files under the anonymous name.
ReviewerMode = Literal["self", "anonymous"]

#: How a review contribution reaches GitHub: a pull request that the ``gh``
#: login opens, or a file that the reader takes to GitHub in the browser.
#: ``sitting.js`` reads it.
ContributionMode = Literal["direct", "browser"]

#: What the page calls each rail state. One table, keyed by
#: :data:`~evals.harness.sitting.RowState`. ``/api/review-states`` hands the
#: page the state and the label together, so the page keys its own behaviour
#: on the state vocabulary the rail already speaks and spells no label itself.
REVIEW_LABELS: dict[sittings.RowState, str] = {
    "todo": "Not reviewed",
    "draft": "In progress",
    "finished": "Reviewed",
    "signed": "Submitted",
    "error": "Error",
}
assert set(REVIEW_LABELS) == set(get_args(sittings.RowState))


class ContributionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: ReviewerMode = "anonymous"
    author: str | None = Field(default=None, max_length=64)


build_session = base.build_session
Session = base.Session


class ReviewState(NamedTuple):
    """One rail row as the page reads it: the state it keys on, and the label."""

    state: sittings.RowState
    label: str


def _review_state(session: Session, row: sittings.Row) -> ReviewState:
    """The state a rail row is in, as the page sees it.

    A draft holding an own list and nothing else reads as not started: the
    reader wrote the list, and the review is the marks that follow it.
    """
    state = row.state
    if state == "draft":
        held = session.draft(row.case_id)
        started = held is not None and (
            held.marks or held.missing or held.notes.strip()
        )
        if not started:
            state = "todo"
    return ReviewState(state, REVIEW_LABELS[state])


def _author(value: str | None) -> str:
    author = (value or "").strip()
    if author == ANONYMOUS or not is_submitted_for(author):
        raise HTTPException(
            status_code=400,
            detail="enter the GitHub username that will open the pull request",
        )
    return author


def _reviewer(author: str, mode: ReviewerMode) -> str:
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
    reviewer_mode: ReviewerMode,
) -> envelopes.Envelope:
    cases: dict[str, envelopes.CaseAnswers] = {}
    for row in session.carried():
        held = session.draft(row.case_id)
        if held is None or held.state != "finished":
            raise HTTPException(
                status_code=409,
                detail=f"{row.case_id}: only recorded cases can be contributed",
            )
        # Before the digest check can say it in its own words: a record whose
        # set moved since it was served is one the reader has to see again.
        if moved := base.moved_under(session, row.case_id):
            raise HTTPException(
                status_code=409,
                detail=f"{row.case_id}: {', '.join(moved)} changed since you"
                " recorded it; open the case, read what moved, and record again",
            )
        # A draft is the reader's own file, and a hand edit can hold what an
        # envelope refuses. The envelope's rules decide, and the refusal names
        # the case rather than failing the request.
        try:
            cases[row.case_id] = envelopes.CaseAnswers(
                own_list=held.own_list,
                marks=held.marks,
                missing=held.missing,
                notes=held.notes,
                opened_digests=held.opened_digests,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"{row.case_id}: the recorded review will not package —"
                f" {sittings.first_problem(exc)}",
            ) from None
    if not cases:
        raise HTTPException(status_code=409, detail="record a review first")
    try:
        envelope = envelopes.Envelope(
            envelope=envelopes.VERSION,
            submitted_by=author,
            submitted_for=_reviewer(author, reviewer_mode),
            generated=envelopes.stamp(),
            cases=cases,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"the review will not package — {sittings.first_problem(exc)}",
        ) from None
    problems = review_submissions.validate(envelope, session.root, author=author)
    if problems:
        raise HTTPException(status_code=409, detail="; ".join(problems))
    return envelope


def _preview(session: Session, envelope: envelopes.Envelope) -> dict[str, str]:
    """What a reader without a `gh` login is handed: the file, and a way out.

    ``url`` opens GitHub's editor with the file already typed, so the way out
    is one press rather than a download, a search for the right directory and a
    drag. A file too long for that link opens GitHub's upload page instead, and
    ``route`` says which, so the page shows the steps for the door it opened.
    The content rides beside it because a reader is entitled to read what they
    are about to publish before they publish it.
    """
    route = review_submissions.contribution_route(
        envelope, submit_spine.repo_slug(session.root)
    )
    return {
        "filename": review_submissions.submission_name(envelope),
        "path": review_submissions.relative_path(envelope),
        "content": review_submissions.serialize(envelope).decode("utf-8"),
        "url": route.url,
        "route": route.kind,
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
<style nonce="__CSP_NONCE__"><!--workspace_style-->
/* The review page on the first-run app's style: its case rail and its cards. */
body { display:flex; min-height:100vh; }
nav { flex:0 0 20rem; background:var(--sidebar); border-right:1px solid var(--line);
      padding:28px 18px; position:sticky; top:0; height:100vh; display:flex;
      flex-direction:column; overflow-x:hidden; }
#cases { flex:1; overflow-y:auto; overflow-x:hidden; list-style:none; padding:0; margin:12px 0; }
main { flex:1; margin:0; padding:36px 36px 6rem; max-width:52rem; }
h1 { font-size:18px; margin:0 0 4px; } h2 { font-size:19px; } h3 { font-size:16px; }
.sub { color:var(--muted); margin-top:0; }
input[type=radio] { width:auto; padding:0; accent-color:var(--accent); }
.help-button,.pin button { width:100%; }.pin { border-top:1px solid var(--line); padding-top:12px; }
.row { display:flex; gap:10px; align-items:flex-start; justify-content:flex-start; width:100%;
       min-height:0; text-align:left; border:0; border-radius:7px; background:none;
       padding:10px 12px; color:var(--text); }
button.row:hover,li.current .row { background:var(--soft); color:var(--accent); }
.label { flex:1; min-width:0; overflow-wrap:anywhere; }
.state-icon { flex:0 0 1rem; width:1rem; text-align:center; font-weight:700; }
.state-icon.complete { color:var(--accent); }.state-icon.error { color:var(--error); }
.reason { display:block; color:var(--muted); font-size:13px; font-weight:400; margin-top:3px; overflow-wrap:anywhere; }
.hidden { display:none !important; }.note { background:var(--sidebar); padding:12px 16px; border-radius:7px; }
.guide-panel { border:1px solid var(--line); border-radius:18px; padding:16px 20px; margin-bottom:24px;
               background:var(--canvas); box-shadow:0 1px 3px rgba(0,0,0,.05),0 4px 14px rgba(0,0,0,.05); }
.example { margin:10px 0; padding-left:12px; border-left:2px solid var(--line); }.why { color:var(--muted); font-size:14px; }
header { display:flex; gap:16px; align-items:baseline; justify-content:space-between; } header h2 { margin:0; }
.walk { display:flex; gap:8px; align-items:center; }.walk.bottom { margin-top:32px; justify-content:flex-end; border-top:1px solid var(--line); padding-top:16px; }
.progress { color:var(--muted); font-size:13px; } section { border-top:1px solid var(--line); margin-top:32px; padding-top:16px; }
textarea { min-height:9rem; }
pre { background:var(--sidebar); padding:16px; overflow-x:auto; white-space:pre-wrap; border-radius:7px; font-size:14px; }
.card { border:1px solid var(--line); border-radius:12px; background:var(--canvas); padding:14px 18px; margin:0 0 12px;
        box-shadow:0 1px 3px rgba(0,0,0,.04); }
.head { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }.head h4 { margin:0; flex:1; }
.num,.id { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; color:var(--muted); }
.verbatim { background:none; border-left:2px solid var(--line); border-radius:0; padding:0 0 0 16px; font:inherit; }
.scroll { overflow-x:auto; } table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); } th { color:var(--muted); }
ul.terms { list-style:none; padding:0; }.rec { border-left:3px solid var(--accent); }
.fields { list-style:none; padding:0; font-size:14px; }.fields li { margin:6px 0; }
/* A field label and an element id both read as the report's rounded boxes. */
.fields .lbl { display:inline-block; border:1px solid var(--line); border-radius:999px;
  padding:1px 9px; margin-right:6px; background:var(--sidebar); color:var(--muted);
  text-transform:uppercase; letter-spacing:.05em; font-size:11px; font-weight:600; }
.fields code.id { background:var(--sidebar); border:1px solid var(--line); border-radius:6px;
  padding:1px 6px; }
.mark { display:flex; gap:10px; align-items:baseline; margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
.mark select { width:auto; }
.aside { color:var(--muted); font-size:13px; }
details.standard { margin:5px 0 2px; font-size:14px; } details.standard summary { font-weight:600; }
.quote { margin:6px 0 3px; padding-left:12px; border-left:2px solid var(--line); }.framework-picker { border:0; padding:0; margin:16px 0; }
.framework-picker label { margin-right:16px; } details.framework { border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin:12px 0; }
.save-status { margin-left:12px; color:var(--muted); }.line { display:flex; justify-content:space-between; gap:16px; padding:8px 0; }
.attribution { border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin:16px 0; }.attribution label { display:block; margin:6px 0; }
.text-input { width:min(24rem,100%); }
.file-preview { border:1px solid var(--line); border-radius:12px; padding:16px; margin:16px 0; }.file-preview h3 { margin-top:0; }.file-preview pre { max-height:40vh; overflow:auto; }
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
  <p><b>Part 2</b> shows the recorded findings for that same case, grouped by framework. Not every framework applies to every case, and only reference sets carried by the case are shown; a missing reference set alone does not prove that a framework is inapplicable. Answer every finding in every set shown: the record waits until none is left unmarked, and <b>Unsure</b> is a real answer for one you cannot judge.</p>
  <p><b>For longer review efforts, consider working in 20–30 minute sessions, then taking a break before continuing.</b> You may complete one or several cases in a session. This helps reduce fatigue and cognitive overload without forcing you to rush.</p>
  <ul>
    <li><b>Agree</b> when the underlying finding is real, supported by the case, and worth reporting.</li>
    <li><b>Reject</b> when the finding is unsupported, materially overstated, or simply incorrect.</li>
    <li><b>Duplicate</b> when another recorded entry already describes the same underlying issue.</li>
    <li><b>Unsure</b> when you read the finding and cannot decide. It is a real answer: record it rather than pick one of the other three to get past the entry.</li>
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
  <p><button class="primary" id="start">Begin review</button></p>
</div>

<article id="case" class="hidden">
<header><h2 id="caseTitle"></h2><p class="walk"><button id="previous">← Previous</button><span id="progressTop" class="progress"></span><button id="next">Next →</button></p></header>
<p id="moved" class="note hidden"></p><p id="waiting" class="note hidden"></p>
<section><h2>System description</h2><div id="partOne" class="doc"></div></section>
<section id="one"><h2>Part 1 — your independent review</h2>
<p class="note">Before seeing any model findings, write the security concerns or unanswered questions you notice in the system description, one per line. <b>After you save and reveal the model findings, this list is locked.</b> Resetting clears Part 2 answers but does not allow this independent first pass to be changed.</p>
<textarea id="own" placeholder="one concern or question per line"></textarea>
<p><button class="primary" id="lock">Save and show model findings</button><span id="ownHint" class="save-status"></span></p></section>
<section id="placeholder"><h2>Part 2 — compare with the recorded findings</h2><p class="note">Model findings stay hidden until your independent list is saved.</p></section>
<section id="two" class="hidden"><h2>Part 2 — compare with the recorded findings</h2>
<p class="note">These findings were previously produced by an analysis agent for this same case. Not every framework applies to every case, and only reference sets carried by this case are shown; absence alone does not establish that a framework is inapplicable. Answer every finding in every set: the record waits until none is left unmarked, and <b>Unsure</b> is a real answer for one you cannot judge. Collapse a framework to work through one set at a time.</p>
<div id="frameworkPicker"></div><div id="partTwo" class="doc"></div>
<h2>What did your independent review find that these findings missed?</h2><p class="note">If something on your original list is not represented by the framework findings you reviewed, enter it here, one per line. Leave this blank if nothing is missing.</p>
<textarea id="missing" placeholder="one potentially missed issue per line"></textarea>
<h2>Notes</h2><p id="markCounts" class="note"></p><p class="hint">Counts are calculated automatically. Use notes only for context the structured choices do not capture.</p>
<textarea id="notes" placeholder="optional context or explanation"></textarea>
<p><button class="primary" id="finish" disabled>Record review</button><span id="saveStatus" class="save-status" role="status"></span></p><p id="finishHint" class="hint" role="status"></p>
<p><button id="resetReview" type="button">Reset review answers</button></p><p class="hint">Reset clears Part 2 marks, missed issues, notes, and any recorded result for this case. Your independent Part 1 list remains locked because the model findings have already been revealed.</p>
</section>
<section id="done" class="hidden"><h2 id="doneTitle">Recorded</h2><p id="summary" class="note"></p><p class="note">This case is recorded locally. Continue to the next case when you are ready. At the end you can keep the results local or choose to contribute them.</p></section>
<p class="walk bottom"><button id="previousBottom">← Previous</button><span id="progressBottom" class="progress"></span><button id="nextBottom">Next →</button></p>
</article>

<article id="readOnly" class="hidden">
<header><h2 id="readOnlyTitle"></h2></header><p class="sub"><span class="readonly-badge">Read only</span></p>
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
<p><button id="showFiles">Show files</button> <button class="primary" id="submit">Contribute</button><span id="contributeStatus" class="save-status" role="status"></span></p>
<pre id="result" class="hidden"></pre><p id="thanks" class="hidden">Thank you for contributing this review.</p>
<div id="browserSteps" class="note hidden"><p><b>Your review is ready to publish.</b></p><p>The link below opens GitHub with the file and its name already filled in. Sign in if you need to. GitHub shows one of two buttons. An account without write access presses <b>Propose changes</b>, and GitHub opens the pull request. An account with write access presses <b>Commit changes</b> and then chooses <b>Create a new branch for this commit and start a pull request</b>, because the default there commits straight to main. CI validates the review before it can merge.</p><p><a id="contributeLink" href="#" target="_blank" rel="noopener">Open the pull request on GitHub</a></p><p class="sub">A copy has also been downloaded, in case you would rather upload it by hand.</p></div>
<div id="uploadSteps" class="note hidden"><p><b>Your review is ready to publish, and it is too long for GitHub's editor link.</b></p><p>The file has been downloaded. The link below opens GitHub's upload page at the folder it belongs in. Sign in if you need to, drop the downloaded file onto the page, and keep its name exactly as downloaded. GitHub then shows one of two buttons. An account without write access presses <b>Propose changes</b>, and GitHub opens the pull request. An account with write access presses <b>Commit changes</b> and then chooses <b>Create a new branch for this commit and start a pull request</b>, because the default there commits straight to main. CI validates the review before it can merge.</p><p><a id="uploadLink" href="#" target="_blank" rel="noopener">Open the upload page on GitHub</a></p></div>
<div id="filePreview" class="file-preview hidden"><h3>Files to submit</h3><p id="previewPath"></p><pre id="previewContent"></pre></div>
</section></article>
</main>
<script nonce="__CSP_NONCE__">
const TOKEN = <!--token-->;
const MIN_OWN_LIST = <!--minownlist-->;
const MARK_VALUES = <!--markvalues-->;
const DONE = <!--donestates-->;
</script>
<script nonce="__CSP_NONCE__"><!--script--></script></body></html>
"""


def create_app(session: Session) -> FastAPI:
    app = base.create_app(session, _PAGE, client_script("sitting.js"))
    app.title = "Review"

    @app.get("/api/review-states")
    def review_states() -> JSONResponse:
        rows = session.refresh()
        return JSONResponse(
            {
                "states": {
                    row.case_id: _review_state(session, row)._asdict() for row in rows
                }
            }
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
        return JSONResponse({"case": prepared.case_id, "state": "open"})

    @app.get("/api/contribution-status")
    def contribution_status(request: Request) -> JSONResponse:
        # Whether a `gh` login exists, and which, is a fact about the
        # operator's machine. It goes only to a request that proved it read
        # the page, which is what the token proves; a read that never did is
        # a stranger asking.
        base.refuse_cross_origin(request)
        base.require_token(request, session)
        login = _gh_login(session.root)
        return JSONResponse({"mode": _contribution_mode(login), "author": login})

    @app.post("/api/contribution-preview")
    def contribution_preview(
        request: Request, body: ContributionChoice
    ) -> JSONResponse:
        base.refuse_cross_origin(request)
        base.require_token(request, session)
        author = _choice_author(session, body)
        return JSONResponse(
            _preview(session, _review_envelope(session, author, body.reviewer))
        )

    @app.post("/api/contribute")
    def contribute(request: Request, body: ContributionChoice) -> JSONResponse:
        base.refuse_cross_origin(request)
        base.require_token(request, session)
        login = _gh_login(session.root)
        author = _author(login or body.author)
        envelope = _review_envelope(session, author, body.reviewer)
        mode = _contribution_mode(login)
        if mode == "browser":
            return JSONResponse(
                {"mode": mode, "ok": True, **_preview(session, envelope)}
            )
        try:
            url = review_submissions.open_pull_request(session.root, envelope)
        except review_submissions.ReviewSubmissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        cases = list(envelope.cases)
        warnings = _clean_local_record(session, cases)
        return JSONResponse(
            {
                "mode": mode,
                "ok": True,
                "url": url,
                "author": author,
                "warnings": warnings,
            }
        )

    return app


def _contribution_mode(login: str) -> ContributionMode:
    return "direct" if login else "browser"


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
