// PROTOTYPE for ticket #1762 on map #1760. Throwaway: three layouts of the
// Settings page. `PROTO` comes from webapp/settings_prototype.py. A save runs
// no loader and writes nothing; `check` copies the real loader messages.

const VARIANTS = { A: "One form", B: "Setup steps", C: "Table and changes" };
const KEYS = Object.keys(VARIANTS);
const TIERS = ["base", "strong", "review"];

const byKey = Object.fromEntries(PROTO.settings.map((s) => [s.key, s]));
let saved = Object.fromEntries(PROTO.settings.map((s) => [s.key, s.value]));
let draft = { ...saved };
let engineError = PROTO.engine_error;
let result = null; // {kind: "refusal" | "saved", message, restart}
let refreshVariant = () => {};

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "class") el.className = value;
    else if (name.startsWith("on")) el.addEventListener(name.slice(2), value);
    else if (value === true) el.setAttribute(name, "");
    else if (value !== false && value != null) el.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const chosenVendors = () => [...new Set(TIERS.map((t) => draft[`tiers.${t}.vendor`]).filter(Boolean))];
const modeOf = (vendor) => {
  const modes = PROTO.vendor_modes[vendor];
  return modes.length === 1 ? modes[0] : draft[`credentials.${vendor}.mode`];
};
// A credential field shows only for a vendor a tier chose, under a mode that reads it.
const visible = (s) => {
  if (s.when && draft[s.when[0]] !== s.when[1]) return false;
  if (!s.vendor) return true;
  if (!chosenVendors().includes(s.vendor)) return false;
  return !s.modes || s.modes.includes(modeOf(s.vendor));
};
const locked = (s) => s.mark === "shows" || s.source === "outside";
const isChanged = (s) =>
  s.kind === "secret" ? draft[s.key] !== saved[s.key] && draft[s.key] !== "" : !same(draft[s.key], saved[s.key]);
const changes = () => PROTO.settings.filter((s) => visible(s) && !locked(s) && isChanged(s));
const groups = () => [...new Set(PROTO.settings.map((s) => s.group))];
const fill = (template, values) => template.replace(/\{(\w+)\}/g, (_, k) => values[k]);
const present = (s) =>
  s.kind === "secret" ? saved[s.key] === "set" || (draft[s.key] !== saved[s.key] && draft[s.key] !== "") : String(draft[s.key]).trim() !== "";

// The loader's message for the first credential this vendor lacks, or null.
function missingCredential(vendor) {
  const mode = modeOf(vendor);
  if (!mode) {
    return fill(PROTO.messages["no-mode"], { vendor, modes: PROTO.vendor_modes[vendor].join(", ") });
  }
  const lacking = PROTO.settings.find((s) => s.vendor === vendor && s.modes && s.modes.includes(mode) && !present(s));
  return lacking ? fill(PROTO.messages["no-credential"], { vendor, var: lacking.env, mode }) : null;
}

// The stand-in for "build a test Deployment from the new mapping".
function check() {
  for (const tier of TIERS) {
    if (draft[`tiers.${tier}.vendor`] && !draft[`tiers.${tier}.model`].trim()) {
      return fill(PROTO.messages["no-model"], { TIER: tier.toUpperCase() });
    }
  }
  if (!draft["frameworks.carried"].length) {
    return "config/frameworks.toml: 'carried' must be a non-empty list of framework names";
  }
  const missing = ["base", "strong"].filter((t) => !draft[`tiers.${t}.vendor`]);
  if (missing.length) {
    return fill(PROTO.messages["no-vendor"], { tiers: JSON.stringify(missing).replaceAll('"', "'") });
  }
  for (const vendor of chosenVendors()) {
    const message = missingCredential(vendor);
    if (message) return message;
  }
  // Deployment.from_env does not read the store: the check runs build_store too.
  if (draft["store.job"] === "sql") {
    if (!draft["store.object"]) return "set ANALYSIS_OBJECT_STORE";
    if (draft["store.object"] === "filesystem" && !draft["store.object.root"]) {
      return "set ANALYSIS_OBJECT_ROOT for the filesystem store";
    }
    if (!draft["store.database.url"]) return "set ANALYSIS_DATABASE_URL for the sql job store";
  }
  return null;
}

