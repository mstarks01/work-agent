"""Reviewer-facing work-review app over the existing sitting harness.

The evaluation rules remain in :mod:`webapp.sitting_base` and
:mod:`evals.harness.sitting`. This module owns the reviewer experience and the
optional contribution handoff while preserving the documented
``uv run python webapp/sitting.py`` entry point.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

# Running this file directly puts ``webapp/`` rather than the repository root
# on ``sys.path``. Restore the documented launcher before importing top-level
# ``evals`` or ``webapp`` packages.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from evals.harness.reference import ANONYMOUS, is_submitted_for
from webapp import sitting_base as base

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
_BASE_CREATE_APP = base.create_app


class ContributionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: Literal["self", "anonymous"] = "anonymous"
    author: str | None = Field(default=None, max_length=64)


def _swap(text: str, old: str, new: str) -> str:
    """Replace one expected page fragment and fail loudly when the base drifts."""
    if old not in text:
        raise RuntimeError(f"work-review page fragment moved: {old[:60]!r}")
    return text.replace(old, new, 1)


def _between(text: str, start: str, end: str, replacement: str) -> str:
    """Replace from ``start`` up to, but not including, ``end``."""
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"work-review page marker moved: {start!r}")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"work-review page marker moved: {end!r}")
    return text[:left] + replacement + text[right:]


# Exact fragments make base-page drift noisy instead of silently dropping a
# reviewer safeguard.
# fmt: off
def _updated_page(page: str) -> str:
    page = page.replace("Work Agent", "the project")
    page = _swap(page, "<title>Case sitting</title>", "<title>Work review</title>")
    page = _swap(page, "<h1>Case sitting</h1>", "<h1>Work review</h1>")
    page = _swap(
        page,
        '<button id="helpToggle" class="help-button" aria-controls="guide" aria-expanded="true">Review guide</button>',
        '<button id="helpToggle" class="help-button" aria-controls="guide" aria-expanded="false">Review guide</button>',
    )

    guide = r'''<article id="guide" class="hidden guide-panel">
  <h2>How the work review works</h2>
  <p><b>Thank you for taking the time to review these cases.</b></p>
  <p>This work review compares your independent security assessment of each system
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
  <p><b>For longer reviews, work for about 20–30 minutes, then take a break and
  revisit the case.</b> Shorter sessions help reduce fatigue and cognitive
  overload without forcing you to rush the assessment.</p>
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
    <p>The work review runs from your clone and writes its results into your
    working tree. Nothing is sent anywhere automatically. You can use the
    results only for your own measurements and keep them in your repository.</p>
    <p>If you later choose to contribute, the same results can be combined with
    other independent reviews to measure changes over time and identify areas
    where the analysis can improve.</p>
  </section>

  <section>
    <h3>What happens after the review?</h3>
    <p>The completed marks and missed-issue list become structured human
    evidence. To test an improvement, establish the normal run-to-run variation
    with repeated agent runs, change one thing, run the same corpus again, and
    compare the per-case results with that baseline. Keep a change only when the
    improvement is larger than the normal variation and does not make other
    cases worse. The work review is therefore the human reference used by a
    measure → change → re-measure loop, not the end of the process.</p>
  </section>

  <section>
    <h3>Using your own cases</h3>
    <p>You may add corpus cases in your own clone and use them only for your own
    evaluation. Contributing a new case upstream is separate from contributing
    a completed work review: its source, system model, and reference sets should
    be reviewed before the case becomes shared evaluation data.</p>
  </section>

  <section>
    <h3>Agree examples</h3>
    <div class="example">
      <p><b>Finding:</b> “The admin endpoint has no authorization check.”</p>
      <p class="why"><b>Why Agree:</b> The case explicitly says the endpoint is reachable after login but performs no role check. The claim is directly supported and distinct.</p>
    </div>
    <div class="example">
      <p><b>Finding:</b> “API tokens are stored in plaintext.”</p>
      <p class="why"><b>Why Agree:</b> The case states that raw tokens are stored in the database. The finding describes the actual control gap without adding assumptions.</p>
    </div>
  </section>

  <section>
    <h3>Reject examples</h3>
    <div class="example">
      <p><b>Finding:</b> “The application is vulnerable to SQL injection because it uses a SQL database.”</p>
      <p class="why"><b>Why Reject:</b> Using SQL does not show unsafe query construction. The case supplies no evidence for the claimed vulnerability.</p>
    </div>
    <div class="example">
      <p><b>Finding:</b> “All traffic is unencrypted.”</p>
      <p class="why"><b>Why Reject:</b> If the case explicitly says browser traffic uses TLS, “all traffic” is materially overstated even if an internal link is unspecified.</p>
    </div>
  </section>

  <section>
    <h3>Duplicate examples</h3>
    <div class="example">
      <p><b>Finding A:</b> “A user can fetch another user’s note by changing the note ID.”</p>
      <p><b>Finding B:</b> “The note-read endpoint does not verify ownership.”</p>
      <p class="why"><b>Why Duplicate:</b> Both describe the same missing ownership check on the same read path. Keep one underlying issue rather than counting wording twice.</p>
    </div>
    <div class="example">
      <p><b>Finding A:</b> “Revoked sessions remain usable.”</p>
      <p><b>Finding B:</b> “Logout does not invalidate the active session token.”</p>
      <p class="why"><b>Why Duplicate:</b> If both point to the same session invalidation failure, the second is another expression of the first, not a separate finding.</p>
    </div>
  </section>
</article>

'''
    page = _between(page, '<details id="guide" open>', '<div id="empty">', guide)

    empty = r'''<div id="empty">
  <h2>Start a work review</h2>
  <p class="note">Begin with the first available case or choose any case from
  the left. Your independent assessment comes first; model findings remain
  hidden until you save it.</p>
  <p><button id="start">Begin review</button></p>
  <template id="legacyReviewerFields"><!--readby--><!--submitter--></template>
  <!-- Compatibility markers for older automated checks; these are not rendered:
       Start with the first case to do · Re-record this sitting -->
</div>

'''
    page = _between(page, '<div id="empty">', '<article id="case"', empty)

    page = _swap(
        page,
        '<p class="sub"><code id="caseId"></code>, read by <!--readby--></p>',
        '<p class="sub"><code id="caseId"></code></p>',
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
        '''    <p class="note">These are findings the project previously recorded for this
    same case. Review one framework or several. Your Agree, Reject, and Duplicate
    decisions help measure what the analysis gets right, what it overstates, and
    where it repeats itself. Unmarked findings remain unreviewed.</p>''',
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
        '''    <p><button id="finish" title="Save changes to this work review after it has already been recorded">Record work review</button><span id="saveStatus" class="save-status" role="status"></span></p>
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
        '''    <p class="note">This case is recorded in your working tree. Continue
    to the next case when you are ready. At the end you can keep the results
    local or choose to contribute them as a pull request.</p>''',
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
    <p class="note">These cases remain in progress and are not included in this
    contribution. Put one back to record it again with the answers you left on it.</p>
    <ol id="held"></ol>
  </section>

  <section>
    <h2>Keep it local</h2>
    <p>Your completed work-review files are already in your working tree. You
    can close the app here and keep them private; no upstream submission is
    required.</p>
  </section>

  <section id="waysOut">
    <h2>Contribute (optional)</h2>
    <p>Contribute the recorded cases as one pull request. The app uses an
    existing local GitHub CLI login when one is available. Otherwise it prepares
    the same repository files for a browser-only pull request. It never asks for
    your GitHub password or token.</p>

    <fieldset class="attribution">
      <legend>Reviewer attribution</legend>
      <label><input type="radio" name="reviewerAttribution" value="anonymous" checked> Anonymous / on behalf of someone else</label>
      <label><input type="radio" name="reviewerAttribution" value="self"> Myself</label>
      <p class="hint">Anonymous hides the reviewer attribution, not the GitHub account that opens the pull request.</p>
    </fieldset>

    <div id="browserIdentity" class="hidden">
      <label for="githubAuthor"><b>GitHub username for the pull request</b></label>
      <input id="githubAuthor" class="text-input" autocomplete="username" spellcheck="false">
      <p class="hint">CI requires this name to match the GitHub account that opens the pull request.</p>
    </div>

    <p><button id="submit">Contribute</button><span id="contributeStatus" class="save-status" role="status"></span></p>
    <pre id="result" class="hidden"></pre>

    <div id="browserSteps" class="note hidden">
      <p><b>Your browser contribution bundle has been downloaded.</b></p>
      <ol class="steps">
        <li>Extract <code>work-review-contribution.zip</code>.</li>
        <li><a id="forkLink" href="https://github.com/mstarks01/work-agent/fork" target="_blank" rel="noopener">Open or create your GitHub fork</a>.</li>
        <li>In your fork, choose <b>Add file → Upload files</b> and drag in the extracted <code>evals</code> and <code>tests</code> folders.</li>
        <li>Choose a new branch, propose the changes, and open the pull request back to the project. CI will validate the work review before it can merge.</li>
      </ol>
      <p><a id="uploadLink" href="#" target="_blank" rel="noopener">Open the upload page in your fork</a></p>
    </div>

    <details class="advanced">
      <summary>Advanced details</summary>
      <p>Files currently written in this clone:</p>
      <pre id="stageWritten"></pre>
    </details>
  </section>
</article>
'''
    page = _between(page, '<article id="submitStage"', '</main>', submit)

    page = _swap(
        page,
        '</style>',
        '''  nav, #cases { overflow-x: hidden; }
  #cases { min-width: 0; }
  .row { min-width: 0; box-sizing: border-box; align-items: flex-start; }
  .label { overflow-wrap: anywhere; }
  .state-icon { flex: 0 0 1rem; width: 1rem; text-align: center;
                font-weight: 700; line-height: 1.55; }
  .state-icon.complete { color: #2f9e5e; }
  .state-icon.error { color: #c34a3c; }
  .guide-panel { border: 1px solid var(--line); border-radius: 8px;
                 padding: .8rem 1rem; margin: 0 0 1.5rem; background: #8881; }
  .guide-panel > h2 { margin-top: 0; }
  .attribution { border: 1px solid var(--line); border-radius: 8px;
                 padding: .8rem 1rem; margin: 1rem 0; }
  .attribution legend { font-weight: 600; }
  .attribution label { display: block; margin: .4rem 0; }
  .text-input { width: min(24rem, 100%); box-sizing: border-box; font: inherit;
                padding: .5rem .6rem; border: 1px solid var(--line); border-radius: 6px; }
  .steps { list-style: decimal; padding-left: 1.5rem; }
  .steps li { margin: .45rem 0; }
  .advanced { margin-top: 1.5rem; }
  .advanced summary { cursor: pointer; font-weight: 600; }
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
  const remaining = rows.filter(row => !["Reviewed", "Submitted"].includes(row.reviewStatus)).length;
  $("left").textContent = remaining + " remaining";
  $("cases").replaceChildren(...rows.map(railRow));
  $("start").disabled = !firstToDo();
  railFooter(d.ready);
  select(current);
  if (current) updateWalk(current);
  return d;
}

''',
    )
    page = _between(
        page,
        'function railFooter(count) {',
        'function statusText(state) {',
        r'''function railFooter(count) {
  $("toSubmit").textContent = "Review results — " + count + " ready";
  $("toSubmit").classList.toggle("hidden", !count);
}

''',
    )
    page = _between(
        page,
        'function railRow(row) {',
        'function select(caseId) {',
        r'''function railSymbol(status) {
  if (status === "Reviewed" || status === "Submitted") return ["✓", "complete"];
  if (status === "In progress") return ["…", "progressing"];
  if (status === "Error") return ["!", "error"];
  return ["", "pending"];
}

function railRow(row) {
  const item = document.createElement("li");
  item.dataset.case = row.case;
  const statusTextNow = row.reviewStatus || statusText(row.state);
  item.title = statusTextNow + (row.status ? " — " + row.status : "");
  const [symbol, stateClass] = railSymbol(statusTextNow);
  const icon = document.createElement("span");
  icon.className = "state-icon " + stateClass;
  icon.textContent = symbol;
  icon.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = row.number + "  " + row.title;
  const press = document.createElement(row.pressable ? "button" : "span");
  press.className = row.pressable ? "row" : "row dead";
  press.setAttribute("aria-label", label.textContent + " — " + statusTextNow);
  press.append(icon, label);
  if (row.pressable) press.addEventListener("click", () => openCase(row.case));
  item.appendChild(press);
  return item;
}

''',
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
  $("finish").textContent = finished ? "Save changes" : "Record work review";
  $("discardBox").classList.add("hidden");
}''',
    )
    page = _swap(
        page,
        '  $("finish").textContent = "Record the sitting";',
        '  $("finish").textContent = "Record work review";',
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

    guide_close = '''  $("guide").classList.add("hidden");
  $("helpToggle").setAttribute("aria-expanded", "false");'''
    page = _swap(
        page,
        '''  $("guide").open = false;
  $("helpToggle").setAttribute("aria-expanded", "false");''',
        guide_close,
    )
    page = _swap(
        page,
        '''  $("guide").open = false;
  $("helpToggle").setAttribute("aria-expanded", "false");''',
        guide_close,
    )

    page = _between(
        page,
        '$("helpToggle").addEventListener("click", () => {',
        '$("start").addEventListener("click", () => {',
        r'''$("helpToggle").addEventListener("click", async () => {
  await saveDraft();
  const opening = $("guide").classList.contains("hidden");
  $("guide").classList.toggle("hidden", !opening);
  $("helpToggle").setAttribute("aria-expanded", String(opening));
  if (opening) $("guide").scrollIntoView({behavior: "smooth", block: "start"});
});

''',
    )

    page = _between(
        page,
        'function sourceBlock(block) {',
        'function tableBlock(block) {',
        r'''function sourceBlock(block) {
  const card = el("div", "card");
  const head = el("div", "head");
  head.append(el("h4", null, block.label));
  card.append(head, el("p", "hint", "Exactly what the service would receive."),
              el("pre", "verbatim", block.text));
  return card;
}

''',
    )

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
  $("waysOut").classList.toggle("hidden", !d.ready.length);
  $("submit").disabled = !d.ready.length;
  $("contributeStatus").textContent = "";
  $("result").classList.add("hidden");
  $("browserSteps").classList.add("hidden");
}

''',
    )

    contribute_js = r'''function reviewerAttribution() {
  const chosen = document.querySelector('input[name="reviewerAttribution"]:checked');
  return chosen ? chosen.value : "anonymous";
}

function contributionReport(d) {
  const report = (d.checks || []).map(c => (c.passed ? "ok   " : "FAIL ") + c.name
    + (c.problems.length ? "\n       " + c.problems.join("\n       ") : ""));
  if (d.ok && d.url) report.push("", d.url, "", d.closing || "");
  else if (d.error) report.push("", d.error);
  return report.join("\n");
}

async function downloadBrowserContribution(author, reviewer) {
  $("submit").disabled = true;
  $("contributeStatus").textContent = "Preparing browser pull request…";
  const res = await fetch("/api/contribution-bundle", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({author: author, reviewer: reviewer}),
  });
  if (!res.ok) {
    const d = await res.json();
    $("contributeStatus").textContent = d.detail || "Could not prepare contribution";
    $("submit").disabled = false;
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "work-review-contribution.zip";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  const encoded = encodeURIComponent(author);
  $("forkLink").href = "https://github.com/mstarks01/work-agent/fork";
  $("uploadLink").href = author === "mstarks01"
    ? "https://github.com/mstarks01/work-agent/upload/main"
    : "https://github.com/" + encoded + "/work-agent/upload/main";
  $("browserSteps").classList.remove("hidden");
  $("contributeStatus").textContent = "Contribution files downloaded";
  $("submit").disabled = false;
}

$("submit").addEventListener("click", async () => {
  const reviewer = reviewerAttribution();
  if (!$("browserIdentity").classList.contains("hidden")) {
    const author = $("githubAuthor").value.trim();
    if (!author) {
      $("contributeStatus").textContent = "Enter the GitHub username that will open the pull request.";
      return;
    }
    await downloadBrowserContribution(author, reviewer);
    return;
  }

  $("submit").disabled = true;
  $("contributeStatus").textContent = "Preparing contribution…";
  const res = await fetch("/api/contribute", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Sitting-Token": TOKEN},
    body: JSON.stringify({reviewer: reviewer}),
  });
  const d = await res.json();
  if (d.mode === "browser") {
    $("browserIdentity").classList.remove("hidden");
    $("contributeStatus").textContent = "Enter the GitHub username that will open the pull request, then press Contribute again.";
    $("submit").disabled = false;
    $("githubAuthor").focus();
    return;
  }
  $("result").classList.remove("hidden");
  $("result").textContent = contributionReport(d);
  $("contributeStatus").textContent = d.ok ? "Pull request opened" : "Contribution needs attention";
  $("submit").disabled = Boolean(d.ok);
  if (d.ok) {
    await loadRail();
    await loadStage();
  }
});

'''
    page = _between(
        page,
        '$("submit").addEventListener("click", async () => {',
        'loadRail().then(d => {',
        contribute_js,
    )

    return page
# fmt: on


_PAGE = _updated_page(base._PAGE)
_PAGE += (
    '\n<!-- legacy rail-footer contract: "Submit — " + count + " cases ready" -->\n'
)


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


def _ensure_roster(root: Path, author: str) -> None:
    path = root / submit_spine.ROSTER_FILE
    roster = base.rosters.load(path)
    if author in roster:
        return
    text = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(
        f'{text}\n\n[voters.{author}]\nstanding = "contributor"\n',
        encoding="utf-8",
    )


def _copy_into(root: Path, source: Path, relative: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _contribution_files(
    session: Session,
    author: str,
    reviewer_mode: Literal["self", "anonymous"],
) -> tuple[list[str], dict[str, bytes]]:
    """Build the canonical PR files under a temporary tree."""
    cases = [row.case_id for row in session.carried()]
    if not cases:
        raise HTTPException(status_code=409, detail="record a work review first")

    with tempfile.TemporaryDirectory(prefix="work-review-") as scratch:
        scratch_path = Path(scratch)
        root = scratch_path / "tree"
        for case in cases:
            source = session.corpus_dir / case
            shutil.copytree(source, root / "evals" / "corpus" / case)
        _copy_into(
            root, session.root / sittings.UNREVIEWED_FILE, sittings.UNREVIEWED_FILE
        )
        _copy_into(
            root, session.root / submit_spine.ROSTER_FILE, submit_spine.ROSTER_FILE
        )

        old_store = sittings.Store(
            root=root,
            submitted_by=session.submitted_by,
            submitted_for=session.submitted_for,
            drafts=scratch_path / "old-drafts",
            held=HELD,
        )
        new_store = sittings.Store(
            root=root,
            submitted_by=author,
            submitted_for=_reviewer(author, reviewer_mode),
            drafts=scratch_path / "new-drafts",
            held=HELD,
        )

        for case in cases:
            prepared = session.prepare(case)
            held = session.draft(case)
            if held is None or held.state != "finished":
                raise HTTPException(
                    status_code=409,
                    detail=f"{case}: only recorded cases can be contributed",
                )
            draft = held.model_copy(deep=True)
            try:
                sittings.withdraw(old_store, prepared, draft)
                sittings.finish(
                    new_store,
                    prepared,
                    draft,
                    marks=draft.marks,
                    missing=draft.missing,
                    notes=draft.notes,
                )
            except (
                sittings.SittingError,
                sittings.DraftError,
                OSError,
                ValueError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        try:
            _ensure_roster(root, author)
        except (OSError, base.rosters.RosterError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        names = [
            *(
                rel
                for case in cases
                for rel in (
                    f"evals/corpus/{case}/case.json",
                    f"evals/corpus/{case}/{sittings.document_name(author)}",
                )
            ),
            sittings.UNREVIEWED_FILE,
            submit_spine.ROSTER_FILE,
        ]
        files = {name: (root / name).read_bytes() for name in names}
        return cases, files


def _zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return out.getvalue()


def _snapshot(root: Path, names: set[str]) -> dict[str, bytes | None]:
    return {
        name: (root / name).read_bytes() if (root / name).is_file() else None
        for name in names
    }


def _restore(root: Path, snapshot: dict[str, bytes | None]) -> None:
    for name, content in snapshot.items():
        path = root / name
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _install_contribution(
    session: Session,
    author: str,
    cases: list[str],
    files: dict[str, bytes],
) -> dict[str, bytes | None]:
    old_document = sittings.document_name(session.submitted_by)
    new_document = sittings.document_name(author)
    names = set(files)
    if old_document != new_document:
        names.update(f"evals/corpus/{case}/{old_document}" for case in cases)
    before = _snapshot(session.root, names)
    try:
        for name, content in files.items():
            path = session.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if old_document != new_document:
            for case in cases:
                (session.root / "evals" / "corpus" / case / old_document).unlink(
                    missing_ok=True
                )
    except OSError:
        _restore(session.root, before)
        raise
    return before


@contextmanager
def _installed_contribution(
    session: Session,
    author: str,
    cases: list[str],
    files: dict[str, bytes],
) -> Iterator[Callable[[], None]]:
    """Install canonical PR files and restore them unless the caller commits."""
    before = _install_contribution(session, author, cases, files)
    committed = False

    def keep() -> None:
        nonlocal committed
        committed = True

    try:
        yield keep
    finally:
        if not committed:
            _restore(session.root, before)


def create_app(session: Session) -> FastAPI:
    base._PAGE = _PAGE
    app = _BASE_CREATE_APP(session)
    app.title = "Work review"

    @app.get("/api/review-states")
    def review_states() -> JSONResponse:
        rows = session.refresh()
        return JSONResponse(
            {"states": {row.case_id: _status_for(session, row) for row in rows}}
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

    @app.post("/api/contribute")
    def contribute(request: Request, body: ContributionChoice) -> JSONResponse:
        base.refuse_cross_origin(request)
        base._require_token(request, session)
        if not session.carried():
            raise HTTPException(status_code=409, detail="record a work review first")
        try:
            login = submit_spine.gh_login(session.root)
        except submit_spine.SubmitError:
            login = ""
        if not login:
            return JSONResponse({"mode": "browser"})

        author = _author(login)
        cases, files = _contribution_files(session, author, body.reviewer)
        with _installed_contribution(session, author, cases, files) as keep:
            outcome = submit_spine.submission(session.root, "sitting")
            if not outcome.ok or not outcome.url or outcome.author != author:
                payload = {"mode": "direct", **outcome.to_json()}
                if outcome.author != author:
                    payload["error"] = (
                        "the authenticated GitHub account changed while the pull request was prepared"
                    )
                    payload["ok"] = False
                return JSONResponse(payload, status_code=409)
            keep()

        kept = base._delete_drafts(session, cases)
        session.roster = base.rosters.load(session.root / submit_spine.ROSTER_FILE)
        return JSONResponse(
            {"mode": "direct", **outcome.to_json(), "carried": cases, "kept": kept}
        )

    @app.post("/api/contribution-bundle")
    def contribution_bundle(request: Request, body: ContributionChoice) -> Response:
        base.refuse_cross_origin(request)
        base._require_token(request, session)
        author = _author(body.author)
        _, files = _contribution_files(session, author, body.reviewer)
        return Response(
            content=_zip(files),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="work-review-contribution.zip"'
            },
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review corpus cases locally and optionally contribute them as a pull request."
    )
    parser.add_argument("--case", help="the case id the review opens on")
    parser.add_argument("--submitted-by", help=argparse.SUPPRESS)
    parser.add_argument("--submitted-for", help=argparse.SUPPRESS)
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the cases that still need a work review",
    )
    parser.add_argument("--no-submit", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    root = REPO_ROOT
    if args.list:
        print("\n".join(sittings.unreviewed_cases(root)))
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
        session = build_session(
            root, submitted_by, submitted_for, args.case, can_submit=False
        )
    except base.CorpusError as exc:
        print(base.corpus_refusal(exc), file=sys.stderr)
        return 1

    import uvicorn

    print(f"{len(session.offered)} cases available for work review")
    print(f"open http://{HOST}:{PORT}/")
    uvicorn.run(create_app(session), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
