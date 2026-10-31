// The question list as every page that asks open facts draws it: the early
// list at a run's pause, and a report's Questions section. Each row sits in a
// box for its group, in the service's order. A box whose rows ask one kind of
// two or more elements asks it once, and a person ticks each part the answer
// covers (ADR 0081, ADR 0113). A facets question is a table: a column per facet
// and a row per element. A row asks only the facets in its question's `asks`
// (ADR 0112), and "Additional details" shows the rest.
//
// The page keeps its own rule for what it sends and when. It hands in:
// `list`, the node the boxes go in; `onEdit(row, event)`, run on each edit;
// `inputFor(q, prefill, changed)`, the answerEditor for a row; `reopen`,
// whether a facet offers "I don't know" over a known answer; `contextOf(q)`,
// the lines under a question's "?"; `notes(row)`, nodes under a row's title;
// and `extras(row)`, nodes after a row's input, such as a skip button.
//
// A row is the page's own object: `{ q, kind, node }`, `kind` "fact" or
// "link". Drawing sets `row.read`, the answer as the service takes it or
// null, `row.set`, `row.input` and `row.compact`. A fact row also sets
// `row.known`, whether its answer says more than "I don't know" (for a facets
// row, to each facet it asks), and a facets row `row.knownFor(facets)`,
// whether the facets named are. Every label is
// untrusted and lands as text.
const questionList = ({
  list,
  onEdit,
  inputFor,
  reopen,
  contextOf = () => [],
  notes = () => [],
  extras = () => [],
}) => {
  const button = (text, onClick, className = "quiet") => {
    const control = document.createElement("button");
    control.type = "button";
    control.className = className;
    control.textContent = text;
    control.addEventListener("click", onClick);
    return control;
  };
  // Keep supporting context next to the prompt, without taking up a row.
  const context = (q) => {
    const parts = contextOf(q);
    if (!parts.length) return null;
    const help = document.createElement("details");
    help.className = "question-context";
    const trigger = document.createElement("summary");
    trigger.textContent = "?";
    trigger.setAttribute("aria-label", "Why this question?");
    trigger.title = "Why this question?";
    const content = document.createElement("div");
    content.className = "question-context-content";
    for (const part of parts) {
      const line = document.createElement("p");
      line.textContent = part;
      content.append(line);
    }
    help.append(trigger, content);
    return help;
  };
  const questionTitle = (q, text) => {
    const title = document.createElement("div");
    title.className = "question-title";
    const label = document.createElement("b");
    label.textContent = text;
    title.append(label);
    const about = context(q);
    if (about) title.append(about);
    return title;
  };

  // Set while the shared answer writes rows, so those edits are not exceptions.
  let sharing = false;
  // An edit to a row under a shared answer makes it an exception, which the
  // shared answer leaves alone from then on.
  const edited = (row, event) => {
    if (!sharing && row.box && row.box.shared) {
      row.exception = true;
      row.inherited = false;
      if (row.tick) row.tick.checked = false;
    }
    onEdit(row, event);
  };
  // A part a person ticks takes the shared answer. Unticking it takes back
  // only what the shared answer wrote, so the part's own answer stays
  // (ADR 0113).
  const tickChanged = (row, event) => {
    const box = row.box;
    if (!box || !box.shared || row.compact) return;
    sharing = true;
    try {
      if (row.tick.checked) {
        row.exception = false;
        box.sharedWrite(row);
        row.inherited = true;
      } else if (row.inherited) {
        box.sharedClear(row);
        row.inherited = false;
      }
    } finally {
      sharing = false;
    }
    onEdit(row, event);
  };

  // ---- Rows ---------------------------------------------------------------
  // A fact row: label, context, editor. `read` is the answer as the service
  // takes it, or null; `set` writes an answer into the editor.
  const drawFact = (row, prefill) => {
    const q = row.q;
    const title = questionTitle(q, q.label);
    const { input, nodes, set, answer, readDetail, known } = inputFor(q, prefill, (event) =>
      edited(row, event),
    );
    row.input = input;
    row.read = answer;
    row.known = known;
    row.detail = readDetail;
    row.set = (next) => set(next.value);
    row.compact = false;
    const fields = document.createElement("div");
    fields.className = "answer-fields";
    fields.append(" ", ...nodes, " ", ...extras(row));
    row.node.dataset.layout = q.choices?.length ? "split" : "stack";
    row.node.replaceChildren(...(row.tick ? [row.tick] : []), title, ...notes(row), fields);
  };
  // A saved answer, compact and in place, with "Change".
  const drawCompact = (row, answer) => {
    const q = row.q;
    const title = questionTitle(q, q.label);
    const shown = document.createElement("span");
    shown.textContent = ` — ${answerText(answer)}`;
    row.compact = true;
    row.read = () => null;
    const fields = document.createElement("div");
    fields.className = "answer-fields";
    fields.append(shown, " ", button("Change", () => {
      drawFact(row, answer);
      if (row.input && typeof row.input.focus === "function") row.input.focus();
    }));
    row.node.replaceChildren(title, ...notes(row), fields);
  };
  // A link row: which element a principal is.
  const linkSelect = (q, prefill) => {
    const select = document.createElement("select");
    select.dataset.principal = q.principal;
    select.append(optionOf("(leave unanswered)", ""));
    // The element whose name matches comes first, marked, and is not chosen.
    const match = q.options.filter((option) => option.id === q.suggested);
    for (const option of [...match, ...q.options.filter((option) => option.id !== q.suggested)]) {
      const text = option.name ? `${option.name} (${option.id})` : option.id;
      select.append(optionOf(option.id === q.suggested ? `${text} (same name)` : text, option.id));
    }
    select.append(optionOf("None of these", "none"));
    select.value = prefill || "";
    return select;
  };
  const drawLink = (row, prefill) => {
    const q = row.q;
    const name = document.createElement("b");
    name.textContent = q.principal;
    const select = linkSelect(q, prefill);
    select.setAttribute("aria-label", `Matching part of your system for ${q.principal}`);
    select.addEventListener("change", (event) => edited(row, event));
    row.input = select;
    row.read = () =>
      select.value ? { principal: q.principal, element: select.value } : null;
    row.set = (next) => {
      select.value = next.element;
    };
    row.compact = false;
    const fields = document.createElement("div");
    fields.className = "answer-fields";
    // The element whose name matches, offered as an arrow beside the select
    // and never chosen for the person: the page sends only what a person picks.
    const match = !prefill && q.options.find((option) => option.id === q.suggested);
    if (match) {
      // The arrow gives up the focus: a row that holds it stays open, and
      // this answer is complete, so the row closes once it saves.
      const use = button("→", (event) => {
        select.value = match.id;
        use.blur();
        edited(row, event);
      }, "use-match");
      use.title = `Use ${match.name || match.id}`;
      use.setAttribute("aria-label", use.title);
      fields.append(use);
    }
    fields.append(" ", select, " ", ...extras(row));
    row.node.dataset.layout = "split";
    row.node.replaceChildren(name, ...notes(row), fields);
  };
  const drawCompactLink = (row, answer) => {
    const name = document.createElement("b");
    name.textContent = row.q.principal;
    const shown = document.createElement("span");
    shown.textContent = ` — ${linkAnswerText(answer)}`;
    row.compact = true;
    row.read = () => null;
    const fields = document.createElement("div");
    fields.className = "answer-fields";
    fields.append(shown, " ", button("Change", () => drawLink(row, answer.element)));
    row.node.dataset.layout = "split";
    row.node.replaceChildren(name, ...notes(row), fields);
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
    const heads = q.facets.map((facet) => {
      const cell = document.createElement("th");
      cell.textContent = facet.question;
      head.append(cell);
      return cell;
    });
    const detailHead = document.createElement("th");
    detailHead.textContent = "Exceptions or detail (optional)";
    head.append(detailHead);
    table.append(head);
    const grid = { table, facets: q.facets, heads, rows: new Set(), shared: [], more: false, count: 0 };
    grid.toggle = button("", () => {
      grid.more = !grid.more;
      showFacets(grid);
    });
    return grid;
  };
  // A table shows each facet a row asks for and each facet that holds an
  // answer. "Additional details" shows the rest, which stay unknown until a
  // person answers them (ADR 0112). A column that no row shows hides whole,
  // so the cells stay under their headings.
  const showFacets = (grid) => {
    let extra = 0;
    const open = grid.facets.map(() => false);
    for (const row of grid.rows) {
      row.cells.forEach(({ asked, select }, column) => {
        const shown = asked || Boolean(select.value) || grid.more;
        select.hidden = !shown;
        if (!asked && !select.value) extra += 1;
        if (shown) open[column] = true;
      });
    }
    for (const row of grid.rows) {
      row.cells.forEach(({ cell }, column) => {
        cell.hidden = !open[column];
      });
    }
    grid.heads.forEach((head, column) => {
      head.hidden = !open[column];
    });
    grid.shared.forEach((label, column) => {
      label.hidden = !open[column];
    });
    grid.toggle.hidden = !extra;
    grid.toggle.textContent = grid.more ? "Hide additional details" : `Additional details (${extra})`;
    grid.toggle.setAttribute("aria-expanded", String(grid.more));
  };
  const drawFacetRow = (row, prefill) => {
    const q = row.q;
    const grid = row.grid;
    const name = document.createElement("td");
    name.append(...(row.tick ? [row.tick] : []), questionTitle(q, q.element), ...notes(row));
    // A facet answered before offers no blank where the page cannot reopen
    // it: the service keeps the earlier answer to a facet left out.
    const selects = q.facets.map((facet) => {
      const before = (prefill && prefill.facets && prefill.facets[facet.id]) || "";
      const select = facetSelect(reopen || !before ? "(leave unanswered)" : null, before, reopen);
      select.dataset.key = questionKey(q.key);
      select.dataset.facet = facet.id;
      select.addEventListener("change", (event) => edited(row, event));
      const cell = document.createElement("td");
      cell.append(select);
      return cell;
    });
    const detail = answerDetail((prefill && prefill.detail) || "");
    detail.addEventListener("input", (event) => edited(row, event));
    const detailCell = document.createElement("td");
    detailCell.append(detail, " ", ...extras(row));
    const inputs = selects.map((cell) => cell.children[0]);
    const asks = new Set(q.asks);
    row.cells = q.facets.map((facet, column) => ({
      asked: asks.has(facet.id),
      cell: selects[column],
      select: inputs[column],
    }));
    row.read = () => {
      const given = {};
      for (const select of inputs) {
        if (select.value) given[select.dataset.facet] = select.value;
      }
      return Object.keys(given).length
        ? withDetail({ key: q.key, facets: given }, detail.value.trim())
        : null;
    };
    // The critic names the kind, not a facet, so a finding waiting on it is
    // covered only once every facet says more than "I don't know". A finding
    // that needs only some facets is covered once those are known (ADR 0073);
    // `facets` lists them, or is empty for every facet.
    row.knownFor = (facets) => inputs
      .filter((select) => !facets.length || facets.includes(select.dataset.facet))
      .every((select) => select.value && select.value !== DONT_KNOW);
    row.known = () => row.knownFor(q.asks);
    // Whether a facet it asks is still blank, which a skip sets aside.
    row.open = () => row.cells.some(({ asked, select }) => asked && !select.value);
    // A shared answer reaches only the facets the row shows.
    row.setFacet = (id, value) => {
      for (const select of inputs) {
        if (select.dataset.facet === id && !select.hidden) select.value = value;
      }
    };
    row.set = (next) => {
      for (const select of inputs) select.value = (next.facets || {})[select.dataset.facet] || "";
      detail.value = next.detail || "";
    };
    row.input = row.cells.find(({ asked }) => asked).select;
    row.compact = false;
    row.node.replaceChildren(name, ...selects, detailCell);
    grid.rows.add(row);
    showFacets(grid);
  };
  const drawCompactFacets = (row, answer) => {
    const name = document.createElement("td");
    name.append(questionTitle(row.q, row.q.element), ...notes(row));
    const shown = document.createElement("td");
    shown.textContent = answerText(answer);
    const change = document.createElement("td");
    change.append(button("Change", () => drawFacetRow(row, answer)));
    row.compact = true;
    row.read = () => null;
    row.cells = [];
    row.node.replaceChildren(name, shown, change);
    showFacets(row.grid);
  };
  // Draw a row's open editor, with `prefill` as its saved answer or null.
  const drawOpen = (row, prefill) => {
    if (row.kind === "link") drawLink(row, prefill ? prefill.element : "");
    else if (row.q.form === "facets") drawFacetRow(row, prefill);
    else drawFact(row, prefill);
    row.saved = prefill;
  };
  // Draw a row's saved answer, compact.
  const drawSaved = (row, answer) => {
    if (row.kind === "link") drawCompactLink(row, answer);
    else if (row.q.form === "facets") drawCompactFacets(row, answer);
    else drawCompact(row, answer);
  };

  // ---- Boxes --------------------------------------------------------------
  // The list is a sequence of boxes, one for each run of rows that share a
  // group, in the service's order. The service lists each question kind's
  // rows together (ADR 0080), so a kind takes one box. A capability keeps
  // its place in its framework's turn, so the application's box can come
  // back later in the list, and then it opens a new box.
  // Every box the list holds, so held shares can find theirs.
  const boxes = new Set();
  const newBox = (q) => {
    const node = document.createElement("details");
    node.className = "question-group";
    const title = document.createElement("summary");
    const applies = document.createElement("small");
    applies.className = "group-applies";
    applies.hidden = true;
    const top = document.createElement("div");
    const each = document.createElement("details");
    each.className = "each-one";
    each.open = true;
    const eachTitle = document.createElement("summary");
    eachTitle.hidden = true;
    each.append(eachTitle);
    node.append(title);
    if (q.group === "links") {
      const help = document.createElement("p");
      help.className = "hint group-help";
      help.textContent =
        "Your description says things about these users, teams and accounts, such as " +
        "whether they sign in with MFA. The analysis can use those facts only after you " +
        "match each one to a part of your system. Choose \"None of these\" if no part matches.";
      node.append(help);
    }
    node.append(top, each);
    const box = { node, title, applies, top, each, eachTitle, group: q.group, heading: q.group_heading, rows: [], grid: null, shared: null };
    boxes.add(box);
    return box;
  };
  const titleBox = (box) => {
    const heading = document.createElement("span");
    heading.className = "group-heading";
    heading.textContent = box.heading;
    box.title.replaceChildren(heading, box.applies);
  };
  // A box whose rows ask one attribute or kind of two or more elements asks
  // it once (ADR 0081): one answer for each part a person ticks, sent as that
  // element's own answer (ADR 0113). A row edited by hand is an exception the
  // shared answer leaves alone. A capability asks about the whole
  // application, so it never shares.
  const applyShared = (box, write) => {
    sharing = true;
    try {
      for (const row of box.rows) {
        if (row.kind !== "fact" || row.compact || row.exception) continue;
        if (row.tick && !row.tick.checked) continue;
        write(row);
        row.inherited = true;
        edited(row);
      }
    } finally {
      sharing = false;
    }
  };
  const sharedControl = (box, q) => {
    const line = document.createElement("p");
    if (q.form === "facets") {
      line.className = "shared-facets";
      const selects = [];
      box.grid.shared = q.facets.map((facet) => {
        const label = document.createElement("label");
        label.className = "shared-facet";
        const select = facetSelect("(leave unanswered)", "", reopen);
        select.dataset.facet = facet.id;
        select.addEventListener("change", () =>
          applyShared(box, (row) => row.setFacet && row.setFacet(facet.id, select.value)));
        selects.push(select);
        const prompt = document.createElement("span");
        prompt.textContent = facet.question;
        label.append(prompt, select);
        line.append(label);
        return label;
      });
      box.sharedWrite = (row) => {
        for (const select of selects) if (select.value) row.setFacet(select.dataset.facet, select.value);
      };
      box.sharedClear = (row) => {
        for (const select of selects) if (select.value) row.setFacet(select.dataset.facet, "");
      };
      box.sharedAnswer = () => {
        const facets = {};
        for (const select of selects) if (select.value) facets[select.dataset.facet] = select.value;
        return Object.keys(facets).length ? { facets } : null;
      };
      box.sharedSet = (answer) => {
        for (const select of selects) select.value = (answer.facets || {})[select.dataset.facet] || "";
      };
      showFacets(box.grid);
      return line;
    }
    const shared = inputFor(q, null, () =>
      applyShared(box, (row) => row.set({ value: shared.read() })));
    box.sharedWrite = (row) => {
      if (shared.read()) row.set({ value: shared.read() });
    };
    box.sharedClear = (row) => row.set({ value: "" });
    box.sharedAnswer = () => {
      const answer = shared.answer();
      if (!answer) return null;
      const { key, ...rest } = answer;
      return rest;
    };
    box.sharedSet = (answer) => shared.set(answer.value);
    // The box's heading asks the question for every part it lists, so it
    // names each control; a second control adds what it takes.
    shared.controls.forEach((control, at) => control.setAttribute("aria-label",
      at ? `${q.group_heading}: ${control.placeholder}` : q.group_heading));
    line.append(...shared.nodes);
    return line;
  };
  const ensureShared = (box) => {
    const members = box.rows.filter((row) => row.kind === "fact");
    const one = members.length >= 2
      && members.every((row) => shareable(row.q, members[0].q));
    box.eachTitle.hidden = !one;
    box.applies.hidden = !one;
    box.eachTitle.textContent = `Parts the answer can cover (${members.length})`;
    if (!one) {
      box.top.className = "";
      box.top.replaceChildren();
      box.shared = null;
      if (box.grid) box.grid.shared = [];
      box.each.open = true;
      return;
    }
    if (!box.shared) box.shared = sharedControl(box, members[0].q);
    box.each.open = true;
    box.applies.textContent = "Parts: " + members.map((row) => row.q.element).join(", ");
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "Tick each part this answer covers. A part left unticked keeps"
      + " its own answer, or none.";
    box.top.className = "shared-controls";
    box.top.replaceChildren(box.shared, hint);
    for (const row of members) addTick(row);
  };
  // A part starts unticked: the same question does not prove that one control
  // covers every part (ADR 0113). A ticked part takes the shared answer.
  const addTick = (row) => {
    if (row.tick) return;
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = false;
    tick.title = "Covered by the answer above";
    tick.setAttribute("aria-label", `${row.q.element}: covered by the answer above`);
    tick.className = "share-answer";
    tick.addEventListener("change", (event) => tickChanged(row, event));
    row.tick = tick;
    if (row.compact || !row.node.children.length) return;
    // A facets row holds the tick in its first cell, beside the element's name.
    const holder = row.grid ? row.node.children[0] : row.node;
    holder.replaceChildren(tick, ...[...holder.children]);
  };
  // Put a new row after `prev`, the listed row before it in the service's
  // order, or first where there is none. A row joins `prev`'s box where the
  // groups match, and a facet row joins its table; otherwise a new box opens
  // right after `prev`'s box. The row's node is created here.
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
        box.each.append(box.grid.table, box.grid.toggle);
      }
      row.grid = box.grid;
      const key = row.node.dataset.key;
      row.node = document.createElement("tr");
      row.node.dataset.key = key;
      const after = prev && prev.grid === box.grid ? prev.node : [...box.grid.table.children][0];
      insertAfter(box.grid.table, row.node, after);
      box.grid.count += 1;
    } else {
      insertAfter(box.each, row.node, prev && prev.box === box ? prev.node : box.eachTitle);
    }
    titleBox(box);
    ensureShared(box);
  };
  const removeRow = (row) => {
    const box = row.box;
    row.node.remove();
    if (!box) return;
    box.rows = box.rows.filter((other) => other !== row);
    if (row.grid) {
      row.grid.count -= 1;
      row.grid.rows.delete(row);
      showFacets(row.grid);
    }
    if (!box.rows.length) {
      box.node.remove();
      boxes.delete(box);
    }
    else {
      titleBox(box);
      ensureShared(box);
    }
  };
  // A row drawn on its own, outside the list's boxes, such as one opened from
  // a box of earlier answers. A facets row takes its own table.
  const drawAlone = (row, holder, prefill) => {
    if (row.kind !== "link" && row.q.form === "facets") {
      row.grid = facetTable(row.q);
      row.node = document.createElement("tr");
      row.grid.table.append(row.node);
      holder.append(row.grid.table, row.grid.toggle);
    }
    drawOpen(row, prefill);
  };
  // The share a box holds now, as the service takes it (ADR 0114): the shared
  // answer under the box's question with the element left empty, and each
  // part that holds it. A part of the box holds it where it is ticked; a part
  // the `held` share covers that the box does not list, such as one answered
  // before a reload, keeps holding it. Null where the box shares nothing or
  // has no answer.
  const shareOf = (box, held = null) => {
    if (!box || !box.shared || !box.sharedAnswer) return null;
    const answer = box.sharedAnswer();
    const members = box.rows.filter((row) => row.kind === "fact");
    if (!answer || !members.length) return null;
    const listed = new Set(members.map((row) => row.q.key[0]));
    const kept = held ? held.covers.filter((element) => !listed.has(element)) : [];
    const covers = [...kept, ...members.filter((row) => row.inherited).map((row) => row.q.key[0])];
    return { answer: { ...answer, key: ["", ...members[0].q.key.slice(1)] }, covers };
  };
  // Show the shares the service holds: each box takes its share's answer, and
  // each part it covers is ticked and holds it. A box whose share waits to be
  // saved (`pending`) keeps what the person did.
  const restoreShares = (shares, pending) => {
    const held = new Map(shares.map((share) => [shareKey(share.answer.key), share]));
    for (const box of boxes) {
      const members = box.rows.filter((row) => row.kind === "fact");
      if (!box.shared || !members.length) continue;
      const key = shareKey(members[0].q.key);
      const share = held.get(key);
      if (!share || pending(key)) continue;
      box.sharedSet(share.answer);
      for (const row of members) {
        const covered = share.covers.includes(row.q.key[0]);
        row.inherited = covered;
        if (covered) row.exception = false;
        if (row.tick) row.tick.checked = covered;
      }
    }
  };
  return { place, removeRow, drawOpen, drawSaved, drawAlone, button, shareOf, restoreShares };
};

