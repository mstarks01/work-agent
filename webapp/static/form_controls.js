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
  // What the page knows of the list: "loading" until a read answers,
  // "loaded" after a good read, and "failed" after a first read fails. A
  // failed read after a good one keeps the last list and sets `stale`.
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
  const APPLY = {
    asked: "You confirm what each Control protects when the questions open.",
    unasked: "Questions are off, so your Controls apply with no check by you. A Control of the whole application always applies. A Control of a part or a zone applies where its name or an alias matches, and a zone covers only the parts that a source places in it. A Control that covers no part is left out. The report says what each Control did.",
  };
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
    // How a ticked Control applies: the owner confirms each match at the
    // pause, or, with the questions off, the default resolution applies it
    // with no check (ADR 0125).
    el("controls-apply").textContent = !names.length ? ""
      : el("ask").checked ? APPLY.asked : APPLY.unasked;
    const known = state === "loaded";
    // Until a good read, the operator's choice decides what the line says.
    const view = !known && declined ? "declined" : state;
    el("controls-status").replaceChildren(...{
      loading: ["Loading your Controls…"],
      failed: ["Your Controls did not load, so this analysis has none yet."],
      declined: ["No Controls: you chose to continue without them."],
      loaded: stale ? ["The list did not refresh. It shows the Controls from the last load."] : [],
    }[view]);
    el("controls-status").className = view === "failed" ? "problem" : "controls-line";
    el("controls-status").hidden = known && !stale;
    el("controls-recover").hidden = known ? !stale : view === "loading";
    el("controls-skip").hidden = view !== "failed";
    el("controls-summary").hidden = !known;
    el("controls-apply").hidden = !known || !names.length;
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
      state = "failed";
    }
    drawChecklist();
  };
  // Whether a submission knows its Controls: from a read, by the operator's
  // choice, or from a reopened analysis.
  const ready = () => reopened || declined || state === "loaded";
  el("controls-retry")?.addEventListener("click", load);
  el("controls-skip")?.addEventListener("click", () => {
    declined = true;
    drawChecklist();
    el("controls-retry").focus();
  });
  el("frameworks")?.addEventListener("change", drawChecklist);
  el("ask").addEventListener("change", drawChecklist);
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
  // The candidates of a zone Control on `element`: the zone it matched, or a
  // zone the owner chose from its choices. Empty for any other element.
  const candidatesOf = (entry, element) => (element === entry.element?.id
    ? entry.covers
    : entry.choices.find((choice) => choice.id === element)?.covers ?? []);
  const defaultTicks = (candidates) => new Set(candidates.filter((cover) => cover.ticked).map((cover) => cover.element));
  // The element of each Control's last accepted save on this page, by
  // Control ID. A zone saved with no ticked part leaves its Control out, and
  // the service then keeps no element, so the page keeps it here. A Control
  // with no saved zone is left out as "missing-element", and shows no zone.
  // The elements belong to one run: an offer from another run forgets them.
  const targets = new Map();
  let targetsRun = null;
  // Record what a save the service accepted sent: each Control it names
  // keeps its element, and a Control it leaves out keeps none.
  const accepted = (scopes) => {
    targets.clear();
    for (const scope of scopes) if (scope.element) targets.set(scope.control, scope.element);
  };
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
    // A Control left out has no tick saved, so it shows no tick.
    const target = resolution.reason === "missing-element" ? undefined : targets.get(entry.id);
    return { on: false, element: target ?? (entry.element ? entry.element.id : ""), ticks: new Set() };
  };
  // A zone Control with an element goes in each save, with no tick while it
  // is off: the service then leaves it out as "unticked" or "empty-zone",
  // and `accepted` keeps its zone. Any other Control that is off stays out
  // of the save.
  const scopesOf = (offer, picks) => offer.flatMap((entry) => {
    const pick = picks.get(entry.id);
    if (entry.scope.type === "boundary") {
      return pick.element ? [{ control: entry.id, element: pick.element, ticked: pick.on ? [...pick.ticks] : [] }] : [];
    }
    if (!pick.on) return [];
    return [entry.scope.type === "application" ? { control: entry.id } : { control: entry.id, element: pick.element }];
  });

  // A note that "Keep this name" leaves for the next draw of the offer.
  let keptNote = "";
  // `box` holds the offer. `actions.save(scopes)` saves a change, and the
  // service's answer redraws the offer; `actions.refuse(message)` shows a
  // refusal.
  const drawOffer = (box, data, actions) => {
    if (data.run !== targetsRun) {
      targets.clear();
      targetsRun = data.run;
    }
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
        on: { change: (event) => {
          pick.on = event.target.checked;
          // A zone ticked on again with no part takes its default ticks.
          if (pick.on && !pick.ticks.size) pick.ticks = defaultTicks(candidatesOf(entry, pick.element));
          send();
        } } });
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
      if (candidates.length && !pick.ticks.size) {
        parts.push(h("p", { class: "hint" }, `Tick each part of the ${names.get(pick.element)} that this Control protects.`));
      }
      if (candidates.length) {
        parts.push(h("ul", { class: "fanout" }, ...candidates.map((cover) => {
          const tick = h("input", { type: "checkbox", id: `cover-${entry.id}-${cover.element}`, checked: pick.ticks.has(cover.element),
            on: { change: (event) => {
              if (event.target.checked) pick.ticks.add(cover.element);
              else pick.ticks.delete(cover.element);
              // The zone is sent with no tick, so the service says the
              // Control is left out because no part is ticked.
              pick.on = true;
              send();
            } } });
          return h("li", {}, tick, " ", h("label", { for: tick.id }, cover.name),
            h("span", { class: "hint" }, ` — ${cover.meaning}`));
        })));
      }
      const own = facts.filter((fact) => fact.control === entry.id);
      if (own.length) {
        // Each fact reads as it does on the report page, in the service's words.
        parts.push(h("ul", { class: "hint" }, ...own.map((fact) => h("li", {}, `${fact.label}: ${fact.answer.value} — ${fact.meaning}.${fact.stated ? ` The description states “${fact.stated}”.` : ""}`))));
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
        refuse(await messageOf(kept, "The service could not keep the name. Check Controls before you keep the name again."));
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
  const drawUsed = (used) => {
    reopened = true;
    el("controls-offer").hidden = true;
    holdStart();
    el("controls-used-list").replaceChildren(...(used || []).map((control) => h("li", {}, `${control.name} (${control.state_text})`)));
    el("controls-used").hidden = !(used || []).length;
  };

  const onChange = (handler) => { changed = handler; };
  return { load, ready, onChange, chosen, drawOffer, accepted, drawUsed };
})();
