  const form = document.getElementById("analyze");
  const box = document.getElementById("description");
  const ticks = document.getElementById("ticks");
  const problem = document.getElementById("problem");
  const go = document.getElementById("go");
  const status = document.getElementById("status");
  const statusText = document.getElementById("status-text");

  // What the service is doing now, beside a spinner, from the moment a run
  // starts until it stops for answers, ends, or fails.
  const working = (message) => {
    if (typeof workspace !== "undefined") workspace.show("progress-panel");
    statusText.textContent = message;
    status.hidden = false;
  };
  const idle = () => {
    status.hidden = true;
  };

  // The picker. A checkbox reaches its own option controls through the row
  // that contains them, never through a selector built from its value: the
  // DOM already says which controls belong to which framework, and reading
  // that beats keeping a second copy of the mapping on this page.
  const boxes = [...document.querySelectorAll("input[name=framework]")];
  const optionsOf = (checkbox) => [
    ...checkbox.closest(".pick").querySelectorAll("select"),
  ];

  // Keep each framework's options visible so its choices are discoverable.
  // Unticking disables them without resetting what was chosen.
  const sync = (checkbox) => {
    for (const control of optionsOf(checkbox)) control.disabled = !checkbox.checked;
  };
  for (const checkbox of boxes) {
    checkbox.addEventListener("change", () => sync(checkbox));
    sync(checkbox);
  }

  // What the server allow-lists. Each choice's value is the JSON of the choice
  // itself, so a level posts as the number its options model declares rather
  // than as the string a form control would otherwise send.
  const selection = () =>
    boxes
      .filter((checkbox) => checkbox.checked)
      .map((checkbox) => ({
        name: checkbox.value,
        options: Object.fromEntries(
          optionsOf(checkbox).map((s) => [s.dataset.option, JSON.parse(s.value)]),
        ),
      }));

  document.getElementById("load").addEventListener("click", async () => {
    try {
      const response = await fetch("/example");
      if (!response.ok) throw new Error();
      box.value = await response.text();
    } catch {
      fail("Could not load the example. Try again.");
    }
  });

  // Nodes and strings, never markup. replaceChildren() inserts a string as a
  // text node, so a source label or a validator message that spells markup
  // shows the characters the submitter typed. Same rule as the report viewer,
  // and for the same reason: with no escape helper on the page there is none
  // to forget, and forgetting shows junk on screen instead of running.
  const fail = (...content) => {
    problem.replaceChildren(...content);
    problem.hidden = false;
    ticks.hidden = true;
    idle();
    go.disabled = false;
  };

  // The question toggle is on the page only where the install can ask.
  const ask = document.getElementById("ask");
  const asked = document.getElementById("asked");
  const questions = document.getElementById("questions");
  const list = document.getElementById("list");
  const saveButton = document.getElementById("save");
  const skipButton = document.getElementById("skip");
  const skippedBox = document.getElementById("skipped");
  const moreBox = document.getElementById("more");
  const earlierBox = document.getElementById("earlier");
  const saveStatus = document.getElementById("save-status");
  const changesNote = document.getElementById("changes");
  const startButton = document.getElementById("continue");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.hidden = true;
    ticks.replaceChildren();
    go.disabled = true;

    try {
      const started = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sources: [
            { kind: "description", label: "Pasted description", text: box.value },
          ],
          name: document.getElementById("analysis-name")?.value.trim() || "Your system",
          frameworks: selection(),
          questions: Boolean(ask && ask.checked),
          answers_from: document.getElementById("answers-from")?.value || undefined,
        }),
      });
      if (!started.ok) {
        fail((await started.json()).message);
        return;
      }
      follow(
        (await started.json()).run,
        ask && ask.checked
          ? "Reading your description and building the system model. The service"
            + " stops for your answers before the threat analysis starts."
          : "Reading your description and running the threat analysis. This takes"
            + " a few minutes.",
      );
    } catch {
      fail("The connection was interrupted. Check Analyses before starting again.");
    }
  });

  // ------------------------------------------------------------------------
  // A paused run's questions: one adaptive list, saved as the submitter answers
  // (ADR 0078). The service decides what the list holds and in what order.
  // This page draws each row once, by its key, keeps it in place while the
  // list around it changes, and sends each change on its own. The service
  // answers every save with the list again, and the page reconciles by key.
  // Every label and option is untrusted and lands as text.
  // ------------------------------------------------------------------------
  let pausedRun = null;
  // The revision this page last read. Every save and the start send it, so a
  // page left open on an earlier revision cannot write over a later one.
  let revision = 0;
  // The list as the service last served it.
  let view = null;
  // Every row the page has drawn, by its key as a string: the question, its
  // node, its editor, and the answer the service last acknowledged for it.
  const rows = new Map();
  // The submitter's edits the service has not acknowledged, by key string:
  // { q, kind, base, answer, skip, refused, conflict }. `q` and `kind` name
  // the question, and `base` is the saved answer the edit was made over.
  // `conflict` is set only while another tab's save contradicts the edit:
  // { theirs }, the answer saved there, or null where it was removed.
  // Every edit is a new entry, and an acknowledgement clears an entry only
  // while it is the one it sent.
  const edits = new Map();
  // How long the page waits after a keystroke before it saves. A chosen
  // option is saved at once. 400 ms is the initial target #1632 sets; no
  // measurement of typing chose it.
  const AUTOSAVE_DELAY = 400;
  // How much of an element's source words a row shows; the rest is its title.
  const EXCERPT = 200;
  const keyOf = (key) => JSON.stringify(key);
  // A row's key as the page's maps hold it: a link question's key is a
  // string, every other question's the JSON of its key tuple.
  const rowKey = (row) => (row.kind === "link" ? row.q.key : keyOf(row.q.key));
  // Draw a row's open editor, with `prefill` as its saved answer or null.
  const drawOpen = (row, prefill) => {
    if (row.kind === "link") drawLink(row, prefill ? prefill.element : "");
    else if (row.q.form === "facets") drawFacetRow(row, prefill);
    else drawFact(row, prefill);
    row.saved = prefill;
  };
  // Two answers are one answer when the fields the submitter gives agree. The
  // service serves every field, with its defaults and with the value it
  // writes from a facets answer; the page builds only the fields given.
  const sameAnswer = (a, b) => {
    if (!a || !b) return !a && !b;
    if ("principal" in a) return a.principal === b.principal && a.element === b.element;
    if ((a.detail || "") !== (b.detail || "")) return false;
    if (!a.facets && !b.facets) return a.value === b.value;
    if (!a.facets || !b.facets) return false;
    const given = Object.entries(a.facets);
    return given.length === Object.keys(b.facets).length
      && given.every(([facet, value]) => b.facets[facet] === value);
  };
  const say = (node, text) => {
    if (node) node.textContent = text;
  };
  // The input for one early question, built by answerEditor. `prefill` is an
  // earlier answer, or null. A job waiting at its pause takes "I don't know"
  // over a known answer, so every input offers it.
  const inputFor = (q, prefill, changed) =>
    answerEditor(q, { choices: q.choices, prefill, reopen: true, changed });
  // Whether `node` holds the focused element. A stand-in document has no
  // focus, and a row then counts as not focused.
  const focused = (node) => {
    const active = document.activeElement;
    return Boolean(active && typeof node.contains === "function" && node.contains(active));
  };
  const dirty = (row) => edits.has(rowKey(row));
  // A row the page must not rebuild: the submitter is editing it, or its edit is
  // not acknowledged yet.
  const busy = (row) => dirty(row) || focused(row.node);

  // The intro, the list and the tail of the question area. The intro and the
  // tail are redrawn from each payload; the list is reconciled by key.
  const intro = document.createElement("div");
  intro.className = "list-intro";
  const tail = document.createElement("div");
  tail.className = "list-tail";
  let framed = false;
  const frame = () => {
    if (framed) return;
    questions.replaceChildren(intro, list, tail);
    framed = true;
  };

  // Which analysis an answer serves: a capability decides what a framework
  // covers, and every other fact is read by the analysis itself.
  const serves = (q) => {
    const names = q.frameworks || [];
    if (!names.length) return null;
    const which = `the ${names.join(" and ")} ${names.length > 1 ? "analyses" : "analysis"}`;
    if ((q.gates || []).length) return `Decides whether ${which} runs`;
    return q.kind === "capability" ? `Decides what ${which} covers` : `Used by ${which}`;
  };
  // What each answer to a yes/no question proves about the others, from the
  // service's own figures (QuestionSet.effects). "May remove" and "lists",
  // never "resolves": no finding exists yet. Neither answer is favoured.
  const effectOf = (q) => {
    const rank = q.rank;
    if (!rank || !rank.answers) return null;
    const parts = [];
    for (const [answer, effect] of Object.entries(rank.answers)) {
      const said = [];
      if (effect.settles) said.push(`may remove ${effect.settles} follow-up question(s)`);
      if (effect.unlocks) said.push(`lists ${effect.unlocks} more`);
      if (said.length) parts.push(`“${answer}” ${said.join(" and ")}`);
    }
    return parts.length ? parts.join("; ") : null;
  };
  // Why a question is asked, which analysis it serves, what each answer
  // does to the list, and the words of the description its element was read
  // from. The words are what the service read, not an answer to the question.
  const context = (q) => {
    const parts = [];
    if ((q.reasons || []).length) parts.push(`Why: ${q.reasons[0]}`);
    const use = serves(q);
    if (use) parts.push(use);
    const effect = effectOf(q);
    if (effect) parts.push(effect);
    if (q.excerpt) {
      const cut = q.excerpt.length > EXCERPT ? `${q.excerpt.slice(0, EXCERPT)}…` : q.excerpt;
      parts.push(`Your description of this part, for reference: “${cut}”`);
    }
    if (!parts.length) return null;
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent = parts.join(" · ");
    hint.title = q.excerpt || "";
    return hint;
  };
  const button = (text, onClick, className = "quiet") => {
    const control = document.createElement("button");
    control.type = "button";
    control.className = className;
    control.textContent = text;
    control.addEventListener("click", onClick);
    return control;
  };

  // ---- Saving -------------------------------------------------------------
  // One request at a time, for this run. A request carries the edits as they
  // stood when it was sent; an edit made while it was in flight is a new
  // entry and goes in the next request.
  let saveTimer = null;
  let saving = false;
  let again = false;
  // A save that landed, or one in flight, says nothing; the other states do.
  // The save line says something only on an error: a failed save, or an
  // answer that changed elsewhere. `state` is always on the line's data.
  const setStatus = (state, text) => {
    const words = {
      failed: "Not saved",
      conflict: "Not saved: an answer changed elsewhere",
    };
    const word = words[state] || "";
    say(saveStatus, word && text ? `${word}: ${text}` : word);
    if (saveStatus) saveStatus.dataset.state = state;
    // "Save now" shows after a failed save, so it can be retried by hand.
    saveButton.hidden = state !== "failed";
  };
  // The status the edits warrant: a conflict first, then anything unsaved.
  const showState = () => {
    const conflict = [...edits.values()].some((edit) => edit.conflict);
    setStatus(conflict ? "conflict" : edits.size ? "unsaved" : "saved");
  };
  const scheduleSave = (delay) => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flush(), delay);
  };
  // The listed early questions this page shows: every row drawn from the
  // list the service serves now. The service records only these as shown,
  // and takes a skip only of one of them (ADR 0078). A link row is not one.
  const presented = () =>
    [...rows.values()]
      .filter((row) => row.kind === "fact" && row.listed)
      .map((row) => row.q.key);
  // The edits a request sends: every unacknowledged one that is not refused
  // and not in conflict. Returns the request body, or null where nothing is
  // left to send.
  const bodyOf = (sent) => {
    const facts = [];
    const links = [];
    const skip = [];
    for (const edit of sent.values()) {
      if (edit.skip) skip.push(edit.q.key);
      if (!edit.answer) continue;
      if (edit.kind === "link") links.push(edit.answer);
      else facts.push(edit.answer);
    }
    if (!facts.length && !links.length && !skip.length) return null;
    return { links, facts, save: true, skip, presented: presented(), revision };
  };
  const sendable = () =>
    new Map([...edits].filter(([, edit]) => !edit.refused && !edit.conflict));
  // The edits one request carries: the sendable ones, in order, up to the
  // limits the service publishes for one request (ANSWER_LIMITS). The rest
  // go in the next request.
  const batch = () => {
    const sent = new Map();
    const count = { facts: 0, links: 0, skips: 0 };
    for (const [key, edit] of sendable()) {
      const need = { facts: 0, links: 0, skips: edit.skip ? 1 : 0 };
      if (edit.answer) need[edit.kind === "link" ? "links" : "facts"] = 1;
      const over = Object.keys(need).some((kind) => count[kind] + need[kind] > ANSWER_LIMITS[kind]);
      if (over) continue;
      for (const kind of Object.keys(need)) count[kind] += need[kind];
      sent.set(key, edit);
    }
    return sent;
  };
  // Save the edits that wait. While a request is in flight, the edits wait
  // for it, and the promise returned settles once they are sent too, so a
  // caller that awaits it sees every edit made before the call answered.
  let inFlight = Promise.resolve();
  const flush = () => {
    clearTimeout(saveTimer);
    if (saving) {
      again = true;
      return inFlight;
    }
    inFlight = send();
    return inFlight;
  };
  const send = async () => {
    const sent = batch();
    const body = bodyOf(sent);
    if (!body) {
      showState();
      return;
    }
    saving = true;
    setStatus("saving");
    try {
      const response = await fetch("/answer/" + pausedRun, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (response.ok) {
        acknowledge(sent);
        // A batch that left edits behind is followed by the next one.
        if (sendable().size) again = true;
        answerProblem.hidden = true;
        // A save answers with the list again; a stand-in may answer less.
        if (data.early_questions) reconcile(data);
        showState();
      } else if (response.status === 409) {
        // Another tab saved first. Read the answers again, keep every edit
        // that does not contradict them, and show the ones that do.
        await resync();
      } else {
        for (const [key, edit] of sent) {
          if (edits.get(key) === edit) edit.refused = true;
        }
        refuse(data.message);
        setStatus("failed", data.message);
        if (data.contradiction) offerCorrection(data.contradiction);
      }
    } catch {
      // The response was lost, and the save may have landed. Read the
      // answers back before anything is sent again.
      setStatus("failed", "The connection was interrupted. Retry to save again.");
      await resync();
    } finally {
      saving = false;
      if (again) {
        again = false;
        await flush();
      }
    }
  };
  // An acknowledgement clears each entry it sent; an edit made since is a new
  // entry, which stays and is sent next.
  const acknowledge = (sent) => {
    for (const [key, edit] of sent) {
      if (edits.get(key) === edit) edits.delete(key);
    }
  };
  // What the service holds for each answered question, by key string.
  const answersOf = (data) => {
    const held = new Map();
    for (const a of data.answered_early || []) held.set(keyOf(a.key), a.answer);
    for (const a of data.answered_links || []) {
      held.set(a.key, { principal: a.principal, element: a.answer.element });
    }
    return held;
  };
  // Read the answers again, after a stale save or a lost response. An edit
  // the service already holds is acknowledged; an edit over an answer that
  // changed since this page read it is a conflict for the submitter to settle;
  // every other edit is kept and sent again.
  const resync = async () => {
    let payload;
    try {
      const response = await fetch("/workspace/runs/" + encodeURIComponent(pausedRun));
      payload = await response.json();
      if (!response.ok) throw new Error();
    } catch {
      setStatus("failed", "Could not read the saved answers. Retry.");
      return;
    }
    if (!payload.questions) {
      // The run no longer waits: its answers started a run, or it stopped.
      refuse("This analysis no longer waits for answers. Open Analyses to see where it went.");
      startButton.disabled = true;
      return;
    }
    const latest = payload.questions;
    const theirs = answersOf(latest);
    for (const [key, edit] of edits) {
      const held = theirs.get(key);
      if (edit.answer && sameAnswer(held, edit.answer)) {
        edits.delete(key);
      } else if (!sameAnswer(held, edit.base)) {
        // An edit with no row on the page is the other half of a correction,
        // and the answer it corrected changed: it is dropped, not shown.
        if (rows.has(key)) edit.conflict = { theirs: theirs.get(key) || null };
        else edits.delete(key);
      }
    }
    reconcile(latest);
    showState();
    // The edits that stand are sent again: now, or once the request that
    // met the conflict has returned.
    if (sendable().size) {
      if (saving) again = true;
      else scheduleSave(0);
    }
  };
  // Leaving the page with an edit the service has not acknowledged sends it,
  // and the browser asks the submitter to confirm.
  window.addEventListener("beforeunload", (event) => {
    if (!edits.size && !saving) return;
    flush();
    event.preventDefault();
    event.returnValue = "";
  });
  // The service refused an answer that would leave a part present under an
  // absent ancestor (a contradiction it names). The page offers to answer
  // the other question the same way in one save: "no" to the part under a
  // "no", or "yes" to the ancestor over a "yes". The service decides again.
  const offerCorrection = ({ part, ancestor }) => {
    const [partKey, ancestorKey] = [keyOf(part), keyOf(ancestor)];
    const mine = [partKey, ancestorKey].find((key) => edits.has(key) && edits.get(key).refused);
    const own = mine && edits.get(mine);
    if (!own || !own.answer) return;
    const otherKey = mine === partKey ? ancestorKey : partKey;
    const other = questionOf(otherKey);
    // The sources state the other side: nothing on the page answers it.
    if (!other) return;
    const value = own.answer.value;
    const fix = button(`Answer “${other.label}” “${value}” too, in one save`, () => {
      fix.remove();
      delete own.refused;
      edits.set(otherKey, {
        q: other,
        kind: "fact",
        base: answersOf(view).get(otherKey) || null,
        answer: { key: other.key, value },
        skip: false,
      });
      flush();
    });
    answerProblem.append(" ", fix);
  };
  // A question the payload names, by key string: listed, waiting on its
  // parent, answered, or set aside. Null where the payload names none.
  const questionOf = (key) =>
    [
      ...(view.early_questions || []),
      ...(view.early_blocked || []),
      ...(view.answered_early || []),
      ...(view.skipped_early || []),
      ...(view.early_held_back || []),
      ...(view.early_below_floor || []),
    ].find((q) => keyOf(q.key) === key) || null;
  // "Save now": every editor's current value, and every refused edit again.
  saveButton.addEventListener("click", () => {
    collectEdits();
    for (const edit of edits.values()) delete edit.refused;
    if (sendable().size) flush();
    else resync();
  });

  // ---- Edits --------------------------------------------------------------
  // Register what a row's editor holds now. A value equal to the saved one is
  // no edit; a blank sends nothing, and a saved answer stays as it was.
  const registerEdit = (row) => {
    const key = rowKey(row);
    const answer = row.read();
    if (!answer || sameAnswer(answer, row.saved)) {
      edits.delete(key);
      return false;
    }
    // The same answer already waits to be saved: it is sent once.
    const pending = edits.get(key);
    if (pending && !pending.skip && !pending.refused && !pending.conflict
      && sameAnswer(pending.answer, answer)) return false;
    edits.set(key, { q: row.q, kind: row.kind, base: row.saved, answer, skip: false });
    return true;
  };
  const onEdit = (row, event) => {
    const typed = Boolean(event && event.type === "input");
    const changed = registerEdit(row);
    if (row.conflict) clearConflict(row);
    if (saving) setStatus("saving");
    else showState();
    if (changed) scheduleSave(typed ? AUTOSAVE_DELAY : 0);
    updateQuestionProgress();
  };
  // Skip one listed question for now, with any part answer it holds. A skip
  // is not an answer: the fact stays open, and "Answer it" opens it again.
  const skipRow = (row) => {
    const key = rowKey(row);
    const answer = row.read();
    const partial = answer && row.q.form === "facets" && row.open && row.open();
    edits.set(key, {
      q: row.q,
      kind: row.kind,
      base: row.saved,
      answer: partial ? answer : null,
      skip: true,
    });
    row.node.hidden = true;
    scheduleSave(0);
    setStatus("unsaved");
  };
  // A row's "Skip for now". The service takes a skip only of a question the
  // list holds, so a row the list no longer holds, such as one just answered
  // or one opened from a box, shows none (`reconcile` keeps this current).
  const skipControl = (row) => {
    const skip = button("Skip for now", () => skipRow(row), "quiet skip");
    skip.hidden = !row.listed;
    row.skipButton = skip;
    return skip;
  };
  const clearConflict = (row) => {
    delete row.conflict;
    const edit = edits.get(rowKey(row));
    if (edit) delete edit.conflict;
    if (row.conflictNode) {
      row.conflictNode.remove();
      row.conflictNode = null;
    }
  };
  // The submitter settles a conflict: keep this page's answer, or take the one
  // saved elsewhere. Neither happens in silence.
  const showConflict = (row, edit) => {
    if (row.conflictNode) row.conflictNode.remove();
    const note = document.createElement("div");
    note.className = "conflict";
    const { theirs } = edit.conflict;
    const text = document.createElement("span");
    text.textContent = theirs
      ? `Changed elsewhere to “${answerText(theirs.element ? { value: theirs.element } : theirs)}”. `
      : "Removed elsewhere. ";
    const keep = button("Keep mine", () => {
      row.saved = theirs;
      edit.base = theirs;
      clearConflict(row);
      scheduleSave(0);
    });
    const take = button("Take theirs", () => {
      edits.delete(rowKey(row));
      clearConflict(row);
      row.saved = theirs;
      if (theirs) row.set(theirs);
      showState();
    });
    note.append(text, keep, " ", take);
    row.node.append(note);
    row.conflictNode = note;
    row.conflict = true;
  };

  // ---- Rows ---------------------------------------------------------------
  // A fact row: label, context, editor, skip. `read` is the answer as the
  // service takes it, or null; `set` writes an answer into the editor.
  const drawFact = (row, prefill) => {
    const q = row.q;
    const label = document.createElement("b");
    label.textContent = q.label;
    label.title = (q.reasons || []).join(" ");
    const { input, nodes, set, answer } = inputFor(q, prefill, (event) =>
      onEdit(row, event),
    );
    row.input = input;
    row.read = answer;
    row.set = (next) => set(next.value);
    row.compact = false;
    const skip = skipControl(row);
    const about = context(q);
    row.node.replaceChildren(...(row.tick ? [row.tick, " "] : []), label, " ", ...nodes, " ", skip);
    if (about) row.node.append(about);
  };
  // A saved answer, compact and in place, with "Change".
  const drawCompact = (row, answer) => {
    const q = row.q;
    const label = document.createElement("b");
    label.textContent = q.label;
    const shown = document.createElement("span");
    shown.textContent = ` — ${answerText(answer)}`;
    row.compact = true;
    row.read = () => null;
    row.node.replaceChildren(label, shown, " ", button("Change", () => {
      drawFact(row, answer);
      if (row.input && typeof row.input.focus === "function") row.input.focus();
    }));
  };
  // A link row: which element a principal is. A skipped link places nothing.
  const linkSelect = (q, prefill) => {
    const select = document.createElement("select");
    select.dataset.principal = q.principal;
    select.append(optionOf("(leave unanswered)", ""));
    for (const option of q.options) {
      select.append(optionOf(option.name ? `${option.name} (${option.id})` : option.id, option.id));
    }
    select.append(optionOf("None of these", "none"));
    select.value = prefill || "";
    return select;
  };
  const drawLink = (row, prefill) => {
    const q = row.q;
    const label = document.createElement("label");
    const name = document.createElement("b");
    name.textContent = q.principal;
    const select = linkSelect(q, prefill);
    select.addEventListener("change", (event) => onEdit(row, event));
    row.input = select;
    row.read = () =>
      select.value ? { principal: q.principal, element: select.value } : null;
    row.set = (next) => {
      select.value = next.element;
    };
    row.compact = false;
    label.append(name, ` — places ${q.rows} stated fact(s) `, select);
    row.node.replaceChildren(label, " ", skipControl(row));
    // The element whose name matches, offered and never chosen for the
    // person: the page sends only what a person picks.
    const match = !prefill && q.options.find((option) => option.id === q.suggested);
    if (match) {
      row.node.append(" ", button(`Use ${match.name || match.id}`, (event) => {
        select.value = match.id;
        onEdit(row, event);
      }));
    }
  };
  const drawCompactLink = (row, answer) => {
    const name = document.createElement("b");
    name.textContent = row.q.principal;
    const shown = document.createElement("span");
    shown.textContent = ` — ${answer.element === "none" ? "none of these" : answer.element}`;
    row.compact = true;
    row.read = () => null;
    row.node.replaceChildren(name, shown, " ", button("Change", () => drawLink(row, answer.element)));
  };
  // A question with facets is a table: a column per facet, a row per
  // element, and a first row that sets the whole column where two or more
  // rows share it.
  const facetTable = (q) => {
    const table = document.createElement("table");
    const head = document.createElement("tr");
    const corner = document.createElement("th");
    corner.textContent = "Part of your system";
    head.append(corner);
    const columns = q.facets.map((facet) => {
      const cell = document.createElement("th");
      cell.textContent = facet.question;
      head.append(cell);
      return [];
    });
    const detailHead = document.createElement("th");
    detailHead.textContent = "Exceptions or detail (optional)";
    head.append(detailHead);
    table.append(head);
    const all = document.createElement("tr");
    all.className = "same-for-all";
    const allLabel = document.createElement("td");
    allLabel.textContent = "Same for all";
    all.append(allLabel);
    q.facets.forEach((facet, column) => {
      const cell = document.createElement("td");
      const every = facetSelect("(set every row)", "", true);
      every.addEventListener("change", () => {
        for (const select of columns[column]) {
          select.value = every.value;
          select.dispatchEvent(new Event("change"));
        }
      });
      cell.append(every);
      all.append(cell);
    });
    // The detail column takes no shared answer: each row's are its own.
    all.append(document.createElement("td"));
    // The row joins the table once two rows share it (`shareAll`).
    return { table, columns, all, count: 0 };
  };
  // "Same for all" sets a column of two or more rows; one row needs none.
  const shareAll = (grid) => {
    const shared = Boolean(grid.all.parentNode);
    if (grid.count >= 2 && !shared) {
      grid.table.insertBefore(grid.all, [...grid.table.children][1] || null);
    } else if (grid.count < 2 && shared) {
      grid.all.remove();
    }
  };
  const drawFacetRow = (row, prefill) => {
    const q = row.q;
    const grid = row.grid;
    const name = document.createElement("td");
    const who = document.createElement("b");
    who.textContent = q.element;
    name.append(who);
    const about = context(q);
    if (about) name.append(about);
    const selects = q.facets.map((facet, column) => {
      const select = facetSelect("(leave unanswered)", (prefill && prefill.facets && prefill.facets[facet.id]) || "", true);
      select.dataset.key = keyOf(q.key);
      select.dataset.facet = facet.id;
      select.addEventListener("change", (event) => onEdit(row, event));
      grid.columns[column].push(select);
      const cell = document.createElement("td");
      cell.append(select);
      return cell;
    });
    const detail = answerDetail((prefill && prefill.detail) || "");
    detail.addEventListener("input", (event) => onEdit(row, event));
    const detailCell = document.createElement("td");
    detailCell.append(detail, " ", skipControl(row));
    const inputs = selects.map((cell) => cell.children[0]);
    row.read = () => {
      const given = {};
      for (const select of inputs) {
        if (select.value) given[select.dataset.facet] = select.value;
      }
      return Object.keys(given).length
        ? withDetail({ key: q.key, facets: given }, detail.value.trim())
        : null;
    };
    // Whether a facet is still blank, which a skip sets aside.
    row.open = () => inputs.some((select) => !select.value);
    row.set = (next) => {
      for (const select of inputs) select.value = (next.facets || {})[select.dataset.facet] || "";
      detail.value = next.detail || "";
    };
    row.input = inputs[0];
    row.compact = false;
    row.node.replaceChildren(name, ...selects, detailCell);
  };
  const drawCompactFacets = (row, answer) => {
    const name = document.createElement("td");
    const who = document.createElement("b");
    who.textContent = row.q.element;
    name.append(who);
    const shown = document.createElement("td");
    shown.textContent = answerText(answer);
    const change = document.createElement("td");
    change.append(button("Change", () => drawFacetRow(row, answer)));
    row.compact = true;
    row.read = () => null;
    row.node.replaceChildren(name, shown, change);
  };

  // ---- Boxes --------------------------------------------------------------
  // The list is a sequence of boxes, one for each run of rows that share a
  // group, in the service's order. The service lists each question kind's
  // rows together (ADR 0080), so a kind takes one box. A capability keeps
  // its place in its framework's turn, so the application's box can come
  // back later in the list, and then it opens a new box.
  const newBox = (q) => {
    const node = document.createElement("details");
    node.className = "question-group";
    node.open = true;
    const title = document.createElement("summary");
    const top = document.createElement("div");
    node.append(title, top);
    return { node, title, top, group: q.group, heading: q.group_heading, rows: [], grid: null, shared: null };
  };
  const titleBox = (box) => {
    const heading = document.createElement("span");
    heading.className = "group-heading";
    heading.textContent = box.heading;
    box.title.replaceChildren(heading);
  };
  // Rows of one box take "Same for all" where two or more ask one attribute
  // or kind about different elements in one form. A capability asks about
  // the whole application, so it never shares.
  const shareable = (a, b) =>
    a.key[0] && b.key[0] && a.key[1] === b.key[1] && a.key[4] === b.key[4]
    && a.form === b.form && a.form !== "facets"
    && JSON.stringify(a.choices) === JSON.stringify(b.choices);
  const ensureShared = (box) => {
    const plain = box.rows.filter((row) => row.kind === "fact" && row.q.form !== "facets");
    if (box.shared || plain.length < 2 || !plain.every((row) => shareable(row.q, plain[0].q))) return;
    const shared = inputFor(plain[0].q, null, () => {});
    const apply = button("Apply to the ticked rows", () => {
      for (const row of box.rows) {
        if (row.tick && row.tick.checked && !row.compact) {
          row.set({ value: shared.read() });
          onEdit(row);
        }
      }
    });
    const line = document.createElement("p");
    const title = document.createElement("b");
    title.textContent = "Same for all";
    line.append(title, " ", ...shared.nodes, " ", apply);
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent = "Untick a row the answer does not fit. Each row is sent as its"
      + " own answer, and you can still change it.";
    box.top.className = "shared-controls";
    box.top.replaceChildren(line, hint);
    box.shared = shared;
    for (const row of plain) addTick(row);
  };
  // Ticked rows take the shared answer; an unticked row is an exception.
  const addTick = (row) => {
    if (row.tick) return;
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = true;
    tick.title = "Take the answer under \"Same for all\"";
    tick.className = "share-answer";
    row.tick = tick;
    if (!row.compact) row.node.replaceChildren(tick, " ", ...[...row.node.children]);
  };
  // Put a new row after `prev`, the listed row before it in the service's
  // order, or first where there is none. A row joins `prev`'s box where the
  // groups match, and a facet row joins its table; otherwise a new box opens
  // right after `prev`'s box.
  const insertAfter = (parent, node, after) => {
    const children = [...parent.children];
    const at = after ? children.indexOf(after) : -1;
    const next = children[at + 1] || null;
    parent.insertBefore(node, next);
  };
  const place = (row, before) => {
    // A row opened from a box beside the list has no place in it.
    const prev = before && before.box ? before : null;
    let box = prev && prev.box.group === row.q.group ? prev.box : null;
    if (!box) {
      box = newBox(row.q);
      insertAfter(list, box.node, prev ? prev.box.node : null);
    }
    row.box = box;
    const at = prev && prev.box === box ? box.rows.indexOf(prev) : -1;
    box.rows.splice(at + 1, 0, row);
    if (row.q.form === "facets" && row.kind === "fact") {
      if (!box.grid) {
        box.grid = facetTable(row.q);
        box.node.append(box.grid.table);
      }
      row.grid = box.grid;
      row.node = document.createElement("tr");
      row.node.dataset.key = keyOf(row.q.key);
      const after = prev && prev.grid === box.grid ? prev.node : [...box.grid.table.children][box.grid.all.parentNode ? 1 : 0];
      insertAfter(box.grid.table, row.node, after);
      box.grid.count += 1;
      shareAll(box.grid);
    } else {
      insertAfter(box.node, row.node, prev && prev.box === box ? prev.node : box.top);
    }
    titleBox(box);
    ensureShared(box);
  };
  const removeRow = (row) => {
    const box = row.box;
    rows.delete(rowKey(row));
    row.node.remove();
    if (!box) return;
    box.rows = box.rows.filter((other) => other !== row);
    if (row.grid) {
      row.grid.count -= 1;
      shareAll(row.grid);
    }
    if (!box.rows.length) box.node.remove();
    else titleBox(box);
  };

  // ---- Reconcile ----------------------------------------------------------
  // The list the service serves now, drawn over the rows the page holds. A
  // row the submitter is editing, or whose edit is not acknowledged, stays as it
  // is; everything else follows the service. Rows are matched by key and
  // never moved: a new row is inserted where the service's order puts it.
  let pass = 0;
  let updateQuestionProgress = () => {};
  // True while a skip, a start or an amendment runs. The skip and start
  // buttons stay disabled until it ends.
  let answerBusy = false;
  // How many new rows wait above a focused editor (`reconcile`).
  let queued = 0;
  const reconcile = (data) => {
    const before = view;
    view = data;
    revision = data.revision;
    pass += 1;
    const held = answersOf(data);
    const listed = [
      ...data.link_questions.map((q) => ({
        q: { ...q, group: "links", group_heading: "Which element is each of these?" },
        kind: "link",
      })),
      ...data.early_questions.map((q) => ({ q, kind: "fact" })),
    ];
    // A new row the service ranks above the editor the submitter is typing
    // in waits until the focus leaves that editor, so the editor does not
    // move. A new row below it is drawn at once.
    const anchor = [...rows.values()].find((row) => row.listed && focused(row.node));
    let above = Boolean(anchor) && listed.some((each) => rowKey(each) === rowKey(anchor));
    queued = 0;
    let prev = null;
    for (const { q, kind } of listed) {
      const key = rowKey({ q, kind });
      if (anchor && key === rowKey(anchor)) above = false;
      let row = rows.get(key);
      if (!row && above) {
        queued += 1;
        continue;
      }
      if (!row) {
        row = { q, kind, node: document.createElement("p"), saved: null, seen: 0, listed: true };
        row.node.className = "question";
        row.node.dataset.key = key;
        rows.set(key, row);
        place(row, prev);
        drawOpen(row, held.get(key) || null);
      } else {
        row.q = q;
        row.listed = true;
        row.node.hidden = false;
        if (row.skipButton) row.skipButton.hidden = false;
        // A row answered in part comes back listed: its editor stays open.
        if (row.compact && !busy(row)) drawOpen(row, held.get(key) || null);
      }
      row.seen = pass;
      prev = row;
    }
    for (const [key, row] of [...rows]) {
      if (row.seen === pass) continue;
      row.listed = false;
      if (row.skipButton) row.skipButton.hidden = true;
      // What the service holds is what the row has saved, unless an edit is
      // still on its way: a row kept open because it has the focus must not
      // send an acknowledged answer again.
      if (held.has(key) && !dirty(row)) row.saved = held.get(key);
      if (busy(row)) {
        // The edit goes with the next save; the service then says what
        // became of the question.
        const edit = edits.get(key);
        if (edit && edit.conflict && !row.conflict) showConflict(row, edit);
        continue;
      }
      if (held.has(key)) {
        // Answered: compact, in place, still editable.
        const answer = held.get(key);
        if (row.kind === "link") drawCompactLink(row, answer);
        else if (row.q.form === "facets") drawCompactFacets(row, answer);
        else drawCompact(row, answer);
        continue;
      }
      removeRow(row);
    }
    for (const [key, row] of rows) {
      if (row.seen === pass && held.has(key) && !dirty(row)) row.saved = held.get(key);
      const edit = edits.get(key);
      if (edit && edit.conflict && !row.conflict) showConflict(row, edit);
    }
    drawIntro(data);
    drawTail(data);
    drawBoxes(data);
    announce(before, data);
    updateQuestionProgress();
    if (typeof workspace !== "undefined") workspace.grouped(data);
  };
  // What changed in the list since the last payload, in one short line the
  // page announces: questions no longer needed, and questions added.
  // The last change the note told; a redraw of the same list keeps it.
  let noted = [];
  const announce = (before, data) => {
    if (!before) return;
    if (before !== data) {
      const was = new Set(before.early_questions.map((q) => keyOf(q.key)));
      const now = new Set(data.early_questions.map((q) => keyOf(q.key)));
      const moot = (data.early_unnecessary || []).filter((q) => was.has(keyOf(q.key))).length;
      const added = [...now].filter((key) => !was.has(key)).length;
      noted = [];
      if (moot) noted.push(`${moot} question(s) are no longer needed`);
      if (added) noted.push(`${added} relevant question(s) added`);
    }
    const parts = queued
      ? [...noted, `${queued} of them show when you leave the answer you are typing`]
      : noted;
    say(changesNote, parts.join(". ") + (parts.length ? "." : ""));
  };
  // The focus left an editor: draw the rows that waited above it.
  list.addEventListener("focusout", () => {
    if (!queued) return;
    setTimeout(() => {
      if (view && ![...rows.values()].some((row) => focused(row.node))) reconcile(view);
    }, 0);
  });
  // The intro: what will not run, why the list ends, and what stays open.
  // Redrawn from each payload.
  const drawIntro = (data) => {
    intro.replaceChildren();
    const left = data.early_stop == null;
    // Each selected analysis whose precondition does not hold yet
    // (QuestionSet.gates): it will not run as the model reads now.
    const gates = Object.entries(data.framework_gates || {}).filter(([, state]) => state !== "satisfied");
    for (const [name, state] of gates) {
      const note = document.createElement("p");
      note.className = "hint gate-note";
      note.textContent = state === "refuted"
        ? `The system model rules out the ${name} analysis, so it will not run and`
          + " none of its questions are asked."
        : `The ${name} analysis runs only once the system model shows that it applies.`
          + " The questions that decide this come first; its other questions follow"
          + " once an answer settles it.";
      intro.append(note);
    }
    if (!left) {
      const ready = document.createElement("p");
      ready.className = "hint";
      // The service's own stop reason decides the sentence (QuestionSet.stop).
      // No stop says every fact is settled, so the page lists what stays open.
      const stops = {
        "budget-exhausted": `The question limit is reached, so ${(data.early_held_back || []).length}`
          + " more question(s) are not in the list. ",
        "below-floor": "No recommended question is left. ",
        "skipped": "You skipped every question that is left. ",
        "nothing-left": "No question is left to ask. ",
      };
      ready.textContent = stops[data.early_stop]
        + "You can still change an answer below. Nothing runs until you choose Start the analysis.";
      intro.append(ready);
    }
    // What the pause leaves open (PauseSummary): never "everything is settled".
    const summary = data.early_summary;
    if (summary) {
      const open = [];
      const count = (n, text) => { if (n) open.push(`${n} ${text}`); };
      count(summary.skipped, "question(s) skipped for now");
      count(summary.partial, "question(s) answered in part");
      count(summary.unknown, "answer(s) of “I don't know”");
      count(summary.held_back, "question(s) held back by the question limit");
      count(summary.below_floor, "question(s) ranked below the threshold for the list");
      for (const [name, bands] of Object.entries(summary.applicability || {})) {
        for (const band of bands) {
          const unknown = band.states.unknown || 0;
          if (!unknown) continue;
          const total = Object.values(band.states).reduce((sum, n) => sum + n, 0);
          const where = band.band ? `${name}, ${band.band}` : name;
          open.push(`${where}: whether ${unknown} of ${total} unit(s) apply is still unknown`
            + (band.unaskable ? `, and no open question can settle ${band.unaskable} of them` : ""));
        }
      }
      if (!left && open.length) {
        const lead = document.createElement("p");
        lead.className = "hint";
        lead.textContent = "Still open:";
        const items = document.createElement("ul");
        items.className = "still-open";
        for (const line of open) {
          const item = document.createElement("li");
          item.textContent = line;
          items.append(item);
        }
        intro.append(lead, items);
      }
    }
  };
  // The tail: answers an amendment could not carry, and the amendment itself.
  let tailDrawn = false;
  const drawTail = (data) => {
    if (tailDrawn) return;
    tailDrawn = true;
    // Answers this run carried from an earlier one and could not take: each
    // names a part this model does not have, or a fact it states
    // (AnswerState.carried, ADRs 0072 and 0079).
    const dropped = [
      ...(data.carried_dropped || []).map((a) => `${a.label} — ${answerText(a)}`),
      ...(data.carried_dropped_links || []).map((a) => `${a.principal} — ${
        a.element === "none" ? "none of these" : a.element}`),
    ];
    if (dropped.length) {
      const lead = document.createElement("p");
      lead.className = "hint";
      lead.textContent = `${dropped.length} earlier answer(s) do not fit this`
        + " system model, so they are not kept:";
      const items = document.createElement("ul");
      for (const line of dropped) {
        const item = document.createElement("li");
        item.textContent = line;
        items.append(item);
      }
      tail.append(lead, items);
    }
    // An amendment to the description itself: a part missing, or a fact read
    // wrongly. The service reads the description again with it, asks again,
    // and keeps every answer that still fits (ADR 0072).
    const fix = document.createElement("details");
    fix.className = "amend";
    const fixTitle = document.createElement("summary");
    fixTitle.textContent = "Is a part of your system missing, or described wrongly?";
    const fixHint = document.createElement("p");
    fixHint.className = "hint";
    fixHint.textContent = "Write what is true. The service reads your description again"
      + " with it, which takes a few minutes, and asks again. Answers that still fit"
      + " are kept.";
    const fixText = document.createElement("textarea");
    fixText.rows = 3;
    const fixButton = button("Read the description again with this", () => answerAction(async () => {
      const text = fixText.value.trim();
      if (!text) {
        refuse("Write the amendment first.");
        return;
      }
      await flush();
      if (edits.size) {
        refuse("Some answers are not saved yet. Save them, then amend.");
        return;
      }
      const amended = await fetch("/amend/" + pausedRun, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, revision }),
      });
      const body = await amended.json();
      if (!amended.ok) {
        refuse(body.message);
        return;
      }
      answerProblem.hidden = true;
      asked.hidden = true;
      follow(body.run, "Reading your description again with your amendment. The"
        + " service stops for your answers again before the threat analysis starts.");
    }), "");
    fix.append(fixTitle, fixHint, fixText, fixButton);
    tail.append(fix);
  };
  // The boxes beside the list: every earlier answer, every skipped question,
  // and the questions the list leaves out. Each is redrawn from the payload
  // unless the submitter is editing inside it; then it waits for the next save.
  // "Answer it" and "Change" build an editor only when pressed.
  const drawBoxes = (data) => {
    const held = answersOf(data);
    // A question the list holds, compact or open, is not drawn in a box too.
    const inList = (key) => {
      const row = rows.get(key);
      return Boolean(row && !row.lazyBox);
    };
    const lazyRow = (box, q, kind, before) => {
      const key = rowKey({ q, kind });
      const row = document.createElement("p");
      const label = document.createElement("b");
      label.textContent = kind === "link" ? q.principal : q.label;
      row.append(label);
      const kept = document.createElement("span");
      if (before) {
        kept.textContent = ` — ${kind === "link"
          ? (before.element === "none" ? "none of these" : before.element)
          : answerText(before)}`;
      }
      row.append(kept);
      const open = button(before ? "Change" : "Answer it", () => {
        open.hidden = true;
        kept.hidden = true;
        const entry = { q, kind, node: row, saved: before || null, listed: false, lazyBox: box };
        rows.set(key, entry);
        if (kind === "link") drawLink(entry, before ? before.element : "");
        else if (q.form === "facets") {
          entry.grid = facetTable(q);
          entry.node = document.createElement("tr");
          entry.grid.table.append(entry.node);
          row.append(entry.grid.table);
          drawFacetRow(entry, before);
        } else drawFact(entry, before);
        if (entry.input && typeof entry.input.focus === "function") entry.input.focus();
      });
      row.append(" ", open);
      box.append(row);
    };
    const redraw = (box, title, entries) => {
      if (holdsLazyBusy(box)) return;
      for (const [key, row] of [...rows]) if (row.lazyBox === box) rows.delete(key);
      box.replaceChildren();
      box.hidden = !entries.length;
      const summary = document.createElement("summary");
      summary.textContent = title(entries.length);
      box.append(summary);
      for (const { q, kind, before } of entries) lazyRow(box, q, kind, before);
    };
    const outside = (entries) =>
      entries.filter((entry) => !inList(rowKey(entry)));
    // A question answered in part and skipped has its one editor under
    // "Skipped for now", so the page cannot send two answers to one key.
    const setAside = new Set([
      ...(data.skipped_links || []).map((q) => q.key),
      ...(data.skipped_early || []).map((q) => keyOf(q.key)),
    ]);
    const answered = outside([
      ...(data.answered_links || []).map((a) => ({ q: a, kind: "link", before: { principal: a.principal, element: a.answer.element } })),
      ...(data.answered_early || []).map((a) => ({ q: a, kind: "fact", before: a.answer })),
    ]).filter((entry) => !setAside.has(rowKey(entry)));
    redraw(earlierBox, (n) => `Your answers (${n})`, answered);
    const skipped = outside([
      ...(data.skipped_links || []).map((q) => ({ q, kind: "link", before: null })),
      ...(data.skipped_early || []).map((q) => ({ q, kind: "fact", before: held.get(keyOf(q.key)) || null })),
    ]);
    redraw(skippedBox, (n) => `Skipped for now (${n}, optional)`, skipped);
    const more = outside([...(data.early_held_back || []), ...(data.early_below_floor || [])]
      .map((q) => ({ q, kind: "fact", before: held.get(keyOf(q.key)) || null })));
    redraw(moreBox, (n) => `More questions (${n}, optional, not in the list)`, more);
    if (!moreBox.hidden) {
      const moreHint = document.createElement("p");
      moreHint.className = "hint";
      moreHint.textContent = "The list leaves these out: some are past the question limit,"
        + " and others rank below the threshold for the list. An answer to one still"
        + " reaches the analysis.";
      moreBox.insertBefore(moreHint, [...moreBox.children][1] || null);
    }
  };
  const holdsLazyBusy = (box) =>
    [...rows.values()].some((row) => row.lazyBox === box && busy(row));

  // ---- Showing the list ---------------------------------------------------
  const showQuestions = (data) => {
    // The service says whether the pause asks anything more (QuestionSet.stop).
    const left = data.early_stop == null;
    const saved = (data.answered_early || []).length || (data.answered_links || []).length
      || (data.skipped_early || []).length || (data.skipped_links || []).length;
    const gates = Object.entries(data.framework_gates || {}).filter(([, state]) => state !== "satisfied");
    // Questions the list leaves out, which the submitter may still choose to answer.
    const optional = (data.early_held_back || []).length + (data.early_below_floor || []).length;
    // A pause with nothing to ask, nothing answered or skipped, no optional
    // question and no analysis to explain starts the analysis, with no page
    // between. After a save, only the start button starts it.
    if (!left && !saved && !gates.length && !optional) {
      pausedRun = data.run;
      revision = data.revision;
      startAnalysis();
      return;
    }
    if (pausedRun !== data.run) {
      // A different run: start the page afresh.
      rows.clear();
      edits.clear();
      list.replaceChildren();
      tailDrawn = false;
      tail.replaceChildren();
      view = null;
    }
    pausedRun = data.run;
    frame();
    skipButton.hidden = false;
    reconcile(data);
    setStatus(edits.size ? "unsaved" : "saved");
    asked.hidden = false;
  };
  // What the page says about progress: filled answers among the listed rows,
  // the listed choices, and the start button's weight.
  updateQuestionProgress = () => {
    if (!view) return;
    const listed = [...rows.values()].filter((row) => row.listed);
    const visible = listed.reduce((sum, row) => sum + (row.q.decisions || 1), 0);
    const filled = listed.reduce((sum, row) => {
      const answer = row.read ? row.read() : null;
      return sum + (!answer ? 0 : answer.facets ? Object.keys(answer.facets).length : 1);
    }, 0);
    const left = view.early_stop == null;
    skipButton.disabled = answerBusy || !left;
    startButton.className = left ? "" : "primary";
    if (typeof workspace !== "undefined") workspace.progress(view, filled, visible);
  };

  // A refused answer is shown beside the list, and every answer stays as it
  // was, for the submitter to correct.
  const answerProblem = document.getElementById("answer-problem");
  const refuse = (message) => {
    answerProblem.textContent = message;
    answerProblem.hidden = false;
  };

  // Register every editor's current value, for a start or a skip that must
  // not lose what the submitter typed and did not commit.
  const collectEdits = () => {
    for (const row of rows.values()) {
      if (!row.compact && row.read) registerEdit(row);
    }
  };
  // Start the analysis once: every unsaved answer is saved first, at the
  // revision the save returns, and the start names that revision. A lost
  // response is read back from the run, so a second click cannot start a
  // second run.
  let starting = false;
  const startAnalysis = async () => {
    if (starting) return;
    starting = true;
    startButton.disabled = true;
    try {
      collectEdits();
      if (edits.size) {
        await flush();
        if (edits.size) {
          // A refusal names the answer; anything else needs a retry.
          if (![...edits.values()].some((edit) => edit.refused)) {
            refuse("Some answers are not saved. Save them, then start the analysis.");
          }
          return;
        }
      }
      let resumed;
      try {
        resumed = await fetch("/answer/" + pausedRun, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ links: [], facts: [], presented: presented(), revision }),
        });
      } catch {
        // The response was lost. Where the start landed, the run now names
        // the job it started; follow it instead of starting again.
        const snapshot = await fetch("/workspace/runs/" + encodeURIComponent(pausedRun));
        const payload = await snapshot.json();
        if (snapshot.ok && payload.run !== pausedRun) {
          asked.hidden = true;
          follow(payload.run, "Running the threat analysis with your answers. This takes a few minutes.");
          return;
        }
        refuse("The connection was interrupted. Reload this analysis to check whether it started.");
        return;
      }
      if (!resumed.ok) {
        refuse((await resumed.json()).message);
        return;
      }
      answerProblem.hidden = true;
      asked.hidden = true;
      follow(
        (await resumed.json()).run,
        "Running the threat analysis with your answers. This takes a few minutes.",
      );
    } finally {
      starting = false;
      startButton.disabled = false;
    }
  };

  const answerAction = async (action) => {
    if (answerBusy) return;
    answerBusy = true;
    const controls = [skipButton, startButton];
    controls.forEach((control) => { control.disabled = true; });
    try { await action(); }
    catch { refuse("The connection was interrupted. Reload this analysis to check whether your answers were saved."); }
    finally {
      answerBusy = false;
      controls.forEach((control) => { control.disabled = false; });
      updateQuestionProgress();
    }
  };
  startButton.addEventListener("click", () => answerAction(startAnalysis));

  // Skip every listed question left blank, and every one with facets
  // answered only in part: its facets given are kept, and the rest are set
  // aside for now (QuestionSet.presented).
  skipButton.addEventListener("click", () => answerAction(async () => {
    collectEdits();
    for (const row of [...rows.values()]) {
      if (!row.listed || row.compact || row.node.hidden) continue;
      const answer = row.read();
      if (!answer || (row.open && row.open())) skipRow(row);
    }
    await flush();
  }));

  asked.addEventListener("input", () => updateQuestionProgress());
  asked.addEventListener("change", () => updateQuestionProgress());
  asked.addEventListener("click", () => updateQuestionProgress());

  // Follow one run's progress to its end: a report, questions, or a failure.
  const follow = (runId, message) => {
    go.disabled = true;
    if (typeof workspace !== "undefined") workspace.track(runId);
    working(message);
    ticks.replaceChildren();
    ticks.hidden = false;
    if (typeof workspace !== "undefined") workspace.processing();
    const stream = new EventSource("/events/" + encodeURIComponent(runId));
    stream.addEventListener("error", () => {
      stream.close();
      fail("The progress connection was interrupted. Open Analyses to check this run before starting another.");
    });
    stream.addEventListener("questions", (event) => {
      stream.close();
      ticks.hidden = true;
      idle();
      showQuestions(JSON.parse(event.data));
    });
    stream.addEventListener("node", (event) => {
      const item = document.createElement("li");
      item.textContent = JSON.parse(event.data).node;
      ticks.append(item);
      if (typeof workspace !== "undefined") workspace.processing();
    });
    stream.addEventListener("done", (event) => {
      stream.close();
      location.href = "/report/" + encodeURIComponent(runId);
    });
    stream.addEventListener("rejected", (event) => {
      stream.close();
      const lead = document.createElement("b");
      lead.textContent = "That description could not be modelled.";
      const issues = document.createElement("ul");
      for (const issue of JSON.parse(event.data).issues) {
        const item = document.createElement("li");
        const code = document.createElement("code");
        code.textContent = issue.code;
        item.append(code, " " + issue.message);
        issues.append(item);
      }
      fail(lead, issues);
    });
    stream.addEventListener("failed", (event) => {
      stream.close();
      fail(JSON.parse(event.data).message);
    });
  };

  // A report page that started a resumed run sends the browser here to watch
  // it, because the report page shows one finished report and nothing else.
  const followed = new URLSearchParams(location.search).get("follow");
  if (followed) {
    follow(followed, "Running the analysis again with your answers. This takes a few minutes.");
  }