function save() {
  const pending = changes();
  const message = check();
  if (message) {
    result = { kind: "refusal", message };
  } else {
    const restart = pending.some((s) => s.restart);
    for (const s of pending) {
      saved[s.key] = s.kind === "secret" ? "set" : draft[s.key];
      s.source = "page";
    }
    draft = { ...saved };
    engineError = null;
    result = { kind: "saved", restart, count: pending.length };
  }
  draw();
}

function discard() {
  draft = { ...saved };
  result = null;
  draw();
}

// ---- shared pieces --------------------------------------------------------

// A model field with suggestions from the pinned litellm map for the tier's
// vendor. Free text stays allowed: the map lags each new model.
function modelControl(s, id, set) {
  const vendor = draft[s.key.replace(/\.model$/, ".vendor")];
  const known = (PROTO.models[vendor] || []);
  const listId = `models-${s.key}`;
  const note = h("small", { class: "hint" });
  const showNote = () => {
    const value = draft[s.key].trim();
    note.textContent = !vendor
      ? "Choose the vendor first."
      : !value
        ? `${known.length} models in the list. Type to filter, or type any model.`
        : known.includes(value)
          ? ""
          : "Not in the model list. The check still runs it.";
  };
  const input = h("input", {
    id,
    type: "text",
    list: listId,
    autocomplete: "off",
    disabled: !vendor,
    value: draft[s.key],
    placeholder: vendor ? "Type to search, e.g. opus" : "",
    oninput: (e) => {
      set(e.target.value);
      showNote();
    },
  });
  showNote();
  return h("span", {}, input, h("datalist", { id: listId }, known.map((m) => h("option", { value: m }))), note);
}

// A dropdown button that opens a panel of checkboxes, one for each option.
function multiDropdown(s, id, set) {
  const text = () => (draft[s.key].length ? draft[s.key].join(", ") : "None chosen");
  const label = h("span", {}, text());
  return h(
    "details",
    { class: "multi" },
    h("summary", { id, "aria-label": `${s.label}: ${text()}` }, label),
    h(
      "div",
      { class: "multi-menu", role: "group", "aria-label": s.label },
      s.options.map((o) =>
        h(
          "label",
          {},
          h("input", {
            type: "checkbox",
            value: o,
            checked: draft[s.key].includes(o),
            onchange: (e) => {
              const next = e.target.checked ? [...draft[s.key], o] : draft[s.key].filter((x) => x !== o);
              set(s.options.filter((x) => next.includes(x)));
              label.textContent = text();
            },
          }),
          ` ${o}`,
        ),
      ),
    ),
  );
}

function control(s, onRedraw, suggest = false) {
  const id = `f-${s.key}`;
  if (locked(s)) {
    return h("span", { id }, h("code", {}, String(s.value)));
  }
  const set = (value, redraw) => {
    draft[s.key] = value;
    if (redraw) onRedraw();
    else refresh();
  };
  if (suggest && /^tiers\.\w+\.model$/.test(s.key)) return modelControl(s, id, set);
  if (suggest && s.kind === "multi") return multiDropdown(s, id, set);
  switch (s.kind) {
    case "choice":
      return h(
        "select",
        { id, onchange: (e) => set(e.target.value, true) },
        h("option", { value: "" }, s.optional ? "— not used —" : "— choose —"),
        s.options.map((o) => h("option", { value: o, selected: draft[s.key] === o }, o)),
      );
    case "bool":
      return h("input", { id, type: "checkbox", checked: draft[s.key], onchange: (e) => set(e.target.checked) });
    case "multi":
      return h(
        "span",
        { id },
        s.options.map((o) =>
          h(
            "label",
            { class: "pick" },
            h("input", {
              type: "checkbox",
              checked: draft[s.key].includes(o),
              onchange: (e) =>
                set(e.target.checked ? [...draft[s.key], o] : draft[s.key].filter((x) => x !== o)),
            }),
            ` ${o}  `,
          ),
        ),
      );
    case "secret":
      return h("input", {
        id,
        type: "password",
        autocomplete: "off",
        value: draft[s.key] === saved[s.key] ? "" : draft[s.key],
        placeholder: saved[s.key] === "set" ? "Type a new key to replace it." : "Paste the key.",
        oninput: (e) => set(e.target.value || saved[s.key]),
      });
    case "number":
      return h("input", { id, type: "number", value: draft[s.key], oninput: (e) => set(Number(e.target.value)) });
    default:
      return h("input", {
        id,
        type: "text",
        value: draft[s.key],
        placeholder: s.example ? `e.g. ${s.example}` : null,
        oninput: (e) => set(e.target.value),
      });
  }
}

