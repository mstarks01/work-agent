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
  let factInputs = [];
  // The answer that says the submitter does not know. The service writes
  // nothing for it, so the fact stays open.
  const DONT_KNOW = "unknown";
  const showQuestions = (data) => {
    pausedRun = data.run;
    factInputs = [];
    questions.replaceChildren();
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
    if (data.questions.length) {
      heading("Which element is each of these?",
        "Your description states facts about these people or systems, but not"
        + " which element of the model each one is.");
    }
    for (const q of data.questions) {
      const row = document.createElement("p");
      const label = document.createElement("label");
      const name = document.createElement("b");
      name.textContent = q.principal;
      const select = document.createElement("select");
      select.dataset.principal = q.principal;
      const skip = document.createElement("option");
      skip.value = "";
      skip.textContent = "(leave unanswered)";
      select.append(skip);
      for (const option of q.options) {
        const choice = document.createElement("option");
        choice.value = option.id;
        choice.textContent = option.name ? `${option.name} (${option.id})` : option.id;
        select.append(choice);
      }
      const none = document.createElement("option");
      none.value = "none";
      none.textContent = "None of these";
      select.append(none);
      label.append(name, ` \u2014 places ${q.rows} stated fact(s) `, select);
      row.append(label);
      questions.append(row);
    }
    // The open facts, ranked before any finding exists, asked once per kind of
    // question or attribute with a row per element. A group ranks by its best
    // question, so the order of the list is kept. The first groups are open,
    // and the rest are one click away.
    const OPEN_GROUPS = 5;
    if (data.facts.length) {
      heading("Facts your description does not state",
        "Each question is asked once, with a row for each part of your system it"
        + " applies to. The first ones are the most likely to matter. Leave any"
        + " row blank that you cannot answer.");
    }
    const groups = new Map();
    for (const q of data.facts) {
      if (!groups.has(q.group)) {
        const box = document.createElement("details");
        box.open = groups.size < OPEN_GROUPS;
        const title = document.createElement("summary");
        box.append(title);
        groups.set(q.group, { box, title, heading: q.group_heading, count: 0 });
      }
    }
    const optionOf = (label, value) => {
      const choice = document.createElement("option");
      choice.value = value;
      choice.textContent = label;
      return choice;
    };
    data.facts.forEach((q, index) => {
      const row = document.createElement("p");
      let input;
      // `beside` is what follows the label.
      let beside;
      if (q.choices.length) {
        input = document.createElement("select");
        input.append(optionOf("(leave unanswered)", ""));
        for (const option of q.choices) {
          input.append(optionOf(option.name ? `${option.name} (${option.id})` : option.id, option.id));
        }
        input.append(optionOf("I don't know", DONT_KNOW));
        beside = [input];
      } else if (q.form === "control") {
        // A control: say there is none, say you do not know, or name the
        // mechanism. The suggestions are a start; the text is the answer.
        input = document.createElement("input");
        input.type = "text";
        input.maxLength = 1000;
        input.placeholder = "type it, or pick a common one";
        const list = document.createElement("datalist");
        list.id = `early-suggest-${index}`;
        for (const suggestion of q.suggestions) list.append(optionOf(suggestion, suggestion));
        input.setAttribute("list", list.id);
        const state = document.createElement("select");
        state.append(optionOf("(leave unanswered)", ""), optionOf("There is none", "none"),
          optionOf("I don't know", DONT_KNOW), optionOf("A mechanism, in my own words:", "mechanism"));
        state.addEventListener("change", () => {
          const fixed = state.value === "none" || state.value === DONT_KNOW;
          input.value = fixed ? state.value : "";
          input.disabled = fixed;
        });
        beside = [state, " ", input, list];
      } else {
        input = document.createElement("input");
        input.type = "text";
        input.maxLength = 1000;
        input.placeholder = "(leave unanswered)";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.addEventListener("change", () => {
          input.value = box.checked ? DONT_KNOW : "";
          input.disabled = box.checked;
        });
        const dontKnow = document.createElement("label");
        dontKnow.append(box, " I don't know");
        beside = [input, " ", dontKnow];
      }
      input.dataset.key = JSON.stringify(q.key);
      factInputs.push(input);
      const label = document.createElement("b");
      label.textContent = q.element;
      // Why the fact matters: the questions of the rules that fire on it.
      label.title = q.reasons.join(" ");
      row.append(label, " ", ...beside);
      const group = groups.get(q.group);
      group.count += 1;
      group.box.append(row);
    });
    for (const group of groups.values()) {
      group.title.textContent = `${group.heading} (${group.count})`;
      questions.append(group.box);
    }
    asked.hidden = false;
  };

  document.getElementById("continue").addEventListener("click", async () => {
    const links = [...questions.querySelectorAll("select")]
      .filter((select) => select.dataset.principal && select.value)
      .map((select) => ({ principal: select.dataset.principal, element: select.value }));
    const facts = factInputs
      .filter((input) => input.value.trim())
      .map((input) => ({ key: JSON.parse(input.dataset.key), value: input.value.trim() }));
    const resumed = await fetch("/answer/" + pausedRun, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links, facts }),
    });
    if (!resumed.ok) {
      fail((await resumed.json()).message);
      return;
    }
    asked.hidden = true;
    follow(
      (await resumed.json()).run,
      "Running the threat analysis with your answers. This takes a few minutes.",
    );
  });

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
