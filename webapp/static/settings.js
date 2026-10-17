// The Settings page: one table of each Deployment Setting, with its value and
// the source of the value, and a panel of the unsaved changes. `SETTINGS`
// comes from webapp/settings_view.py, and a save posts to /settings
// (webapp/settings_save.py). Each value lands as text, never as markup. A key
// field only writes: the page never gets a key, and puts a typed key in no
// attribute.

let SETTINGS = JSON.parse(document.getElementById("settings").textContent);

// Each unsaved change: a Deployment Setting key to its new value, or null for
// a reset. `removals` holds the unknown lines to remove.
const changes = new Map();
const removals = new Set();

function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
  for (const child of children.flat()) {
    if (child == null) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

const hint = (...text) => h("small", { class: "hint" }, ...text);
const rowOf = (key) => SETTINGS.rows.find((row) => row.key === key);
const editable = (row) => row.mark === "changes" && row.source !== "process" && !row.secret;

// The value that a row shows: the unsaved change where one exists.
function pending(row) {
  if (!changes.has(row.key)) return row.value;
  const value = changes.get(row.key);
  return value === null ? row.shipped : value;
}

const TIER_VENDOR = /^tiers\.[^.]+\.vendor$/;

// Each vendor that a tier names, with the unsaved changes applied.
const chosenVendors = () =>
  new Set(SETTINGS.rows.filter((row) => TIER_VENDOR.test(row.key)).map(pending).filter(Boolean));

// The credential mode of a vendor: its only one, else the chosen one.
function chosenMode(vendor) {
  const modes = SETTINGS.modes[vendor];
  return modes.length === 1 ? modes[0] : pending(rowOf(`credentials.${vendor}.mode`));
}

// A vendor row shows where a tier names the vendor and its mode reads the row.
const visible = (row) =>
  !row.vendor || (chosenVendors().has(row.vendor) && (!row.modes || row.modes.includes(chosenMode(row.vendor))));

// Each saved key of settings.env that a save deletes, because no tier names
// its vendor after the changes.
const deletedKeys = () =>
  SETTINGS.rows.filter((row) => row.secret && row.key_saved && row.source === "page" && row.vendor && !chosenVendors().has(row.vendor));

function change(row, value) {
  if (value === (row.value ?? "")) changes.delete(row.key);
  else changes.set(row.key, value);
  panel();
}

function textControl(row) {
  const attrs = { type: "text", "aria-label": row.label, value: pending(row) ?? "", placeholder: "Not set" };
  if (row.suggests) attrs.list = `models-${row.suggests}`;
  const input = h("input", attrs);
  input.addEventListener("input", () => change(row, input.value));
  return input;
}

function choiceControl(row) {
  const now = pending(row);
  const options = row.options.includes(now) || now == null ? row.options : [now, ...row.options];
  const select = h("select", { "aria-label": row.label });
  if (now == null) select.append(h("option", { value: "", disabled: "", selected: "" }, "Not set"));
  for (const option of options) {
    const attrs = { value: option };
    if (option === now) attrs.selected = "";
    select.append(h("option", attrs, option));
  }
  select.addEventListener("change", () => {
    change(row, select.value);
    fill(currentFilter());
  });
  return select;
}

// A list of names separated by commas, in the order of the options.
function multipleControl(row) {
  const chosen = new Set((pending(row) ?? "").split(",").filter(Boolean));
  const boxes = row.options.map((option) => {
    const attrs = { type: "checkbox", value: option };
    if (chosen.has(option)) attrs.checked = "";
    return h("input", attrs);
  });
  const update = () => change(row, boxes.filter((box) => box.checked).map((box) => box.value).join(","));
  for (const box of boxes) box.addEventListener("change", update);
  return h(
    "fieldset",
    { class: "choices", "aria-label": row.label },
    boxes.map((box) => h("label", {}, box, ` ${box.value}`)),
  );
}

function keyControl(row) {
  const input = h("input", {
    type: "password",
    "aria-label": row.label,
    autocomplete: "off",
    placeholder: row.key_saved ? "Type a key to replace the saved key" : "Type the key",
  });
  input.value = changes.get(row.key) ?? "";
  input.addEventListener("input", () => change(row, input.value));
  return input;
}

function control(row) {
  if (row.multiple) return multipleControl(row);
  if (row.options) return choiceControl(row);
  return textControl(row);
}

// The value, or a key's state: the page never shows a key.
function shownValue(row) {
  if (row.secret) {
    const state = row.key_saved ? hint("A key is saved. The page never shows it.") : hint("No key is saved.");
    return row.source === "process" ? state : [keyControl(row), state];
  }
  if (editable(row)) return control(row);
  if (row.value == null) return h("span", { class: "not-set" }, "Not set");
  return h("code", {}, row.value);
}

// A model that the pinned litellm map does not list still runs.
function modelNote(row) {
  const value = pending(row);
  if (!row.suggests || value == null) return null;
  const known = SETTINGS.models[row.suggests] || [];
  return known.includes(value) ? null : hint("Not in the model list. A check still runs it.");
}

// A reset removes the line, so it is offered only where a shipped value exists.
function resetButton(row) {
  if (row.source !== "page" || row.shipped == null || changes.get(row.key) === null) return null;
  const button = h("button", { type: "button", class: "reset" }, "Reset to shipped");
  button.addEventListener("click", () => {
    changes.set(row.key, null);
    panel();
    fill(currentFilter());
  });
  return button;
}

function notes(row) {
  return [
    row.source === "page" ? hint("Shipped: ", row.shipped == null ? "none shipped" : h("code", {}, row.shipped)) : null,
    resetButton(row),
    modelNote(row),
    row.restart ? hint("Takes effect after a restart.") : null,
    row.mark === "shows" ? hint(`Shown only. ${row.reason}`) : null,
    row.source === "process" ? hint("Unset the variable to change this here.") : null,
  ];
}

function source(row) {
  if (row.source === "process") {
    return [h("span", { class: "tag outside" }, "process environment"), hint(h("code", {}, row.variable))];
  }
  if (row.source === "page") return h("span", { class: "tag" }, "this page");
  if (row.source === "file") return h("code", {}, row.file);
  return h("span", { class: "tag" }, "default");
}

function iamRows() {
  const chosen = SETTINGS.iam.filter((setup) => chosenVendors().has(setup.vendor) && chosenMode(setup.vendor) === "iam");
  return chosen.map((setup) =>
    h(
      "tr",
      {},
      h("td", {}, `${setup.vendor} setup`),
      h("td", { colspan: "2" }, hint(`${setup.note} `, h("code", {}, setup.command))),
    ),
  );
}

function modelLists() {
  const lists = document.getElementById("model-lists");
  lists.replaceChildren(
    ...Object.entries(SETTINGS.models).map(([vendor, models]) =>
      h("datalist", { id: `models-${vendor}` }, models.map((model) => h("option", { value: model }))),
    ),
  );
}

const currentFilter = () => document.getElementById("settings-search").value.toLowerCase();

function fill(filter) {
  const body = document.getElementById("settings-rows");
  body.replaceChildren();
  const groups = [...new Set(SETTINGS.rows.map((row) => row.group))];
  for (const group of groups) {
    const rows = SETTINGS.rows.filter(
      (row) => row.group === group && visible(row) && `${row.label} ${row.key}`.toLowerCase().includes(filter),
    );
    const extra = group === "Credentials" && !filter ? iamRows() : [];
    if (!rows.length && !extra.length) continue;
    body.append(h("tr", { class: "groupline" }, h("th", { colspan: "3", scope: "colgroup" }, group)));
    for (const row of rows) {
      body.append(
        h(
          "tr",
          { "data-key": row.key },
          h("th", { scope: "row" }, row.label),
          h("td", {}, shownValue(row), notes(row)),
          h("td", {}, source(row)),
        ),
      );
    }
    body.append(...extra);
  }
}

const shown = (value) => (value == null ? h("span", { class: "not-set" }, "Not set") : h("code", {}, value));

// The panel of unsaved changes: each one with its old value and its new value.
function panel() {
  const list = document.getElementById("changes-list");
  list.replaceChildren();
  for (const [key, value] of changes) {
    const row = rowOf(key);
    if (!visible(row)) continue;
    let before = shown(row.value);
    let after = value === null ? [shown(row.shipped), " (shipped)"] : [shown(value)];
    if (row.secret) {
      before = row.key_saved ? "a saved key" : "no key";
      after = value === null ? "no key" : "a new key";
    }
    list.append(h("li", { "data-key": key }, h("b", {}, row.label), ": ", before, " → ", after));
  }
  if (changes.size) {
    for (const row of deletedKeys()) {
      list.append(
        h("li", { "data-key": row.key, class: "warning" }, `No tier uses ${row.vendor} after this save, so the save deletes the saved ${row.label}.`),
      );
    }
  }
  for (const key of removals) list.append(h("li", { "data-key": key }, "Remove the line ", h("code", {}, key)));
  const count = changes.size + removals.size;
  document.getElementById("changes-empty").hidden = count > 0;
  document.getElementById("save-changes").disabled = count === 0;
  document.getElementById("discard-changes").disabled = count === 0;
}

function showErrors(errors) {
  const box = document.getElementById("save-errors");
  box.replaceChildren(
    h("b", {}, "The settings were not saved."),
    h("ul", {}, errors.map((error) => h("li", {}, error.message))),
  );
  box.hidden = false;
}

function showSaved(restart) {
  const box = document.getElementById("save-status");
  const labels = restart.map((key) => rowOf(key)?.label ?? key);
  box.replaceChildren(
    "Saved. The next analysis uses the new settings, and an analysis that runs now finishes on the old ones.",
    labels.length ? h("div", {}, "Restart the app to apply the job storage change: ", labels.join(", "), ".") : null,
  );
  box.hidden = false;
}

async function save() {
  const button = document.getElementById("save-changes");
  button.disabled = true;
  document.getElementById("save-errors").hidden = true;
  document.getElementById("save-status").hidden = true;
  let response;
  try {
    response = await fetch("/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        revision: SETTINGS.revision,
        changes: Object.fromEntries([...changes].filter(([key]) => visible(rowOf(key)))),
        remove: [...removals],
      }),
    });
  } catch {
    showErrors([{ message: "The app did not answer. Check that it runs, then try again." }]);
    panel();
    return;
  }
  const payload = await response.json().catch(() => ({}));
  if (response.ok) {
    SETTINGS = payload.settings;
    changes.clear();
    removals.clear();
    render();
    showSaved(payload.restart);
    return;
  }
  showErrors(payload.errors ?? [{ message: payload.message ?? `The save failed with status ${response.status}.` }]);
  panel();
}

