const $ = id => document.getElementById(id);
const lines = id => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);

// The case on the stage, the rail as the server last described it, the
// pending save, and which way a contribution leaves.
let current = null;
let rows = [];
let queued = 0;
let contributionMode = "browser";

function hide(id) { $(id).classList.add("hidden"); }
function show(id) { $(id).classList.remove("hidden"); }

const headers = {"Content-Type": "application/json", "X-Sitting-Token": TOKEN};

function post(path, body) {
  return fetch(path, {method: "POST", headers: headers, body: JSON.stringify(body)});
}

// Every field in part two saves on a short delay, and the walk flushes before
// it moves, so what leaves the stage is already on disk.
function queueSave() {
  clearTimeout(queued);
  queued = setTimeout(saveDraft, 600);
}

async function saveDraft() {
  clearTimeout(queued);
  if (!current || $("two").classList.contains("hidden")) return;
  await post("/api/draft", {
    case: current, marks: marksNow(), missing: lines("missing"),
    notes: $("notes").value,
  });
}

// --- The rail --------------------------------------------------------------

// The state and the label both come off `/api/review-states`. The page keys
// on the state — the vocabulary the rail already speaks — and spells no label
// of its own, so a label the server changes changes nothing here.
const DONE = ["finished", "signed"];

function railSymbol(state) {
  if (DONE.includes(state)) return ["✓", "complete"];
  if (state === "draft") return ["…", "progressing"];
  if (state === "error") return ["!", "error"];
  return ["", "pending"];
}

function railRow(row) {
  const item = document.createElement("li");
  item.dataset.case = row.case;
  const status = row.review.label;
  item.title = status + (row.status ? " — " + row.status : "");
  const [symbol, cls] = railSymbol(row.review.state);
  const icon = el("span", "state-icon " + cls, symbol);
  icon.setAttribute("aria-hidden", "true");
  const label = el("span", "label", row.number + "  " + row.title);
  const readonly = row.state === "signed";
  const clickable = row.pressable || readonly;
  const press = document.createElement(clickable ? "button" : "span");
  press.className = clickable ? "row" : "row dead";
  press.setAttribute("aria-label",
    label.textContent + " — " + status + (readonly ? " — read only" : ""));
  press.append(icon, label);
  if (row.pressable) press.addEventListener("click", () => openCase(row.case));
  else if (readonly) press.addEventListener("click", () => openReadOnly(row.case));
  item.append(press);
  return item;
}

// A refusal is said where the count would go, rather than left as a page
// that reads "reading the cases…" forever.
async function getJson(path) {
  const res = await fetch(path);
  const d = await res.json();
  if (!res.ok) throw new Error(d.detail || path + " refused");
  return d;
}

async function loadRail() {
  let d, states;
  try {
    [d, states] = await Promise.all([getJson("/api/rail"), getJson("/api/review-states")]);
  } catch (err) {
    $("left").textContent = err.message;
    return {};
  }
  rows = d.cases.map(r => ({...r, review: states.states[r.case]}));
  const remaining = rows.filter(r => !DONE.includes(r.review.state)).length;
  $("left").textContent = remaining + " remaining";
  $("cases").replaceChildren(...rows.map(railRow));
  $("start").disabled = !firstToDo();
  $("toSubmit").textContent = "Review results — " + d.ready + " ready";
  $("toSubmit").classList.toggle("hidden", !d.ready);
  select(current);
  return d;
}

function select(id) {
  for (const item of $("cases").children) {
    item.classList.toggle("current", item.dataset.case === id);
  }
}

// The cases the walk steps over: the rail's pressable rows, in rail order,
// which is the same list the server resolves a request against.
function walkable() {
  return rows.filter(row => row.pressable);
}

function firstToDo() {
  return rows.find(r => r.pressable && r.review.state === "todo");
}

function updateWalk(id) {
  const list = walkable();
  const i = list.findIndex(r => r.case === id);
  const text = i >= 0 ? "Case " + (i + 1) + " of " + list.length : "";
  $("progressTop").textContent = text;
  $("progressBottom").textContent = text;
  const disabled = i <= 0;
  $("previous").disabled = disabled;
  $("previousBottom").disabled = disabled;
}

function step(delta) {
  const at = rows.findIndex(r => r.case === current);
  for (let i = at + delta; i >= 0 && i < rows.length; i += delta) {
    if (rows[i].pressable) {
      openCase(rows[i].case);
      return;
    }
  }
  if (delta > 0) openSubmit();
}