// Two rules both pages read, so neither keeps its own copy.
// A question's key as every page map holds it: the JSON of the key the
// service serves, for a link row and a fact row alike. One reader, so a
// finding's link and the question it reaches agree on every page.
const questionKey = (key) => JSON.stringify(key);
// A row's key is its question's key.
const rowKey = (row) => questionKey(row.q.key);
// The key a box's share is kept under: its question with the element left
// empty, read from any part's key or from the share's own (ADR 0114).
const shareKey = (key) => `share:${questionKey(["", ...key.slice(1)])}`;
// The key of each part's question the held shares cover.
const sharedKeys = (shares) => new Set(shares.flatMap((share) =>
  share.covers.map((element) => questionKey([element, ...share.answer.key.slice(1)]))));
// Whether a page asks two questions once (ADR 0081): one attribute or kind,
// each of an element, with one form and one set of choices. The
// `shared-answers` instrument reads the same rule, and a test holds the two to
// each other.
const shareable = (a, b) =>
  Boolean(a.key[0] && b.key[0]) && a.key[1] === b.key[1] && a.key[4] === b.key[4]
  && a.form === b.form
  && JSON.stringify(a.choices) === JSON.stringify(b.choices);
// The heading of the box of link questions, on both pages and in the export.
const LINK_GROUP_HEADING = "Match each user, team or account to a part of your system";
// A link answer as one line of text: the element, or "none of these".
const linkAnswerText = (answer) => (answer.element === "none" ? "none of these" : answer.element);
// The index beside the list: a link for each box with a row that shows, with
// its count of rows. A box whose rows are all hidden hides. A click opens the
// box, runs `select(box, link)`, and moves the focus to the box's heading.
const indexGroups = (index, list, select) => {
  index.replaceChildren();
  [...list.children].forEach((box, at) => {
    const rows = [...box.querySelectorAll(".question, tr[data-key]")].filter((row) => !row.hidden);
    box.hidden = !rows.length;
    if (box.hidden) return;
    box.id = `question-group-${at}`;
    const link = document.createElement("a");
    link.textContent = box.querySelector(".group-heading").textContent;
    const count = document.createElement("small");
    count.textContent = counted(rows.length, "question", "questions");
    link.append(count);
    link.href = `#${box.id}`;
    link.box = box;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      box.open = true;
      select(box, link);
      box.querySelector("summary").focus();
    });
    index.append(link);
  });
};
// Two shares are one share when their answers agree and they cover the same
// parts, in any order (ADR 0114).
const sameShare = (a, b) => {
  if (!a || !b) return !a && !b;
  const covers = (share) => [...share.covers].sort().join("\n");
  return sameAnswer(a.answer, b.answer) && covers(a) === covers(b);
};
// Two answers are one answer when the fields the submitter gives agree. The
// service serves every field, with its defaults and with the value it
// writes from a facets answer; a page builds only the fields given.
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