// The iam note and setup command, on the first visible field of the vendor.
function iamNote(s) {
  const first = s.vendor && PROTO.settings.find((x) => x.vendor === s.vendor && visible(x)) === s;
  if (!first || modeOf(s.vendor) !== "iam") return null;
  const [text, command] = PROTO.iam_notes[s.vendor];
  return h("small", { class: "hint" }, `${text} `, h("code", {}, command));
}

// A key's state, as a sentence: the page never shows the key itself.
function keyState(s) {
  return saved[s.key] === "set"
    ? h("small", { class: "hint saved" }, "A key is saved. The page never shows it.")
    : h("small", { class: "hint missing" }, "No key is saved.");
}

function tags(s) {
  const out = [];
  if (s.kind === "secret") out.push(keyState(s));
  if (s.source === "outside") out.push(h("span", { class: "tag outside" }, `set outside this page: ${s.env}`));
  if (s.mark === "shows") out.push(h("span", { class: "tag" }, "shown only"));
  if (s.restart) out.push(h("span", { class: "tag restart" }, "restart needed"));
  out.push(iamNote(s));
  return out;
}

function engineBanner() {
  if (!engineError) return null;
  return h(
    "div",
    { class: "refusal", role: "alert" },
    h("b", {}, "The engine could not start. "),
    "No analysis can run until these settings are complete.",
    h("div", {}, h("code", {}, engineError)),
  );
}

function resultBox() {
  if (!result) return null;
  if (result.kind === "refusal") {
    return h(
      "div",
      { class: "refusal", role: "alert" },
      h("b", {}, "Not saved. "),
      "The settings did not load:",
      h("div", {}, h("code", {}, result.message)),
    );
  }
  return h(
    "div",
    { class: "notice", role: "status" },
    h("b", {}, `Saved ${result.count} change${result.count === 1 ? "" : "s"}. `),
    "The next analysis uses the new settings. An analysis in progress finishes on the settings it started with.",
    result.restart
      ? h(
          "p",
          {},
          h("b", {}, "Restart needed: "),
          "the job storage change takes effect after you restart the app: ",
          h("code", {}, "uv run python webapp/main.py"),
        )
      : null,
  );
}

function shownOnly(s) {
  return h("p", { class: "hint" }, s.reason);
}

// ---- Variant A: one long form, grouped, a sticky save bar -------------------

function variantA() {
  const summary = h("span");
  const button = h("button", { class: "primary", type: "button", onclick: save }, "Check and save");
  const page = h(
    "div",
    {},
    h("h1", {}, "Settings"),
    h("p", { class: "muted" }, `The app keeps these in ${PROTO.settings_dir}.`),
    engineBanner(),
    resultBox(),
    h("nav", { class: "group-nav", "aria-label": "Setting groups" }, groups().map((g) => h("a", { href: `#g-${g}` }, g))),
    groups().map((g) =>
      h(
        "section",
        { id: `g-${g}` },
        h("h2", {}, g),
        PROTO.settings
          .filter((s) => s.group === g && visible(s))
          .map((s) =>
            h(
              "div",
              { class: `row${locked(s) ? " locked" : ""}` },
              h("label", { for: `f-${s.key}` }, s.label),
              control(s, draw),
              h("div", { class: "meta" }, s.env ? h("code", {}, s.env) : null, tags(s)),
              s.mark === "shows" ? h("div", { class: "meta" }, shownOnly(s)) : null,
            ),
          ),
      ),
    ),
    h(
      "div",
      { class: "savebar" },
      summary,
      h("button", { type: "button", onclick: discard }, "Discard"),
      button,
    ),
  );
  refreshVariant = () => {
    const n = changes().length;
    summary.textContent = n ? `${n} unsaved change${n === 1 ? "" : "s"}` : "No unsaved changes";
    button.disabled = n === 0;
  };
  return page;
}

