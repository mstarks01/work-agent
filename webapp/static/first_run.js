  const form = document.getElementById("analyze");
  const box = document.getElementById("description");
  const ticks = document.getElementById("ticks");
  const problem = document.getElementById("problem");
  const go = document.getElementById("go");

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
    follow((await started.json()).run);
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
    // The open facts, ranked before any finding exists. The first few in
    // full, the rest one click away; each says which rules make it matter.
    const SHOWN = 10;
    const more = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `More questions (${data.facts.length - SHOWN})`;
    more.append(summary);
    data.facts.forEach((q, index) => {
      const row = document.createElement("p");
      let input;
      if (q.choices.length) {
        input = document.createElement("select");
        const skip = document.createElement("option");
        skip.value = "";
        skip.textContent = "(leave unanswered)";
        input.append(skip);
        for (const option of q.choices) {
          const choice = document.createElement("option");
          choice.value = option.id;
          choice.textContent = option.name ? `${option.name} (${option.id})` : option.id;
          input.append(choice);
        }
        const dontKnow = document.createElement("option");
        dontKnow.value = DONT_KNOW;
        dontKnow.textContent = "I don't know";
        input.append(dontKnow);
      } else {
        input = document.createElement("input");
        input.type = "text";
        input.maxLength = 1000;
        input.placeholder = "(leave unanswered)";
      }
      input.dataset.key = JSON.stringify(q.key);
      factInputs.push(input);
      const label = document.createElement("b");
      label.textContent = q.label;
      const why = document.createElement("div");
      why.className = "meta";
      why.textContent = q.reasons.join(" ");
      row.append(label, " ", input);
      if (!q.choices.length) {
        const box = document.createElement("input");
        box.type = "checkbox";
        box.addEventListener("change", () => {
          input.value = box.checked ? DONT_KNOW : "";
          input.disabled = box.checked;
        });
        const dontKnow = document.createElement("label");
        dontKnow.append(box, " I don't know");
        row.append(" ", dontKnow);
      }
      row.append(why);
      (index < SHOWN ? questions : more).append(row);
    });
    if (data.facts.length > SHOWN) questions.append(more);
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
    follow((await resumed.json()).run);
  });

  // Follow one run's progress to its end: a report, questions, or a failure.
  const follow = (runId) => {
    go.disabled = true;
    ticks.replaceChildren();
    ticks.hidden = false;
    const stream = new EventSource("/events/" + runId);
    stream.addEventListener("questions", (event) => {
      stream.close();
      ticks.hidden = true;
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
  if (followed) follow(followed);
