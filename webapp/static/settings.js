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
const invalidNumbers = new Map();
let saving = false;

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

// Each vendor that a tier names, with the unsaved changes applied. The server
// lists the keys of the rows that choose a tier's vendor.
const chosenVendors = () =>
  new Set(SETTINGS.vendor_rows.map(rowOf).filter(Boolean).map(pending).filter(Boolean));

// The credential mode of a vendor: its only one, else the chosen one.
function chosenMode(vendor) {
  const modes = SETTINGS.modes[vendor];
  return modes.length === 1 ? modes[0] : pending(rowOf(`credentials.${vendor}.mode`));
}

// A vendor row shows where a tier names the vendor and its mode reads the row.
const visible = (row) =>
  !row.vendor || (chosenVendors().has(row.vendor) && (!row.modes || row.modes.includes(chosenMode(row.vendor))));

// Each saved key of settings.env that a save deletes, because no tier names
// its vendor after the changes. The server reads the same rule in
// `unused_keys` (webapp/settings_save.py), and a test in
// tests/test_browser.py checks the two against each other.
const deletedKeys = () =>
  SETTINGS.rows.filter((row) => row.secret && row.key_saved && row.source === "page" && row.vendor && !chosenVendors().has(row.vendor));

// The vendor whose models a tier's model row suggests: the vendor that the
// unsaved changes choose for the tier. The server puts the vendor row's key
// on the model row.
function suggests(row) {
  const vendor = row.vendor_key && rowOf(row.vendor_key);
  return vendor ? pending(vendor) : null;
}

function change(row, value) {
  // A removal where this page saved no line removes nothing.
  const unchanged = value === null ? row.source !== "page" : value === (row.value ?? "");
  if (unchanged) changes.delete(row.key);
  else changes.set(row.key, value);
  document.getElementById("save-errors").hidden = true;
  panel();
}

function textControl(row) {
  const placeholder = row.example ? `e.g. ${row.example}` : "Not set";
  const attrs = { type: "text", "aria-label": row.label, value: pending(row) ?? "", placeholder };
  if (suggests(row)) attrs.list = `models-${suggests(row)}`;
  const input = h("input", attrs);
  input.addEventListener("input", () => change(row, input.value));
  // A model decides what its tier's thinking row offers, so a finished edit
  // draws the table again.
  if (suggests(row)) input.addEventListener("change", () => fill(currentFilter()));
  return input;
}

function numberError(row, value) {
  if (row.numeric === "integer") {
    return /^-?\d+$/.test(value) && Number.isSafeInteger(Number(value))
      ? null : "Enter a whole number.";
  }
  return value !== "" && Number.isFinite(Number(value)) ? null : "Enter a number.";
}

function markNumber(row, input, note) {
  const error = numberError(row, input.value);
  if (error) invalidNumbers.set(row.key, error);
  else invalidNumbers.delete(row.key);
  input.setAttribute("aria-invalid", String(Boolean(error)));
  note.textContent = error || "";
  note.hidden = !error;
  return error;
}

function numberControl(row) {
  const attrs = {
    type: "number", "aria-label": row.label, value: pending(row) ?? "",
    inputmode: row.numeric === "integer" ? "numeric" : "decimal",
    step: row.numeric === "integer" ? "1" : "any",
  };
  if (row.example) attrs.placeholder = `e.g. ${row.example}`;
  const input = h("input", attrs);
  const note = h("small", { class: "field-error", id: `error-${row.key.replaceAll(".", "-")}` });
  note.hidden = true;
  input.setAttribute("aria-describedby", note.id);
  input.addEventListener("input", () => {
    change(row, input.value);
    if (invalidNumbers.has(row.key)) markNumber(row, input, note);
  });
  input.addEventListener("blur", () => markNumber(row, input, note));
  if (invalidNumbers.has(row.key)) markNumber(row, input, note);
  return h("div", {}, input, note);
}

