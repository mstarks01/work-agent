// The Settings page: one table of each Deployment Setting, with its value and
// the source of the value. `SETTINGS` comes from webapp/settings_view.py. Each
// value lands as text, never as markup.

const SETTINGS = JSON.parse(document.getElementById("settings").textContent);

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

// The value, or a key's state: the page never shows a key.
function shownValue(row) {
  if (row.secret) {
    return row.key_saved ? hint("A key is saved. The page never shows it.") : hint("No key is saved.");
  }
  if (row.value == null) return h("span", { class: "not-set" }, "Not set");
  return h("code", {}, row.value);
}

// A model that the pinned litellm map does not list still runs.
function modelNote(row) {
  if (!row.suggests || row.value == null) return null;
  const known = SETTINGS.models[row.suggests] || [];
  return known.includes(row.value) ? null : hint("Not in the model list. A check still runs it.");
}

function notes(row) {
  return [
    row.source === "page" ? hint("Shipped: ", row.shipped == null ? "none shipped" : h("code", {}, row.shipped)) : null,
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
  return SETTINGS.iam.map((setup) =>
    h(
      "tr",
      {},
      h("td", {}, `${setup.vendor} setup`),
      h("td", { colspan: "2" }, hint(`${setup.note} `, h("code", {}, setup.command))),
    ),
  );
}

function fill(filter) {
  const body = document.getElementById("settings-rows");
  body.replaceChildren();
  const groups = [...new Set(SETTINGS.rows.map((row) => row.group))];
  for (const group of groups) {
    const rows = SETTINGS.rows.filter(
      (row) => row.group === group && `${row.label} ${row.key}`.toLowerCase().includes(filter),
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

if (SETTINGS.error) {
  const banner = document.getElementById("engine-error");
  banner.append(h("b", {}, "The engine could not start. "), "No analysis can run until this is fixed.", h("div", {}, h("code", {}, SETTINGS.error)));
  banner.hidden = false;
}
if (SETTINGS.unknown.length) {
  const unknown = document.getElementById("unknown-lines");
  unknown.append(
    h("b", {}, "settings.env has lines that this version does not read."),
    h("ul", {}, SETTINGS.unknown.map((line) => h("li", {}, h("code", {}, line.key), ` on line ${line.line}`))),
  );
  unknown.hidden = false;
}
document.getElementById("settings-file").textContent = SETTINGS.settings_file;
document.getElementById("settings-search").addEventListener("input", (event) => fill(event.target.value.toLowerCase()));
fill("");
