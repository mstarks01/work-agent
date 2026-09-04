"""The whole corpus as one file a reader opens in a browser.

``webapp/sitting.py`` is the sitting a maintainer holds, with a local server, a
clone and a command line. This writes the same sitting as a single standalone
HTML file, so a reader who has none of those can still do the one thing no lint
can do: read a case, and say whether its reference sets describe what could
actually go wrong.

The browser is the runtime, which is what makes this independent of the
operating system. Nothing is installed, nothing is signed, no per-platform build
exists, and the page opens from ``file://``. The payload is the whole corpus, at
about 440 KB of sources, models and reference sets. That is one ordinary email
attachment, so there is no reason to split it per case and every reason not to:
the rail, and with it the reader's own choice of what to sit, exists only over
the whole set.

It lays out the same blocks the other two surfaces do.
``evals.build_review_docs`` describes a case as blocks precisely so that a page
and a document cannot describe two different systems. This is the third renderer
over that description rather than a third description.

The rules it carries, in the words the app carries them:

* The own list comes first, per case. A case's recorded sets are not in the
  page's reach until that case's own list is written, and the list has to say at
  least ``MIN_OWN_LIST`` characters. This surface cannot enforce that the way
  the app does, because a static file holds its own payload, so a reader who
  opens the source can read the sets. #373 already ruled what the gate is for:
  it protects the evidence in the filled document rather than the reader. The
  import re-checks the same rule against the same constant.
* A case takes one own list. Once written, that case's list is fixed for the
  session. A list typed after the sets opened would be evidence of an order that
  did not happen, and days of reading is exactly when that becomes tempting.
* Marks, the missing list and the notes stay editable to the last minute.
  Nothing is a record until the operator imports it, so a reader who changes
  their mind on day four simply changes it.
* The page counts the own list and says nothing about the count, because a
  running total sets the length somebody writes to.

The envelope is the save file. Download it to stop, and load it to carry on.
That is one format for two jobs, and it means the page depends on no browser
storage: ``localStorage`` has no dependable origin on ``file://``.

Run ``uv run python webapp/offline_sitting.py --submitted-for anonymous``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.envelope import VERSION
from evals.harness.reference import (
    ANONYMOUS,
    CorpusError,
    corpus_refusal,
    is_submitted_for,
)
from webapp.page import script_json

REPO_ROOT = Path(__file__).resolve().parents[1]


def payload(corpus_dir: Path, submitted_by: str, submitted_for: str) -> dict:
    """Everything the page needs, taken from the corpus once.

    The digests are taken here, so they say what each required file held when
    the page was built — which is what the import compares against, and what
    tells the operator that a reader spent a week on words that have since
    changed.
    """
    cases = []
    for case in sittings.load_corpus(corpus_dir):
        case_dir = corpus_dir / case.meta.id
        prepared = sittings.prepare(case_dir)
        cases.append(
            {
                "case": prepared.case_id,
                "number": case.meta.id.split("-")[0],
                "title": prepared.title,
                "part_one": prepared.part_one_blocks,
                "part_two": prepared.part_two_blocks,
                "targets": [
                    {"fingerprint": target.fingerprint, "claims": list(target.claims)}
                    for target in prepared.mark_targets
                ],
                "digests": sittings.digests(case_dir, prepared.files),
            }
        )
    return {
        "envelope": VERSION,
        "submitted_by": submitted_by,
        "submitted_for": submitted_for,
        "generated": datetime.now(UTC).date().isoformat(),
        "min_own_list": sittings.MIN_OWN_LIST,
        "marks": list(sittings.MARKS),
        "cases": cases,
    }


def build(corpus_dir: Path, submitted_by: str, submitted_for: str) -> str:
    """One standalone page, payload and all."""
    return _PAGE.replace(
        "__PAYLOAD__", script_json(payload(corpus_dir, submitted_by, submitted_for))
    )


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Case Sitting</title>
<style>
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1a1a18;--dim:#5c5c56;
--line:#dcdcd6;--card:#fff;--accent:#3a5a8c;--warn:#8a4b16}
@media (prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#e8e8e4;--dim:#9a9a94;
--line:#33333a;--card:#1e1e24;--accent:#8fb0e0;--warn:#d69a5c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
#shell{display:flex;min-height:100vh}
#rail{width:270px;flex:none;border-right:1px solid var(--line);padding:14px;
position:sticky;top:0;height:100vh;overflow:auto}
#stage{flex:1;padding:26px 34px;max-width:900px}
h1{font-size:18px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px}
h3{font-size:14px;margin:18px 0 6px;color:var(--dim)}
.sub{color:var(--dim);font-size:13px;margin:0 0 14px}
.row{display:block;width:100%;text-align:left;background:none;border:0;
padding:7px 8px;border-radius:6px;color:var(--fg);font:inherit;cursor:pointer}
.row:hover{background:var(--card)}
.row[aria-current=true]{background:var(--card);outline:1px solid var(--line)}
.row .t{font-size:13px}
.row .s{font-size:11px;color:var(--dim)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
margin-right:7px;background:var(--line)}
.dot.draft{background:var(--warn)}.dot.finished{background:var(--accent)}
blockquote{margin:0;padding:10px 14px;border-left:3px solid var(--line);
background:var(--card);white-space:pre-wrap;font-size:13px;overflow-x:auto}
table{border-collapse:collapse;margin:8px 0;font-size:13px;display:block;
overflow-x:auto}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left}
textarea{width:100%;min-height:150px;font:inherit;padding:9px;
border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--fg)}
textarea[readonly]{opacity:.72}
button{font:inherit;padding:7px 13px;border-radius:6px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer}
button.go{background:var(--accent);color:#fff;border-color:transparent}
button:disabled{opacity:.45;cursor:not-allowed}
.rec{border:1px solid var(--line);border-radius:8px;padding:11px 13px;
margin:10px 0;background:var(--card)}
.rec .meta{color:var(--dim);font-size:12px;margin-top:5px}
.marks{margin-top:8px;display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
.marks label{cursor:pointer}
.bar{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);
padding:11px 0;margin-top:26px;display:flex;gap:10px;flex-wrap:wrap;
align-items:center}
.hint{color:var(--dim);font-size:13px}
</style></head><body>
<div id="shell">
  <nav id="rail"><h1>Cases</h1><p class="sub" id="who"></p><div id="rows"></div></nav>
  <main id="stage"></main>
</div>
<script>
const DATA = __PAYLOAD__;
const BY_ID = Object.fromEntries(DATA.cases.map(c => [c.case, c]));
// One entry per case the reader has written a list for. The own list creates
// it, exactly as opening a case creates nothing in the app.
const answers = {};
let current = DATA.cases[0].case;

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  // Every word of case prose lands through textContent, so a sentence that
  // spells a tag arrives as those characters.
  if (text !== undefined) n.textContent = text;
  return n;
};
const typed = list => list.reduce((n, s) => n + s.trim().length, 0);
const written = id => answers[id] && typed(answers[id].own_list) >= DATA.min_own_list;
const marked = id => answers[id] ? Object.keys(answers[id].marks).length : 0;

function state(id) {
  if (!answers[id]) return ["", "to do"];
  if (marked(id) >= BY_ID[id].targets.length && BY_ID[id].targets.length)
    return ["finished", "every record marked"];
  return ["draft", "in progress"];
}

function rail() {
  const box = document.getElementById("rows");
  box.textContent = "";
  for (const c of DATA.cases) {
    const [cls, label] = state(c.case);
    const b = el("button", "row");
    b.setAttribute("aria-current", c.case === current);
    const t = el("div", "t");
    t.appendChild(el("span", "dot " + cls));
    t.appendChild(el("span", null, c.number + "  " + c.title));
    b.appendChild(t);
    b.appendChild(el("div", "s", label));
    b.onclick = () => { current = c.case; draw(); };
    box.appendChild(b);
  }
  document.getElementById("who").textContent =
    "read by " + DATA.submitted_for + ", submitted by " + DATA.submitted_by;
}

function blocks(into, list) {
  for (const b of list) {
    if (b.kind === "source") {
      into.appendChild(el("h3", null, b.label + " (" + b.source_kind + ")"));
      into.appendChild(el("blockquote", null, b.text));
    } else if (b.kind === "table") {
      into.appendChild(el("h3", null, b.caption));
      const t = el("table");
      const head = el("tr");
      for (const h of b.headers) head.appendChild(el("th", null, h));
      t.appendChild(head);
      for (const row of b.rows) {
        const tr = el("tr");
        for (const cell of row) tr.appendChild(el("td", null, cell));
        t.appendChild(tr);
      }
      into.appendChild(t);
    } else {
      into.appendChild(el("h3", null, b.caption + (b.hint ? " — " + b.hint : "")));
      const ul = el("ul");
      for (const item of b.items)
        ul.appendChild(el("li", null, item.term + " — " + item.text));
      into.appendChild(ul);
    }
  }
}

function ownList(into, c) {
  into.appendChild(el("h2", null, "Your list, written first"));
  into.appendChild(el("p", "hint",
    "What could go wrong? An attack, a missing control, a question the text " +
    "does not answer. One per line. The recorded sets stay closed until this " +
    "says something, and once you open them this list is fixed — a list " +
    "written afterwards would be evidence of an order that did not happen."));
  const box = el("textarea");
  const locked = written(c.case);
  box.value = answers[c.case] ? answers[c.case].own_list.join("\\n") : "";
  box.readOnly = locked;
  into.appendChild(box);
  if (locked) {
    into.appendChild(el("p", "hint", "Written. This case's sets are open."));
    return;
  }
  const go = el("button", "go", "Open the recorded sets");
  go.onclick = () => {
    const lines = box.value.split("\\n").map(s => s.trim()).filter(Boolean);
    if (typed(lines) < DATA.min_own_list) {
      alert("Write what you think could go wrong first. The sitting measures " +
            "your list against theirs.");
      return;
    }
    answers[c.case] = {own_list: lines, marks: {}, missing: [], notes: "",
                       opened_digests: c.digests};
    draw();
  };
  const p = el("p");
  p.appendChild(go);
  into.appendChild(p);
}

function records(into, c) {
  const held = answers[c.case];
  let part = 2;
  for (const [name, set] of Object.entries(c.part_two)) {
    into.appendChild(el("h2", null, "Part " + part++ + " — " + set.heading));
    into.appendChild(el("p", "hint", set.question));
    for (const group of set.groups) {
      into.appendChild(el("h3", null, group.name));
      for (const rec of group.records) {
        const target = c.targets.find(t => t.claims.includes(rec.title));
        const card = el("div", "rec");
        card.appendChild(el("div", null,
          rec.label + ". " + (rec.identifier ? rec.identifier + " — " : "") + rec.title));
        for (const row of rec.fields)
          card.appendChild(el("div", "meta",
            row.map(f => f.label + ": " + f.values.join(", ")).join(" · ")));
        if (target) {
          const marks = el("div", "marks");
          for (const mark of DATA.marks) {
            const lab = el("label");
            const radio = el("input");
            radio.type = "radio";
            radio.name = "m-" + target.fingerprint;
            radio.checked = held.marks[target.fingerprint] === mark;
            radio.onchange = () => { held.marks[target.fingerprint] = mark; rail(); };
            lab.appendChild(radio);
            lab.appendChild(el("span", null, " " + mark));
            marks.appendChild(lab);
          }
          card.appendChild(marks);
        }
        into.appendChild(card);
      }
    }
  }
  into.appendChild(el("h2", null, "On your list and on neither of theirs"));
  into.appendChild(el("p", "hint",
    "The point of the sitting. One per line, and say which set you expected it in."));
  const miss = el("textarea");
  miss.value = held.missing.join("\\n");
  miss.oninput = () =>
    held.missing = miss.value.split("\\n").map(s => s.trim()).filter(Boolean);
  into.appendChild(miss);
  into.appendChild(el("h2", null, "Notes"));
  into.appendChild(el("p", "hint",
    "Counts, anything you would change, and any correction to a recorded set."));
  const notes = el("textarea");
  notes.value = held.notes;
  notes.oninput = () => held.notes = notes.value;
  into.appendChild(notes);
}

function bar(into) {
  const done = Object.keys(answers).length;
  const b = el("div", "bar");
  const save = el("button", "go", "Download my answers");
  save.disabled = !done;
  save.onclick = download;
  b.appendChild(save);
  const load = el("button", null, "Load a saved file");
  load.onclick = () => document.getElementById("file").click();
  b.appendChild(load);
  b.appendChild(el("span", "hint", done + " case(s) started. Download to stop " +
    "and load the same file to carry on — nothing is sent from this page."));
  into.appendChild(b);
}

function draw() {
  rail();
  const c = BY_ID[current];
  const stage = document.getElementById("stage");
  stage.textContent = "";
  stage.appendChild(el("h1", null, c.number + " — " + c.title));
  stage.appendChild(el("p", "sub",
    "Read Part 1 and write your own list before you open the recorded sets. " +
    "Roughly an hour."));
  stage.appendChild(el("h2", null, "Part 1 — the system"));
  blocks(stage, c.part_one);
  ownList(stage, c);
  if (written(c.case)) records(stage, c);
  bar(stage);
  window.scrollTo(0, 0);
}

function envelope() {
  const cases = {};
  for (const [id, a] of Object.entries(answers))
    cases[id] = {own_list: a.own_list, marks: a.marks, missing: a.missing,
                 notes: a.notes, opened_digests: a.opened_digests};
  return {envelope: DATA.envelope, submitted_by: DATA.submitted_by,
          submitted_for: DATA.submitted_for, generated: DATA.generated,
          cases: cases};
}

function download() {
  const text = JSON.stringify(envelope(), null, 2);
  const url = URL.createObjectURL(new Blob([text], {type: "application/json"}));
  const a = el("a");
  a.href = url;
  a.download = "sitting-" + DATA.submitted_by + ".json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function restore(text) {
  let held;
  try {
    held = JSON.parse(text);
  } catch (e) {
    alert("That file is not readable JSON.");
    return;
  }
  if (!held || held.envelope !== DATA.envelope) {
    alert("That file was written for a different version of this page.");
    return;
  }
  for (const [id, a] of Object.entries(held.cases || {})) {
    if (!BY_ID[id]) continue;
    answers[id] = {own_list: a.own_list || [], marks: a.marks || {},
                   missing: a.missing || [], notes: a.notes || "",
                   opened_digests: a.opened_digests || BY_ID[id].digests};
  }
  draw();
}

const picker = el("input");
picker.type = "file";
picker.id = "file";
picker.accept = "application/json,.json";
picker.style.display = "none";
picker.onchange = () => {
  const f = picker.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => restore(r.result);
  r.readAsText(f);
  picker.value = "";
};
document.body.appendChild(picker);
draw();
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submitted-by",
        help="the GitHub login that will open the pull request. Read from the"
        " authenticated `gh` when omitted.",
    )
    parser.add_argument(
        "--submitted-for",
        help="who will read: a GitHub login, or"
        f" {ANONYMOUS!r} for a reader who takes part on no name of their own."
        " Defaults to --submitted-by.",
    )
    parser.add_argument(
        "--out",
        default="sitting.html",
        help="where to write the page. Send this file to the reader.",
    )
    args = parser.parse_args(argv)

    try:
        login = submit_spine.gh_login(REPO_ROOT)
    except submit_spine.SubmitError:
        login = ""
    submitted_by = args.submitted_by or login
    if not submitted_by:
        print(
            "cannot read your gh login, so pass --submitted-by with the login"
            " that will carry the sitting",
            file=sys.stderr,
        )
        return 1
    submitted_for = args.submitted_for or submitted_by
    if not is_submitted_for(submitted_for):
        print(
            f"{submitted_for!r} is not a GitHub login and is not"
            f" {ANONYMOUS!r}; --submitted-for takes one of those two",
            file=sys.stderr,
        )
        return 1

    # Built before the file is opened, for the reason `webapp/sitting.py`
    # catches the same error: a corpus that does not parse is the operator's
    # to fix, and it names the case rather than a pydantic frame.
    try:
        page = build(sittings.CORPUS_DIR, submitted_by, submitted_for)
    except CorpusError as exc:
        print(corpus_refusal(exc), file=sys.stderr)
        return 1

    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    size = out.stat().st_size
    print(f"wrote {out} ({size // 1024} KB)")
    print(f"read by {submitted_for}, submitted by {submitted_by}")
    print(
        "\nSend that one file. The reader opens it in any browser, walks as"
        "\nmany cases as they choose, and sends back one JSON file. Then:"
        f"\n\n  python -m evals.harness.run sitting-import sitting-{submitted_by}.json"
        "\n  python -m evals.harness.run submit sitting"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