function choiceControl(row) {
  const now = pending(row);
  const options = row.options.includes(now) || now == null ? row.options : [now, ...row.options];
  const select = h("select", { "aria-label": row.label });
  if (row.unset) {
    const attrs = { value: "" };
    if (now == null) attrs.selected = "";
    select.append(h("option", attrs, row.unset));
  } else if (now == null) {
    select.append(h("option", { value: "", disabled: "", selected: "" }, "Not set"));
  }
  for (const option of options) {
    const attrs = { value: option };
    if (option === now) attrs.selected = "";
    select.append(h("option", attrs, option));
  }
  select.addEventListener("change", () => {
    change(row, row.unset && select.value === "" ? null : select.value);
    fill(currentFilter());
  });
  return select;
}

// The vendor and the model of the tier whose thinking row this is, with the
// unsaved changes applied, or null for another row. The server states the
// row's tier and the keys of that tier's vendor row and model row.
function thinkingModel(row) {
  if (!row.tier) return null;
  const vendor = rowOf(row.vendor_key);
  const model = rowOf(row.model_key);
  return vendor && model ? { vendor: pending(vendor), model: pending(model) } : null;
}

// Whether a suggested model takes no thinking level; the server's
// `model_gate.takes_thinking` decides each listed model.
function takesNoLevel(row) {
  const chosen = thinkingModel(row);
  if (!chosen || !chosen.vendor || !Object.hasOwn(SETTINGS.no_thinking, chosen.vendor)) return false;
  return SETTINGS.no_thinking[chosen.vendor].includes(chosen.model);
}

function noLevelControl(row) {
  const said = hint("This model takes no thinking level.");
  if (pending(row) == null) return said;
  const remove = h("button", { type: "button", class: "reset" }, "Remove the level");
  remove.addEventListener("click", () => {
    change(row, null);
    fill(currentFilter());
  });
  return [said, remove];
}

// A model that the list does not hold: the save checks whether it takes a level.
function thinkingNote(row) {
  const chosen = thinkingModel(row);
  if (!chosen || !chosen.vendor || !chosen.model) return null;
  const known = Object.hasOwn(SETTINGS.models, chosen.vendor) ? SETTINGS.models[chosen.vendor] : [];
  return known.includes(chosen.model) ? null : hint("The save checks whether this model takes a thinking level.");
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
  if (takesNoLevel(row)) return noLevelControl(row);
  if (row.multiple) return multipleControl(row);
  if (row.options) return choiceControl(row);
  if (row.numeric) return numberControl(row);
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
  const vendor = suggests(row);
  if (!vendor || value == null) return null;
  const known = Object.hasOwn(SETTINGS.models, vendor) ? SETTINGS.models[vendor] : [];
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
    thinkingNote(row),
    row.restart ? hint("Takes effect after a restart.") : null,
    row.mark === "shows" ? hint(`Shown only. ${row.reason}`) : null,
    row.note ? hint(row.note) : null,
    row.source === "process" ? hint("Unset the variable to change this here.") : null,
  ];
}

// One entry for each source that settings_view.Source names.
const sources = {
  "process": (row) => [h("span", { class: "tag outside" }, "process environment"), hint(h("code", {}, row.variable))],
  "page": () => h("span", { class: "tag" }, "this page"),
  "file": (row) => h("code", {}, row.file),
  "refused": (row) => [h("span", { class: "tag outside" }, "refused"), hint(h("code", {}, row.refusal))],
  "default": () => h("span", { class: "tag" }, "default"),
};

