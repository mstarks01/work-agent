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
const visible = (s) => !s.when || draft[s.when[0]] === s.when[1];
const locked = (s) => s.mark === "shows" || s.source === "outside";
const isChanged = (s) =>
  s.kind === "secret" ? draft[s.key] !== saved[s.key] && draft[s.key] !== "" : !same(draft[s.key], saved[s.key]);
const changes = () => PROTO.settings.filter((s) => visible(s) && !locked(s) && isChanged(s));
const groups = () => [...new Set(PROTO.settings.map((s) => s.group))];
const fill = (template, values) => template.replace(/\{(\w+)\}/g, (_, k) => values[k]);
const keySet = (vendor) => {
  const s = PROTO.settings.find((x) => x.kind === "secret" && x.vendor === vendor);
  return !s || saved[s.key] === "set" || draft[s.key] !== saved[s.key] && draft[s.key] !== "";
};

// The stand-in for "build a test Deployment from the new mapping".
function check() {
  for (const tier of TIERS) {
    if (draft[`tiers.${tier}.vendor`] && !draft[`tiers.${tier}.model`].trim()) {
      return fill(PROTO.messages["no-model"], { TIER: tier.toUpperCase() });
    }
  }
  const missing = ["base", "strong"].filter((t) => !draft[`tiers.${t}.vendor`]);
  if (missing.length) {
    return fill(PROTO.messages["no-vendor"], { tiers: JSON.stringify(missing).replaceAll('"', "'") });
  }
  for (const tier of TIERS) {
    const vendor = draft[`tiers.${tier}.vendor`];
    if (vendor && !keySet(vendor)) {
      return fill(PROTO.messages["no-key"], { vendor, VENDOR: vendor.toUpperCase() });
    }
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

function control(s, onRedraw) {
  const id = `f-${s.key}`;
  if (locked(s)) {
    return h("span", { id }, h("code", {}, String(s.value)));
  }
  const set = (value, redraw) => {
    draft[s.key] = value;
    if (redraw) onRedraw();
    else refresh();
  };
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
        placeholder: saved[s.key] === "set" ? "Set. Type a new key to replace it." : "Not set. Paste the key.",
        oninput: (e) => set(e.target.value || saved[s.key]),
      });
    case "number":
      return h("input", { id, type: "number", value: draft[s.key], oninput: (e) => set(Number(e.target.value)) });
    default:
      return h("input", { id, type: "text", value: draft[s.key], oninput: (e) => set(e.target.value) });
  }
}

function tags(s) {
  const out = [];
  if (s.kind === "secret") {
    out.push(saved[s.key] === "set" ? h("span", { class: "tag" }, "set") : h("span", { class: "tag not" }, "not set"));
  }
  if (s.source === "outside") out.push(h("span", { class: "tag outside" }, `set outside this page: ${s.env}`));
  if (s.mark === "shows") out.push(h("span", { class: "tag" }, "shown only"));
  if (s.restart) out.push(h("span", { class: "tag restart" }, "restart needed"));
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
  const chosenVendors = () => [...new Set(TIERS.map((t) => draft[`tiers.${t}.vendor`]).filter(Boolean))];
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
      title: "Add a key for each vendor",
      done: () => chosenVendors().length > 0 && chosenVendors().every(keySet),
      summary: () => chosenVendors().map((v) => `${v}: ${keySet(v) ? "set" : "not set"}`).join(" · ") || "Choose a model first.",
      keys: () => PROTO.settings.filter((s) => s.kind === "secret" && chosenVendors().includes(s.vendor)),
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
        (s) => s.group === g && visible(s) && `${s.label} ${s.env} ${s.key}`.toLowerCase().includes(filter),
      );
      if (!rows.length) continue;
      tbody.append(h("tr", { class: "groupline" }, h("th", { colspan: 4 }, g)));
      for (const s of rows) {
        const source =
          s.mark === "shows" ? "shown only" : s.source === "outside" ? "outside this page" : s.source === "page" ? "this page" : "default";
        tbody.append(
          h(
            "tr",
            { "data-key": s.key },
            h("td", {}, h("label", { for: `f-${s.key}` }, s.label), s.mark === "shows" ? shownOnly(s) : null),
            h("td", {}, control(s, draw)),
            h("td", {}, h("span", { class: `tag${s.source === "outside" ? " outside" : ""}` }, source), " ", s.restart ? h("span", { class: "tag restart" }, "restart") : null, s.kind === "secret" ? tags(s)[0] : null),
            h("td", {}, h("code", {}, s.env || "(TOML only)")),
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
    h(
      "div",
      { class: "grid-c" },
      h(
        "div",
        {},
        h("label", { class: "field-label", for: "filter" }, "Find a setting"),
        h("input", {
          id: "filter",
          type: "search",
          placeholder: "Name or variable",
          oninput: (e) => {
            filter = e.target.value.toLowerCase();
            fillTable();
          },
        }),
        h(
          "table",
          { class: "settings" },
          h("thead", {}, h("tr", {}, h("th", {}, "Setting"), h("th", {}, "Value"), h("th", {}, "Source"), h("th", {}, "Variable"))),
          tbody,
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
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  if (e.key === "ArrowLeft") go(-1);
  if (e.key === "ArrowRight") go(1);
});
draw();