function closeGuide() {
  hide("guide");
  $("helpToggle").setAttribute("aria-expanded", "false");
}

// --- The stage ---------------------------------------------------------------

// The gate re-arms per case, so a case arriving on the stage arrives blind.
function blank() {
  $("partOne").textContent = "loading…";
  $("waiting").classList.add("hidden");
  $("partTwo").replaceChildren();
  $("frameworkPicker").replaceChildren();
  for (const id of ["own", "missing", "notes"]) $(id).value = "";
  $("summary").textContent = "";
  $("markCounts").textContent = "";
  $("finish").disabled = true;
  $("finishHint").textContent = "";
  $("saveStatus").textContent = "";
  $("own").readOnly = false;
  $("lock").disabled = true;
  $("finish").textContent = "Record review";
  show("placeholder");
  for (const id of ["two", "done"]) hide(id);
}

// A case a merged sitting covers in part. The sets it answered arrive marked,
// and the reader is told which set is theirs, so a locked list and a page of
// marks they did not make are not a surprise.
function waiting(d) {
  const partial = d.covered.length && d.waiting.length;
  $("waiting").classList.toggle("hidden", !partial);
  $("waiting").textContent = partial
    ? "A merged review already covers " + d.covered.join(", ") + ". The list and"
      + " those marks ride forward locked; only " + d.waiting.join(", ")
      + " waits for your marks."
    : "";
}

// The text moved under a read in progress. It names files and no more.
function warn(files) {
  $("moved").classList.toggle("hidden", !files.length);
  $("moved").textContent = files.length
    ? "These files changed since you opened this case: " + files.join(", ")
      + ". Read them again and decide whether your answers still apply."
    : "";
}

async function openCase(id) {
  await saveDraft();
  current = id;
  select(id);
  blank();
  closeGuide();
  hide("empty");
  hide("submitStage");
  hide("readOnly");
  show("case");
  updateWalk(id);
  const res = await fetch("/api/part-one?case=" + encodeURIComponent(id));
  const d = await res.json();
  // A second click while this one was in flight owns the stage now.
  if (current !== id) return;
  if (!res.ok) { $("partOne").textContent = d.detail; return; }
  $("caseTitle").textContent = d.title;
  $("caseId").textContent = d.case;
  layout($("partOne"), d.blocks);
  warn(d.moved);
  waiting(d);
  // A case takes one own list. Where a draft holds one — the reader's own, or
  // the one a merged sitting carries forward — the box comes back filled and
  // locked and the sets open.
  if (d.own_list === null) return;
  $("own").value = d.own_list.join("\n");
  $("missing").value = d.missing.join("\n");
  $("notes").value = d.notes;
  lock();
  finishedNow(d.state === "finished");
  await showSets(id);
  if (current === id) setMarks(d.marks);
}

async function openReadOnly(id) {
  await saveDraft();
  current = id;
  select(id);
  closeGuide();
  hide("empty");
  hide("case");
  hide("submitStage");
  show("readOnly");
  const res = await fetch("/api/read-only?case=" + encodeURIComponent(id));
  const d = await res.json();
  if (!res.ok) { $("readOnlyDocument").textContent = d.detail; return; }
  $("readOnlyTitle").textContent = d.title;
  $("readOnlyCase").textContent = d.case;
  $("readOnlyMeta").textContent = "Reviewed by " + d.reviewed_by + " · " + d.date;
  layout($("readOnlyPartOne"), d.blocks);
  $("readOnlyDocument").textContent = d.document;
}

async function openSubmit() {
  await saveDraft();
  current = null;
  select(null);
  closeGuide();
  hide("empty");
  hide("case");
  hide("readOnly");
  show("submitStage");
  await loadStage();
}

// --- The case, laid out --------------------------------------------------------
//
// Case prose is data, so it lands through textContent rather than through any
// markup. Every builder below writes its own elements and puts the case's own
// words inside them, so a sentence spelling a tag arrives as those characters.

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function sourceBlock(block) {
  const card = el("div", "card");
  const head = el("div", "head");
  head.append(el("h4", null, block.label));
  card.append(head, el("p", "hint", "Exactly what the service would receive."),
              el("pre", "verbatim", block.text));
  return card;
}