// ---- Variant B: setup steps, then "More settings" ---------------------------

function variantB() {
  const steps = [
    {
      title: "Choose the models",
      done: () => ["base", "strong"].every((t) => draft[`tiers.${t}.vendor`] && draft[`tiers.${t}.model`]),
      summary: () =>
        TIERS.filter((t) => draft[`tiers.${t}.vendor`])
          .map((t) => `${t}: ${draft[`tiers.${t}.vendor`]} / ${draft[`tiers.${t}.model`] || "?"}`)
          .join(" · ") || "No models chosen.",
      keys: () => PROTO.settings.filter((s) => s.group === "Models"),
    },
    {
      title: "Add the credentials for each vendor",
      done: () => chosenVendors().length > 0 && chosenVendors().every((v) => !missingCredential(v)),
      summary: () =>
        chosenVendors().map((v) => `${v}: ${missingCredential(v) ? "incomplete" : "complete"}`).join(" · ") || "Choose a model first.",
      keys: () => PROTO.settings.filter((s) => s.group === "Credentials" && visible(s)),
    },
    {
      title: "Choose the frameworks",
      done: () => draft["frameworks.carried"].length > 0,
      summary: () => draft["frameworks.carried"].join(", ") || "None.",
      keys: () => [byKey["frameworks.carried"]],
    },
    {
      title: "Choose where to keep analyses (optional)",
      done: () => true,
      summary: () =>
        draft["store.job"] === "memory" ? "In memory. The app forgets them when it stops." : `In a database: ${draft["store.database.url"] || "?"}`,
      keys: () => PROTO.settings.filter((s) => s.group === "Job storage" && visible(s)),
    },
  ];
  const badges = [];
  const summaries = [];
  const page = h(
    "div",
    {},
    h("h1", {}, engineError ? "Set up the app" : "Settings"),
    h("p", { class: "muted" }, engineError ? "Complete the steps, then check and save. The app starts the engine when the check passes." : "Each step shows what the app uses now."),
    engineBanner(),
    h(
      "ol",
      { class: "steps" },
      steps.map((step, i) => {
        const badge = h("span", { class: "num" }, String(i + 1));
        const summary = h("span", { class: "hint" });
        badges.push([badge, step]);
        summaries.push([summary, step]);
        return h(
          "li",
          { class: `step${step.done() ? " done" : ""}` },
          h(
            "details",
            { open: !step.done() || !!engineError },
            h("summary", {}, h("header", {}, badge, h("h2", {}, step.title), summary)),
            h(
              "div",
              { class: "body" },
              step.keys().map((s) =>
                h("div", { class: "row" }, h("label", { for: `f-${s.key}` }, s.label), control(s, draw), h("div", { class: "meta" }, tags(s))),
              ),
            ),
          ),
        );
      }),
    ),
    h("div", { class: "savebar" }, h("span", {}, "The check loads the settings the same way the engine does. It makes no provider call."), h("button", { type: "button", onclick: discard }, "Discard"), h("button", { class: "primary", type: "button", onclick: save }, "Check and save")),
    resultBox(),
    h(
      "details",
      { class: "step" },
      h("summary", {}, h("b", {}, "More settings: optional passes, limits, sampling")),
      ["Optional passes", "Resilience and limits", "Sampling", "Review independence"].map((g) =>
        h(
          "section",
          {},
          h("h3", {}, g),
          PROTO.settings
            .filter((s) => s.group === g)
            .map((s) =>
              h(
                "div",
                { class: `row${locked(s) ? " locked" : ""}` },
                h("label", { for: `f-${s.key}` }, s.label),
                control(s, draw),
                h("div", { class: "meta" }, tags(s)),
                s.mark === "shows" ? h("div", { class: "meta" }, shownOnly(s)) : null,
              ),
            ),
        ),
      ),
    ),
  );
  refreshVariant = () => {
    for (const [badge, step] of badges) badge.closest(".step").classList.toggle("done", step.done());
    for (const [summary, step] of summaries) summary.textContent = step.summary();
  };
  return page;
}

