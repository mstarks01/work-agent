// The Controls of an analysis on the form page (ADRs 0124 and 0127).
//
// At the submission, "Controls for this analysis" lists each Control the
// operator keeps, ticked, and the job copies each ticked one. The model
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
  // Each scope type (ScopeType), as a line names it.
  const TYPES = {
    "application": "The whole application",
    "boundary": "trust boundary",
    "process": "process",
    "store": "data store",
    "entity": "external entity",
  };
  const scopeText = (scope) => (scope.type === "application"
    ? TYPES.application
    : `The ${TYPES[scope.type]} “${scope.name}”`);
  const rowText = (row) => `${row.framework} ${row.unit}`;
  const manage = () => h("a", { href: "/controls" }, "Manage Controls");

  // ---- The submission ------------------------------------------------------
  let held = [];
  let loaded = false;
  // A reopened analysis shows what it used and offers no choice.
  let reopened = false;
  const ticked = new Set();
  const asking = () => Boolean(el("ask")?.checked);

  const drawChecklist = () => {
    const present = new Set(held.map((control) => control.id));
    const lines = held.map((control) => {
      const box = h("input", { type: "checkbox", id: `control-${control.id}`, checked: ticked.has(control.id), disabled: !asking(),
        on: { change: (event) => { if (event.target.checked) ticked.add(control.id); else ticked.delete(control.id); drawChecklist(); } } });
      return h("li", {}, box, " ", h("label", { for: box.id }, h("b", {}, control.name)),
        h("span", { class: "hint" }, ` · ${scopeText(control.scope)}`,
          control.rows.length ? ` · ${control.rows.map(rowText).join(", ")}` : ""));
    });
    const missing = [...ticked].filter((id) => !present.has(id)).map((id) => h("li", { class: "hint" },
      "Missing: a Control you ticked is no longer in Controls. ",
      h("button", { type: "button", class: "quiet", on: { click: () => { ticked.delete(id); drawChecklist(); } } }, "Remove")));
    el("controls-rows").replaceChildren(...lines, ...missing);
    const count = [...ticked].filter((id) => present.has(id)).length;
    el("controls-summary").replaceChildren(
      asking()
        ? `${counted(count, "Control is", "Controls are")} ticked. The service matches each one to your system, and you confirm the match with the questions. `
        : "Controls apply with the questions. Turn the questions on to apply them. ",
      manage());
    el("controls-offer").hidden = reopened || (!held.length && !missing.length);
  };
  // Every Control is ticked when the page first lists it.
  const load = async () => {
    try {
      const listed = await fetch("/workspace/controls");
      const body = listed.ok ? await listed.json() : [];
      held = Array.isArray(body) ? body : [];
    } catch {
      held = [];
    }
    if (!loaded) held.forEach((control) => ticked.add(control.id));
    loaded = true;
    drawChecklist();
  };
  el("ask")?.addEventListener("change", drawChecklist);
  // The IDs a submission sends, or nothing where the questions are off.
  const chosen = () => (asking() && ticked.size ? [...ticked] : undefined);

  // ---- The pause -----------------------------------------------------------
  // Each reason a Control is left out (LeftOutReason), and each reason a
  // zone lists a part unticked (CoverReason).
  const REASONS = {
    "missing-element": "missing-element: no part of this description has the name or an alias",
    "empty-zone": "empty-zone: the zone holds no part of this description",
    "unticked": "unticked: you left it out",
    "zone-inferred": "zone-inferred: the service inferred this placement",
    "no-zone-stated": "no-zone-stated: no source places this part",
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
        ? (resolution.outcome === "application" ? "matches the application" : `matches ${names.get(resolution.element)}`)
        : REASONS[resolution.reason];
      const rows = entry.rows.map((row) => (row.ticked ? rowText(row) : `${rowText(row)} (left out: version ${row.version} is not the version this service carries)`));
      const parts = [
        box, " ",
        h("label", { for: box.id }, h("b", {}, entry.name), " ", h("span", { class: `tag ${resolution.state}` }, chip)),
        h("div", { class: "hint" }, scopeText(entry.scope), rows.length ? ` · ${rows.join(", ")}` : ""),
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
            cover.reason === "stated-zone" ? null : h("span", { class: "hint" }, ` — ${REASONS[cover.reason]}`));
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
    el("controls-used-list").replaceChildren(...(used || []).map((control) => h("li", {}, `${control.name} (${STATES[control.state]})`)));
    el("controls-used").hidden = !(used || []).length;
  };

  return { load, chosen, drawOffer, drawUsed };
})();
