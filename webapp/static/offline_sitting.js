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
  const ready = finished();
  const publish = el("button", "go", "Open the pull request on GitHub");
  publish.disabled = !ready.length;
  publish.title = ready.length
    ? ready.length + " case(s) ready to publish"
    : "Mark every record in a case before you publish it";
  publish.onclick = publishToGitHub;
  if (window.crypto && window.crypto.subtle) b.appendChild(publish);
  b.appendChild(el("span", "hint", done + " case(s) started, " + ready.length +
    " ready to publish. Download to stop and load the same file to carry on — " +
    "nothing is sent from this page."));
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

// Every case whose records all carry a mark. A submission answers every
// finding it read, so a case still in progress may be saved and may not be
// published — the same split the app makes between a draft and the stage.
function finished() {
  return Object.keys(answers).filter(id => state(id)[0] === "finished");
}

function envelope(only) {
  const cases = {};
  for (const [id, a] of Object.entries(answers)) {
    if (only && !only.includes(id)) continue;
    cases[id] = {own_list: a.own_list, marks: a.marks, missing: a.missing,
                 notes: a.notes, opened_digests: a.opened_digests};
  }
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

// The bytes a submission is named by, and the name they give it. Both must
// match what `evals.harness.envelope` computes, or the file this page sends
// lands under a name CI refuses. `tests/test_offline_sitting.py` drives this
// block under node against the Python it has to agree with.
function canonical(env) {
  return JSON.stringify(env, null, 2) + "\n";
}

async function submissionName(env) {
  const bytes = new TextEncoder().encode(canonical(env));
  const hashed = await window.crypto.subtle.digest("SHA-256", bytes);
  const digest = Array.from(new Uint8Array(hashed))
    .map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 12);
  return "review-" + env.generated + "-" + env.submitted_by + "-" + digest + ".json";
}

async function contributionUrl(env) {
  const query = new URLSearchParams({
    filename: DATA.submissions_dir + "/" + await submissionName(env),
    value: canonical(env),
  });
  return "https://github.com/" + DATA.repo + "/new/" + DATA.branch + "?" + query;
}

// Opened from a click handler rather than from the promise, because a popup
// blocker refuses a window a page opens after an await.
async function publishToGitHub() {
  const ready = finished();
  if (!ready.length) return;
  const opened = window.open("", "_blank", "noopener");
  const url = await contributionUrl(envelope(ready));
  if (opened) opened.location = url;
  else window.location = url;
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