// ---- Variant C: one table of every setting, and a changes panel -------------

// Where the value comes from, and nothing else.
function sourceOf(s) {
  if (s.source === "outside") {
    return h("span", {}, h("span", { class: "tag outside" }, "process environment"), h("small", { class: "hint" }, h("code", {}, s.env)));
  }
  if (s.source === "page") return h("span", { class: "tag" }, "this page");
  if (s.source === "file") return h("code", { class: "file" }, s.file);
  return h("span", { class: "tag" }, "default");
}

// What the Value cell says beside the control: a key's state, a restart, a
// value the page only shows, and the vendor's credential mode.
function valueNotes(s) {
  const notes = [];
  if (s.kind === "secret") notes.push(keyState(s));
  if (s.restart) notes.push(h("small", { class: "hint" }, "Takes effect after a restart."));
  if (s.mark === "shows") notes.push(h("small", { class: "hint" }, `Shown only. ${s.reason}`));
  if (s.source === "outside") notes.push(h("small", { class: "hint" }, "Unset the variable to change this here."));
  notes.push(iamNote(s));
  return notes;
}

function variantC() {
  let filter = "";
  const tbody = h("tbody");
  const pendingList = h("ul");
  const pendingHead = h("b");
  const button = h("button", { class: "primary", type: "button", onclick: save }, "Check and save");

  const fillTable = () => {
    tbody.replaceChildren();
    for (const g of groups()) {
      const rows = PROTO.settings.filter(
        (s) => s.group === g && visible(s) && `${s.label} ${s.key}`.toLowerCase().includes(filter),
      );
      if (!rows.length && g === "Credentials" && !filter) {
        tbody.append(h("tr", { class: "groupline" }, h("th", { colspan: 3 }, g)));
        tbody.append(h("tr", {}, h("td", { colspan: 3, class: "hint" }, "Choose a vendor for a tier. Its credential fields show here.")));
      }
      if (!rows.length) continue;
      tbody.append(h("tr", { class: "groupline" }, h("th", { colspan: 3 }, g)));
      for (const s of rows) {
        tbody.append(
          h(
            "tr",
            { "data-key": s.key },
            h("td", {}, h("label", { for: `f-${s.key}` }, s.label)),
            h("td", {}, control(s, draw, true), valueNotes(s)),
            h("td", {}, sourceOf(s)),
          ),
        );
      }
    }
  };

  const page = h(
    "div",
    {},
    h("h1", {}, "Settings"),
    engineBanner(),
    h("label", { class: "field-label", for: "filter" }, "Find a setting"),
    h(
      "div",
      { class: "grid-c" },
      h(
        "div",
        {},
        h("input", {
          id: "filter",
          type: "search",
          placeholder: "Name of a setting",
          oninput: (e) => {
            filter = e.target.value.toLowerCase();
            fillTable();
          },
        }),
        h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "settings" },
            h("thead", {}, h("tr", {}, h("th", {}, "Setting"), h("th", {}, "Value"), h("th", {}, "Source"))),
            tbody,
          ),
        ),
      ),
      h(
        "aside",
        { class: "pending", "aria-label": "Unsaved changes" },
        pendingHead,
        pendingList,
        h("div", {}, h("button", { type: "button", onclick: discard }, "Discard"), " ", button),
        resultBox(),
      ),
    ),
  );
  fillTable();
  refreshVariant = () => {
    const pending = changes();
    pendingHead.textContent = pending.length ? `${pending.length} unsaved change${pending.length === 1 ? "" : "s"}` : "No unsaved changes";
    pendingList.replaceChildren(
      ...pending.map((s) =>
        h("li", {}, `${s.label}: `, s.kind === "secret" ? "new key" : `${JSON.stringify(saved[s.key])} → ${JSON.stringify(draft[s.key])}`, s.restart ? " (restart)" : ""),
      ),
    );
    button.disabled = pending.length === 0;
    for (const tr of tbody.querySelectorAll("tr[data-key]")) {
      tr.classList.toggle("changed", isChanged(byKey[tr.dataset.key]));
    }
  };
  return page;
}

