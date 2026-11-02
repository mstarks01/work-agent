// PROTOTYPE for ticket #2118: three layouts of the Controls surfaces. Never merges.
//
// Three variants via ?variant=A|B|C, three views via ?view=controls|offer|report,
// and five scenarios via ?scenario=. State lives in this page only.
//
// Untrusted text (a Control name, an alias, a fact value, an evidence file name
// or excerpt) reaches the page only through text nodes and input values, never
// through innerHTML. One seed Control has markup in its name to show it.

(() => {
  const { units, scope_types: scopeTypes, evidence_classes: evidenceClasses, model, limits } = PROTO;
  const VARIANTS = {
    A: "One form per Control; a checklist offer; one report table",
    B: "List and detail with tabs; an offer dialog by outcome; report cards",
    C: "Controls grouped by what they protect; the offer on the model; report rows by outcome",
  };
  const VIEWS = { controls: "Controls page", offer: "Submission offer", report: "Report section" };
  const SCENARIOS = {
    normal: "Four Controls, each matches",
    "missing-element": "A Control's element is not in the new model",
    "empty-zone": "A zone scope with no element in it",
    conflict: "Two Controls write one fact with two values",
    "at-limit": "50 Controls, at the count limit",
  };
  const OUTCOME_TEXT = {
    applied: "Applied: the critic ruled out a finding on this unit with this Control's facts.",
    refused: "Refused: the critic judged that this Control does not bear on this unit.",
    "no-finding": "No finding: no lane filed a claim on this unit, so there was nothing to rule on.",
  };
  const FACT_STATE_TEXT = {
    written: "The Control wrote this fact. The question was not asked.",
    changed: "You answered this question yourself, over the Control's value.",
    stated: "The description already stated this fact, so the Control did not write it.",
  };
  const TYPE_LABEL = Object.fromEntries(scopeTypes.map((row) => [row.type, row.label]));
  const CLASS_LABEL = Object.fromEntries(evidenceClasses.map((row) => [row.id, row.label]));

  // ---- small helpers ---------------------------------------------------------

  // Builds an element. A string child becomes a text node, so no value here is
  // ever parsed as markup.
  const h = (tag, attrs = {}, ...children) => {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "on") {
        for (const [event, handler] of Object.entries(value)) element.addEventListener(event, handler);
      } else if (key === "class") {
        element.className = value;
      } else if (key === "value") {
        element.value = value;
      } else if (value === true) {
        element.setAttribute(key, "");
      } else if (value !== false && value != null) {
        element.setAttribute(key, value);
      }
    }
    element.append(...children.flat(Infinity).filter((child) => child != null && child !== false));
    return element;
  };
  const size = (n) => (n < 1024 ? `${n} bytes` : `${(n / 1024).toFixed(1)} KB`);
  const day = (iso) => new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  const newId = () => "control-" + Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (b) => b.toString(16).padStart(2, "0")).join("");
  const tag = (text, cls = "") => h("span", { class: `tag ${cls}` }, text);
  const unitOf = (row) => units[row.framework].units.find((u) => u.id === row.unit);
  const unitText = (row) => {
    const unit = unitOf(row);
    return unit ? `${row.framework} ${unit.id} · ${unit.name}` : `${row.framework} ${row.unit}`;
  };
  const scopeText = (scope) => (scope.type === "application"
    ? "The whole application"
    : `${TYPE_LABEL[scope.type]}: ${scope.name}`);

  // The slug rule of make_element_id: an apostrophe is elided, every other
  // non-slug character separates.
  const slug = (name) => name.toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const elementId = (type, name) => `${type}:${slug(name)}`;

  // ---- state -----------------------------------------------------------------

  const params = new URLSearchParams(location.search);
  const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);
  const S = {
    variant: pick(params.get("variant"), Object.keys(VARIANTS), "A"),
    view: pick(params.get("view"), Object.keys(VIEWS), "controls"),
    scenario: pick(params.get("scenario"), Object.keys(SCENARIOS), "normal"),
  };

  const clone = (value) => JSON.parse(JSON.stringify(value));

  const reset = () => {
    S.controls = clone(PROTO.seed);
    S.model = clone(model);
    if (S.scenario === "missing-element") {
      // The new description renamed the admin console, so the SSO Control's
      // scope matches nothing by name or alias.
      S.model.elements.find((e) => e.id === "process:admin-console").name = "Staff console";
      S.model.elements.find((e) => e.name === "Staff console").id = "process:staff-console";
    }
    if (S.scenario === "empty-zone") {
      S.controls[0].scope = { type: "boundary", name: "Office", aliases: [] };
    }
    if (S.scenario === "conflict") {
      S.controls.push({
        id: newId(), name: "Orders DB disk encryption", updated: "2026-11-01T08:00:00Z",
        scope: { type: "store", name: "Orders DB", aliases: [] },
        facts: [{ attribute: "encryption_at_rest", value: "LUKS on the host volume", detail: "" }],
        rows: [{ framework: "ASVS", unit: "V11", version: "5.0.0" }], evidence: [],
      });
    }
    if (S.scenario === "at-limit") {
      for (let n = S.controls.length + 1; n <= limits.max_controls; n += 1) {
        S.controls.push({
          id: newId(), name: `Backup check ${n}`, updated: "2026-10-29T10:00:00Z",
          scope: { type: "store", name: "Backups", aliases: [] },
          facts: [{ attribute: "backup_access", value: "The platform team only", detail: "" }],
          rows: [], evidence: [],
        });
      }
    }
    S.editing = null;      // the Control ID an editor holds, or "new"
    S.draft = null;        // the editor's working copy
    S.tab = "scope";       // variant B's detail tab
    S.selected = S.controls[0]?.id ?? null;
    S.confirm = null;      // the IDs a delete waits on
    S.chosen = new Set();  // variant A's selected rows
    S.sortBy = "date"; S.descending = true;
    S.search = ""; S.filterKind = ""; S.filterFramework = "";
    S.problem = null;
    S.notice = null;
    S.dialog = false;      // variant B's offer dialog
    S.filter = "all";      // variant C's report filter
    S.offer = {};          // Control ID -> { ticked, choice }
    S.sent = null;         // what a submission would carry
    for (const control of S.controls) {
      const match = matchScope(control.scope);
      S.offer[control.id] = { ticked: match.outcome === "matched", choice: "" };
    }
  };

  const controlById = (id) => S.controls.find((control) => control.id === id);

  // ---- rules: the scope match of #2115 ---------------------------------------

  // A match is ID equality through make_element_id, by the name and then by
  // each alias. A similar name proposes nothing.
  const findByNames = (type, scope) => {
    const pool = type === "boundary" ? S.model.boundaries : S.model.elements.filter((e) => e.type === type);
    for (const name of [scope.name, ...scope.aliases]) {
      const id = elementId(type, name);
      const hit = pool.find((e) => e.id === id);
      if (hit) return hit;
    }
    return null;
  };

  const matchScope = (scope) => {
    if (scope.type === "application") {
      return { outcome: "matched", resolved: S.model.elements.map((e) => ({ element: e, ticked: true, reason: "" })) };
    }
    const hit = findByNames(scope.type, scope);
    if (!hit) {
      const choices = scope.type === "boundary" ? S.model.boundaries : S.model.elements.filter((e) => e.type === scope.type);
      return { outcome: scope.type === "boundary" ? "empty-zone" : "missing-element", reason: "missing-element", resolved: [], choices };
    }
    if (scope.type !== "boundary") return { outcome: "matched", hit, resolved: [{ element: hit, ticked: true, reason: "" }] };
    // A zone scope fans out onto each element whose trust zone holds the
    // boundary, ticked. An inferred zone and an unplaced element are listed
    // unticked, with the reason.
    const resolved = S.model.elements
      .filter((e) => e.zone === hit.name || (!e.zone && e.type !== "entity"))
      .map((e) => ({
        element: e,
        ticked: e.zone === hit.name && !e.inferred,
        reason: !e.zone ? "unplaced: the description places it in no zone"
          : e.inferred ? "zone inferred: the service chose this zone, the description did not state it" : "",
      }));
    if (!resolved.some((r) => r.element.zone === hit.name)) {
      return { outcome: "empty-zone", hit, reason: "empty-zone", resolved: [], choices: S.model.boundaries };
    }
    return { outcome: "matched", hit, resolved };
  };

  // The offer's resolved elements, after an owner's choice for an unmatched
  // Control. A choice is one entry of the choice list, never a free name.
  const resolvedFor = (control) => {
    const match = matchScope(control.scope);
    const choice = S.offer[control.id]?.choice;
    if (match.outcome !== "matched" && choice) {
      const chosen = match.choices.find((c) => c.id === choice);
      if (chosen) return matchScope({ ...control.scope, name: chosen.name, aliases: [] });
    }
    return match;
  };

  // ---- rules: the writer precedence of #2116 ----------------------------------

  // A Control fills only an open fact. A stated value stands, as `stated`.
  const factState = (control, fact, element) => {
    if (element?.stated?.[fact.attribute]) return "stated";
    return "written";
  };

  // Two ticked Controls that write one fact of one element with two values
  // refuse the submission.
  const conflicts = () => {
    const seen = new Map();
    const found = [];
    for (const control of S.controls) {
      if (!S.offer[control.id]?.ticked) continue;
      for (const r of resolvedFor(control).resolved.filter((r) => r.ticked)) {
        for (const fact of control.facts) {
          const key = `${r.element.id}/${fact.attribute}`;
          const earlier = seen.get(key);
          if (earlier && earlier.value !== fact.value) found.push({ key, element: r.element, attribute: fact.attribute, a: earlier.control, b: control });
          else seen.set(key, { control, value: fact.value });
        }
      }
    }
    return found;
  };

  // ---- rules: the Control save -------------------------------------------------

  const refuseSave = (draft) => {
    const name = draft.name.trim();
    if (!name) return "Give the Control a name.";
    if ([...name].length > limits.name_chars) return `The name has more than ${limits.name_chars} characters. Shorten it.`;
    const twin = S.controls.find((c) => c.id !== draft.id && c.name.trim().toLowerCase() === name.toLowerCase());
    if (twin) return `You already have a Control named “${twin.name}”. Give this Control a different name.`;
    if (draft.scope.type !== "application" && !draft.scope.name.trim()) return "Name what the Control protects, or choose the whole application.";
    if (draft.scope.type !== "application") {
      try { slug(draft.scope.name); if (!slug(draft.scope.name)) throw new Error(); } catch { return "The name of what the Control protects has no letters or digits."; }
    }
    if (!draft.facts.length && !draft.rows.length) return "Add at least one fact or one row. A Control with neither changes nothing.";
    for (const fact of draft.facts) {
      if (!fact.value.trim()) return `Give the fact “${fact.attribute}” a value.`;
      if ([...fact.value].length > limits.value_chars) return `The value of “${fact.attribute}” has more than ${limits.value_chars} characters.`;
    }
    const dup = draft.facts.find((f, i) => draft.facts.findIndex((g) => g.attribute === f.attribute) !== i);
    if (dup) return `The fact “${dup.attribute}” appears twice. One Control writes one value for one fact.`;
    const twinRow = draft.rows.find((r, i) => draft.rows.findIndex((g) => g.framework === r.framework && g.unit === r.unit) !== i);
    if (twinRow) return `The row ${unitText(twinRow)} appears twice.`;
    if (draft.id === "new" && S.controls.length >= limits.max_controls) return LIMIT_TEXT();
    return null;
  };
  const atLimit = () => S.controls.length >= limits.max_controls;
  const LIMIT_TEXT = () => `Controls holds ${limits.max_controls} Controls, the most it can keep. Delete a Control before you add another.`;

  const saveDraft = () => {
    const draft = S.draft;
    draft.name = draft.name.trim();
    draft.scope.name = draft.scope.type === "application" ? "" : draft.scope.name.trim();
    draft.scope.aliases = draft.scope.type === "application" ? [] : draft.scope.aliases.map((a) => a.trim()).filter(Boolean);
    const problem = refuseSave(draft);
    if (problem) { S.problem = { where: "editor", text: problem }; render(); return; }
    draft.updated = new Date().toISOString();
    if (draft.id === "new") {
      draft.id = newId();
      S.controls.push(draft);
    } else {
      S.controls[S.controls.findIndex((c) => c.id === draft.id)] = draft;
    }
    S.offer[draft.id] = { ticked: matchScope(draft.scope).outcome === "matched", choice: "" };
    S.selected = draft.id;
    S.editing = null; S.draft = null; S.problem = null;
    S.notice = `“${draft.name}” is saved. The analyses that already applied it keep their copy.`;
    render();
  };

  const startEdit = (id) => {
    S.problem = null; S.notice = null;
    S.editing = id;
    S.draft = id === "new"
      ? { id: "new", name: "", scope: { type: "process", name: "", aliases: [] }, facts: [], rows: [], evidence: [] }
      : clone(controlById(id));
    render();
  };

  const deleteControls = (ids) => {
    const names = ids.map((id) => controlById(id).name);
    S.controls = S.controls.filter((c) => !ids.includes(c.id));
    for (const id of ids) { delete S.offer[id]; S.chosen.delete(id); }
    if (ids.includes(S.selected)) S.selected = S.controls[0]?.id ?? null;
    S.confirm = null;
    S.notice = names.length === 1
      ? `“${names[0]}” is deleted. The analyses that applied it keep their copy.`
      : `${names.length} Controls are deleted. The analyses that applied them keep their copy.`;
    render();
  };

  // ---- rules: what a submission and a report copy hold --------------------------

  const submission = () => {
    const found = conflicts();
    if (found.length) {
      const c = found[0];
      return { refused: `Two Controls write “${c.attribute}” on ${c.element.name} with two values: “${c.a.name}” and “${c.b.name}”. Untick one, or edit one Control so that they agree.` };
    }
    return {
      control_scopes: S.controls.filter((c) => S.offer[c.id]?.ticked).map((c) => {
        const match = resolvedFor(c);
        return {
          control_id: c.id, scope: c.scope, outcome: match.outcome,
          elements: match.resolved.map((r) => ({ element_id: r.element.id, ticked: r.ticked, reason: r.reason })),
        };
      }),
      left_out: S.controls.filter((c) => !S.offer[c.id]?.ticked).map((c) => ({ control_id: c.id, reason: matchScope(c.scope).outcome === "matched" ? "unticked" : matchScope(c.scope).outcome })),
    };
  };

  // A STUB of the report copy: the critic's rulings per row, as #2117 records
  // them. The stub assigns an outcome by the row's unit so that each kind
  // shows at least once.
  const RULINGS = {
    "ASVS/V2.4": { outcome: "applied", claims: ["asvs-v2.4.1-api-gateway"], reason: "The WAF policy limits each client IP to 100 requests a minute on the gateway, which is the anti-automation control V2.4.1 asks for. The health endpoint exemption does not reach an application function." },
    "STRIDE/denial-of-service": { outcome: "applied", claims: ["dos-flood-web-portal"], reason: "A flood from one source is capped at the edge before it reaches the portal." },
    "ASVS/V11.2": { outcome: "refused", reason: "V11.2 asks how the application uses cryptography in code. Encryption of the storage volume says nothing about the application's own cryptographic calls, so this Control does not bear on V11.2." },
    "STRIDE/information-disclosure": { outcome: "applied", claims: ["info-disclosure-orders-db-backup"], reason: "A copied volume or backup is unreadable without the KMS key, which the Terraform excerpt binds to the instance." },
    "ASVS/V6.2.1": { outcome: "no-finding" },
    "ASVS/V6.3": { outcome: "applied", claims: ["asvs-v6.3.1-admin-console"], reason: "The access policy requires a FIDO2 key for the admin console, which meets the MFA requirement the lane raised." },
    "STRIDE/spoofing": { outcome: "no-finding" },
    "ASVS/V16": { outcome: "refused", reason: "The Control states that events reach the SIEM but gives no evidence. V16 needs the log content and the protection of the logs, and a statement alone does not settle it." },
    "STRIDE/repudiation": { outcome: "no-finding" },
    "ASVS/V11": { outcome: "no-finding" },
  };
  const rulingFor = (row) => RULINGS[`${row.framework}/${row.unit}`] ?? { outcome: "no-finding" };

  const reportCopy = () => S.controls.filter((c) => S.offer[c.id]?.ticked).map((c) => {
    const match = resolvedFor(c);
    const ticked = match.resolved.filter((r) => r.ticked);
    return {
      control: c, match, ticked,
      facts: c.facts.map((fact) => {
        // "changed" shows once, so the owner sees the three states.
        const state = c.id === S.controls[0]?.id && fact.attribute === "rate_limiting" ? "changed"
          : ticked.some((r) => factState(c, fact, r.element) === "stated") ? "stated" : "written";
        return { ...fact, state, own: state === "changed" ? "100 requests a minute, and 20 a minute on /login" : "" };
      }),
      rows: c.rows.map((row) => ({ ...row, ...rulingFor(row) })),
    };
  });

  // ---- shared pieces -------------------------------------------------------------

  const noticeBox = () => (S.notice ? h("div", { class: "notice", role: "status" }, S.notice) : null);
  const problemBox = (where) => (S.problem?.where === where ? h("div", { class: "refusal", role: "alert" }, S.problem.text) : null);
  const introLine = () => h("p", { class: "muted" },
    "A Control records a protection your systems already have. An analysis that applies a Control keeps its own copy, so a later edit changes no report.");
  const countLine = () => h("p", { class: "hint" }, `${S.controls.length} of ${limits.max_controls} Controls`);

  const confirmDialog = () => {
    const ids = S.confirm ?? [];
    const text = ids.length === 1
      ? `Delete “${controlById(ids[0]).name}”? Each analysis that already applied it keeps its own copy, and its reports do not change. You cannot undo this.`
      : `Delete ${ids.length} Controls? Each analysis that already applied them keeps its own copy, and its reports do not change. You cannot undo this.`;
    const dialog = h("dialog", { id: "confirm-delete", "aria-labelledby": "confirm-delete-text" },
      h("p", { id: "confirm-delete-text" }, ids.length ? text : ""),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", on: { click: () => { S.confirm = null; render(); } } }, "Cancel"),
        h("button", { type: "button", class: "danger", on: { click: () => deleteControls(S.confirm) } }, "Delete")));
    if (ids.length) queueMicrotask(() => dialog.showModal());
    return dialog;
  };

  const askDelete = (...ids) => { S.confirm = ids; render(); };

  // The editor's four fieldsets, shared by the variants that edit in a form
  // or a dialog. Variant C edits inline with the same pieces.
  const editorScope = (draft) => {
    const type = scopeTypes.find((row) => row.type === draft.scope.type);
    const nameField = draft.scope.type === "application" ? null : h("div", {},
      h("label", { class: "field-label", for: "scope-name" }, "Its name in your descriptions"),
      h("input", { type: "text", id: "scope-name", value: draft.scope.name, placeholder: "For example, Orders DB",
        on: { input: (e) => { draft.scope.name = e.target.value; } } }),
      h("p", { class: "hint" }, "A Control matches an element when the name is the same, letter for letter after case and punctuation. A similar name matches nothing."),
      h("label", { class: "field-label" }, "Other names it goes by"),
      h("div", { class: "aliases" },
        ...draft.scope.aliases.map((alias, i) => h("span", { class: "tag" }, alias, " ",
          h("button", { type: "button", class: "quiet", "aria-label": `Remove the alias ${alias}`, on: { click: () => { draft.scope.aliases.splice(i, 1); render(); } } }, "×"))),
        h("input", { type: "text", id: "alias-input", placeholder: "Add an alias and press Enter", "aria-label": "Add an alias",
          on: { keydown: (e) => { if (e.key === "Enter") { e.preventDefault(); const v = e.target.value.trim(); if (v) { draft.scope.aliases.push(v); render(); } } } } })));
    return h("fieldset", {}, h("legend", {}, "What it protects"),
      h("div", { class: "line" },
        h("div", {},
          h("label", { class: "field-label", for: "scope-type" }, "Kind"),
          h("select", { id: "scope-type", on: { change: (e) => { draft.scope.type = e.target.value; draft.facts = []; render(); } } },
            ...scopeTypes.map((row) => h("option", { value: row.type, selected: row.type === draft.scope.type }, row.label)))),
        nameField),
      type && draft.scope.type === "boundary" ? h("p", { class: "hint" }, "A zone Control applies to each element the description places in that zone. The offer lists each one before the analysis runs.") : null);
  };

  const editorFacts = (draft) => {
    const type = scopeTypes.find((row) => row.type === draft.scope.type);
    const open = type.facts.filter((f) => !draft.facts.some((fact) => fact.attribute === f));
    let attribute = open[0] ?? "";
    let value = "";
    return h("fieldset", {}, h("legend", {}, "What it proves"),
      h("p", { class: "hint" }, "Each fact answers a question the analysis would otherwise ask. A fact the description already states stands as stated, and the Control does not change it."),
      h("ul", { class: "facts-list" }, ...draft.facts.map((fact, i) => h("li", {},
        h("span", { class: "u" }, h("b", {}, fact.attribute), ": ", fact.value, fact.detail ? h("span", { class: "sub" }, ` (${fact.detail})`) : null),
        h("button", { type: "button", class: "quiet", on: { click: () => { draft.facts.splice(i, 1); render(); } } }, "Remove")))),
      open.length ? h("div", { class: "add-line" },
        h("div", {}, h("label", { class: "field-label", for: "fact-attr" }, "Fact"),
          h("select", { id: "fact-attr", on: { change: (e) => { attribute = e.target.value; } } }, ...open.map((f) => h("option", { value: f }, f)))),
        h("div", {}, h("label", { class: "field-label", for: "fact-value" }, "Value"),
          h("input", { type: "text", id: "fact-value", placeholder: "For example, AES-256 through the cloud KMS", on: { input: (e) => { value = e.target.value; } } })),
        h("button", { type: "button", on: { click: () => { if (value.trim()) { draft.facts.push({ attribute, value: value.trim(), detail: "" }); render(); } } } }, "Add fact"))
        : h("p", { class: "hint" }, "Every fact of this kind has a value."));
  };

  const editorRows = (draft) => {
    let framework = "ASVS";
    let unit = units.ASVS.units[0].id;
    const unitSelect = () => h("select", { id: "row-unit", on: { change: (e) => { unit = e.target.value; } } },
      ...units[framework].units.map((u) => h("option", { value: u.id }, `${u.id} · ${u.name}`.slice(0, 90))));
    const holder = h("div", {}, h("label", { class: "field-label", for: "row-unit" }, "Unit"), unitSelect());
    return h("fieldset", {}, h("legend", {}, "What it covers"),
      h("p", { class: "hint" }, "A row says this Control covers one unit of one framework. The analysis judges each row against its findings. A row that does not bear on its unit is refused with a reason."),
      h("ul", { class: "rows-list" }, ...draft.rows.map((row, i) => h("li", {},
        h("span", { class: "u" }, unitText(row), " ", h("span", { class: "sub" }, `v${row.version}`)),
        h("button", { type: "button", class: "quiet", on: { click: () => { draft.rows.splice(i, 1); render(); } } }, "Remove")))),
      h("div", { class: "add-line" },
        h("div", {}, h("label", { class: "field-label", for: "row-fw" }, "Framework"),
          h("select", { id: "row-fw", on: { change: (e) => { framework = e.target.value; unit = units[framework].units[0].id; holder.replaceChildren(h("label", { class: "field-label", for: "row-unit" }, "Unit"), unitSelect()); } } },
            ...Object.keys(units).map((f) => h("option", { value: f }, `${f} ${units[f].version}`)))),
        holder,
        h("button", { type: "button", on: { click: () => { draft.rows.push({ framework, unit, version: units[framework].version }); render(); } } }, "Add row")));
  };

  const editorEvidence = (draft) => {
    let cls = "config";
    return h("fieldset", {}, h("legend", {}, "Evidence"),
      h("p", { class: "hint" }, "A file that shows the protection. It is not a source: the analysis quotes it beside a ruling and never reads it as a description. Say what kind of evidence it is."),
      h("ul", { class: "evidence-list" }, ...draft.evidence.map((file, i) => h("li", {},
        h("span", { class: "u" }, h("b", {}, file.name), " ", tag(CLASS_LABEL[file.class]), " ", h("span", { class: "sub" }, size(file.size))),
        h("button", { type: "button", class: "quiet", on: { click: () => { draft.evidence.splice(i, 1); render(); } } }, "Remove")))),
      h("div", { class: "add-line" },
        h("div", {}, h("label", { class: "field-label", for: "ev-class" }, "Kind of evidence"),
          h("select", { id: "ev-class", on: { change: (e) => { cls = e.target.value; } } },
            ...evidenceClasses.map((row) => h("option", { value: row.id }, `${row.label}: ${row.hint}`)))),
        h("div", {}, h("label", { class: "field-label", for: "ev-file" }, "File"),
          h("input", { type: "file", id: "ev-file", on: { change: (e) => {
            const file = e.target.files[0];
            if (!file) return;
            if (file.size > limits.max_evidence_bytes) { S.problem = { where: "editor", text: `This file is larger than the ${limits.max_evidence_bytes.toLocaleString()} bytes one evidence file can hold.` }; render(); return; }
            draft.evidence.push({ name: file.name, class: cls, size: file.size, excerpt: { lines: "", text: "" } });
            render();
          } } })),
        h("span", {})));
  };

  const editorActions = (onCancel) => h("div", { class: "dialog-actions" },
    h("button", { type: "button", on: { click: onCancel } }, "Cancel"),
    h("button", { type: "button", class: "primary", on: { click: saveDraft } }, "Save Control"));

  const editorForm = (draft, onCancel) => h("div", { class: "editor" },
    h("h2", {}, draft.id === "new" ? "New Control" : `Edit “${draft.name}”`),
    h("div", {}, h("label", { class: "field-label", for: "control-name" }, "Name"),
      h("input", { type: "text", id: "control-name", value: draft.name, placeholder: "For example, Edge WAF and rate limit", on: { input: (e) => { draft.name = e.target.value; } } })),
    editorScope(draft), editorFacts(draft), editorRows(draft), editorEvidence(draft),
    problemBox("editor"), editorActions(onCancel));

  // A Control's summary, used by lists.
  const summaryBits = (control) => [
    scopeText(control.scope),
    `${control.facts.length} fact${control.facts.length === 1 ? "" : "s"}`,
    `${control.rows.length} row${control.rows.length === 1 ? "" : "s"}`,
    `${control.evidence.length} evidence file${control.evidence.length === 1 ? "" : "s"}`,
  ].join(" · ");

  // ---- the submission offer, shared rules ----------------------------------------

  const offerLine = (control) => {
    const match = resolvedFor(control);
    const entry = S.offer[control.id];
    const box = h("input", { type: "checkbox", checked: entry.ticked, disabled: match.outcome !== "matched",
      on: { change: (e) => { entry.ticked = e.target.checked; S.sent = null; render(); } } });
    const why = match.outcome === "missing-element"
      ? `Not in this description: no ${TYPE_LABEL[control.scope.type].toLowerCase().replace(/^an? /, "")} is named “${control.scope.name}”${control.scope.aliases.length ? ` or ${control.scope.aliases.map((a) => `“${a}”`).join(", ")}` : ""}.`
      : match.outcome === "empty-zone"
        ? (match.hit ? `The zone “${control.scope.name}” holds no element in this description.` : `No zone is named “${control.scope.name}”.`)
        : "";
    const choice = match.outcome !== "matched" ? h("div", { class: "choice" },
      h("label", { for: `choice-${control.id}` }, "Apply it to"),
      h("select", { id: `choice-${control.id}`, on: { change: (e) => { entry.choice = e.target.value; entry.ticked = Boolean(e.target.value); S.sent = null; render(); } } },
        h("option", { value: "" }, "Not this time"),
        ...match.choices.map((c) => h("option", { value: c.id, selected: entry.choice === c.id }, c.name))),
      h("span", { class: "sub" }, "This choice applies to this analysis only. Add the name as an alias on the Control to match it next time.")) : null;
    const fanout = control.scope.type === "boundary" && match.resolved.length ? h("ul", { class: "fanout" },
      ...match.resolved.map((r) => h("li", { class: r.ticked ? "" : "unticked" }, r.ticked ? "☑" : "☐", ` ${r.element.name}`, r.reason ? h("span", { class: "sub" }, ` — ${r.reason}`) : null))) : null;
    const facts = control.facts.length ? h("div", { class: "m" }, "Answers for you: ", control.facts.map((f) => {
      const stated = match.resolved.some((r) => r.ticked && factState(control, f, r.element) === "stated");
      return h("span", {}, h("b", {}, f.attribute), stated ? " (already stated in the description, so it stands)" : ` = ${f.value}`, "; ");
    })) : null;
    box.id = `tick-${control.id}`;
    return h("div", { class: "offer-row" }, box, h("span", {},
      h("label", { class: "n", for: box.id }, control.name, " ", match.outcome === "matched" ? tag(`matches ${match.hit ? match.hit.name : "the application"}`, "matched") : tag(match.outcome, "refused")),
      h("div", { class: "m" }, scopeText(control.scope), " · ", control.rows.map(unitText).join(" · ") || "no rows"),
      why ? h("div", { class: "why" }, why) : null,
      choice, fanout, facts));
  };

  const offerSummary = () => {
    const found = conflicts();
    const ticked = S.controls.filter((c) => S.offer[c.id]?.ticked).length;
    return [
      found.length ? h("div", { class: "refusal", role: "alert" }, submission().refused) : null,
      h("p", { class: "hint" }, `${ticked} of ${S.controls.length} Controls will apply. Each applied Control writes its facts before the analysis runs, and the analysis judges each of its rows against the findings. `,
        h("a", { href: "#", on: { click: (e) => { e.preventDefault(); go({ view: "controls" }); } } }, "Manage Controls")),
    ];
  };

  // The analysis form around the offer, as a stub. The real form keeps its
  // own fields; the prototype shows where the offer sits.
  const formStub = (...offer) => h("form", { class: "entry form-stub", on: { submit: (e) => { e.preventDefault(); S.sent = submission(); render(); } } },
    h("h1", {}, "New analysis"),
    h("label", { class: "field-label", for: "analysis-name" }, "Analysis name"),
    h("input", { type: "text", id: "analysis-name", value: "Customer portal", readonly: true }),
    h("label", { class: "field-label", for: "description" }, "What are you building?"),
    h("textarea", { id: "description", readonly: true, value: "A shopper uses the web portal in the DMZ over HTTPS. The portal calls the billing API through the API gateway. Orders live in the Orders DB. The backups volume is encrypted with AES-256. A reporting job reads the orders nightly. Staff use an admin console." }),
    h("div", { class: "files-used" }, h("h2", {}, "Files for this analysis"), h("p", { class: "hint" }, "(the Files checklist of #2062 stays here)")),
    ...offer,
    S.sent?.refused ? h("div", { class: "refusal", role: "alert" }, S.sent.refused) : null,
    S.sent && !S.sent.refused ? h("div", { class: "notice", role: "status" }, `The analysis would start with ${S.sent.control_scopes.length} Controls applied. The state block below shows what the job keeps.`) : null,
    h("div", { class: "entry-actions" }, h("button", { type: "submit", class: "primary" }, "Prepare analysis")));

  // ---- the report section, shared rules --------------------------------------------

  const reportHead = () => [
    h("h2", { class: "report-h" }, "Controls"),
    h("p", { class: "report-lead" }, "The Controls this analysis applied, as they were when it ran. The critic judged each row against the findings; a Control settles nothing on its own."),
  ];
  const factLine = (fact) => h("li", { class: "fact" }, h("b", {}, fact.attribute), ": ", fact.value, " ", tag(fact.state, fact.state),
    fact.state === "changed" ? h("div", { class: "sub" }, `Your answer: ${fact.own}`) : null,
    h("div", { class: "sub" }, FACT_STATE_TEXT[fact.state]));
  const evidenceQuote = (file) => h("div", {}, h("div", { class: "sub" }, `${file.name} · ${CLASS_LABEL[file.class]} · ${file.excerpt.lines}`),
    h("blockquote", { class: "excerpt" }, file.excerpt.text));
  const rowDetail = (entry, row) => [
    row.outcome === "applied" ? h("div", { class: "sub" }, "Ruled out: ", ...row.claims.map((id, i) => [i ? ", " : "", h("a", { href: `#${id}` }, id)])) : null,
    row.reason ? h("p", {}, h("span", { class: "sub" }, "The critic's reason: "), row.reason) : null,
    row.outcome === "applied" ? entry.control.evidence.map(evidenceQuote) : null,
    h("div", { class: "sub" }, OUTCOME_TEXT[row.outcome]),
  ];
  const matchLine = (entry) => {
    const { match, control } = entry;
    const names = entry.ticked.map((r) => r.element.name);
    return `${scopeText(control.scope)} → ${names.length ? names.join(", ") : "nothing"}${control.scope.type === "boundary" ? `, ${match.resolved.filter((r) => !r.ticked).length} listed and left out` : ""}`;
  };

  // ---- variant A: form page, checklist offer, one report table --------------------

  // The Analyses page's shape: the filters, "Delete selected", a sortable
  // table with a select column, and the row actions.
  const controlsA = () => {
    const text = S.search.toLocaleLowerCase();
    const shown = S.controls.filter((c) =>
      [c.name, scopeText(c.scope), ...c.rows.map(unitText), ...c.evidence.map((f) => f.name)].some((v) => v.toLocaleLowerCase().includes(text))
      && (!S.filterKind || c.scope.type === S.filterKind)
      && (!S.filterFramework || c.rows.some((r) => r.framework === S.filterFramework)));
    shown.sort((a, b) => {
      const order = S.sortBy === "name"
        ? a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true })
        : new Date(a.updated) - new Date(b.updated);
      return (S.descending ? -order : order) || a.name.localeCompare(b.name);
    });
    const chosen = shown.filter((c) => S.chosen.has(c.id));
    const refocus = (id) => queueMicrotask(() => { const input = document.getElementById(id); if (input) { input.focus(); input.setSelectionRange?.(input.value.length, input.value.length); } });
    const sortHead = (key, label, id, cls) => h("th", { scope: "col", id, class: cls, "aria-sort": S.sortBy === key ? (S.descending ? "descending" : "ascending") : "none" },
      h("button", { type: "button", "data-sort": key, on: { click: () => { S.descending = S.sortBy === key ? !S.descending : key === "date"; S.sortBy = key; render(); } } }, label));
    const selectShown = h("input", { type: "checkbox", id: "select-shown", "aria-label": "Select all shown Controls", checked: Boolean(shown.length) && chosen.length === shown.length,
      on: { change: (e) => { shown.forEach((c) => (e.target.checked ? S.chosen.add(c.id) : S.chosen.delete(c.id))); render(); } } });
    selectShown.indeterminate = Boolean(chosen.length) && chosen.length < shown.length;
    return [
      h("div", { class: "count-line" }, h("h1", {}, "Controls"),
        h("span", {}, h("button", { type: "button", class: "primary", disabled: atLimit() || S.editing === "new", on: { click: () => startEdit("new") } }, "New Control"))),
      introLine(), countLine(),
      atLimit() ? h("div", { class: "refusal" }, LIMIT_TEXT()) : null,
      noticeBox(),
      S.editing ? editorForm(S.draft, () => { S.editing = null; S.draft = null; S.problem = null; render(); }) : null,
      h("div", { class: "analysis-filters" },
        h("div", {}, h("label", { class: "field-label", for: "controls-search" }, "Find a Control"),
          h("input", { type: "search", id: "controls-search", placeholder: "Search by name, what it protects or a unit", value: S.search, on: { input: (e) => { S.search = e.target.value; render(); refocus("controls-search"); } } })),
        h("div", {}, h("label", { class: "field-label", for: "controls-kind" }, "Protects"),
          h("select", { id: "controls-kind", on: { change: (e) => { S.filterKind = e.target.value; render(); } } }, h("option", { value: "" }, "All kinds"),
            ...scopeTypes.map((row) => h("option", { value: row.type, selected: S.filterKind === row.type }, row.label)))),
        h("div", {}, h("label", { class: "field-label", for: "controls-framework" }, "Framework"),
          h("select", { id: "controls-framework", on: { change: (e) => { S.filterFramework = e.target.value; render(); } } }, h("option", { value: "" }, "All frameworks"),
            ...Object.keys(units).map((f) => h("option", { value: f, selected: S.filterFramework === f }, f))))),
      h("div", { class: "analysis-actions" }, h("button", { type: "button", id: "delete-selected", disabled: !chosen.length, on: { click: () => askDelete(...chosen.map((c) => c.id)) } },
        chosen.length ? `Delete selected (${chosen.length})` : "Delete selected")),
      h("div", { class: "analysis-table-wrap" }, h("table", { id: "controls-list", hidden: !shown.length },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", class: "select-cell" }, selectShown),
          sortHead("name", "Name", "sort-name", "name-cell"), h("th", { scope: "col" }, "Protects"), h("th", { scope: "col" }, "Covers"), h("th", { scope: "col" }, "Evidence"),
          sortHead("date", "Updated", "sort-date"), h("th", { scope: "col" }, "Actions"))),
        h("tbody", {}, ...shown.map((c) => h("tr", {},
          h("td", {}, h("input", { type: "checkbox", checked: S.chosen.has(c.id), "aria-label": `Select ${c.name}`, on: { change: (e) => { e.target.checked ? S.chosen.add(c.id) : S.chosen.delete(c.id); render(); } } })),
          h("td", { class: "name-cell" }, h("a", { href: "#", on: { click: (e) => { e.preventDefault(); startEdit(c.id); } } }, c.name),
            c.facts.length ? h("div", { class: "sub" }, `Proves ${c.facts.map((f) => f.attribute).join(", ")}`) : null),
          h("td", {}, scopeText(c.scope)),
          h("td", {}, h("div", { class: "chips" }, ...c.rows.map((r) => h("span", { class: "chip" }, `${r.framework} ${r.unit}`)))),
          h("td", {}, c.evidence.map((f) => `${f.name} (${CLASS_LABEL[f.class]})`).join(", ") || "—"),
          h("td", {}, h("time", { datetime: c.updated }, day(c.updated))),
          h("td", {}, h("div", { class: "row-actions" },
            h("button", { type: "button", "aria-label": `Edit ${c.name}`, on: { click: () => startEdit(c.id) } }, "Edit"),
            h("button", { type: "button", class: "danger", "aria-label": `Delete ${c.name}`, on: { click: () => askDelete(c.id) } }, "Delete")))))))),
      shown.length ? null : h("p", { class: "muted" }, S.controls.length ? "No Controls match your filters." : "Record a protection your systems already have. Your Controls will appear here."),
      confirmDialog(),
    ];
  };

  const offerA = () => formStub(
    h("h2", {}, "Controls for this analysis"),
    h("p", { class: "hint" }, "Each Control is ticked where its scope matches this description."),
    h("div", { class: "offer" }, ...S.controls.map(offerLine)),
    ...offerSummary());

  const reportA = () => {
    const copy = reportCopy();
    return [...reportHead(),
      h("table", { class: "grid-table" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Control"), h("th", { scope: "col" }, "Match"), h("th", { scope: "col" }, "Facts"), h("th", { scope: "col" }, "Rows and outcomes"))),
        h("tbody", {}, ...copy.map((entry) => h("tr", {},
          h("td", { class: "name" }, h("b", {}, entry.control.name)),
          h("td", {}, matchLine(entry)),
          h("td", {}, h("ul", { class: "fanout" }, ...entry.facts.map(factLine))),
          h("td", {}, ...entry.rows.map((row) => h("details", {}, h("summary", {}, unitText(row), " ", tag(row.outcome, row.outcome)), ...rowDetail(entry, row)))))))),
      copy.length ? null : h("p", { class: "muted" }, "This analysis applied no Control."),
    ];
  };

  // ---- variant B: master-detail with tabs, offer dialog by outcome, report cards ----

  const detailB = () => {
    const control = S.editing && S.draft ? S.draft : controlById(S.selected);
    if (!control) return h("div", { class: "detail" }, h("p", { class: "muted" }, "Choose a Control, or add one."));
    const editing = Boolean(S.editing);
    const tabs = [["scope", "Protects"], ["facts", "Proves"], ["rows", "Covers"], ["evidence", "Evidence"]];
    const body = {
      scope: editing ? editorScope(control) : h("dl", { class: "facts" }, h("dt", {}, "Kind"), h("dd", {}, TYPE_LABEL[control.scope.type]),
        control.scope.type !== "application" ? [h("dt", {}, "Name"), h("dd", {}, control.scope.name), h("dt", {}, "Aliases"), h("dd", {}, control.scope.aliases.join(", ") || "none")] : null),
      facts: editing ? editorFacts(control) : (control.facts.length ? h("ul", { class: "facts-list" }, ...control.facts.map((f) => h("li", {}, h("span", { class: "u" }, h("b", {}, f.attribute), ": ", f.value)))) : h("p", { class: "muted" }, "No facts.")),
      rows: editing ? editorRows(control) : (control.rows.length ? h("ul", { class: "rows-list" }, ...control.rows.map((r) => h("li", {}, h("span", { class: "u" }, unitText(r), " ", h("span", { class: "sub" }, `v${r.version}`))))) : h("p", { class: "muted" }, "No rows.")),
      evidence: editing ? editorEvidence(control) : (control.evidence.length ? h("ul", { class: "evidence-list" }, ...control.evidence.map((f) => h("li", {}, h("span", { class: "u" }, h("b", {}, f.name), " ", tag(CLASS_LABEL[f.class]), " ", h("span", { class: "sub" }, size(f.size)))))) : h("p", { class: "muted" }, "No evidence file.")),
    }[S.tab];
    return h("div", { class: "detail" },
      editing ? h("div", {}, h("label", { class: "field-label", for: "control-name" }, "Name"),
        h("input", { type: "text", id: "control-name", value: control.name, on: { input: (e) => { control.name = e.target.value; } } }))
        : h("div", { class: "count-line" }, h("h2", {}, control.name), h("span", {},
          h("button", { type: "button", class: "quiet", on: { click: () => startEdit(control.id) } }, "Edit"), " ",
          h("button", { type: "button", class: "quiet", on: { click: () => askDelete(control.id) } }, "Delete"))),
      h("div", { class: "tabs", role: "tablist" }, ...tabs.map(([key, label]) => h("button", { type: "button", role: "tab", "aria-selected": String(S.tab === key), on: { click: () => { S.tab = key; render(); } } }, label))),
      body, problemBox("editor"),
      editing ? editorActions(() => { S.editing = null; S.draft = null; S.problem = null; render(); }) : null);
  };

  const controlsB = () => [
    h("div", { class: "count-line" }, h("h1", {}, "Controls"),
      h("span", {}, h("button", { type: "button", class: "primary", disabled: atLimit(), on: { click: () => { startEdit("new"); S.tab = "scope"; render(); } } }, "Add a Control"))),
    introLine(), countLine(), noticeBox(),
    atLimit() ? h("div", { class: "refusal" }, LIMIT_TEXT()) : null,
    h("div", { class: "master" },
      h("ul", { class: "master-list" }, ...S.controls.map((c) => h("li", {}, h("button", { type: "button", class: "row", "aria-pressed": String(S.selected === c.id && !S.editing),
        on: { click: () => { S.selected = c.id; S.editing = null; S.draft = null; render(); } } },
        h("span", { class: "n" }, c.name), h("span", { class: "m" }, summaryBits(c))))),
        S.controls.length ? null : h("li", { class: "muted" }, "No Controls yet.")),
      detailB()),
    confirmDialog(),
  ];

  const offerB = () => {
    const groups = { matched: [], choice: [] };
    for (const c of S.controls) (matchScope(c.scope).outcome === "matched" ? groups.matched : groups.choice).push(c);
    const ticked = S.controls.filter((c) => S.offer[c.id]?.ticked);
    const dialog = h("dialog", { class: "wide", "aria-labelledby": "offer-heading" },
      h("h2", { id: "offer-heading" }, "Apply Controls"),
      h("h3", {}, `Match this description (${groups.matched.length})`),
      h("div", { class: "offer" }, ...groups.matched.map(offerLine), groups.matched.length ? null : h("p", { class: "muted" }, "None.")),
      groups.choice.length ? [h("h3", {}, `Need a choice (${groups.choice.length})`), h("div", { class: "offer" }, ...groups.choice.map(offerLine))] : null,
      ...offerSummary(),
      h("div", { class: "dialog-actions" }, h("button", { type: "button", class: "primary", on: { click: () => { S.dialog = false; render(); } } }, "Done")));
    if (S.dialog) queueMicrotask(() => dialog.showModal());
    return formStub(
      h("div", { class: "count-line" }, h("h2", {}, "Controls"), h("button", { type: "button", on: { click: () => { S.dialog = true; render(); } } }, "Apply Controls")),
      ticked.length ? h("ul", { class: "fanout" }, ...ticked.map((c) => {
        const m = resolvedFor(c);
        return h("li", {}, "☑ ", h("b", {}, c.name), ` → ${m.hit ? m.hit.name : "the application"}`, c.scope.type === "boundary" ? ` (${m.resolved.filter((r) => r.ticked).length} elements)` : "");
      })) : h("p", { class: "hint" }, "No Control applies yet."),
      S.controls.length - ticked.length ? h("p", { class: "hint" }, `${S.controls.length - ticked.length} Controls do not apply: ${S.controls.filter((c) => !S.offer[c.id]?.ticked).map((c) => c.name).join(", ")}.`) : null,
      conflicts().length ? h("div", { class: "refusal", role: "alert" }, submission().refused) : null,
      dialog);
  };

  const reportB = () => [...reportHead(), ...reportCopy().map((entry) => h("div", { class: "control-card" },
    h("h3", {}, entry.control.name),
    h("p", { class: "scope-line" }, matchLine(entry)),
    h("div", { class: "chips" }, ...entry.rows.map((row) => h("span", { class: `chip ${row.outcome}` }, `${row.framework} ${row.unit}: ${row.outcome}`))),
    h("details", {}, h("summary", {}, `Facts (${entry.facts.length})`), h("ul", { class: "fanout" }, ...entry.facts.map(factLine))),
    ...entry.rows.map((row) => h("details", { open: row.outcome !== "no-finding" }, h("summary", {}, unitText(row), " ", tag(row.outcome, row.outcome)), ...rowDetail(entry, row))))),
  reportCopy().length ? null : h("p", { class: "muted" }, "This analysis applied no Control.")];

  // ---- variant C: grouped by what they protect; offer on the model; rows by outcome --

  const groupsC = () => {
    const groups = new Map();
    for (const c of S.controls) {
      const key = scopeText(c.scope);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(c);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  };

  const inlineC = (c) => {
    const editing = S.editing === c.id;
    return h("li", {}, h("details", { open: editing }, h("summary", {}, h("b", {}, c.name), " ", h("span", { class: "sub" }, summaryBits(c))),
      editing ? editorForm(S.draft, () => { S.editing = null; S.draft = null; S.problem = null; render(); }) : [
        c.facts.length ? h("p", {}, h("span", { class: "sub" }, "Proves: "), c.facts.map((f) => `${f.attribute} = ${f.value}`).join("; ")) : null,
        c.rows.length ? h("div", { class: "chips" }, ...c.rows.map((r) => h("span", { class: "chip" }, unitText(r)))) : null,
        c.evidence.length ? h("p", {}, h("span", { class: "sub" }, "Evidence: "), c.evidence.map((f) => `${f.name} (${CLASS_LABEL[f.class]})`).join(", ")) : null,
        h("p", {}, h("button", { type: "button", class: "quiet", on: { click: () => startEdit(c.id) } }, "Edit"), " ",
          h("button", { type: "button", class: "quiet", on: { click: () => askDelete(c.id) } }, "Delete")),
      ]));
  };

  const controlsC = () => [
    h("div", { class: "count-line" }, h("h1", {}, "Controls"),
      h("span", {}, h("button", { type: "button", class: "primary", disabled: atLimit() || S.editing === "new", on: { click: () => startEdit("new") } }, "Record a protection"))),
    introLine(), countLine(), noticeBox(),
    atLimit() ? h("div", { class: "refusal" }, LIMIT_TEXT()) : null,
    S.editing === "new" ? editorForm(S.draft, () => { S.editing = null; S.draft = null; S.problem = null; render(); }) : null,
    ...groupsC().map(([key, list]) => h("div", { class: "group" }, h("h2", {}, key, h("span", { class: "sub" }, `${list.length}`)), h("ul", {}, ...list.map(inlineC)))),
    S.controls.length ? null : h("p", { class: "muted" }, "No Controls yet."),
    confirmDialog(),
  ];

  // The offer on the model: each element lists the Controls that reach it.
  const offerC = () => {
    const perElement = new Map(S.model.elements.map((e) => [e.id, []]));
    const unmatched = [];
    const application = [];
    for (const c of S.controls) {
      const m = resolvedFor(c);
      if (m.outcome !== "matched") { unmatched.push(c); continue; }
      if (c.scope.type === "application") { application.push(c); continue; }
      for (const r of m.resolved) perElement.get(r.element.id)?.push({ control: c, r });
    }
    const line = (c, r) => {
      const entry = S.offer[c.id];
      return h("div", { class: "ctl" },
        h("input", { type: "checkbox", checked: entry.ticked && (r ? r.ticked : true), disabled: r && !r.ticked, "aria-label": c.name, on: { change: (e) => { entry.ticked = e.target.checked; S.sent = null; render(); } } }),
        h("span", {}, h("b", {}, c.name), r?.reason ? h("div", { class: "sub" }, r.reason) : null,
          c.facts.length ? h("div", { class: "sub" }, c.facts.map((f) => `${f.attribute} = ${f.value}`).join("; ")) : null));
    };
    return formStub(
      h("h2", {}, "Controls on this description"),
      h("p", { class: "hint" }, "The description names these parts. Each part lists the Controls that reach it. A zone Control is ticked on each part the description places in that zone."),
      application.length ? h("ul", { class: "elements" }, h("li", {}, h("div", { class: "head" }, h("b", {}, "The whole application")), ...application.map((c) => line(c, null)))) : null,
      h("ul", { class: "elements" }, ...S.model.elements.map((e) => h("li", {},
        h("div", { class: "head" }, h("b", {}, e.name), h("span", { class: "sub" }, TYPE_LABEL[e.type]), e.zone ? tag(e.inferred ? `${e.zone} (inferred)` : e.zone) : tag("no zone")),
        ...(perElement.get(e.id).length ? perElement.get(e.id).map(({ control, r }) => line(control, r)) : [h("div", { class: "sub" }, "No Control reaches this part.")])))),
      unmatched.length ? [h("h3", {}, "Controls that match nothing here"), h("div", { class: "offer" }, ...unmatched.map(offerLine))] : null,
      ...offerSummary());
  };

  const reportC = () => {
    const copy = reportCopy();
    const rows = copy.flatMap((entry) => entry.rows.map((row) => ({ entry, row })));
    const shown = rows.filter(({ row }) => S.filter === "all" || row.outcome === S.filter);
    const count = (o) => rows.filter(({ row }) => row.outcome === o).length;
    return [...reportHead(),
      h("p", { class: "hint" }, `${copy.length} Controls applied · ${count("applied")} rows applied · ${count("refused")} refused · ${count("no-finding")} with no finding`),
      h("div", { class: "filters" }, ...["all", "applied", "refused", "no-finding"].map((f) => h("button", { type: "button", "aria-pressed": String(S.filter === f), on: { click: () => { S.filter = f; render(); } } }, f === "all" ? "All rows" : f))),
      h("table", { class: "grid-table" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Unit"), h("th", { scope: "col" }, "Outcome"), h("th", { scope: "col" }, "Control"), h("th", { scope: "col" }, "The ruling"))),
        h("tbody", {}, ...shown.map(({ entry, row }) => h("tr", {},
          h("td", {}, unitText(row)), h("td", {}, tag(row.outcome, row.outcome)),
          h("td", { class: "name" }, h("b", {}, entry.control.name), h("div", { class: "sub" }, matchLine(entry))),
          h("td", {}, ...rowDetail(entry, row)))))),
      h("details", { class: "group" }, h("summary", {}, "The facts each Control wrote"),
        ...copy.map((entry) => h("div", {}, h("b", {}, entry.control.name), h("ul", { class: "fanout" }, ...entry.facts.map(factLine))))),
    ];
  };

  // ---- switcher and state ----------------------------------------------------------

  const link = (change) => `/controls?${new URLSearchParams({ variant: S.variant, view: S.view, scenario: S.scenario, ...change })}`;

  const PAGES = {
    A: { controls: controlsA, offer: offerA, report: reportA },
    B: { controls: controlsB, offer: offerB, report: reportB },
    C: { controls: controlsC, offer: offerC, report: reportC },
  };

  const render = () => {
    history.replaceState(null, "", link({}));
    document.getElementById("variant").replaceChildren(...[PAGES[S.variant][S.view]()].flat(Infinity).filter(Boolean));
    document.getElementById("variant-label").textContent = `${S.variant} (${VARIANTS[S.variant]})`;
    document.getElementById("view").value = S.view;
    const nav = document.getElementById("controls-nav");
    nav.href = link({ view: "controls" });
    if (S.view === "controls") nav.setAttribute("aria-current", "page"); else nav.removeAttribute("aria-current");
    document.querySelector("#sidebar-navigation .new-analysis").href = link({ view: "offer" });
    document.getElementById("proto-state").textContent = JSON.stringify({
      variant: S.variant, view: S.view, scenario: S.scenario,
      controls: S.controls.slice(0, 5),
      controls_count: S.controls.length,
      submission: submission(),
      report_copy: reportCopy().map((e) => ({ control_id: e.control.id, outcome: e.match.outcome, elements: e.ticked.map((r) => r.element.id), facts: e.facts.map((f) => [f.attribute, f.state]), rows: e.rows.map((r) => [r.framework, r.unit, r.outcome, r.claims ?? []]) })),
    }, null, 2);
  };

  const go = (change) => {
    const keep = !("scenario" in change);
    Object.assign(S, change);
    if (keep && S.controls) {
      S.editing = null; S.draft = null; S.problem = null; S.notice = null; S.confirm = null; S.dialog = false;
    } else {
      reset();
    }
    render();
  };

  const keys = Object.keys(VARIANTS);
  const step = (by) => go({ variant: keys[(keys.indexOf(S.variant) + by + keys.length) % keys.length] });
  document.getElementById("prev").addEventListener("click", () => step(-1));
  document.getElementById("next").addEventListener("click", () => step(1));
  const view = document.getElementById("view");
  view.replaceChildren(...Object.entries(VIEWS).map(([key, label]) => h("option", { value: key }, `View: ${label}`)));
  view.addEventListener("change", () => go({ view: view.value }));
  const scenario = document.getElementById("scenario");
  scenario.replaceChildren(...Object.entries(SCENARIOS).map(([key, label]) => h("option", { value: key }, `Scenario: ${label}`)));
  scenario.value = S.scenario;
  scenario.addEventListener("change", () => go({ scenario: scenario.value }));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
  document.getElementById("controls-nav").addEventListener("click", (event) => { event.preventDefault(); go({ view: "controls" }); });
  document.querySelector("#sidebar-navigation .new-analysis").addEventListener("click", (event) => { event.preventDefault(); go({ view: "offer" }); });

  reset();
  render();
})();
