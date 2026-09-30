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

  // An unticked framework's options are hidden rather than removed, so
  // re-ticking it restores what was chosen instead of resetting it.
  const sync = (checkbox) => {
    checkbox.closest(".pick").querySelector(".opts").hidden = !checkbox.checked;
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
    box.value = await (await fetch("/example")).text();
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
  const earlierBox = document.getElementById("earlier");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.hidden = true;
    ticks.replaceChildren();
    go.disabled = true;

    const started = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sources: [
          { kind: "description", label: "Pasted description", text: box.value },
        ],
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
  const inputFor = (q, prefill) => {
    let input;
    // `read` is the answer the row sends, or "" for none; `set` writes an
    // answer into the row, as "Same for all" and an earlier answer do.
    let read = () => input.value.trim();
    let set;
    // `beside` is what follows the label.
    let beside;
    if (q.choices.length) {
      input = document.createElement("select");
      input.append(optionOf("(leave unanswered)", ""));
      for (const option of q.choices) {
        input.append(optionOf(option.name ? `${option.name} (${option.id})` : option.id, option.id));
      }
      input.append(optionOf("I don't know", DONT_KNOW));
      set = (value) => { input.value = value; };
      beside = [input];
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
    return { input, beside, read, set };
  };

  // A paused run's round: the link questions, then the open facts grouped by
  // kind of question or attribute, and every earlier answer below them.
  // Every label and option is untrusted and lands as text.
  const showQuestions = (data) => {
    // The service says whether the pause asks anything more (QuestionSet.stop).
    const left = data.stop == null;
    revision = data.revision;
    const saved = (data.answered || []).length || (data.answered_links || []).length
      || (data.skipped || []).length;
    // A pause with nothing to ask and nothing answered or skipped starts the
    // analysis, with no page between. After a save, only the start button
    // starts it: a round of skips is a save too.
    if (!left && !saved) {
      pausedRun = data.run;
      startAnalysis([], []);
      return;
    }
    pausedRun = data.run;
    saveButton.hidden = skipButton.hidden = !left;
    answers = [];
    roundRows = [];
    questions.replaceChildren();
    earlierBox.replaceChildren();
    skippedBox.replaceChildren();
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
    if (remaining.capability) {
      parts.push(`about ${remaining.capability} yes/no question(s) about the application`);
    }
    if (remaining.field) {
      parts.push(`about ${remaining.field} question(s) about parts of your system`);
    }
    if (!left) {
      const ready = document.createElement("p");
      ready.className = "hint";
      // The service's own stop reason decides the sentence (QuestionSet.stop).
      ready.textContent = (data.stop === "budget-exhausted"
        ? `The question limit is reached, so ${data.withheld} more question(s) will not be asked. `
        : "No question is left. ")
        + "You can still change an answer below. Nothing runs until you choose Start the analysis.";
      questions.append(ready);
    }
    if (parts.length) {
      const estimate = document.createElement("p");
      estimate.className = "hint";
      // Each link question is one choice too, though no round limit counts it.
      const choices = data.facts.reduce((sum, q) => sum + q.decisions, 0) + data.questions.length;
      estimate.textContent = `There are ${parts.join(" and ")} that can change the`
        + ` analysis. This round asks ${choices} choice(s).`;
      questions.append(estimate);
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
      label.append(name, ` — places ${q.rows} stated fact(s) `, linkSelect(q, ""));
      row.append(label);
      questions.append(row);
    }
    // The open facts, asked once per kind of question or attribute with a row
    // per element. A group ranks by its best question, so the order of the
    // list is kept. The first groups are open, and the rest are one click away.
    const OPEN_GROUPS = 5;
    if (data.facts.length) {
      heading("Facts your description does not state",
        "Each question is asked once, with a row for each part of your system it"
        + " applies to. The first ones are the most likely to matter. Leave any"
        + " row blank that you cannot answer.");
    }
    // An earlier answer by its key: a question with facets comes back while a
    // facet has no answer, and its answered facets are filled in.
    const earlier = new Map((data.answered || []).map((a) => [JSON.stringify(a.key), a.answer]));
    const groups = new Map();
    for (const q of data.facts) {
      if (!groups.has(q.group)) {
        const box = document.createElement("details");
        box.open = groups.size < OPEN_GROUPS;
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
        table.append(all);
      }
      into.append(table);
      return { table, columns };
    };
    // One row of a facet table, filled in from `prefill`, and its reader.
    const facetRow = (grid, q, label, prefill) => {
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
      grid.table.append(row);
      const read = () => {
        const given = {};
        for (const select of selects) {
          if (select.value) given[select.dataset.facet] = select.value;
        }
        return Object.keys(given).length ? { key: q.key, facets: given } : null;
      };
      // Whether a facet is still blank, which "Skip the rest" sets aside.
      read.open = () => selects.some((select) => !select.value);
      return read;
    };
    // Why a question is asked, and the words of the description its element
    // was read from, so the owner sees what the service read.
    const context = (q) => {
      const parts = [];
      if (q.reasons.length) parts.push(`Why: ${q.reasons[0]}`);
      if (q.excerpt) {
        const cut = q.excerpt.length > EXCERPT ? `${q.excerpt.slice(0, EXCERPT)}\u2026` : q.excerpt;
        parts.push(`Your description: \u201c${cut}\u201d`);
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
    data.facts.forEach((q) => {
      const group = groups.get(q.group);
      group.count += 1;
      const label = document.createElement("b");
      label.textContent = q.element;
      // Why the fact matters: the questions of the rules that fire on it.
      label.title = q.reasons.join(" ");
      const about = context(q);
      if (q.form === "facets") {
        group.grid = group.grid || facetTable(group.box, q, true);
        const before = earlier.get(JSON.stringify(q.key));
        const who = document.createElement("span");
        who.append(label);
        if (about) who.append(about);
        const read = facetRow(group.grid, q, who, before && before.facets);
        answers.push(read);
        roundRows.push({ key: q.key, read, open: read.open });
        return;
      }
      const row = document.createElement("p");
      const { input, beside, read, set } = inputFor(q, "");
      inputs.set(input.dataset.key, input);
      if (shareable.has(q.group)) {
        // Ticked rows take the shared answer; an unticked row is an exception.
        const tick = document.createElement("input");
        tick.type = "checkbox";
        tick.checked = true;
        tick.title = "Take the answer under \"Same for all\"";
        row.append(tick, " ");
        group.rows.push({ tick, set });
      }
      // A part of another capability is asked only once its parent is "yes".
      // A hidden row sends no answer, so a "no" to the parent is never
      // contradicted by an answer to its part.
      const parent = q.parent ? inputs.get(JSON.stringify(q.parent)) : null;
      if (parent) {
        const follow = () => { row.hidden = parent.value !== "yes"; };
        parent.addEventListener("change", follow);
        follow();
      }
      const answer = () => {
        const value = read();
        return value && !row.hidden ? { key: q.key, value } : null;
      };
      answers.push(answer);
      roundRows.push({ key: q.key, read: answer });
      row.append(label, " ", ...beside);
      if (about) row.append(about);
      group.box.append(row);
    });
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
        group.top.append(line, hint);
      }
      group.title.textContent = `${group.heading} (${group.count})`;
      questions.append(group.box);
    }
    // Every earlier answer, each with a button that opens it again. An
    // answer opened and changed is sent with this round's answers.
    const answeredLinks = data.answered_links || [];
    const answeredFacts = (data.answered || []).filter(
      (a) => !data.facts.some((q) => JSON.stringify(q.key) === JSON.stringify(a.key)));
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
          answers.push(facetRow(grid, a, name, a.answer.facets));
          shown.hidden = true;
          return;
        }
        const { beside, read } = inputFor(a, a.answer.value);
        shown.replaceChildren(" — ", ...beside);
        answers.push(() => {
          const value = read();
          return value && value !== a.answer.value ? { key: a.key, value } : null;
        });
      });
      earlierBox.append(row);
    }
    // Every question skipped for now. A skip is not an answer: no round shows
    // it again, and "Answer it" opens it here.
    const skipped = data.skipped || [];
    skippedBox.hidden = !skipped.length;
    const skippedTitle = document.createElement("summary");
    skippedTitle.textContent = `Skipped for now (${skipped.length})`;
    skippedBox.append(skippedTitle);
    for (const q of skipped) {
      const row = document.createElement("p");
      const label = document.createElement("b");
      label.textContent = q.label;
      row.append(label);
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "Answer it";
      open.addEventListener("click", () => {
        open.hidden = true;
        if (q.form === "facets") {
          const name = document.createElement("b");
          name.textContent = q.element;
          answers.push(facetRow(facetTable(row, q, false), q, name, null));
          return;
        }
        const { beside, read } = inputFor(q, "");
        row.append(" ", ...beside);
        answers.push(() => (read() ? { key: q.key, value: read() } : null));
      });
      row.append(" ", open);
      skippedBox.append(row);
    }
    asked.hidden = false;
  };

  // The round's answers, as the service takes them.
  const roundAnswers = () => ({
    links: [...questions.querySelectorAll("select"), ...earlierBox.querySelectorAll("select")]
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

  document.getElementById("continue").addEventListener("click", () => {
    const { links, facts } = roundAnswers();
    startAnalysis(links, facts);
  });

  // Save the round and show the next one. A save never starts the analysis;
  // where nothing is left to ask, the page says so and waits.
  // `skipAll` also skips every question of this round left blank.
  const saveRound = async (skipAll) => {
    const { links, facts } = roundAnswers();
    // A blank row is skipped, and so is a row with facets answered in part:
    // its facets given are kept, and the rest are set aside for now.
    const skip = skipAll
      ? roundRows.filter((row) => !row.read() || (row.open && row.open())).map((row) => row.key)
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
  saveButton.addEventListener("click", () => saveRound(false));
  skipButton.addEventListener("click", () => saveRound(true));

  // Follow one run's progress to its end: a report, questions, or a failure.
  const follow = (runId, message) => {
    go.disabled = true;
    working(message);
    ticks.replaceChildren();
    ticks.hidden = false;
    const stream = new EventSource("/events/" + runId);
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
    });
    stream.addEventListener("done", (event) => {
      stream.close();
      location.href = JSON.parse(event.data).url;
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
