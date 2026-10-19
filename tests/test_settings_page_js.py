"""Offline checks for pending Settings edits while a save is in flight."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SETTINGS_SCRIPT = Path(__file__).resolve().parents[1] / "webapp/static/settings.js"


def test_inactive_edit_stays_visible_and_pending_after_save():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to run the Settings script")
    harness = r"""
const fs = require("fs");
const assert = require("assert");
class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.textContent = "";
    this.value = ""; this.hidden = false; this.disabled = false; this.listeners = {};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const rows = [
  { key: "tiers.base.vendor", group: "Models", label: "Base vendor", value: "anthropic",
    shipped: "anthropic", source: "page", mark: "changes", options: ["anthropic", "openai"] },
  { key: "credentials.anthropic.api_key", group: "Credentials", label: "Anthropic API key",
    value: null, shipped: null, source: "page", mark: "changes", secret: true,
    key_saved: false, vendor: "anthropic", modes: ["api_key"] },
];
const settings = { revision: "one", rows, modes: { anthropic: ["api_key"], openai: ["api_key"] },
  models: {}, iam: [], unknown: [], settings_file: "settings.env", error: null };
const ids = Object.fromEntries([
  "settings", "settings-rows", "model-lists", "settings-search", "changes-list",
  "changes-panel", "save-changes", "discard-changes", "save-errors", "save-status",
  "engine-error", "unknown-lines", "settings-file",
].map((id) => [id, new Node(id)]));
ids.settings.textContent = JSON.stringify(settings);
const layout = new Node("layout");
globalThis.Node = Node;
globalThis.document = {
  getElementById: (id) => ids[id],
  querySelector: () => layout,
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => { const item = new Node("text"); item.textContent = text; return item; },
};
let resolveSave;
let sent;
globalThis.fetch = (_url, request) => {
  sent = JSON.parse(request.body);
  return new Promise((resolve) => { resolveSave = resolve; });
};
const scriptText = fs.readFileSync(process.argv[1], "utf8");
eval(scriptText + `
(async () => {
  assert.strictEqual(document.getElementById("changes-panel").hidden, true);
  change(rowOf("credentials.anthropic.api_key"), "private-key");
  change(rowOf("tiers.base.vendor"), "openai");
  assert.strictEqual(document.getElementById("changes-panel").hidden, false);
  const item = document.getElementById("changes-list").children.find(
    (entry) => entry["data-key"] === "credentials.anthropic.api_key");
  const text = (entry) => entry.textContent + entry.children.map(text).join("");
  assert(item);
  assert(text(item).includes("stays pending"));
  assert(!text(item).includes("private-key"));
  const pendingSave = save();
  assert.strictEqual(layout.inert, true);
  assert.strictEqual(document.getElementById("unknown-lines").inert, true);
  assert.strictEqual(document.getElementById("save-changes").disabled, true);
  assert.deepStrictEqual(sent.changes, { "tiers.base.vendor": "openai" });
  resolveSave({ ok: true, json: async () => ({ settings: {
    ...${JSON.stringify(settings)}, revision: "two", rows: ${JSON.stringify(rows)}.map((row) =>
      row.key === "tiers.base.vendor" ? { ...row, value: "openai" } : row),
  }, restart: [] }) });
  await pendingSave;
  assert.strictEqual(layout.inert, false);
  assert.strictEqual(changes.has("tiers.base.vendor"), false);
  assert.strictEqual(changes.get("credentials.anthropic.api_key"), "private-key");
  assert.strictEqual(document.getElementById("changes-panel").hidden, false);
  assert.strictEqual(document.getElementById("save-changes").disabled, true);
  assert.strictEqual(document.getElementById("discard-changes").disabled, false);
  discard();
  assert.strictEqual(document.getElementById("changes-panel").hidden, true);
})().catch((error) => { console.error(error); process.exitCode = 1; });
`);
"""
    result = subprocess.run(
        [node, "-e", harness, str(SETTINGS_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
