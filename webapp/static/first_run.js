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
  const saveButton = document.getElementById("save");
  const skipButton = document.getElementById("skip");
  const skippedBox = document.getElementById("skipped");
  const moreBox = document.getElementById("more");
  const earlierBox = document.getElementById("earlier");

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

  // A paused run's questions: one select per principal. "" leaves a question
  // unanswered; "none" says the principal is no element. Every label and
  // option is untrusted and lands as text.
  let pausedRun = null;
  // The paused run's round revision this page read. A save or a start sends
  // it, so a page left open on an earlier round cannot write over a later one.
  let revision = 0;
  // One reader per question: its answer as the service takes it, or null.
  let answers = [];
  // This round's questions, each with its key and reader, so "Skip the rest"
  // can name the ones left blank.
  let roundRows = [];
  // The answer that says the submitter does not know. The service writes
  // nothing for it, so the fact stays open.
  const DONT_KNOW = "unknown";
  // How much of an element's source words a row shows; the rest is its title.
  const EXCERPT = 200;
  // The answers a facet takes, as the service lists them in FACET_ANSWERS.
  const FACET_CHOICES = [
    ["yes", "yes"], ["no", "no"], ["not applicable", "not applicable"],
    ["I don't know", DONT_KNOW],
  ];
  // The inputs for one early question: a select of its choices, a control's
  // none / don't know / mechanism, or a line of text. `prefill` is an earlier
  // answer's value. The round and "Your answers" both build with this, so
  // the two cannot differ.
  const optionOf = (label, value) => {
    const choice = document.createElement("option");
    choice.value = value;
    choice.textContent = label;
    return choice;
  };
  const facetSelect = (blank) => {
    const select = document.createElement("select");
    select.append(optionOf(blank, ""));
    for (const [label, value] of FACET_CHOICES) select.append(optionOf(label, value));
    return select;
  };
  let suggestLists = 0;
  const withDetail = (answer, detail) => (detail ? { ...answer, detail } : answer);
  const inputFor = (q, prefill, prefillDetail) => {
    let input;
    // `read` is the answer the row sends, or "" for none; `set` writes an
    // answer into the row, as "Same for all" and an earlier answer do.
    let read = () => input.value.trim();
    let set;
    // `beside` is what follows the label.
    let beside;
    // A closed answer takes a detail beside it; free text needs none.
    let detail = null;
    if (q.choices.length) {
      input = document.createElement("select");
      input.append(optionOf("(leave unanswered)", ""));
      for (const option of q.choices) {
        input.append(optionOf(option.name ? `${option.name} (${option.id})` : option.id, option.id));
      }
      input.append(optionOf("I don't know", DONT_KNOW));
      set = (value) => { input.value = value; };
      detail = answerDetail(prefillDetail || "");
      beside = [input, " ", detail];
    } else if (q.form === "control") {
      // A control: say there is none, say you do not know, or name the
      // mechanism. The suggestions are a start; the text is the answer.
      input = document.createElement("input");
      input.type = "text";
      input.maxLength = q.max_length;
      input.placeholder = "type it, or pick a common one";
      const list = document.createElement("datalist");
      list.id = `early-suggest-${suggestLists++}`;
      for (const suggestion of q.suggestions) list.append(optionOf(suggestion, suggestion));
      input.setAttribute("list", list.id);
      const state = document.createElement("select");
      state.append(optionOf("(leave unanswered)", ""), optionOf("There is none", "none"),
        optionOf("I don't know", DONT_KNOW), optionOf("A mechanism, in my own words:", "mechanism"));
      // The state is the answer: blank sends nothing, and the text is read
      // only under "mechanism". Typing a mechanism chooses it.
      const fix = () => { input.disabled = state.value === "none" || state.value === DONT_KNOW; };
      state.addEventListener("change", fix);
      input.addEventListener("input", () => { if (input.value.trim()) state.value = "mechanism"; });
      set = (value) => {
        const fixed = value === "none" || value === DONT_KNOW;
        state.value = fixed || !value ? value : "mechanism";
        input.value = fixed ? "" : value;
        fix();
      };
      read = () => (state.value === "mechanism" ? input.value.trim() : state.value);
      beside = [state, " ", input, list];
    } else {
      input = document.createElement("input");
      input.type = "text";
      input.maxLength = q.max_length;
      input.placeholder = "(leave unanswered)";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.addEventListener("change", () => {
        input.value = box.checked ? DONT_KNOW : "";
        input.disabled = box.checked;
      });
      const dontKnow = document.createElement("label");
      dontKnow.append(box, " I don't know");
      set = (value) => {
        input.value = value;
        box.checked = input.disabled = value === DONT_KNOW;
      };
      beside = [input, " ", dontKnow];
    }
    set(prefill || "");
    input.dataset.key = JSON.stringify(q.key);
    const readDetail = () => (detail ? detail.value.trim() : "");
    return { input, beside, read, set, readDetail };
  };

  // A paused run's round: the link questions, then one box per open-fact
  // category in first-appearance order, and every earlier answer below them.
  // Every label and option is untrusted and lands as text.
  let updateQuestionProgress = () => {};
  const showQuestions = (data) => {
    // The service says whether the pause asks anything more (QuestionSet.stop).
    const left = data.stop == null;
    revision = data.revision;
    const saved = (data.answered || []).length || (data.answered_links || []).length
      || (data.skipped || []).length || (data.skipped_links || []).length;
    // Each selected analysis whose precondition does not hold yet
    // (QuestionSet.gates): it will not run as the model reads now.
    const gates = Object.entries(data.gates || {}).filter(([, state]) => state !== "satisfied");
    // Questions no round asks, which the submitter may still choose to answer.
    const optional = (data.held_back || []).length + (data.below_floor || []).length;
    // A pause with nothing to ask, nothing answered or skipped, no optional
    // question and no analysis to explain starts the analysis, with no page
    // between. After a save, only the start button starts it: a round of skips
    // is a save too.
    if (!left && !saved && !gates.length && !optional) {
      pausedRun = data.run;
      startAnalysis([], []);
      return;
    }
    pausedRun = data.run;
    saveButton.hidden = skipButton.hidden = false;
    skipButton.disabled = !left;
    answers = [];
    roundRows = [];
    questions.replaceChildren();
    earlierBox.replaceChildren();
    skippedBox.replaceChildren();
    moreBox.replaceChildren();
    const heading = (title, text) => {
      const lead = document.createElement("p");
      const bold = document.createElement("b");
      bold.textContent = title;
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = text;
      lead.append(bold, hint);
      questions.append(lead);
    };
    // How much is left: an estimate, because an answer can add or take away
    // questions (ADR 0053).
    const remaining = data.remaining || {};
    const parts = [];
    if (remaining.gate) {
      parts.push(`${remaining.gate} question(s) that decide whether an analysis runs`);
    }
    if (remaining.capability) {
      parts.push(`about ${remaining.capability} yes/no question(s) about the application`);
    }
    if (remaining.field) {
      parts.push(`about ${remaining.field} question(s) about parts of your system`);
    }
    if (data.questions.length) {
      parts.push(`${data.questions.length} question(s) about which part of your system a name is`);
    }
    // Why an analysis will not run, or what it waits on, from the service's
    // own reading of its precondition.
    for (const [name, state] of gates) {
      const note = document.createElement("p");
      note.className = "hint gate-note";
      note.textContent = state === "refuted"
        ? `The system model rules out the ${name} analysis, so it will not run and`
          + " none of its questions are asked."
        : `The ${name} analysis runs only once the system model shows that it applies.`
          + " The questions that decide this come first; its other questions follow"
          + " once an answer settles it.";
      questions.append(note);
    }
    if (!left) {
      const ready = document.createElement("p");
      ready.className = "hint";
      // The service's own stop reason decides the sentence (QuestionSet.stop).
      // No stop says every fact is settled, so the page lists what stays open.
      const stops = {
        "budget-exhausted": `The question limit is reached, so ${data.withheld} more question(s)`
          + " are not asked in a round. ",
        "below-floor": "No recommended question is left. ",
        "skipped": "You skipped every question that is left. ",
        "nothing-left": "No question is left to ask. ",
      };
      ready.textContent = stops[data.stop]
        + "You can still change an answer below. Nothing runs until you choose Start the analysis.";
      questions.append(ready);
    }
    // What the pause leaves open (PauseSummary): never "everything is settled".
    const summary = data.summary;
    if (summary) {
      const open = [];
      const count = (n, text) => { if (n) open.push(`${n} ${text}`); };
      count(summary.skipped, "question(s) skipped for now");
      count(summary.partial, "question(s) answered in part");
      count(summary.unknown, "answer(s) of \u201cI don't know\u201d");
      count(summary.held_back, "question(s) held back by the question limit");
      count(summary.below_floor, "question(s) ranked below the threshold for a round");
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
        const list = document.createElement("ul");
        list.className = "still-open";
        for (const line of open) {
          const item = document.createElement("li");
          item.textContent = line;
          list.append(item);
        }
        questions.append(lead, list);
      }
    }
    // The round's choices as the page shows them: each link question is one,
    // and a part counts only while it shows, after its parent's "yes". The
    // line is told again each time a part shows or hides.
    const counted = [];
    const estimate = document.createElement("p");
    estimate.className = "hint";
    const choicesOf = (rows) => rows.reduce((sum, row) => sum + row.decisions, 0);
    const shownChoices = () => choicesOf(counted.filter((row) => !row.hidden())) + data.questions.length;
    const tally = () => {
      const shown = shownChoices();
      const later = choicesOf(counted.filter((row) => row.hidden()));
      estimate.textContent = `There are ${parts.join(" and ")} that can change the`
        + ` analysis. This round asks ${shown} choice(s).`
        + (later ? ` Up to ${later} more appear if you answer \u201cyes\u201d.` : "")
        + (summary ? ` So far the rounds showed ${summary.introduced} question(s),`
          + ` ${summary.choices} choice(s).` : "");
    };
    if (parts.length) {
      const explanation = document.createElement("details");
      explanation.className = "round-estimate";
      const title = document.createElement("summary");
      title.textContent = "How this round is counted";
      explanation.append(title, estimate);
      questions.append(explanation);
    }
    if (data.questions.length) {
      heading("Which element is each of these?",
        "Your description states facts about these people or systems, but not"
        + " which element of the model each one is.");
    }
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
    for (const q of data.questions) {
      const row = document.createElement("p");
      const label = document.createElement("label");
      const name = document.createElement("b");
      name.textContent = q.principal;
      const select = linkSelect(q, "");
      label.append(name, ` — places ${q.rows} stated fact(s) `, select);
      row.append(label);
      questions.append(row);
      // A skipped link places nothing: it is not "None of these".
      roundRows.push({ key: q.key, read: () => select.value });
    }
    // One box per category, ordered by its first appearance in the round.
    // Build rows in service order so parents exist before their dependants,
    // and append each row to its category even when other categories intervene.
    // An earlier answer by its key: a question with facets comes back while a
    // facet has no answer, and its answered facets are filled in.
    const earlier = new Map((data.answered || []).map((a) => [JSON.stringify(a.key), a.answer]));
    const groups = new Map();
    for (const q of data.facts) {
      if (!groups.has(q.group)) {
        const box = document.createElement("details");
        // The workspace's index lists each box of this class as a group.
        box.className = "question-group";
        box.open = true;
        const title = document.createElement("summary");
        // "Same for all" goes here, above the rows, where a group takes one.
        const top = document.createElement("div");
        box.append(title, top);
        groups.set(q.group, { box, title, top, heading: q.group_heading, count: 0, rows: [] });
      }
    }
    // A group takes "Same for all" where two or more rows ask one attribute
    // or kind about different elements in one form. A capability asks about
    // the whole application, so it never shares.
    const shareable = new Set([...groups.keys()].filter((name) => {
      const rows = data.facts.filter((q) => q.group === name && q.form !== "facets");
      const [first] = rows;
      return rows.length >= 2 && rows.every((q) => q.key[0]
        && q.key[1] === first.key[1] && q.key[4] === first.key[4] && q.form === first.form
        && JSON.stringify(q.choices) === JSON.stringify(first.choices));
    }));
    // A question with facets is a table: a column per facet, a row per
    // element, and a first row that sets the whole column.
    const facetTable = (into, q, withAll) => {
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
      if (withAll) {
        const all = document.createElement("tr");
        const allLabel = document.createElement("td");
        allLabel.textContent = "Same for all";
        all.append(allLabel);
        q.facets.forEach((facet, column) => {
          const cell = document.createElement("td");
          const every = facetSelect("(set every row)");
          every.addEventListener("change", () => {
            for (const select of columns[column]) select.value = every.value;
          });
          cell.append(every);
          all.append(cell);
        });
        // The detail column takes no shared answer: each row's are its own.
        all.append(document.createElement("td"));
        table.append(all);
      }
      into.append(table);
      return { table, columns };
    };
    // One row of a facet table, filled in from `prefill`, and its reader.
    const facetRow = (grid, q, label, prefill, prefillDetail) => {
      const row = document.createElement("tr");
      const name = document.createElement("td");
      name.append(label);
      row.append(name);
      const selects = q.facets.map((facet, column) => {
        const select = facetSelect("(leave unanswered)");
        select.dataset.key = JSON.stringify(q.key);
        select.dataset.facet = facet.id;
        select.value = (prefill && prefill[facet.id]) || "";
        grid.columns[column].push(select);
        const cell = document.createElement("td");
        cell.append(select);
        row.append(cell);
        return select;
      });
      const detail = answerDetail(prefillDetail || "");
      const detailCell = document.createElement("td");
      detailCell.append(detail);
      row.append(detailCell);
      grid.table.append(row);
      const read = () => {
        const given = {};
        for (const select of selects) {
          if (select.value) given[select.dataset.facet] = select.value;
        }
        return Object.keys(given).length
          ? withDetail({ key: q.key, facets: given }, detail.value.trim())
          : null;
      };
      // Whether a facet is still blank, which "Skip the rest" sets aside.
      read.open = () => selects.some((select) => !select.value);
      return read;
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
    // Why a question is asked, which analysis it serves, and the words of the
    // description its element was read from. The words are what the service
    // read, not an answer to the question.
    const context = (q) => {
      const parts = [];
      if (q.reasons.length) parts.push(`Why: ${q.reasons[0]}`);
      const use = serves(q);
      if (use) parts.push(use);
      if (q.excerpt) {
        const cut = q.excerpt.length > EXCERPT ? `${q.excerpt.slice(0, EXCERPT)}\u2026` : q.excerpt;
        parts.push(`Your description of this part, for reference: \u201c${cut}\u201d`);
      }
      if (!parts.length) return null;
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = parts.join(" \u00b7 ");
      hint.title = q.excerpt;
      return hint;
    };
    // Each answer's input by its key, so a capability that is part of another
    // can follow its parent's answer.
    const inputs = new Map();
    // Each choice row by its key, and the parts that follow each answer.
    const rows = new Map();
    const followers = new Map();
    data.facts.forEach((q) => {
      const group = groups.get(q.group);
      group.count += 1;
      group.choices = (group.choices || 0) + (q.decisions || 1);
      const label = document.createElement("b");
      label.textContent = q.label;
      // Why the fact matters: the questions of the rules that fire on it.
      label.title = q.reasons.join(" ");
      const about = context(q);
      if (q.form === "facets") {
        // "Same for all" sets a column of two or more rows; one row needs none.
        const rows = data.facts.filter((f) => f.group === q.group && f.form === "facets");
        group.grid = group.grid || facetTable(group.box, q, rows.length >= 2);
        const before = earlier.get(JSON.stringify(q.key));
        const who = document.createElement("span");
        label.textContent = q.element;
        who.append(label);
        if (about) who.append(about);
        const read = facetRow(group.grid, q, who, before && before.facets, before && before.detail);
        answers.push(read);
        roundRows.push({ key: q.key, read, open: read.open });
        counted.push({ decisions: q.decisions || 1, hidden: () => false });
        return;
      }
      const row = document.createElement("p");
      const { input, beside, read, set, readDetail } = inputFor(q, "");
      inputs.set(input.dataset.key, input);
      if (shareable.has(q.group)) {
        // Ticked rows take the shared answer; an unticked row is an exception.
        const tick = document.createElement("input");
        tick.type = "checkbox";
        tick.checked = true;
        tick.title = "Take the answer under \"Same for all\"";
        tick.className = "share-answer";
        row.append(tick, " ");
        group.rows.push({ tick, set });
      }
      // A part of another capability is asked only once its parent is "yes".
      // A hidden row sends no answer, so a "no" to the parent is never
      // contradicted by an answer to its part.
      const parent = q.parent ? inputs.get(JSON.stringify(q.parent)) : null;
      counted.push({ decisions: q.decisions || 1, hidden: () => row.hidden });
      rows.set(input.dataset.key, row);
      if (parent) {
        // A part of a hidden part hides too, whatever its parent's answer.
        const above = rows.get(parent.dataset.key);
        const follow = () => {
          row.hidden = parent.value !== "yes" || above.hidden;
          for (const next of followers.get(input.dataset.key) || []) next();
          tally();
        };
        if (!followers.has(parent.dataset.key)) {
          followers.set(parent.dataset.key, []);
          parent.addEventListener("change", () => {
            for (const next of followers.get(parent.dataset.key)) next();
          });
        }
        followers.get(parent.dataset.key).push(follow);
        follow();
      }
      const answer = () => {
        const value = read();
        return value && !row.hidden ? withDetail({ key: q.key, value }, readDetail()) : null;
      };
      answers.push(answer);
      roundRows.push({ key: q.key, read: answer, hidden: () => row.hidden });
      row.append(label, " ", ...beside);
      if (about) row.append(about);
      group.box.append(row);
    });
    tally();
    for (const [name, group] of groups) {
      if (group.rows.length) {
        const shared = inputFor(data.facts.find((q) => q.group === name), "");
        const apply = document.createElement("button");
        apply.type = "button";
        apply.textContent = "Apply to the ticked rows";
        apply.addEventListener("click", () => {
          for (const row of group.rows) if (row.tick.checked) row.set(shared.read());
        });
        const line = document.createElement("p");
        const title = document.createElement("b");
        title.textContent = "Same for all";
        line.append(title, " ", ...shared.beside, " ", apply);
        const hint = document.createElement("div");
        hint.className = "hint";
        hint.textContent = "Untick a row the answer does not fit. Each row is sent as its"
          + " own answer, and you can still change it.";
        group.top.className = "shared-controls";
        group.top.append(line, hint);
      }
      const groupHeading = document.createElement("span");
      groupHeading.className = "group-heading";
      groupHeading.textContent = group.heading;
      const groupCount = document.createElement("small");
      groupCount.textContent = ` (${group.count} question(s), ${group.choices} choice(s))`;
      group.title.replaceChildren(groupHeading, groupCount);
      questions.append(group.box);
    }
    // Every earlier answer, each with a button that opens it again. An
    // answer opened and changed is sent with this round's answers. A question
    // answered in part and then skipped has its one editor under "Skipped for
    // now", so it is never sent twice.
    const answeredLinks = data.answered_links || [];
    const skipped = data.skipped || [];
    const elsewhere = new Set([...data.facts, ...skipped].map((q) => JSON.stringify(q.key)));
    const answeredFacts = (data.answered || []).filter((a) => !elsewhere.has(JSON.stringify(a.key)));
    earlierBox.hidden = !(answeredLinks.length || answeredFacts.length);
    const title = document.createElement("summary");
    title.textContent = `Your answers (${answeredLinks.length + answeredFacts.length})`;
    earlierBox.append(title);
    const said = (answer) => answer.facets
      ? Object.entries(answer.facets).map(([facet, value]) => `${facet}: ${value}`).join("; ")
      : (answer.value === DONT_KNOW ? "I don't know" : answer.value);
    const change = (row, open) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Change";
      button.addEventListener("click", () => {
        button.hidden = true;
        open();
      });
      row.append(" ", button);
    };
    for (const a of answeredLinks) {
      const row = document.createElement("p");
      const name = document.createElement("b");
      name.textContent = a.principal;
      const shown = document.createElement("span");
      shown.textContent = ` — ${a.answer.element === "none" ? "none of these" : a.answer.element}`;
      row.append(name, shown);
      change(row, () => { shown.replaceChildren(" — ", linkSelect(a, a.answer.element)); });
      earlierBox.append(row);
    }
    for (const a of answeredFacts) {
      const row = document.createElement("p");
      const label = document.createElement("b");
      label.textContent = a.label;
      const shown = document.createElement("span");
      shown.textContent = ` — ${said(a.answer)}`;
      row.append(label, shown);
      change(row, () => {
        if (a.form === "facets") {
          const grid = facetTable(row, a, false);
          const name = document.createElement("b");
          name.textContent = a.element;
          answers.push(facetRow(grid, a, name, a.answer.facets, a.answer.detail));
          shown.hidden = true;
          return;
        }
        const { beside, read, readDetail } = inputFor(a, a.answer.value, a.answer.detail);
        shown.replaceChildren(" — ", ...beside);
        answers.push(() => {
          const value = read();
          const changed = value !== a.answer.value || readDetail() !== (a.answer.detail || "");
          return value && changed ? withDetail({ key: a.key, value }, readDetail()) : null;
        });
      });
      earlierBox.append(row);
    }
    // Every question skipped for now. A skip is not an answer: no round shows
    // it again, and "Answer it" opens it here, with any part answered before.
    const skippedLinks = data.skipped_links || [];
    skippedBox.hidden = !(skipped.length || skippedLinks.length);
    const skippedTitle = document.createElement("summary");
    skippedTitle.textContent = `Skipped for now (${skipped.length + skippedLinks.length},`
      + " optional)";
    skippedBox.append(skippedTitle);
    for (const q of skippedLinks) {
      const row = document.createElement("p");
      const name = document.createElement("b");
      name.textContent = q.principal;
      row.append(name);
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "Answer it";
      open.addEventListener("click", () => {
        open.hidden = true;
        row.append(" \u2014 ", linkSelect(q, ""));
      });
      row.append(" ", open);
      skippedBox.append(row);
    }
    // A question no round shows, which the submitter may still answer: a
    // skipped one, or one a limit or a floor keeps out of the rounds.
    // "Answer it" opens it, with any part answered before.
    const answerLater = (box, q) => {
      const row = document.createElement("p");
      const label = document.createElement("b");
      label.textContent = q.label;
      row.append(label);
      const before = earlier.get(JSON.stringify(q.key));
      const kept = document.createElement("span");
      if (before) kept.textContent = ` \u2014 ${said(before)}`;
      row.append(kept);
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "Answer it";
      open.addEventListener("click", () => {
        open.hidden = true;
        if (q.form === "facets") {
          const name = document.createElement("b");
          name.textContent = q.element;
          kept.hidden = true;
          answers.push(facetRow(facetTable(row, q, false), q, name, before && before.facets,
            before && before.detail));
          return;
        }
        const { beside, read, readDetail } = inputFor(q, "");
        row.append(" ", ...beside);
        answers.push(() => (read() ? withDetail({ key: q.key, value: read() }, readDetail()) : null));
      });
      row.append(" ", open);
      box.append(row);
    };
    for (const q of skipped) answerLater(skippedBox, q);
    // Questions the rounds do not ask: past the question limit, or ranked
    // under the threshold. Answering one is the submitter's choice.
    const heldBack = data.held_back || [];
    const belowFloor = data.below_floor || [];
    moreBox.hidden = !(heldBack.length || belowFloor.length);
    const moreTitle = document.createElement("summary");
    moreTitle.textContent = `More questions (${heldBack.length + belowFloor.length},`
      + " optional, not asked in a round)";
    moreBox.append(moreTitle);
    const moreHint = document.createElement("p");
    moreHint.className = "hint";
    moreHint.textContent = "The rounds leave these out: some are past the question limit,"
      + " and others rank below the threshold for a round. An answer to one still"
      + " reaches the analysis.";
    moreBox.append(moreHint);
    for (const q of [...heldBack, ...belowFloor]) answerLater(moreBox, q);
    // Answers the run an amendment started carried and could not take: each
    // names a part the amended model no longer has, or a fact it now states
    // (AnswerState.carried, ADR 0072).
    const dropped = [
      ...(data.carried_dropped || []).map((a) => `${a.label} \u2014 ${a.facets
        ? Object.entries(a.facets).map(([facet, value]) => `${facet}: ${value}`).join("; ")
        : (a.value === DONT_KNOW ? "I don't know" : a.value)}`),
      ...(data.carried_dropped_links || []).map((a) => `${a.principal} \u2014 ${
        a.element === "none" ? "none of these" : a.element}`),
    ];
    if (dropped.length) {
      const lead = document.createElement("p");
      lead.className = "hint";
      lead.textContent = `${dropped.length} earlier answer(s) do not fit the amended`
        + " system model, so they are not kept:";
      const list = document.createElement("ul");
      for (const line of dropped) {
        const item = document.createElement("li");
        item.textContent = line;
        list.append(item);
      }
      questions.append(lead, list);
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
    const fixButton = document.createElement("button");
    fixButton.type = "button";
    fixButton.textContent = "Read the description again with this";
    fixButton.addEventListener("click", () => answerAction(async () => {
      const text = fixText.value.trim();
      if (!text) {
        refuse("Write the amendment first.");
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
    }));
    fix.append(fixTitle, fixHint, fixText, fixButton);
    questions.append(fix);
    updateQuestionProgress = () => {
      const visible = shownChoices();
      const filled = roundRows.reduce((sum, row) => {
        const answer = row.read();
        return sum + (!answer ? 0 : answer.facets ? Object.keys(answer.facets).length : 1);
      }, 0);
      const submitted = roundAnswers();
      saveButton.disabled = !left && !submitted.facts.length && !submitted.links.length;
      skipButton.disabled = !left;
      document.getElementById("continue").className = left ? "" : "primary";
      if (typeof workspace !== "undefined") workspace.progress(data, filled, visible);
    };
    updateQuestionProgress();
    asked.hidden = false;
    if (typeof workspace !== "undefined") workspace.grouped(data);
  };

  // The round's answers, as the service takes them.
  const roundAnswers = () => ({
    links: [questions, earlierBox, skippedBox].flatMap((box) => [...box.querySelectorAll("select")])
      .filter((select) => select.dataset.principal && select.value)
      .map((select) => ({ principal: select.dataset.principal, element: select.value })),
    facts: answers.map((read) => read()).filter(Boolean),
  });

  // A refused answer is shown beside the buttons that sent it, and every
  // answer stays as it was, for the submitter to correct.
  const answerProblem = document.getElementById("answer-problem");
  const refuse = (message) => {
    answerProblem.textContent = message;
    answerProblem.hidden = false;
  };

  const startAnalysis = async (links, facts) => {
    const resumed = await fetch("/answer/" + pausedRun, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links, facts, revision }),
    });
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
  };

  let answerBusy = false;
  const answerAction = async (action) => {
    if (answerBusy) return;
    answerBusy = true;
    const controls = [saveButton, skipButton, document.getElementById("continue")];
    controls.forEach((control) => { control.disabled = true; });
    try { await action(); }
    catch { refuse("The connection was interrupted. Reload this analysis to check whether your answers were saved."); }
    finally {
      answerBusy = false;
      controls.forEach((control) => { control.disabled = false; });
      updateQuestionProgress();
    }
  };
  document.getElementById("continue").addEventListener("click", () => {
    const { links, facts } = roundAnswers();
    answerAction(() => startAnalysis(links, facts));
  });

  // Save the round and show the next one. A save never starts the analysis;
  // where nothing is left to ask, the page says so and waits.
  // `skipAll` also skips every question of this round left blank.
  const saveRound = async (skipAll) => {
    const { links, facts } = roundAnswers();
    // A blank row is skipped, and so is a row with facets answered in part:
    // its facets given are kept, and the rest are set aside for now. A part
    // hidden under its parent's answer was never shown, so it is not skipped:
    // the service asks it once it shows (QuestionSet.presented).
    const skip = skipAll
      ? roundRows
        .filter((row) => !(row.hidden && row.hidden()))
        .filter((row) => !row.read() || (row.open && row.open()))
        .map((row) => row.key)
      : [];
    const saved = await fetch("/answer/" + pausedRun, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links, facts, save: true, skip, revision }),
    });
    const body = await saved.json();
    if (!saved.ok) {
      refuse(body.message);
      return;
    }
    answerProblem.hidden = true;
    showQuestions(body);
    window.scrollTo(0, 0);
  };
  saveButton.addEventListener("click", () => answerAction(() => saveRound(false)));
  skipButton.addEventListener("click", () => answerAction(() => saveRound(true)));

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
      const list = document.createElement("ul");
      for (const issue of JSON.parse(event.data).issues) {
        const item = document.createElement("li");
        const code = document.createElement("code");
        code.textContent = issue.code;
        item.append(code, " " + issue.message);
        list.append(item);
      }
      fail(lead, list);
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

