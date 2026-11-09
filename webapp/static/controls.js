// The Controls page: the operator's Controls (ADR 0127). A Control name, an
// alias, a fact value and an evidence file name reach the page as text nodes
// or input values, never as markup: `h` appends each string as a text node.
(() => {
  const el = (id) => document.getElementById(id);
  const PAGE = JSON.parse(el("controls_page").textContent);
  const { units, limits } = PAGE;
  const SCOPES = Object.fromEntries(PAGE.scope_types.map((row) => [row.type, row]));
  const CLASS_LABEL = Object.fromEntries(PAGE.evidence_classes.map((row) => [row.id, row.label]));
  const FRAMEWORKS = Object.keys(units);

  // Builds an element. A string child becomes a text node, so no value here
  // is ever parsed as markup.
  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "on") {
        for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
      } else if (key === "class") {
        node.className = value;
      } else if (key === "value") {
        node.value = value;
      } else if (value === true) {
        node.setAttribute(key, "");
      } else if (value !== false && value != null) {
        node.setAttribute(key, value);
      }
    }
    node.append(...children.flat(Infinity).filter((child) => child != null && child !== false));
    return node;
  };
  const counted = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const show = (box, text) => {
    el(box).textContent = text;
    el(box).hidden = !text;
  };
  const day = (iso) => new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  // The service's phrase for what a Control protects (scope_phrase), as the
  // start of a cell.
  const scopeText = (phrase) => phrase.charAt(0).toUpperCase() + phrase.slice(1);
  // Each statement of a scope type that a fact ticks: the fact's own, or
  // each facet's that the fact answers.
  const keyText = (key) => JSON.stringify(key);
  const ticks = (scopeType, fact) => SCOPES[scopeType].statements.filter((one) => keyText(one.key) === keyText(fact.key)
    && (!one.facet || fact.facets?.[one.facet] === one.answer));
  // A statement as a list item, with how the part does it where the owner
  // wrote that.
  const statementText = (fact, one) => {
    const text = one.label.replace(/\.$/, "");
    return one.answer === null ? `${text}: ${fact.value}` : text;
  };
  const unitOf = (row) => units[row.framework]?.units.find((unit) => unit.id === row.unit);
  const unitText = (row) => {
    const label = unitOf(row)?.label;
    return label ? `${row.framework} ${row.unit} · ${label}` : `${row.framework} ${row.unit}`;
  };

  let controls = [];
  let editing = null;
  let draft = null;
  let deleting = [];
  const chosen = new Set();
  const sort = { by: "updated", descending: true };
  const atLimit = () => controls.length >= limits.max_controls;

  // ---- The editor ---------------------------------------------------------
  // A draft holds the Control as the editor shows it. A held evidence file
  // has an ID and no text; a new one has its text and no ID.
  // A new Control starts with no scope type: the owner chooses what it
  // protects first.
  const blank = () => ({
    name: "",
    scope: { type: "", name: "", aliases: [] },
    facts: [],
    rows: [],
    evidence: [],
  });
  // Each start and stop of an edit opens a new session. A file read keeps
  // the session it started in, and changes no draft of a later one. While a
  // save runs, the page opens no other draft, so its answer lands on the
  // draft that sent it.
  let session = 0;
  let saving = false;
  // The choices of an edit that no draft field holds yet: the framework, the
  // unit search and the open units of the next row, the evidence kind of the
  // next file, and an alias being typed. A redraw keeps them; a new edit
  // starts them afresh.
  let pending = {};
  // The draft as the edit opened it, to tell a changed draft from one with
  // no change.
  let clean = "";
  const changed = () => draft !== null && JSON.stringify(draft) !== clean;
  // What the last change of "What it protects" removed, which Undo puts
  // back: the scope and the facts before the change, the facts it removed,
  // and the scope and facts after it. Undo lasts until the next edit of the
  // scope or the facts; it leaves the name, the rows and the evidence as
  // they are.
  let removed = null;
  const undoKey = () => JSON.stringify([draft.scope, draft.facts]);
  const dropUndo = () => {
    if (!removed || undoKey() === removed.after) return;
    removed = null;
    el("scope-removed")?.remove();
  };
  const open = (control) => {
    session += 1;
    refuse("");
    pending = { framework: FRAMEWORKS[0], find: "", open: new Set(), evidenceClass: PAGE.evidence_classes[0].id, alias: "" };
    removed = null;
    editing = control ? control.id : "new";
    draft = control
      ? JSON.parse(JSON.stringify({ ...control, evidence: control.evidence.map((file) => ({ ...file })) }))
      : blank();
    clean = JSON.stringify(draft);
    show("controls-notice", "");
    draw();
    el("control-name").focus();
  };
  // Opening another Control or New Control over a changed draft asks the
  // owner to save it, discard it or stay. Opening the open one again keeps
  // its draft.
  let leaving = null;
  const startEdit = (control) => {
    if (saving) return;
    if (draft && editing === (control ? control.id : "new")) {
      el("control-name").focus();
      return;
    }
    if (!changed()) {
      open(control);
      return;
    }
    leaving = { control };
    const title = draft.name ? `“${draft.name}”` : editing === "new" ? "The new Control" : "This Control";
    el("leave-question").textContent = `${title} has changes you have not saved.`;
    el("leave-save").textContent = control ? `Save and open “${control.name}”` : "Save and open a new Control";
    el("leave-dialog").showModal();
  };
  // Closing the editor from inside it moves focus to the edited Control's
  // Edit button, to New Control, or to the search field when New Control is
  // off.
  const stopEdit = () => {
    const was = editing;
    const inside = el("editor").contains(document.activeElement);
    session += 1;
    refuse("");
    removed = null;
    editing = null;
    draft = null;
    draw();
    if (inside) focusFirst(`edit-${was}`, "new-control", "controls-search");
  };
  const editorProblem = h("div", { id: "editor-problem", class: "problem", role: "alert", hidden: true });
  const refuse = (text) => {
    editorProblem.textContent = text;
    editorProblem.hidden = !text;
  };
  // Focus the first element of `ids` that exists and is enabled.
  const focusFirst = (...ids) => ids.map(el).find((node) => node && !node.disabled)?.focus();
  // After a removal, focus goes to the Remove button now at its index, the
  // one before it, or `fallback`.
  const focusNear = (prefix, index, fallback) =>
    focusFirst(`${prefix}-remove-${index}`, `${prefix}-remove-${index - 1}`, fallback);
  const remove = (list, index, label, prefix, fallback) => h("button", {
    type: "button", class: "quiet", id: `${prefix}-remove-${index}`, "aria-label": `Remove ${label}`,
    on: { click: () => {
      list.splice(index, 1);
      drawEditor();
      focusNear(prefix, index, fallback);
    } },
  }, "Remove");
  // The "?" beside a card's title, as the analysis page has beside its
  // question toggle: it opens the card's description.
  const SVG = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs, ...children) => {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    node.append(...children);
    return node;
  };
  const helpIcon = () => svg("svg", { "aria-hidden": "true", viewBox: "0 0 24 24", width: "18", height: "18", fill: "none",
    stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" },
  svg("circle", { cx: "12", cy: "12", r: "9" }),
  svg("path", { d: "M9.7 9a2.5 2.5 0 0 1 4.6 1.3c0 1.7-2.3 2.1-2.3 3.7" }),
  svg("path", { d: "M12 17h.01" }));
  const help = (title, text) => h("details", { class: "question-help" },
    h("summary", { id: `help-${title.toLowerCase().replace(/[^a-z]+/g, "-")}` }, h("span", { class: "sr-only" }, `About ${title}`), helpIcon()),
    h("div", { class: "question-help-content" }, h("strong", {}, title), h("p", {}, text)));
  // A card of the editor, with its place in the order and whether it is
  // optional or required.
  const card = (title, step, about, ...content) => h("section", { class: "q-card" },
    h("div", { class: "card-head" }, h("h3", {}, title), h("span", { class: "card-step" }, step),
      about ? help(title, about) : null), ...content);

  const SCOPE_HINTS = {
    boundary: "The name must match the trust boundary after case and punctuation. The Control applies to each part the description places in it.",
    other: "The name must match the part after case and punctuation. A similar name matches nothing.",
  };
  const scopeCard = () => {
    const named = Boolean(draft.scope.type) && draft.scope.type !== "application";
    const aliasInput = h("input", {
      type: "text", id: "alias-input", maxlength: limits.name_chars, placeholder: "Add an alias and press Enter", value: pending.alias,
      on: { input: (event) => { pending.alias = event.target.value; }, keydown: (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        const alias = event.target.value.trim();
        if (!alias) return;
        if (draft.scope.aliases.length >= limits.max_aliases) {
          refuse(PAGE.at_limit.aliases);
          return;
        }
        draft.scope.aliases.push(alias);
        pending.alias = "";
        drawEditor();
      } },
    });
    // An alias added or removed draws the editor with no input event, so
    // the draw ends the Undo too.
    dropUndo();
    return card("What it protects", "1 of 4", named ? (SCOPE_HINTS[draft.scope.type] ?? SCOPE_HINTS.other) : null,
      h("div", { class: "line" },
        h("div", {},
          h("select", { id: "scope-type", "aria-label": "What it protects", on: { change: (event) => {
            const before = { scope: draft.scope, facts: draft.facts };
            draft.scope = { type: event.target.value, name: "", aliases: [] };
            draft.facts = draft.facts.filter((fact) => ticks(draft.scope.type, fact).length);
            const lost = before.facts.filter((fact) => !draft.facts.includes(fact));
            const after = undoKey();
            // A change that removes nothing keeps the Undo of the change
            // before it.
            if (lost.length || before.scope.name || before.scope.aliases.length) removed = { ...before, lost, after };
            else if (removed) removed.after = after;
            drawEditor();
          } } },
          draft.scope.type ? null : h("option", { value: "", disabled: true, selected: true }, "Choose what it protects"),
          ...PAGE.scope_types.map((row) => h("option", { value: row.type, selected: row.type === draft.scope.type }, row.label)))),
        named ? h("div", {}, h("label", { class: "field-label", for: "scope-name" }, "Its name in your descriptions"),
          h("input", { type: "text", id: "scope-name", maxlength: limits.name_chars, value: draft.scope.name,
            on: { input: (event) => { draft.scope.name = event.target.value; } } })) : null,
        named ? h("div", {}, h("label", { class: "field-label", for: "alias-input" }, "Other names it goes by"),
          h("div", { class: "aliases" },
            ...draft.scope.aliases.map((alias, index) => h("span", { class: "tag" }, alias,
              h("button", { type: "button", class: "quiet", id: `alias-remove-${index}`, "aria-label": `Remove the alias ${alias}`,
                on: { click: () => {
                  draft.scope.aliases.splice(index, 1);
                  drawEditor();
                  focusNear("alias", index, "alias-input");
                } } }, "×"))),
            aliasInput)) : null),
      removed ? removedNote() : null);
  };
  // The notice of what a change of "What it protects" removed: the name,
  // the aliases and each fact the new choice does not take.
  const removedNote = () => {
    const { scope, facts, lost } = removed;
    const aliases = scope.aliases.length ? counted(scope.aliases.length, "alias", "aliases") : "";
    const names = [scope.name ? `its name “${scope.name}”` : "", aliases].filter(Boolean).join(" and ");
    const statements = lost.flatMap((fact) => ticks(scope.type, fact).map((one) => `“${statementText(fact, one)}”`));
    return h("div", { class: "notice", id: "scope-removed", role: "status" },
      "This change removed what does not fit the new choice:",
      h("ul", {}, names ? h("li", {}, names) : null,
        lost.length ? h("li", {}, `${counted(lost.length, "fact", "facts")}: ${statements.join(", ")}`) : null),
      h("button", { type: "button", id: "scope-undo", on: { click: () => {
        draft.scope = scope;
        draft.facts = facts;
        removed = null;
        drawEditor();
        el("scope-type").focus();
      } } }, "Undo"));
  };

  // Each statement is a tick. A tick writes the answer that makes it a
  // protection, and a statement that asks how takes the owner's words.
  const factCard = () => {
    if (!draft.scope.type) {
      return card("What it does", "2 of 4", null,
        h("p", { class: "hint" }, "Choose what it protects to see the protections it can state."));
    }
    const offered = SCOPES[draft.scope.type].statements;
    if (!offered.length) {
      return card("What it does", "2 of 4", null,
        h("p", { class: "hint" }, "A Control of the whole application states no fact. It works through the units it covers."));
    }
    const factOfKey = (key) => draft.facts.find((fact) => keyText(fact.key) === keyText(key));
    const tick = (one, index, on) => {
      const fact = factOfKey(one.key);
      if (one.facet && on && fact) {
        fact.facets = { ...fact.facets, [one.facet]: one.answer };
      } else if (one.facet && on) {
        draft.facts.push({ key: one.key, facets: { [one.facet]: one.answer } });
      } else if (one.facet && fact) {
        const { [one.facet]: _, ...rest } = fact.facets;
        fact.facets = rest;
        if (!Object.keys(rest).length) draft.facts.splice(draft.facts.indexOf(fact), 1);
      } else if (on) {
        draft.facts.push({ key: one.key, value: one.answer ?? "" });
      } else if (fact) {
        draft.facts.splice(draft.facts.indexOf(fact), 1);
      }
      drawEditor();
      if (on && one.answer === null) el(`how-${index}`)?.focus();
    };
    const item = (one, index) => {
      const fact = factOfKey(one.key);
      const on = Boolean(fact) && (!one.facet || fact.facets?.[one.facet] === one.answer);
      return h("li", {},
        h("label", {}, h("input", { type: "checkbox", id: `fact-${index}`, checked: on, on: { change: (event) => tick(one, index, event.target.checked) } }), one.label),
        one.answer === null && on ? h("input", { type: "text", id: `how-${index}`, class: "how", maxlength: limits.value_chars, value: fact.value,
          "aria-label": `How: ${one.label}`, placeholder: "How, such as AES-256 with keys in a KMS",
          on: { input: (event) => { fact.value = event.target.value; } } }) : null);
    };
    const topics = [...new Set(offered.map((one) => one.topic))];
    return card("What it does", "2 of 4", "Tick each protection the part has. The analysis takes each one as a fact about the part. A fact that the description states stands.",
      h("div", { class: "statements", id: "statements" }, ...topics.map((topic) => h("fieldset", {}, h("legend", {}, topic),
        h("ul", {}, ...offered.map((one, index) => (one.topic === topic ? item(one, index) : null)))))));
  };

  // The units of one framework as a tree: each unit under the unit it sits
  // inside, as a chapter holds its sections. A search shows each unit whose
  // ID or label holds every word, under its open ancestors. A unit that is a
  // row already shows "Added", so no row goes in twice.
  const unitTree = (framework) => {
    const all = units[framework].units;
    const byId = new Map(all.map((unit) => [unit.id, unit]));
    const children = new Map();
    for (const unit of all) {
      const parent = byId.has(unit.inside) ? unit.inside : "";
      children.set(parent, [...(children.get(parent) ?? []), unit]);
    }
    const words = pending.find.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = (unit) => words.every((word) => `${unit.id} ${unit.label}`.toLocaleLowerCase().includes(word));
    // With a search, a unit shows where it or a unit inside it matches.
    const shown = new Set();
    if (words.length) {
      for (const unit of all.filter(matches)) {
        for (let at = unit; at && !shown.has(at.id); at = byId.get(at.inside)) shown.add(at.id);
      }
    }
    const added = (unit) => draft.rows.some((row) => row.framework === framework && row.unit === unit.id);
    const order = [];
    const line = (unit) => {
      order.push(unit.id);
      const kids = (children.get(unit.id) ?? []).filter((kid) => !words.length || shown.has(kid.id));
      const open = words.length > 0 || pending.open.has(unit.id);
      const text = unit.label ? `${unit.id} · ${unit.label}` : unit.id;
      return h("li", { class: `unit unit-${unit.grain}` },
        h("div", { class: "unit-line" },
          kids.length ? h("button", { type: "button", class: "quiet unit-toggle", id: `unit-open-${framework}-${unit.id}`, "aria-expanded": open ? "true" : "false",
            on: { click: () => {
              if (pending.open.has(unit.id)) pending.open.delete(unit.id);
              else pending.open.add(unit.id);
              drawTree();
            } } }, open ? "▾ " : "▸ ", text) : h("span", {}, text),
          added(unit) ? h("span", { class: "sub" }, "Added")
            : h("button", { type: "button", class: "quiet", id: `unit-add-${framework}-${unit.id}`, "aria-label": `Add ${unit.id}`,
              on: { click: () => addUnit(framework, unit, order) } }, "Add")),
        kids.length && open ? h("ul", {}, ...kids.map(line)) : null);
    };
    const roots = (children.get("") ?? []).filter((unit) => !words.length || shown.has(unit.id));
    const count = all.filter(matches).length;
    return [
      words.length ? h("p", { class: "hint", id: "unit-count", role: "status" },
        count ? `${counted(count, "unit matches", "units match")}.` : "No unit matches.") : null,
      h("ul", { class: "unit-tree", id: "unit-tree" }, ...roots.map(line)),
    ];
  };
  // After an Add, focus goes to the next unit's Add button that shows, or
  // to the search field.
  const addUnit = (framework, unit, order) => {
    if (draft.rows.length >= limits.max_rows) {
      refuse(PAGE.at_limit.rows);
      return;
    }
    draft.rows.push({ framework, unit: unit.id, version: units[framework].version });
    drawEditor();
    const next = order.slice(order.indexOf(unit.id) + 1).map((id) => `unit-add-${framework}-${id}`);
    focusFirst(...next, "unit-find");
  };
  const drawTree = () => {
    const box = el("unit-box");
    if (box) redraw(box, unitTree(pending.framework));
  };
  const rowCard = () => {
    // The service needs a fact or a row (ADR 0123), so a chosen scope with
    // no fact needs a row. The whole application states no fact.
    const required = Boolean(draft.scope.type) && !draft.facts.length;
    // A finding that names no element reaches only a Control of the whole
    // application. The note names each framework as the rows do.
    const unplaced = draft.scope.type === "application" || !draft.scope.type ? []
      : [...new Set(draft.rows.filter((row) => !units[row.framework]?.names_an_element).map((row) => row.framework))];
    return card("What it covers", required ? "3 of 4 · required" : "3 of 4 · optional", "Each row names one unit this Control covers. The analysis judges each row against its findings, and refuses a row that does not bear on its unit.",
      required ? h("p", { class: "hint" }, draft.scope.type === "application"
        ? "Add at least one unit. The analysis judges each one against its findings."
        : "Tick a protection above, or add at least one unit.") : null,
      h("ul", { class: "lines", id: "rows-list" }, ...draft.rows.map((row, index) => h("li", {},
        h("span", {}, unitText(row), " ", h("span", { class: "sub" }, `version ${row.version}`)),
        remove(draft.rows, index, `the row ${row.unit}`, "row", "row-framework")))),
      unplaced.length ? h("p", { class: "notice", id: "rows-reach", role: "note" },
        `Many findings of ${unplaced.join(" and ")} are about the whole application, not one part of it. This Control protects one part, so the analysis does not apply it to those findings. Choose “${SCOPES.application.label}” only when the protection covers every part of the application. That choice removes the name and the facts of this part.`) : null,
      h("div", { class: "add-line" },
        h("div", {}, h("label", { class: "field-label", for: "row-framework" }, "Framework"),
          h("select", { id: "row-framework", on: { change: (event) => {
            pending.framework = event.target.value;
            drawTree();
          } } }, ...FRAMEWORKS.map((name) => h("option", { value: name, selected: name === pending.framework }, `${name} ${units[name].version}`)))),
        h("div", {}, h("label", { class: "field-label", for: "unit-find" }, "Find a unit"),
          h("input", { type: "search", id: "unit-find", placeholder: "ID or words", autocomplete: "off", value: pending.find,
            on: { input: (event) => {
              pending.find = event.target.value;
              drawTree();
            } } })),
        h("span", {})),
      h("div", { id: "unit-box" }, ...unitTree(pending.framework)));
  };

  // The page reads an evidence file and keeps only its text, as Files does.
  const evidenceCard = () => {
    const classPick = h("select", { id: "evidence-class", on: { change: (event) => { pending.evidenceClass = event.target.value; } } },
      ...PAGE.evidence_classes.map((row) => h("option", { value: row.id, selected: row.id === pending.evidenceClass }, row.label)));
    const fileInput = h("input", { type: "file", id: "evidence-file", on: { change: async (event) => {
      const [file] = event.target.files;
      event.target.value = "";
      if (!file) return;
      if (draft.evidence.length >= limits.max_evidence) {
        refuse(PAGE.at_limit.evidence);
        return;
      }
      // The file holds its place in the list while the page reads it.
      const entry = { name: file.name.slice(0, limits.name_chars), class: classPick.value, reading: true };
      const own = draft;
      const mine = session;
      own.evidence.push(entry);
      draw();
      const read = await textFile.decode(file, limits.max_evidence_bytes);
      const at = own.evidence.indexOf(entry);
      if (mine !== session || at < 0) return;
      if (read.error) {
        own.evidence.splice(at, 1);
        refuse(read.error);
      } else {
        refuse("");
        own.evidence[at] = {
          name: entry.name,
          class: entry.class,
          size_bytes: new TextEncoder().encode(read.text).length,
          text: read.text,
        };
      }
      draw();
    } } });
    return card("Evidence", "4 of 4 · optional", "A file that shows the protection, as plain text. The analysis quotes it beside a ruling and never reads it as a description.",
      h("p", { class: "hint", id: "evidence-note" }, "Remove each password, key and token from a file before you add it. The service refuses a file that looks like it holds one, but it does not find every form. A credential that it does not find is saved with the file."),
      h("ul", { class: "lines", id: "evidence-list" }, ...draft.evidence.map((file, index) => h("li", {},
        h("span", {}, h("b", {}, file.name), file.reading
          ? ` · ${CLASS_LABEL[file.class]} · Reading…`
          : ` · ${CLASS_LABEL[file.class]} · ${textFile.size(file.size_bytes)}`),
        remove(draft.evidence, index, `the file ${file.name}`, "evidence", "evidence-class")))),
      h("div", { class: "add-line" },
        h("div", {}, h("label", { class: "field-label", for: "evidence-class" }, "Kind of evidence"), classPick),
        h("div", {}, h("label", { class: "field-label", for: "evidence-file" }, "File"), fileInput),
        h("span", {})));
  };

  // What a save sends: the document, each held file by its ID, and each new
  // file with its text. An edit names the `updated_at` it read.
  const saveBody = () => ({
    name: draft.name,
    scope: draft.scope.type === "application" ? { type: "application" } : draft.scope,
    facts: draft.facts.map((fact) => (fact.facets ? { key: fact.key, facets: fact.facets } : { key: fact.key, value: fact.value.trim() })),
    rows: draft.rows,
    evidence: draft.evidence.map((file) => (file.id ? { id: file.id } : { name: file.name, class: file.class, text: file.text })),
    ...(editing === "new" ? {} : { updated_at: draft.updated_at }),
  });
  const reading = () => draft.evidence.some((file) => file.reading);
  const WAIT_TEXT = "Save waits for the file read to end.";
  // Whether the save succeeded.
  const save = async () => {
    if (saving || reading()) return false;
    if (!draft.scope.type) {
      refuse("Choose what the Control protects.");
      el("scope-type").focus();
      return false;
    }
    const missing = draft.facts.find((fact) => !fact.facets && !fact.value.trim());
    if (missing) {
      refuse(`Write how the part does this: “${ticks(draft.scope.type, missing)[0]?.label}”`);
      return false;
    }
    // The locked editor drops focus: a save that fails gives it back, and
    // one that succeeds moves it to the saved Control's Edit button. Focus
    // the owner moved elsewhere during the save stays where it is.
    const focused = document.activeElement?.id;
    const inside = el("editor").contains(document.activeElement);
    saving = true;
    const mine = session;
    draw();
    let saved = null;
    try {
      const sent = await fetch(editing === "new" ? "/workspace/controls" : `/workspace/controls/${encodeURIComponent(editing)}`, {
        method: editing === "new" ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(saveBody()),
      });
      const body = await sent.json();
      if (mine === session) {
        if (sent.ok) saved = body;
        else refuse(body.message);
      }
    } catch {
      if (mine === session) refuse("The connection was interrupted. Check Controls before you save again.");
    } finally {
      saving = false;
    }
    if (!saved) {
      draw();
      if (focused && document.activeElement === document.body) el(focused)?.focus();
      return false;
    }
    stopEdit();
    show("controls-notice", `“${saved.name}” is saved.`);
    await load();
    if (inside && document.activeElement === document.body) focusFirst(`edit-${saved.id}`, "new-control", "controls-search");
    return true;
  };
  const editor = () => h("fieldset", { class: "editor", disabled: saving },
    h("h2", {}, editing === "new" ? "New Control" : `Edit “${draft.name}”`),
    h("div", {}, h("label", { class: "field-label", for: "control-name" }, "Name"),
      h("input", { type: "text", id: "control-name", maxlength: limits.name_chars, value: draft.name,
        on: { input: (event) => { draft.name = event.target.value; } } })),
    scopeCard(), factCard(), rowCard(), evidenceCard(),
    editorProblem,
    h("div", { class: "dialog-actions" },
      h("span", { id: "editor-status", class: "hint", role: "status" },
        saving ? `Saving “${draft.name}”…` : reading() ? WAIT_TEXT : ""),
      h("button", { type: "button", id: "cancel-control", on: { click: stopEdit } }, "Cancel"),
      h("button", { type: "button", id: "save-control", class: "primary", disabled: reading(), on: { click: save } }, "Save Control")));

  // ---- The table ----------------------------------------------------------
  el("controls-kind").append(...PAGE.scope_types.map((row) => h("option", { value: row.type }, row.label)));
  el("controls-framework").append(...FRAMEWORKS.map((name) => h("option", { value: name }, name)));

  const shownControls = () => {
    const text = el("controls-search").value.toLocaleLowerCase();
    const kind = el("controls-kind").value;
    const framework = el("controls-framework").value;
    const shown = controls.filter((control) =>
      [control.name, scopeText(control.scope_phrase), ...control.scope.aliases, ...control.rows.map(unitText), ...control.evidence.map((file) => file.name)]
        .some((value) => value.toLocaleLowerCase().includes(text))
      && (!kind || control.scope.type === kind)
      && (!framework || control.rows.some((row) => row.framework === framework)));
    const order = (a, b) => (sort.by === "name"
      ? a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true })
      : a.updated_at.localeCompare(b.updated_at));
    return shown.sort((a, b) => (sort.descending ? -order(a, b) : order(a, b)));
  };
  const confirmDelete = (picked) => {
    deleting = picked;
    el("delete-question").textContent = picked.length === 1
      ? `Delete “${picked[0].name}”? Each analysis that already applied it keeps its own copy, and its reports do not change. You cannot undo this.`
      : `Delete ${picked.length} Controls? Each analysis that already applied them keeps its own copy, and its reports do not change. You cannot undo this.`;
    el("delete-dialog").showModal();
  };
  const row = (control) => {
    const facts = control.facts.flatMap((fact) => ticks(control.scope.type, fact).map((one) => statementText(fact, one)));
    return h("tr", {},
      h("td", {}, h("input", { type: "checkbox", id: `select-${control.id}`, checked: chosen.has(control.id), "aria-label": `Select ${control.name}`,
        on: { change: (event) => { if (event.target.checked) chosen.add(control.id); else chosen.delete(control.id); drawTable(); } } })),
      h("td", { class: "name-cell" },
        h("a", { href: "#", id: `name-${control.id}`, "aria-disabled": saving ? "true" : null, on: { click: (event) => { event.preventDefault(); startEdit(control); } } }, control.name),
        facts.length ? h("div", { class: "sub" }, facts.join("; ")) : null),
      h("td", {}, scopeText(control.scope_phrase)),
      h("td", {}, h("div", { class: "chips" }, ...control.rows.map((one) => h("span", { class: `chip tint-${FRAMEWORKS.indexOf(one.framework) % 3}`, title: unitText(one) },
        `${one.framework} ${one.unit}`)))),
      h("td", {}, control.evidence.length
        ? control.evidence.map((file) => `${file.name} (${CLASS_LABEL[file.class]})`).join(", ")
        : "None"),
      h("td", {}, h("time", { datetime: control.updated_at }, day(control.updated_at))),
      h("td", {}, h("div", { class: "row-actions" },
        h("button", { type: "button", id: `edit-${control.id}`, disabled: saving, "aria-label": `Edit ${control.name}`, on: { click: () => startEdit(control) } }, "Edit"),
        h("button", { type: "button", id: `delete-${control.id}`, class: "danger", disabled: saving, "aria-label": `Delete ${control.name}`, on: { click: () => confirmDelete([control]) } }, "Delete"))));
  };

  // Draw `box` again with `children`, and keep focus on the control in it
  // that had focus, by its ID.
  const redraw = (box, children) => {
    const focused = box.contains(document.activeElement) ? document.activeElement.id : "";
    box.replaceChildren(...children);
    if (focused) el(focused)?.focus();
  };
  // The editor draws again after a change in it. The table draws on its
  // own, so a filter, a sort or a row tick leaves the editor as it is.
  // "Save and open" waits for a file read, as Save does.
  const drawEditor = () => {
    redraw(el("editor"), draft ? [editor()] : []);
    el("leave-save").disabled = Boolean(draft) && reading();
    show("leave-wait", el("leave-save").disabled ? WAIT_TEXT : "");
  };
  const drawTable = () => {
    el("controls-count").textContent = `${controls.length} of ${counted(limits.max_controls, "Control", "Controls")}`;
    el("new-control").disabled = saving || atLimit() || editing === "new";
    show("controls-full", atLimit() ? textFile.refusals.at_limit : "");
    const shown = shownControls();
    const picked = controls.filter((control) => chosen.has(control.id));
    const selectShown = el("select-shown");
    const shownPicked = shown.filter((control) => chosen.has(control.id)).length;
    selectShown.checked = Boolean(shown.length) && shownPicked === shown.length;
    selectShown.indeterminate = Boolean(shownPicked) && shownPicked < shown.length;
    el("delete-selected").disabled = saving || !picked.length;
    el("delete-selected").textContent = picked.length ? `Delete selected (${picked.length})` : "Delete selected";
    for (const head of document.querySelectorAll("th[data-sort]")) {
      head.setAttribute("aria-sort", head.dataset.sort === sort.by ? (sort.descending ? "descending" : "ascending") : "none");
    }
    el("controls-table").hidden = !shown.length;
    el("controls-empty").hidden = Boolean(shown.length);
    el("controls-empty").textContent = controls.length ? "No Controls match your filters." : "No Controls yet.";
    redraw(el("controls-list"), shown.map(row));
  };
  const draw = () => {
    drawEditor();
    drawTable();
  };

  const load = async () => {
    try {
      const listed = await fetch("/workspace/controls");
      if (!listed.ok) throw new Error();
      controls = await listed.json();
    } catch {
      show("controls-problem", "Could not load Controls. Reload the page.");
      return;
    }
    const held = new Set(controls.map((control) => control.id));
    for (const id of chosen) if (!held.has(id)) chosen.delete(id);
    draw();
  };

  el("new-control").addEventListener("click", () => startEdit(null));
  el("editor").addEventListener("input", dropUndo);
  el("editor").addEventListener("change", dropUndo);
  el("leave-dialog").addEventListener("close", () => { leaving = null; });
  el("leave-stay").addEventListener("click", () => el("leave-dialog").close());
  el("leave-discard").addEventListener("click", () => {
    const { control } = leaving;
    el("leave-dialog").close();
    open(control);
  });
  el("leave-save").addEventListener("click", async () => {
    const { control } = leaving;
    el("leave-dialog").close();
    if (!(await save())) return;
    // The list reloaded with the save. The other Control opens as it now
    // is, and does not open when a delete removed it or when the owner
    // opened a draft during the reload. The notice of the save stays.
    if (draft) return;
    const notice = el("controls-notice").textContent;
    const next = control && controls.find((one) => one.id === control.id);
    if (next) open(next);
    else if (!control && !atLimit()) open(null);
    show("controls-notice", notice);
  });
  // Leaving the page with a changed draft, or during a save, makes the
  // browser ask the owner to confirm.
  window.addEventListener("beforeunload", (event) => {
    if (!saving && !changed()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  el("controls-search").addEventListener("input", drawTable);
  el("controls-kind").addEventListener("change", drawTable);
  el("controls-framework").addEventListener("change", drawTable);
  el("select-shown").addEventListener("change", (event) => {
    for (const control of shownControls()) {
      if (event.target.checked) chosen.add(control.id);
      else chosen.delete(control.id);
    }
    drawTable();
  });
  for (const head of document.querySelectorAll("th[data-sort]")) {
    head.querySelector("button").addEventListener("click", () => {
      sort.descending = sort.by === head.dataset.sort ? !sort.descending : head.dataset.sort === "updated";
      sort.by = head.dataset.sort;
      drawTable();
    });
  }
  el("delete-selected").addEventListener("click", () => confirmDelete(controls.filter((control) => chosen.has(control.id))));
  el("delete-cancel").addEventListener("click", () => {
    deleting = [];
    el("delete-dialog").close();
  });
  el("delete-go").addEventListener("click", async () => {
    const picked = deleting;
    deleting = [];
    el("delete-dialog").close();
    // After a delete, focus goes to the row now in the first deleted row's
    // place, the row before it, New Control, or the search field when New
    // Control is off. Focus the owner moved elsewhere during the delete
    // stays where it is.
    const opener = document.activeElement;
    const at = shownControls().findIndex((control) => picked.some((one) => one.id === control.id));
    try {
      for (const control of picked) {
        const removed = await fetch(`/workspace/controls/${encodeURIComponent(control.id)}`, { method: "DELETE" });
        if (!removed.ok && removed.status !== 404) {
          show("controls-problem", (await removed.json()).message);
          await load();
          return;
        }
        chosen.delete(control.id);
        if (editing === control.id) stopEdit();
      }
    } catch {
      show("controls-problem", "The connection was interrupted. Check Controls before you delete again.");
      return;
    }
    show("controls-notice", picked.length === 1
      ? `“${picked[0].name}” is deleted. The analyses that applied it keep their copy.`
      : `${picked.length} Controls are deleted. The analyses that applied them keep their copy.`);
    await load();
    const rows = shownControls();
    if (document.activeElement !== document.body && document.activeElement !== opener) return;
    focusFirst(`select-${rows[at]?.id}`, `select-${rows[at - 1]?.id}`, "new-control", "controls-search");
  });

  load();
})();