function source(row) {
  return sources[row.source](row);
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
    const before = row.secret ? (row.key_saved ? "a saved key" : "no key") : shown(row.value);
    const after = row.secret ? (value === null ? "no key" : "a new key")
      : value === null ? [shown(row.shipped), " (shipped)"] : [shown(value)];
    const item = h("li", { "data-key": key }, h("b", {}, row.label), ": ", before, " → ", after);
    if (!visible(row)) {
      item.append(h("small", { class: "hint pending-note" },
        "This setting is inactive. Its edit stays pending and is not included in this save."));
      const discardEdit = h("button", { type: "button", class: "quiet discard-edit",
        "aria-label": `Discard edit for ${row.label}` }, "Discard this edit");
      discardEdit.disabled = saving;
      discardEdit.addEventListener("click", () => {
        changes.delete(key);
        fill(currentFilter());
        panel();
      });
      item.append(discardEdit);
    }
    list.append(item);
  }
  const saveable = [...changes.keys()].filter((key) => visible(rowOf(key))).length + removals.size;
  if (saveable) {
    for (const row of deletedKeys()) {
      list.append(
        h("li", { "data-key": row.key, class: "warning" }, `No tier uses ${row.vendor} after this save, so the save deletes the saved ${row.label}.`),
      );
    }
  }
  for (const key of removals) list.append(h("li", { "data-key": key }, "Remove the line ", h("code", {}, key)));
  const count = changes.size + removals.size;
  document.getElementById("changes-column").hidden = count === 0;
  document.getElementById("save-changes").disabled = saving || saveable === 0;
  document.getElementById("discard-changes").disabled = saving || count === 0;
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
    changes.size || removals.size
      ? "Saved the active settings. Edits to inactive settings are still pending. The next analysis uses the new settings, and an analysis that runs now finishes on the old ones."
      : "Saved. The next analysis uses the new settings, and an analysis that runs now finishes on the old ones.",
    labels.length ? h("div", {}, "Restart the app to apply the job storage change: ", labels.join(", "), ".") : null,
  );
  box.hidden = false;
}

async function save() {
  if (saving) return;
  const sentChanges = new Map([...changes].filter(([key]) => visible(rowOf(key))));
  const sentRemovals = new Set(removals);
  if (!sentChanges.size && !sentRemovals.size) return;
  const numericErrors = [];
  for (const [key, value] of sentChanges) {
    const row = rowOf(key);
    if (!row.numeric || value === null) continue;
    const input = [...document.querySelectorAll("#settings-rows tr[data-key] input[type=number]")]
      .find((item) => item.closest("tr").dataset.key === key);
    const note = input && document.getElementById(`error-${key.replaceAll(".", "-")}`);
    const error = input && note ? markNumber(row, input, note) : numberError(row, value);
    if (error) {
      invalidNumbers.set(key, error);
      numericErrors.push({ message: `${row.label}: ${error}` });
    }
  }
  if (numericErrors.length) {
    document.getElementById("settings-search").value = "";
    fill("");
    showErrors(numericErrors);
    return;
  }
  saving = true;
  const layout = document.querySelector(".settings-layout");
  const unknown = document.getElementById("unknown-lines");
  layout.inert = true;
  unknown.inert = true;
  layout.setAttribute("aria-busy", "true");
  const button = document.getElementById("save-changes");
  button.textContent = "Saving…";
  panel();
  document.getElementById("save-errors").hidden = true;
  document.getElementById("save-status").hidden = true;
  try {
    let response;
    try {
      response = await fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: SETTINGS.revision,
          changes: Object.fromEntries(sentChanges),
          remove: [...sentRemovals],
        }),
      });
    } catch {
      showErrors([{ message: "The app did not answer. Check that it runs, then try again." }]);
      return;
    }
    const payload = await response.json().catch(() => ({}));
    if (response.ok) {
      SETTINGS = payload.settings;
      for (const key of sentChanges.keys()) changes.delete(key);
      for (const key of sentRemovals) removals.delete(key);
      invalidNumbers.clear();
      render();
      showSaved(payload.restart);
      return;
    }
    showErrors(payload.errors ?? [{ message: payload.message ?? `The save failed with status ${response.status}.` }]);
  } finally {
    saving = false;
    layout.inert = false;
    unknown.inert = false;
    layout.removeAttribute("aria-busy");
    button.textContent = "Check and save";
    panel();
  }
}

function discard() {
  if (saving) return;
  changes.clear();
  removals.clear();
  invalidNumbers.clear();
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