function tableBlock(block) {
  const out = document.createDocumentFragment();
  const table = el("table");
  const thead = el("thead");
  const hr = el("tr");
  const tbody = el("tbody");
  for (const h of block.headers) hr.append(el("th", null, h));
  thead.append(hr);
  for (const row of block.rows) {
    const tr = el("tr");
    for (const cell of row) tr.append(el("td", null, cell));
    tbody.append(tr);
  }
  table.append(thead, tbody);
  const scroll = el("div", "scroll");
  scroll.append(table);
  out.append(el("h3", null, block.caption), scroll);
  return out;
}

function termsBlock(block) {
  const out = document.createDocumentFragment();
  const list = el("ul", "terms");
  out.append(el("h3", null, block.caption));
  if (block.hint) out.append(el("p", "hint", block.hint));
  for (const item of block.items) {
    const li = el("li");
    li.append(el("code", "id", item.term), " — ", item.text);
    list.append(li);
  }
  out.append(list);
  return out;
}

// One builder per block kind, keyed rather than branched.
const BLOCKS = {source: sourceBlock, table: tableBlock, terms: termsBlock};

function layout(box, blocks) {
  box.replaceChildren();
  for (const block of blocks) {
    const build = BLOCKS[block.kind];
    if (build) box.append(build(block));
    else box.append(el("p", "note", "This page cannot display part of this case."));
  }
}

function fieldRow(row) {
  const li = el("li");
  row.forEach((field, n) => {
    if (n) li.append(" · ");
    li.append(el("span", "lbl", field.label + " "));
    field.values.forEach((v, i) => {
      if (i) li.append(", ");
      li.append(field.code ? el("code", "id", v) : document.createTextNode(v));
    });
  });
  return li;
}

function recordCard(record, target, values, answered, framework) {
  const card = el("div", "card rec");
  const head = el("div", "head");
  head.append(el("span", "num", record.label));
  if (record.identifier) head.append(el("code", "id", record.identifier));
  head.append(el("h4", null, record.title));
  card.append(head);
  if (record.fields.length) {
    const list = el("ul", "fields");
    for (const row of record.fields) list.append(fieldRow(row));
    card.append(list);
  }
  if (!target) {
    card.append(el("p", "aside", "No mark is available for this recorded claim."));
    return card;
  }
  // Two claims the identity rule calls one finding share one target, and the
  // second card says so rather than offering a second select.
  const first = answered.get(target.fingerprint);
  if (first !== undefined) {
    card.append(el("p", "aside",
      "The same finding as " + first + " above, so the mark there answers this too."));
    return card;
  }
  answered.set(target.fingerprint, record.label);
  const select = document.createElement("select");
  select.dataset.finding = target.fingerprint;
  select.dataset.framework = framework;
  for (const value of ["", ...values]) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = value ? value[0].toUpperCase() + value.slice(1) : "—";
    select.append(o);
  }
  const mark = el("div", "mark");
  mark.append(el("span", "hint", "Your mark"), select);
  card.append(mark);
  return card;
}

