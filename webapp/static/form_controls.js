// The Controls of an analysis on the form page (ADRs 0124 and 0127).
//
// At the submission, the settings column names the ticked Controls in one
// line, and "Choose Controls" opens a dialog that lists each Control the
// operator keeps, ticked. The job copies each ticked one. The model
// exists only after the extraction, so the pause shows each copy with its
// match: the element it matched, a zone's parts, or a choice of the elements
// of its type. Each change there saves the `control_scopes` of the pause.
// Every name, alias and value reaches the page as a text node.
const formControls = (() => {
  const el = (id) => document.getElementById(id);
  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "on") {
        for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
      } else if (key === "class") {
        node.className = value;
      } else if (key === "checked" || key === "disabled" || key === "value") {
        node[key] = value;
      } else if (value != null && value !== false) {
        node.setAttribute(key, value === true ? "" : value);
      }
    }
    node.append(...children.flat(Infinity).filter((child) => child != null && child !== false));
    return node;
  };
  const counted = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  // The service's phrase for what a Control protects (scope_phrase), as the
  // start of a line.
  const scopeText = (phrase) => phrase.charAt(0).toUpperCase() + phrase.slice(1);
  const rowText = (row) => `${row.framework} ${row.unit}`;
  // The frameworks the form selects now.
  const selected = () => new Set([...document.querySelectorAll('#frameworks input[name="framework"]:checked')]
    .map((box) => box.value));
  // Whether a row of the Control names a selected framework, at the version
  // this service carries (`current`, from control_view's current_rows) or at
  // another.
  const rowsOf = (control, frameworks, current) => control.rows.some((row, at) =>
    frameworks.has(row.framework) && control.current_rows[at] === current);
  // A Control with no fact and no current row of a selected framework changes
  // nothing in this analysis (job_controls.changes_nothing), so the list
  // offers no tick. Before any framework is chosen, the list judges no Control.
  const fits = (control, frameworks) => !frameworks.size || control.facts.length > 0
    || rowsOf(control, frameworks, true);
  const coveredBy = (control) => [...new Set(control.rows.map((row) => row.framework))].join(" and ");
  const manage = () => h("a", { href: "/controls" }, "Manage Controls");
  // "A, B and C", or "A, B, C and 4 more" past three names.
  const nameList = (names) => {
    const shown = names.length > 3 ? [...names.slice(0, 3), `${names.length - 3} more`] : names;
    return shown.length > 1 ? `${shown.slice(0, -1).join(", ")} and ${shown.at(-1)}` : shown.join("");
  };

  // ---- The submission ------------------------------------------------------
  let held = [];
  // What the page knows of the list: "loading" until a first read answers,
  // "loaded" after a good read, "failed" after a first read fails, and
  // "declined" when the operator continues without Controls. A failed read
  // after a good one keeps the last list and sets `stale`.
  let state = "loading";
  let stale = false;
  // The operator's choice to continue without Controls. A good read clears it.
  let declined = false;
  // The form page sets `changed` to learn when `ready()` can change, and
  // holds its start button while the Controls of a submission are unknown.
  let changed = () => {};
  const holdStart = () => {
    el("go-wait").textContent = state === "loading" ? "Wait for your Controls to load."
      : "Retry, or choose to continue without Controls.";
    el("go-wait").hidden = ready();
    changed();
  };
  // Each read takes a number, and only the latest read sets the list.
  let reads = 0;
  // A reopened analysis shows what it used and offers no choice.
  let reopened = false;
  const ticked = new Set();

  // The dialog lists the Controls that fit the selected frameworks first,
  // and folds the ones that do not. The find box shows the Controls whose
  // name holds its text.
  const drawChecklist = () => {
    const focused = document.activeElement?.id;
    const present = new Set(held.map((control) => control.id));
    const frameworks = selected();
    const found = el("controls-find").value.trim().toLocaleLowerCase();
    const shown = (control) => control.name.toLocaleLowerCase().includes(found);
    const line = (control) => {
      const fit = fits(control, frameworks);
      const ownRows = rowsOf(control, frameworks, true);
      const oldRows = !ownRows && rowsOf(control, frameworks, false);
      const box = h("input", { type: "checkbox", id: `control-${control.id}`, checked: fit && ticked.has(control.id),
        disabled: !fit,
        on: { change: (event) => { if (event.target.checked) ticked.add(control.id); else ticked.delete(control.id); drawChecklist(); } } });
      const note = !fit
        ? (oldRows
          ? "Not for this analysis: it states no fact, and its rows are not at the framework version this service carries. Edit its rows in Controls."
          : `Not for this analysis: it states no fact and covers only ${coveredBy(control)}.`)
        : !control.rows.length || ownRows ? ""
          : oldRows ? "Only its facts apply here: its rows are not at the framework version this service carries."
            : `Only its facts apply here: its rows cover ${coveredBy(control)}.`;
      return h("li", { hidden: !shown(control) }, box, " ", h("label", { for: box.id }, h("b", {}, control.name)),
        h("span", { class: "hint" }, ` · ${scopeText(control.scope_phrase)}`,
          control.rows.length ? ` · ${control.rows.map(rowText).join(", ")}` : ""),
        note ? h("span", { class: "hint control-fit" }, ` · ${note}`) : null);
    };
    const fitting = held.filter((control) => fits(control, frameworks));
    const unfit = held.filter((control) => !fits(control, frameworks));
    const missing = [...ticked].filter((id) => !present.has(id)).map((id) => h("li", { class: "hint" },
      "Missing: a Control you ticked is no longer in Controls. ",
      h("button", { type: "button", class: "quiet", on: { click: () => { ticked.delete(id); drawChecklist(); el("controls-find").focus(); } } }, "Remove")));
    el("controls-rows").replaceChildren(...fitting.map(line), ...missing);
    el("controls-unfit-rows").replaceChildren(...unfit.map(line));
    el("controls-unfit-summary").textContent = `${counted(unfit.length, "does", "do")} not apply to this analysis`;
    el("controls-unfit").hidden = !unfit.length;
    const names = fitting.filter((control) => ticked.has(control.id)).map((control) => control.name);
    el("controls-count").textContent = `${names.length} of ${fitting.length} ticked`;
    el("controls-summary").replaceChildren(...[
      names.length ? `${names.length} ticked: ${nameList(names)}.` : "None ticked.",
      unfit.length ? h("span", { class: "hint" }, ` ${counted(unfit.length, "does", "do")} not apply.`) : null,
      missing.length ? h("span", { class: "hint" }, ` ${counted(missing.length, "ticked Control is", "ticked Controls are")} missing.`) : null,
    ].filter(Boolean));
    const known = state === "loaded";
    el("controls-status").replaceChildren(...{
      loading: ["Loading your Controls…"],
      failed: ["Your Controls did not load, so this analysis has none yet."],
      declined: ["No Controls: you chose to continue without them."],
      loaded: stale ? ["The list did not refresh. It shows the Controls from the last load."] : [],
    }[state]);
    el("controls-status").className = state === "failed" ? "problem" : "controls-line";
    el("controls-status").hidden = known && !stale;
    el("controls-recover").hidden = known ? !stale : state === "loading";
    el("controls-skip").hidden = state !== "failed";
    el("controls-summary").hidden = !known;
    el("controls-choose").hidden = !known;
    holdStart();
    el("controls-offer").hidden = reopened || (known && !stale && !held.length && !missing.length);
    // A hidden line takes its dialog with it, so no closed list holds the
    // page, and focus goes to the form.
    if (el("controls-offer").hidden && el("controls-dialog").open) {
      el("controls-dialog").close();
      el("analysis-name").focus();
    }
    if (focused && document.activeElement?.id !== focused) el(focused)?.focus();
  };
  // Tick all and Untick all act on the Controls that fit and that the find
  // box shows.
  const tickShown = (on) => {
    const frameworks = selected();
    const found = el("controls-find").value.trim().toLocaleLowerCase();
    held.filter((control) => fits(control, frameworks) && control.name.toLocaleLowerCase().includes(found))
      .forEach((control) => { if (on) ticked.add(control.id); else ticked.delete(control.id); });
    drawChecklist();
  };
  el("controls-choose")?.addEventListener("click", () => el("controls-dialog").showModal());
  el("controls-done")?.addEventListener("click", () => el("controls-dialog").close());
  el("controls-all")?.addEventListener("click", () => tickShown(true));
  el("controls-none")?.addEventListener("click", () => tickShown(false));
  el("controls-find")?.addEventListener("input", drawChecklist);
  // The dialog sits in the form, so Enter in the find box must not submit it.
  el("controls-find")?.addEventListener("keydown", (event) => { if (event.key === "Enter") event.preventDefault(); });
  // Every Control is ticked when the page first lists it.
  const load = async () => {
    const read = ++reads;
    if (state !== "loaded") state = "loading";
    drawChecklist();
    let body = null;
    try {
      const listed = await fetch("/workspace/controls");
      if (listed.ok) body = await listed.json();
    } catch {
      // A failed read leaves `body` null.
    }
    if (read !== reads) return;
    if (Array.isArray(body)) {
      if (state !== "loaded") body.forEach((control) => ticked.add(control.id));
      held = body;
      state = "loaded";
      declined = false;
      stale = false;
    } else if (state === "loaded") {
      stale = true;
    } else {
      // A failed read keeps the operator's choice to continue without them.
      state = declined ? "declined" : "failed";
    }
    drawChecklist();
  };
  // Whether a submission knows its Controls: from a read, by the operator's
  // choice, or from a reopened analysis.
  const ready = () => reopened || state === "loaded" || state === "declined";
  el("controls-retry")?.addEventListener("click", load);
  el("controls-skip")?.addEventListener("click", () => {
    declined = true;
    state = "declined";
    drawChecklist();
    el("controls-retry").focus();
  });
  el("frameworks")?.addEventListener("change", drawChecklist);
  // The IDs a submission sends, or nothing where none is ticked. A Control
  // that does not fit the selected frameworks stays ticked for later, but
  // goes with no submission.
  const chosenIds = () => {
    const frameworks = selected();
    const byId = new Map(held.map((control) => [control.id, control]));
    // A ticked ID the list no longer holds still goes, so the service names it.
    return [...ticked].filter((id) => !byId.has(id) || fits(byId.get(id), frameworks));
  };
  const chosen = () => {
    const ids = chosenIds();
    return ids.length ? ids : undefined;
  };

  // ---- The pause -----------------------------------------------------------
  // Each reason a zone lists a part (CoverReason).
  const COVER_REASONS = {
    "zone-inferred": "the service inferred this placement",
    "no-zone-stated": "no source places this part",
    "stated-zone": "a source places this part in the zone",
  };
  // The candidates of a zone Control on `element`: the zone it matched, or a
  // zone the owner chose from its choices. Empty for any other element.
  const candidatesOf = (entry, element) => (element === entry.element?.id
    ? entry.covers
    : entry.choices.find((choice) => choice.id === element)?.covers ?? []);
  const defaultTicks = (candidates) => new Set(candidates.filter((cover) => cover.ticked).map((cover) => cover.element));
  // What the owner chose for each copy, by Control ID: whether it applies,
  // its element, and a zone's ticked parts. Drawn from the service's
  // resolution, and sent whole on each change.
  const chosenOf = (entry) => {
    const resolution = entry.resolution;
    if (resolution.state === "applied") {
      return {
        on: true,
        element: resolution.element,
        ticks: new Set(resolution.covers.filter((cover) => cover.ticked).map((cover) => cover.element)),
      };
    }
    return { on: false, element: entry.element ? entry.element.id : "", ticks: defaultTicks(entry.covers) };
  };
  const scopesOf = (offer, picks) => offer
    .filter((entry) => picks.get(entry.id).on)
    .map((entry) => {
      const pick = picks.get(entry.id);
      if (entry.scope.type === "application") return { control: entry.id };
      return entry.scope.type === "boundary"
        ? { control: entry.id, element: pick.element, ticked: [...pick.ticks] }
        : { control: entry.id, element: pick.element };
    });

  // A note that "Keep this name" leaves for the next draw of the offer.
  let keptNote = "";
  // `box` holds the offer. `actions.save(scopes)` saves a change, and the
  // service's answer redraws the offer; `actions.refuse(message)` shows a
  // refusal.
  const drawOffer = (box, data, actions) => {
    const offer = data.controls || [];
    box.hidden = !offer.length;
    if (!offer.length) return box.replaceChildren();
    const picks = new Map(offer.map((entry) => [entry.id, chosenOf(entry)]));
    const names = new Map();
    for (const entry of offer) {
      const candidates = [...entry.covers, ...entry.choices.flatMap((choice) => choice.covers)];
      for (const one of [entry.element, ...entry.choices, ...candidates]) {
        if (one) names.set(one.id ?? one.element, one.name);
      }
    }
    const send = () => actions.save(scopesOf(offer, picks));
    const facts = data.control_facts || [];
    const line = (entry) => {
      const pick = picks.get(entry.id);
      const resolution = entry.resolution;
      const needsChoice = entry.match === "missing-element";
      const box = h("input", { type: "checkbox", id: `offer-${entry.id}`, checked: pick.on, disabled: needsChoice && !pick.element,
        on: { change: (event) => { pick.on = event.target.checked; send(); } } });
      const chip = resolution.state === "applied"
        ? (resolution.outcome === "application" ? entry.meaning : `matches ${names.get(resolution.element)}: ${entry.meaning}`)
        : `left out: ${entry.meaning}`;
      const rows = entry.rows.map((row) => (row.ticked ? rowText(row) : `${rowText(row)} (left out: version ${row.version} is not the version this service carries)`));
      const parts = [
        box, " ",
        h("label", { for: box.id }, h("b", {}, entry.name), " ", h("span", { class: `tag ${resolution.state}` }, chip)),
        h("div", { class: "hint" }, scopeText(entry.scope_phrase), rows.length ? ` · ${rows.join(", ")}` : ""),
      ];
      if (needsChoice) {
        const choose = h("select", { id: `apply-${entry.id}`, on: { change: (event) => {
          pick.element = event.target.value;
          pick.ticks = defaultTicks(candidatesOf(entry, pick.element));
          pick.on = Boolean(pick.element);
          send();
        } } }, h("option", { value: "" }, "Not this time"),
        ...entry.choices.map((choice) => h("option", { value: choice.id }, choice.name)));
        choose.value = pick.element;
        const keep = pick.element ? h("button", { type: "button", class: "quiet", on: { click: () => keepName(entry, names.get(pick.element), actions.refuse, send) } },
          "Keep this name") : null;
        parts.push(h("div", { class: "choice" }, h("label", { for: choose.id }, "Apply it to "), choose, " ", keep,
          h("p", { class: "hint" }, "This choice applies to this analysis only. “Keep this name” adds the name to the Control's aliases, so it matches next time.")));
      }
      const candidates = candidatesOf(entry, pick.element);
      if (candidates.length) {
        parts.push(h("ul", { class: "fanout" }, ...candidates.map((cover) => {
          const tick = h("input", { type: "checkbox", id: `cover-${entry.id}-${cover.element}`, checked: pick.ticks.has(cover.element),
            on: { change: (event) => {
              if (event.target.checked) pick.ticks.add(cover.element);
              else pick.ticks.delete(cover.element);
              pick.on = pick.ticks.size > 0;
              send();
            } } });
          return h("li", {}, tick, " ", h("label", { for: tick.id }, cover.name),
            h("span", { class: "hint" }, ` — ${COVER_REASONS[cover.reason]}`));
        })));
      }
      const own = facts.filter((fact) => fact.control === entry.id);
      if (own.length) {
        parts.push(h("ul", { class: "hint" }, ...own.map((fact) => h("li", {}, fact.state === "stated"
          ? `${fact.label}: your description states “${fact.stated}”, so it stands`
          : `${fact.label}: ${fact.answer.value}`))));
      }
      return h("li", { class: "offer-line" }, ...parts);
    };
    const applies = offer.filter((entry) => entry.resolution.state === "applied").length;
    const note = keptNote;
    keptNote = "";
    box.replaceChildren(...[
      h("h2", {}, "Controls for this analysis"),
      note ? h("p", { class: "hint", role: "status" }, note) : null,
      h("ul", { class: "offer-lines" }, ...offer.map(line)),
      h("p", { class: "hint" }, `${counted(applies, "Control", "Controls")} of ${offer.length} will apply. `, manage()),
    ].filter(Boolean));
  };
  // "Keep this name": the Control learns the element's name as an alias.
  const keepName = async (entry, name, refuse, send) => {
    try {
      const kept = await fetch(`/workspace/controls/${encodeURIComponent(entry.id)}/aliases`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias: name }),
      });
      if (!kept.ok) {
        refuse((await kept.json()).message);
        return;
      }
    } catch {
      refuse("The connection was interrupted. Check Controls before you keep the name again.");
      return;
    }
    keptNote = `“${name}” is now an alias of “${entry.name}”.`;
    send();
  };

  // ---- A reopened analysis -------------------------------------------------
  // Each state of a Control a reopened analysis lists (UsedState).
  const STATES = {
    "applied": "applied",
    "left-out": "left out",
    "pending": "not matched yet",
  };
  const drawUsed = (used) => {
    reopened = true;
    el("controls-offer").hidden = true;
    holdStart();
    el("controls-used-list").replaceChildren(...(used || []).map((control) => h("li", {}, `${control.name} (${STATES[control.state]})`)));
    el("controls-used").hidden = !(used || []).length;
  };

  const onChange = (handler) => { changed = handler; };
  return { load, ready, onChange, chosen, drawOffer, drawUsed };
})();