// ---- prototype state and switcher -----------------------------------------

function writeState() {
  const lines = ["# settings.env (the TOML question is open: 'How does the page write a TOML copy?')"];
  const files = [];
  for (const s of PROTO.settings) {
    if (s.kind === "secret" && !visible(s) && saved[s.key] === "set") {
      lines.push(`${s.env}=${PROTO.settings_dir}/${s.vendor}_api_key   # kept? no tier uses ${s.vendor} now`);
      continue;
    }
    if (!visible(s) || s.mark === "shows") continue;
    if (s.source === "outside") {
      lines.push(`# ${s.env}=${s.value}  <- process environment, not written`);
    } else if (s.kind === "secret") {
      if (saved[s.key] === "set" || isChanged(s)) {
        const file = `${PROTO.settings_dir}/${s.vendor}_api_key`;
        lines.push(`${s.env}=${file}`);
        files.push(`${file}  mode 0600${isChanged(s) ? "  (new key on save)" : ""}`);
      }
    } else if (s.source === "page" || isChanged(s)) {
      const value = Array.isArray(draft[s.key]) ? `frameworks.toml copy: carried = ${JSON.stringify(draft[s.key])}` : `${s.env}=${draft[s.key]}`;
      lines.push(`${value}${isChanged(s) ? "   # unsaved" : ""}`);
    }
  }
  document.getElementById("proto-state").textContent = [
    `scenario: ${PROTO.state}   engine: ${engineError ? "does not build" : "built"}`,
    "",
    ...lines,
    "",
    "# secret files",
    ...(files.length ? files : ["(none)"]),
  ].join("\n");
}

function refresh() {
  refreshVariant();
  writeState();
}

const current = () => {
  const v = new URLSearchParams(location.search).get("variant");
  return KEYS.includes(v) ? v : "A";
};

function draw() {
  const key = current();
  const builders = { A: variantA, B: variantB, C: variantC };
  document.getElementById("variant").replaceChildren(builders[key]());
  document.getElementById("variant-label").textContent = `${key} (${VARIANTS[key]})`;
  document.getElementById("scenario").textContent = `Scenario: ${PROTO.state}`;
  refresh();
}

function go(step) {
  const params = new URLSearchParams(location.search);
  params.set("variant", KEYS[(KEYS.indexOf(current()) + step + KEYS.length) % KEYS.length]);
  history.replaceState(null, "", `?${params}`);
  result = null;
  draw();
}

document.getElementById("prev").addEventListener("click", () => go(-1));
document.getElementById("next").addEventListener("click", () => go(1));
document.getElementById("scenario").addEventListener("click", () => {
  const params = new URLSearchParams(location.search);
  params.set("state", PROTO.state === "ready" ? "first-run" : "ready");
  location.search = `?${params}`;
});
// A dropdown closes on a click outside it and on Escape.
document.addEventListener("click", (e) => {
  for (const menu of document.querySelectorAll("details.multi[open]")) {
    if (!menu.contains(e.target)) menu.open = false;
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    for (const menu of document.querySelectorAll("details.multi[open]")) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  }
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  if (e.key === "ArrowLeft") go(-1);
  if (e.key === "ArrowRight") go(1);
});
draw();