// A framework's checkbox shows or hides its set. It changes nothing about the
// record: every finding in every set still needs a mark before the press.
function frameworkChoice(name, details) {
  const label = el("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = true;
  input.dataset.framework = name;
  input.addEventListener("change", () => {
    details.classList.toggle("hidden", !input.checked);
    if (input.checked) details.open = true;
  });
  label.append(input, document.createTextNode(name.toUpperCase()));
  return label;
}

async function showSets(id) {
  const res = await fetch("/api/part-two?case=" + encodeURIComponent(id));
  const sets = await res.json();
  if (current !== id) return;
  // Said beside the case rather than swallowed: a reader who cannot see why
  // the sets stay closed cannot act on it.
  if (!res.ok) { $("ownHint").textContent = sets.detail; return; }
  const byClaim = new Map();
  for (const target of sets.marks) {
    for (const claim of target.claims) byClaim.set(claim, target);
  }
  const box = $("partTwo");
  const picker = $("frameworkPicker");
  const fieldset = el("fieldset", "framework-picker");
  box.replaceChildren();
  picker.replaceChildren();
  fieldset.append(el("legend", null, "Frameworks shown"));
  for (const [name, part] of Object.entries(sets.frameworks)) {
    const details = el("details", "framework");
    details.open = true;
    details.dataset.framework = name;
    details.append(el("summary", null, part.heading));
    const body = el("div", "framework-body");
    body.append(el("p", "note", part.question));
    const answered = new Map();
    for (const group of part.groups) {
      body.append(el("h3", null, group.name));
      for (const record of group.records) {
        body.append(recordCard(record, byClaim.get(record.title), sets.values, answered, name));
      }
    }
    details.append(body);
    box.append(details);
    fieldset.append(frameworkChoice(name, details));
  }
  picker.append(fieldset);
  hide("placeholder");
  show("two");
  updateMarkCounts();
}

// --- The marks ---------------------------------------------------------------
//
// Every select on the stage, whatever the picker shows. A hidden set is still a
// set the record answers for, so a count over the visible ones would enable a
// press the server refuses.

function allSelects() {
  return [...document.querySelectorAll("select[data-finding]")];
}

function marksNow() {
  const marks = {};
  for (const s of allSelects()) if (s.value) marks[s.dataset.finding] = s.value;
  return marks;
}

function setMarks(marks) {
  for (const s of allSelects()) s.value = marks[s.dataset.finding] || "";
  updateMarkCounts();
}

function markSummary() {
  const selects = allSelects();
  const counts = {};
  for (const value of MARK_VALUES) counts[value] = 0;
  let marked = 0;
  for (const s of selects) {
    if (s.value in counts) { counts[s.value]++; marked++; }
  }
  return "Review summary: " + MARK_VALUES.map(v => counts[v] + " " + v).join(" · ")
    + " · " + (selects.length - marked) + " unmarked";
}

function unmarked() {
  return allSelects().filter(s => !s.value).length;
}

function updateMarkCounts() {
  $("markCounts").textContent = markSummary();
  const left = unmarked();
  $("finish").disabled = left > 0;
  $("finishHint").textContent = left > 0
    ? "Answer every finding to record this review — " + left + " left."
    : "";
}

function lock() {
  $("own").readOnly = true;
  $("lock").disabled = true;
  $("ownHint").textContent = "";
}

function gate() {
  if (!$("own").readOnly) $("lock").disabled = lines("own").join("").length < MIN_OWN_LIST;
}

function finishedNow(done) {
  $("finish").textContent = done ? "Save changes" : "Record review";
}

$("own").addEventListener("input", gate);

$("lock").addEventListener("click", async () => {
  const id = current;
  const res = await post("/api/own-list", {case: id, items: lines("own")});
  if (!res.ok) { $("ownHint").textContent = (await res.json()).detail; return; }
  lock();
  await loadRail();
  await showSets(id);
  $("two").scrollIntoView({behavior: "smooth"});
});

for (const event of ["input", "change"]) {
  $("two").addEventListener(event, () => {
    updateMarkCounts();
    queueSave();
    $("saveStatus").textContent = "";
  });
}

$("finish").addEventListener("click", async () => {
  const id = current;
  const was = $("finish").textContent === "Save changes";
  $("finish").disabled = true;
  const res = await post("/api/finish", {
    case: id, marks: marksNow(), missing: lines("missing"), notes: $("notes").value,
  });
  const d = await res.json();
  updateMarkCounts();
  if (!res.ok) { $("saveStatus").textContent = d.detail; return; }
  warn(d.moved);
  $("summary").textContent = markSummary();
  $("doneTitle").textContent = was ? "Changes saved" : "Recorded";
  finishedNow(true);
  show("done");
  await loadRail();
});

$("resetReview").addEventListener("click", async () => {
  if (!current) return;
  if (!confirm("Reset your Part 2 answers for this case? Your independent list will stay locked.")) return;
  const res = await post("/api/reset", {case: current});
  if (!res.ok) { $("saveStatus").textContent = (await res.json()).detail; return; }
  for (const s of allSelects()) s.value = "";
  $("missing").value = "";
  $("notes").value = "";
  hide("done");
  finishedNow(false);
  updateMarkCounts();
  $("saveStatus").textContent = "Answers reset";
  await loadRail();
});

// --- The results stage -----------------------------------------------------------

function stageRow(row, label, act) {
  const li = el("li");
  const line = el("div", "line");
  const text = el("span", null, row.number + "  " + row.title);
  const b = el("button", null, label);
  b.addEventListener("click", () => act(row.case));
  line.append(text, b);
  li.append(line);
  return li;
}

async function stageAct(path, id) {
  const res = await post(path, {case: id});
  if (!res.ok) { $("ready").textContent = (await res.json()).detail; return; }
  await loadRail();
  await loadStage();
}

const drop = id => stageAct("/api/drop", id);
const putBack = id => stageAct("/api/put-back", id);

async function loadStage() {
  let d, status;
  try {
    [d, status] = await Promise.all([getJson("/api/stage"), getJson("/api/contribution-status")]);
  } catch (err) {
    $("ready").textContent = err.message;
    return;
  }
  contributionMode = status.mode;
  $("ready").textContent = d.ready.length + " cases are recorded locally. "
    + d.unfinished + " cases remain unfinished.";
  $("carrying").replaceChildren(...d.ready.map(r => stageRow(r, "Drop", drop)));
  $("held").replaceChildren(...d.held_back.map(r => stageRow(r, "Put back", putBack)));
  $("heldBox").classList.toggle("hidden", !d.held_back.length);
  $("waysOut").classList.toggle("hidden", !d.ready.length);
  $("showFiles").disabled = !d.ready.length;
  $("submit").disabled = !d.ready.length;
  $("browserIdentity").classList.toggle("hidden", status.mode !== "browser");
  if (status.author) $("githubAuthor").value = status.author;
  hide("filePreview");
  hide("result");
  hide("thanks");
  hide("browserSteps");
  $("contributeStatus").textContent = "";
}

function contributionChoice() {
  const picked = document.querySelector('input[name="reviewerAttribution"]:checked');
  return {
    reviewer: (picked && picked.value) || "anonymous",
    author: $("githubAuthor").value.trim() || null,
  };
}

function requireBrowserAuthor(choice) {
  if (contributionMode === "browser" && !choice.author) {
    $("contributeStatus").textContent = "Enter the GitHub username that will open the pull request.";
    $("githubAuthor").focus();
    return false;
  }
  return true;
}

async function showFiles() {
  const choice = contributionChoice();
  if (!requireBrowserAuthor(choice)) return;
  const res = await post("/api/contribution-preview", choice);
  const d = await res.json();
  if (!res.ok) { $("contributeStatus").textContent = d.detail; return; }
  $("previewPath").textContent = d.path;
  $("previewContent").textContent = d.content;
  show("filePreview");
}

$("showFiles").addEventListener("click", showFiles);
for (const input of document.querySelectorAll('input[name="reviewerAttribution"]')) {
  input.addEventListener("change", () => hide("filePreview"));
}
$("githubAuthor").addEventListener("input", () => hide("filePreview"));

$("submit").addEventListener("click", async () => {
  const choice = contributionChoice();
  if (!requireBrowserAuthor(choice)) return;
  $("submit").disabled = true;
  $("contributeStatus").textContent = "Preparing contribution…";
  const res = await post("/api/contribute", choice);
  const d = await res.json();
  $("submit").disabled = false;
  show("thanks");
  if (!res.ok) {
    $("contributeStatus").textContent = d.detail || "Contribution needs attention";
    return;
  }
  if (d.mode === "direct") {
    $("contributeStatus").textContent = "Pull request opened";
    // A draft that would not delete stops nothing: the pull request is open,
    // and the file is in a store only the reader can clear. So it is said
    // here rather than dropped.
    $("result").textContent = [d.url, ...(d.warnings.length
      ? ["", "these drafts would not delete:", ...d.warnings] : [])].join("\n");
    show("result");
    return;
  }
  $("contributeLink").href = d.url;
  const blob = new Blob([d.content], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = d.filename;
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  $("contributeStatus").textContent = "Ready to publish";
  show("browserSteps");
});

// --- Wiring ------------------------------------------------------------------

$("helpToggle").addEventListener("click", async () => {
  await saveDraft();
  const opening = $("guide").classList.contains("hidden");
  $("guide").classList.toggle("hidden", !opening);
  $("helpToggle").setAttribute("aria-expanded", String(opening));
  if (opening) $("guide").scrollIntoView({behavior: "smooth", block: "start"});
});

$("start").addEventListener("click", () => {
  const first = firstToDo();
  if (first) openCase(first.case);
});
for (const id of ["previous", "previousBottom"]) $(id).addEventListener("click", () => step(-1));
for (const id of ["next", "nextBottom"]) $(id).addEventListener("click", () => step(1));
$("toSubmit").addEventListener("click", openSubmit);
$("backToWalk").addEventListener("click", () => {
  const list = walkable();
  if (list.length) openCase(list[list.length - 1].case);
});

// A preselect moves the rail. It opens the case only when the rail presses it.
loadRail().then(d => {
  if (!d.preselect) return;
  const row = rows.find(r => r.case === d.preselect);
  if (row?.pressable) openCase(row.case);
  else if (row?.state === "signed") openReadOnly(row.case);
});
