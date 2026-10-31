  const form = document.getElementById("analyze");
  const box = document.getElementById("description");
  const problem = document.getElementById("problem");
  const go = document.getElementById("go");
  const status = document.getElementById("status");
  const statusText = document.getElementById("status-text");

  // Show the current stage from the moment a run starts until it stops.
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
      updateDescriptionRemaining();
    } catch {
      fail("Could not load the example. Try again.");
    }
  });

  // A voice transcript, checked here so that only text leaves the page. The
  // server never receives the file: the body carries the decoded text as a
  // JSON string, as it does for a pasted description, so no file name, content
  // type or multipart body reaches the server. The checks run before the text
  // is kept, cheapest first: the size before a byte is read, then a strict
  // UTF-8 decode, then no control character other than a tab or a line break.
  // A refused file leaves the transcript the page holds as it was.
  const transcriptFile = document.getElementById("transcript-file");
  const transcriptNote = document.getElementById("transcript-note");
  const maxTranscriptBytes = Number(transcriptFile.dataset.maxBytes);
  const maxDescriptionBytes = Number(box.dataset.maxBytes);
  const remainingNote = document.getElementById("description-remaining");
  const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/;
  let transcript = null;
  const updateDescriptionRemaining = () => {
    const used = new TextEncoder().encode(box.value).length
      + new TextEncoder().encode(transcript || "").length;
    const remaining = maxDescriptionBytes - used;
    remainingNote.textContent = remaining < 0
      ? "Description and transcript are too long. Shorten them to continue."
      : `Up to ${remaining.toLocaleString()} bytes remaining`;
    remainingNote.classList.toggle("field-error", remaining < 0);
    return remaining;
  };
  box.addEventListener("input", updateDescriptionRemaining);
  updateDescriptionRemaining();
  const holdTranscript = (text, name) => {
    transcript = text;
    document.getElementById("transcript-name").textContent = name;
    transcriptNote.hidden = text === null;
    box.required = text === null;
    updateDescriptionRemaining();
  };
  document.getElementById("upload").addEventListener("click", () => transcriptFile.click());
  document.getElementById("transcript-remove").addEventListener(
    "click", () => holdTranscript(null, ""),
  );
  transcriptFile.addEventListener("change", async () => {
    const [file] = transcriptFile.files;
    transcriptFile.value = "";
    if (!file) return;
    if (file.size > maxTranscriptBytes) {
      fail(`This file is larger than the ${maxTranscriptBytes} bytes that one analysis can carry.`);
      return;
    }
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
    } catch {
      fail("The page could not read this file as UTF-8 text. Upload a plain text transcript.");
      return;
    }
    if (CONTROL.test(text)) {
      fail("This file holds control characters, so it is not a plain text transcript.");
      return;
    }
    if (!text.trim()) {
      fail("This file holds no text.");
      return;
    }
    problem.hidden = true;
    holdTranscript(text, file.name);
  });

  // The description goes with a transcript only where it holds text, and
  // always where there is no transcript, so the service says what is missing.
  const sources = () => [
    ...(box.value.trim() || transcript === null
      ? [{ kind: "description", label: "Pasted description", text: box.value }]
      : []),
    ...(transcript === null
      ? []
      : [{ kind: "transcript", label: "Uploaded transcript", text: transcript }]),
  ];

  // Nodes and strings, never markup. replaceChildren() inserts a string as a
  // text node, so a source label or a validator message that spells markup
  // shows the characters the submitter typed. Same rule as the report viewer,
  // and for the same reason: with no escape helper on the page there is none
  // to forget, and forgetting shows junk on screen instead of running.
  const fail = (...content) => {
    problem.replaceChildren(...content);
    problem.hidden = false;
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
  const noRestButton = document.getElementById("no-rest");
  const noRestHint = document.getElementById("no-rest-hint");
  const skippedBox = document.getElementById("skipped");
  const moreBox = document.getElementById("more");
  const earlierBox = document.getElementById("earlier");
  const saveStatus = document.getElementById("save-status");
  const changesNote = document.getElementById("changes");
  const startButton = document.getElementById("continue");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.hidden = true;
    go.disabled = true;

    const name = document.getElementById("analysis-name").value.trim();
    if (!name) {
      fail("Enter an analysis name.");
      document.getElementById("analysis-name").focus();
      return;
    }
    const frameworks = selection();
    if (!frameworks.length) {
      fail("Please select a framework.");
      return;
    }
    if (updateDescriptionRemaining() < 0) {
      fail("Description and transcript are too long. Shorten them to continue.");
      return;
    }

    try {
      const started = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sources: sources(),
          name,
          frameworks,
          questions: Boolean(ask && ask.checked),
          answers_from: document.getElementById("answers-from")?.value || undefined,
        }),
      });
      if (!started.ok) {
        fail((await started.json()).message);
        return;
      }
      follow((await started.json()).run, "Reading your description and identifying the parts of your system…", Boolean(ask && ask.checked));
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
  // `rowKey` and `sameAnswer` are question_list.js's, shared with the report page.
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
  // A part that holds its box's shared answer waits on the box's share.
  const dirty = (row) => edits.has(rowKey(row))
    || Boolean(row.inherited && edits.has(shareKey(row.q.key)));
  // A row the page must not rebuild: the submitter is editing it, or its edit is
  // not acknowledged yet.
  const busy = (row) => dirty(row) || focused(row.node);

  // The intro, the list and the tail of the question area. The intro and the
  // tail are redrawn from each payload; the list is reconciled by key.
  const intro = document.createElement("div");
  intro.className = "list-intro";
  const tail = document.createElement("div");
  tail.className = "list-tail";
  // The answers this run carried from an earlier one and could not take,
  // redrawn from each payload (drawCarried).
  const carried = document.createElement("div");
  carried.className = "carried";
  let framed = false;
  const frame = () => {
    if (framed) return;
    questions.replaceChildren(intro, list, carried, tail);
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
      if (effect.settles) said.push(`may remove ${counted(effect.settles, "follow-up question", "follow-up questions")}`);
      if (effect.unlocks) said.push(`lists ${effect.unlocks} more`);
      if (said.length) parts.push(`“${answer}” ${said.join(" and ")}`);
    }
    return parts.length ? parts.join("; ") : null;
  };
  // The lines under a question's "?": why it is asked, what it serves, what
  // each answer does, and the submitter's own words about the part.
  const contextOf = (q) => {
    const parts = [];
    if ((q.reasons || []).length) parts.push(`Why: ${q.reasons[0]}`);
    const use = serves(q);
    if (use) parts.push(use);
    const effect = effectOf(q);
    if (effect) parts.push(effect);
    if (q.excerpt) parts.push(`Your description of this part, for reference: “${q.excerpt}”`);
    return parts;
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
    const shares = [];
    const skip = [];
    for (const edit of sent.values()) {
      if (edit.skip) skip.push(edit.q.key);
      if (!edit.answer) continue;
      if (edit.kind === "link") links.push(edit.answer);
      else if (edit.kind === "share") shares.push(edit.answer);
      else facts.push(edit.answer);
    }
    if (!facts.length && !links.length && !shares.length && !skip.length) return null;
    return {
      links, facts, ...(shares.length ? { shares } : {}), save: true, skip, presented: presented(), revision,
    };
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
      // Each part a share covers counts as one fact answer (ADR 0114).
      if (edit.kind === "share") need.facts = edit.answer.covers.length;
      else if (edit.answer) need[edit.kind === "link" ? "links" : "facts"] = 1;
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
    for (const a of data.answered_early || []) held.set(questionKey(a.key), a.answer);
    for (const a of data.answered_links || []) {
      held.set(questionKey(a.key), { principal: a.principal, element: a.answer.element });
    }
    for (const share of data.answered_shares || []) held.set(shareKey(share.answer.key), share);
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
      const same = edit.kind === "share" ? sameShare : sameAnswer;
      if (edit.answer && same(held, edit.answer)) {
        edits.delete(key);
      } else if (edit.kind === "share" && !same(held, edit.base)) {
        // A shared answer changed in another tab: the page shows the saved
        // one, and says so, rather than overwrite it.
        edits.delete(key);
        refuse("A shared answer changed in another tab or window. The page shows the saved one.");
      } else if (!same(held, edit.base)) {
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
    const [partKey, ancestorKey] = [questionKey(part), questionKey(ancestor)];
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
    ].find((q) => questionKey(q.key) === key) || null;
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
  // The share of a row's box, as one edit for the box (ADR 0114). A share equal
  // to the saved one is no edit, and a box that shares nothing and held no
  // share sends none.
  const registerShare = (row) => {
    const key = shareKey(row.q.key);
    const base = answersOf(view || {}).get(key) || null;
    const share = shareOf(row.box, base);
    if (!share || sameShare(share, base) || (!share.covers.length && !base)) {
      edits.delete(key);
      return false;
    }
    const pending = edits.get(key);
    if (pending && !pending.refused && sameShare(pending.answer, share)) return false;
    edits.set(key, { q: null, kind: "share", base, answer: share, skip: false });
    return true;
  };
  const registerEdit = (row) => {
    const shared = Boolean(row.kind === "fact" && row.box && row.box.shared);
    const boxChanged = shared && registerShare(row);
    // A part that holds the shared answer sends no answer of its own: the
    // box's share carries it.
    if (row.inherited) {
      edits.delete(rowKey(row));
      return boxChanged;
    }
    return registerOwn(row) || boxChanged;
  };
  const registerOwn = (row) => {
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
    updateQuestionActions();
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
      ? `Changed elsewhere to “${theirs.element ? linkAnswerText(theirs) : answerText(theirs)}”. `
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

  // ---- Rows and boxes ------------------------------------------------------
  // Drawn by questionList, as every page that asks open facts draws them. A
  // job waiting at its pause takes "I don't know" over a known answer, so
  // every input offers it. Each row offers "Skip for now".
  const { place, removeRow: unplace, drawOpen, drawSaved, drawAlone, button, shareOf, restoreShares } = questionList({
    list,
    onEdit,
    inputFor,
    reopen: true,
    contextOf,
    extras: (row) => [skipControl(row)],
  });
  const removeRow = (row) => {
    rows.delete(rowKey(row));
    unplace(row);
  };

  // ---- Reconcile ----------------------------------------------------------
  // The list the service serves now, drawn over the rows the page holds. A
  // row the submitter is editing, or whose edit is not acknowledged, stays as it
  // is; everything else follows the service. Rows are matched by key and
  // never moved: a new row is inserted where the service's order puts it.
  let pass = 0;
  let updateQuestionActions = () => {};
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
        q: { ...q, group: "links", group_heading: LINK_GROUP_HEADING },
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
        drawSaved(row, answer);
        continue;
      }
      removeRow(row);
    }
    for (const [key, row] of rows) {
      if (row.seen === pass && held.has(key) && !dirty(row)) row.saved = held.get(key);
      const edit = edits.get(key);
      if (edit && edit.conflict && !row.conflict) showConflict(row, edit);
    }
    restoreShares(data.answered_shares || [], (key) => edits.has(key));
    openFirstBatch();
    drawIntro(data);
    drawCarried(data);
    drawTail(data);
    drawBoxes(data);
    announce(before, data);
    updateQuestionActions();
    if (typeof workspace !== "undefined") workspace.grouped(data);
  };
  // What changed in the list since the last payload, in one short line the
  // page announces: questions no longer needed, and questions added.
  // The last change the note told; a redraw of the same list keeps it.
  let noted = [];
  const announce = (before, data) => {
    if (!before) return;
    if (before !== data) {
      const was = new Set(before.early_questions.map((q) => questionKey(q.key)));
      const now = new Set(data.early_questions.map((q) => questionKey(q.key)));
      const moot = (data.early_unnecessary || []).filter((q) => was.has(questionKey(q.key))).length;
      const added = [...now].filter((key) => !was.has(key)).length;
      noted = [];
      if (moot) noted.push(`${counted(moot, "question is", "questions are")} no longer needed`);
      if (added) noted.push(`${counted(added, "relevant question", "relevant questions")} added`);
    }
    const parts = queued
      ? [...noted, `${counted(queued, "of them shows", "of them show")} when you leave the answer you are typing`]
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
        "budget-exhausted": `The question limit is reached, so ${counted((data.early_held_back || []).length,
          "more question is", "more questions are")} not in the list. `,
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
    // What the list asks now, and what an answer can still add. An answer can
    // raise either count, so the line names no total (PauseSummary).
    if (summary && left && summary.recommended) {
      const now = document.createElement("p");
      now.className = "hint list-progress";
      now.textContent = `${counted(summary.recommended, "question is", "questions are")} in the list,`
        + ` with ${counted(summary.recommended_choices, "choice", "choices")} to make.`
        + (summary.blocked
          ? ` ${counted(summary.blocked, "more question waits", "more questions wait")} on an answer`
            + " in the list, so your answers can add questions."
          : "");
      intro.append(now);
    }
    if (summary) {
      const open = [];
      const count = (n, one, many, text) => { if (n) open.push(`${counted(n, one, many)} ${text}`); };
      count(summary.skipped, "question", "questions", "skipped for now");
      count(summary.partial, "question", "questions", "answered in part");
      count(summary.unknown, "answer", "answers", "of “I don't know”");
      count(summary.held_back, "question", "questions", "held back by the question limit");
      count(summary.below_floor, "question", "questions", "ranked below the threshold for the list");
      for (const [name, bands] of Object.entries(summary.applicability || {})) {
        for (const band of bands) {
          const unknown = band.states.unknown || 0;
          if (!unknown) continue;
          const total = Object.values(band.states).reduce((sum, n) => sum + n, 0);
          const where = band.band ? `${name}, ${band.band}` : name;
          open.push(`${where}: whether ${unknown} of ${counted(total, "unit applies", "units apply")} is still unknown`
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
  // ---- First batch -------------------------------------------------------
  // A new pause opens its boxes in the list's order until the open boxes hold
  // FIRST_BATCH choices, once, and leaves the rest closed with their counts.
  // A prototype (#2029 package 8): owner testing (#1286) decides the size. It
  // hides no row, so a skip, "no to the rest" and the questions recorded as
  // shown read the same rows as before.
  const FIRST_BATCH = 3;
  let batchOpened = false;
  const openFirstBatch = () => {
    if (batchOpened) return;
    const opened = new Set();
    let choices = 0;
    for (const node of list.children) {
      if (choices >= FIRST_BATCH) break;
      const held = [...rows.values()].filter((row) => row.box && row.box.node === node);
      if (!held.length) continue;
      node.open = true;
      opened.add(node);
      choices += held.reduce((sum, row) => sum + ((row.q.facets || []).length || 1), 0);
    }
    batchOpened = opened.size > 0;
  };

  // ---- Carried answers ----------------------------------------------------
  // Answers this run carried from an earlier one and could not take
  // (AnswerState.carried, ADRs 0072 and 0079). An answer about a part this
  // model does not have can follow the part to its new name, where the
  // submitter says which part it is now (ADR 0116). Nothing is matched by a
  // similar name.
  const plainList = (lines) => {
    const items = document.createElement("ul");
    for (const line of lines) {
      const item = document.createElement("li");
      item.textContent = line;
      items.append(item);
    }
    return items;
  };
  const hint = (text) => {
    const line = document.createElement("p");
    line.className = "hint";
    line.textContent = text;
    return line;
  };
  // Save every rename the run holds after this change, and nothing else.
  const saveRenames = (renames) => answerAction(async () => {
    await flush();
    if (edits.size) {
      refuse("Some answers are not saved yet. Save them, then try again.");
      return;
    }
    const response = await fetch("/answer/" + pausedRun, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        links: [], facts: [], save: true, skip: [], presented: presented(), revision, renames,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      refuse(data.detail || "The service did not keep this change.");
      return;
    }
    answerProblem.hidden = true;
    if (data.early_questions) reconcile(data);
  });
  const drawCarried = (data) => {
    const dropped = data.carried_dropped || [];
    const renames = (data.carried_renames || []).map(({ earlier, element }) => ({ earlier, element }));
    carried.replaceChildren();
    // The change review: what this run kept from the earlier analysis, what
    // it did not keep, and what waits for the submitter to choose a part.
    const kept = (data.carried_kept || []).length + (data.carried_kept_links || []).length;
    const waiting = dropped.filter((a) => a.reason === "missing-element").length;
    const notKept = dropped.length - waiting + (data.carried_dropped_links || []).length;
    if (kept + waiting + notKept) {
      const parts = [`${counted(kept, "answer is", "answers are")} kept and marked under Your answers`];
      if (notKept) parts.push(`${counted(notKept, "answer is", "answers are")} not kept`);
      if (waiting) parts.push(`${counted(waiting, "answer waits", "answers wait")} for you to choose a part`);
      const review = document.createElement("p");
      review.className = "carried-review";
      review.textContent = `From the earlier analysis: ${parts.join(", ")}.`;
      carried.append(review);
    }
    for (const [index, rename] of (data.carried_renames || []).entries()) {
      const line = document.createElement("p");
      line.className = "hint";
      line.append(
        `Earlier answers about ${rename.earlier} now belong to ${rename.name}. `,
        button("Undo", () => saveRenames(renames.filter((_, other) => other !== index)), "quiet"),
      );
      carried.append(line);
    }
    for (const { earlier, choices } of data.rename_choices || []) {
      const about = dropped.filter((a) => a.key[0] === earlier);
      if (!about.length) continue;
      const box = document.createElement("div");
      box.className = "rename";
      box.append(
        hint(`${counted(about.length, "earlier answer is", "earlier answers are")} about ${earlier},`
          + " which this system model does not have:"),
        plainList(about.map((a) => `${a.label} — ${answerText(a)}`)),
      );
      if (choices.length) {
        const label = document.createElement("label");
        label.textContent = "If it is now another part, choose it: ";
        const pick = document.createElement("select");
        const option = (text, value) => {
          const one = document.createElement("option");
          one.textContent = text;
          one.value = value;
          return one;
        };
        pick.append(option("Not in this system", ""));
        for (const choice of choices) pick.append(option(choice.name, choice.element));
        label.append(pick);
        const keep = button("Keep these answers", () => {
          if (!pick.value) {
            refuse(`Choose the part that ${earlier} is now.`);
            return;
          }
          saveRenames([...renames, { earlier, element: pick.value }]);
        });
        box.append(label, " ", keep);
      }
      carried.append(box);
    }
    const others = [
      ...dropped.filter((a) => a.reason !== "missing-element").map((a) => `${a.label} — ${answerText(a)}`),
      ...(data.carried_dropped_links || []).map((a) => `${a.principal} — ${linkAnswerText(a)}`),
    ];
    if (others.length) {
      carried.append(
        hint(`${counted(others.length, "earlier answer is", "earlier answers are")} not kept, because`
          + " this system model states the fact or does not ask it:"),
        plainList(others),
      );
    }
  };

  // The tail: answers an amendment could not carry, and the amendment itself.
  let tailDrawn = false;
  const drawTail = (data) => {
    if (tailDrawn) return;
    tailDrawn = true;
    // What the extraction read from the description (detected_facts): each
    // part with the facts stated about it and the words they came from, so a
    // wrong fact is seen before the analysis. Read only: the amendment below
    // corrects one.
    const detected = data.detected_facts || [];
    if (detected.length) {
      const TYPES = {
        Process: "process", DataStore: "data store", DataFlow: "data flow",
        ExternalEntity: "external party", TrustBoundary: "trust zone",
      };
      const review = document.createElement("details");
      review.className = "detected";
      const title = document.createElement("summary");
      title.textContent = `What your description says (${counted(detected.length, "part", "parts")})`;
      review.append(title, hint("These facts were read from your description. Where one is wrong, or"
        + " a part is missing, say so in the box below."));
      for (const part of detected) {
        const line = document.createElement("p");
        const name = document.createElement("b");
        name.textContent = part.name;
        const facts = part.facts.map((fact) => `${fact.attribute.replaceAll("_", " ")}: ${fact.value}`
          + (fact.inferred ? ` (inferred: ${fact.inferred})` : ""));
        line.append(name, ` (${TYPES[part.type] || part.type})`, facts.length ? ` — ${facts.join("; ")}` : "");
        review.append(line);
        if (part.excerpt) review.append(hint(`From ${part.source_label}: “${part.excerpt}”`));
      }
      tail.append(review);
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
      follow(body.run, "Reading your description and identifying the parts of your system…", true);
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
    const covered = sharedKeys(data.answered_shares || []);
    // The answers the earlier analysis gave, which this run kept (ADR 0079).
    const carriedKeys = new Set([
      ...(data.carried_kept || []).map(questionKey),
      ...(data.carried_kept_links || []).map(questionKey),
    ]);
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
        kept.textContent = ` — ${kind === "link" ? linkAnswerText(before) : answerText(before)}`;
        // Changing it here makes it this part's own answer (ADR 0114).
        if (covered.has(key)) kept.textContent += " (shared answer)";
        // Changing it here makes it this run's own answer.
        if (carriedKeys.has(key)) kept.textContent += " (from the earlier analysis)";
      }
      row.append(kept);
      const open = button(before ? "Change" : "Answer it", () => {
        open.hidden = true;
        kept.hidden = true;
        const entry = { q, kind, node: row, saved: before || null, listed: false, lazyBox: box };
        rows.set(key, entry);
        drawAlone(entry, row, before || null);
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
      ...(data.skipped_links || []).map((q) => questionKey(q.key)),
      ...(data.skipped_early || []).map((q) => questionKey(q.key)),
    ]);
    const answered = outside([
      ...(data.answered_links || []).map((a) => ({ q: a, kind: "link", before: { principal: a.principal, element: a.answer.element } })),
      ...(data.answered_early || []).map((a) => ({ q: a, kind: "fact", before: a.answer })),
    ]).filter((entry) => !setAside.has(rowKey(entry)));
    redraw(earlierBox, (n) => `Your answers (${n})`, answered);
    const skipped = outside([
      ...(data.skipped_links || []).map((q) => ({ q, kind: "link", before: null })),
      ...(data.skipped_early || []).map((q) => ({ q, kind: "fact", before: held.get(questionKey(q.key)) || null })),
    ]);
    redraw(skippedBox, (n) => `Skipped for now (${n}, optional)`, skipped);
    const more = outside([...(data.early_held_back || []), ...(data.early_below_floor || [])]
      .map((q) => ({ q, kind: "fact", before: held.get(questionKey(q.key)) || null })));
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
      batchOpened = false;
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
  // The capability rows that "no" to the rest answers: listed, open, blank
  // and offering "no". A "no" rules out every unit that needs the capability,
  // so a row with an answer, a typed detail, or a skip is left as it is.
  const blankCapabilities = () => [...rows.values()].filter((row) =>
    row.listed && !row.compact && !row.node.hidden && row.kind === "fact"
    && row.q.kind === "capability" && !row.read() && !row.detail()
    && (row.q.choices || []).some((choice) => choice.id === "no"));
  // Keep bulk-answer and start actions in sync with the changing question set.
  updateQuestionActions = () => {
    if (!view) return;
    const left = view.early_stop == null;
    const blank = blankCapabilities().length;
    noRestButton.hidden = !blank;
    noRestHint.hidden = !blank;
    noRestButton.disabled = answerBusy;
    noRestButton.textContent = "Answer “no” to every question about the application"
      + ` left blank (${blank})`;
    skipButton.disabled = answerBusy || !left;
    startButton.className = left ? "" : "primary";
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
          follow(payload.run, "Using your answers to continue the analysis…", false);
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
      follow((await resumed.json()).run, "Using your answers to continue the analysis…", false);
    } finally {
      starting = false;
      startButton.disabled = false;
    }
  };

  const answerAction = async (action) => {
    if (answerBusy) return;
    answerBusy = true;
    const controls = [skipButton, startButton, noRestButton];
    controls.forEach((control) => { control.disabled = true; });
    try { await action(); }
    catch { refuse("The connection was interrupted. Reload this analysis to check whether your answers were saved."); }
    finally {
      answerBusy = false;
      controls.forEach((control) => { control.disabled = false; });
      updateQuestionActions();
    }
  };
  startButton.addEventListener("click", () => answerAction(startAnalysis));

  // Answer "no" to each of them, as each row's own answer, saved as any
  // answer is. Each one stays in its place and can be changed.
  noRestButton.addEventListener("click", () => {
    for (const row of blankCapabilities()) {
      row.set({ value: "no" });
      onEdit(row);
    }
  });

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

  asked.addEventListener("input", () => updateQuestionActions());
  asked.addEventListener("change", () => updateQuestionActions());
  asked.addEventListener("click", () => updateQuestionActions());

  // Follow one run's progress to its end: a report, questions, or a failure.
  const follow = (runId, initialStage, pauses) => {
    go.disabled = true;
    if (typeof workspace !== "undefined") workspace.track(runId);
    working(initialStage);
    if (typeof workspace !== "undefined") workspace.processing([], pauses, initialStage, runId);
    const stream = new EventSource("/events/" + encodeURIComponent(runId));
    stream.addEventListener("error", () => {
      stream.close();
      fail("The progress connection was interrupted. Open Analyses to check this run before starting another.");
    });
    stream.addEventListener("questions", (event) => {
      stream.close();
      idle();
      showQuestions(JSON.parse(event.data));
    });
    stream.addEventListener("node", (event) => {
      if (typeof workspace !== "undefined") {
        workspace.processing([JSON.parse(event.data).node], pauses, undefined, runId);
      }
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
    stream.addEventListener("stopped", () => {
      stream.close();
      fail("You stopped the analysis.");
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
    follow(followed, "Using your answers to continue the analysis…", false);
  }