function discard() {
  changes.clear();
  removals.clear();
  document.getElementById("save-errors").hidden = true;
  render();
}

function banners() {
  const banner = document.getElementById("engine-error");
  banner.replaceChildren();
  banner.hidden = !SETTINGS.error;
  if (SETTINGS.error) {
    banner.append(
      h("b", {}, "The engine could not start. "),
      "No analysis can run until this is fixed.",
      h("div", {}, h("code", {}, SETTINGS.error)),
    );
  }
  const unknown = document.getElementById("unknown-lines");
  unknown.replaceChildren();
  unknown.hidden = !SETTINGS.unknown.length;
  if (SETTINGS.unknown.length) {
    unknown.append(
      h("b", {}, "settings.env has lines that this version does not read."),
      h(
        "ul",
        {},
        SETTINGS.unknown.map((line) => {
          const button = h("button", { type: "button" }, "Remove the line");
          button.disabled = removals.has(line.key);
          button.addEventListener("click", () => {
            removals.add(line.key);
            button.disabled = true;
            panel();
          });
          return h("li", {}, h("code", {}, line.key), ` on line ${line.line} `, button);
        }),
      ),
    );
  }
}

function render() {
  banners();
  modelLists();
  document.getElementById("settings-file").textContent = SETTINGS.settings_file;
  fill(currentFilter());
  panel();
}

document.getElementById("settings-search").addEventListener("input", (event) => fill(event.target.value.toLowerCase()));
document.getElementById("save-changes").addEventListener("click", save);
document.getElementById("discard-changes").addEventListener("click", discard);
render();
