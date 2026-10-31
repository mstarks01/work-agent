// PROTOTYPE for ticket #2062: three layouts of the Files section. Never merges.
//
// Three variants, switchable via ?variant=A|B|C, two views via ?view=files|form,
// and four scenarios via ?scenario=. State lives in this page only.
//
// Untrusted text (a file name, an Owner File name, a file's text) reaches the
// page only through text nodes and input values, never through innerHTML. One
// seed file has markup in its name and its text to show it.

(() => {
  const { formats, limits, page_labels: pageLabels } = PROTO;
  const VARIANTS = {
    A: "Files page, form above a table; menu picker",
    B: "Files list with a detail panel; checklist picker",
    C: "No Files page: one Files dialog from the form",
  };
  const SCENARIOS = {
    normal: "Five files",
    empty: "No files yet",
    "at-limit": "100 files, at the count limit",
    gone: "A chosen file was deleted in another tab",
  };
  const KIND_LABEL = { description: "Description", transcript: "Transcript", diagram: "Diagram" };
  const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/;
  const encoder = new TextEncoder();

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
  const bytes = (text) => encoder.encode(text).length;
  const size = (n) => (n < 1024 ? `${n} bytes` : `${(n / 1024).toFixed(1)} KB`);
  const day = (iso) => new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  const formatRow = (format) => formats.find((row) => row.format === format);
  const rowForName = (name) => {
    const lower = name.toLowerCase();
    return formats.find((row) => row.extensions.some((extension) => lower.endsWith(extension)));
  };
  const supported = formats
    .map((row) => `${row.label} (${row.extensions.join(", ")})`)
    .join(" or ");
  const accept = formats.flatMap((row) => row.extensions).join(",");
  const newId = () => "file-" + Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (b) => b.toString(16).padStart(2, "0")).join("");

  // ---- state -----------------------------------------------------------------

  const params = new URLSearchParams(location.search);
  const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);
  const S = {
    variant: pick(params.get("variant"), Object.keys(VARIANTS), "A"),
    view: pick(params.get("view"), ["files", "form"], "files"),
    scenario: pick(params.get("scenario"), Object.keys(SCENARIOS), "normal"),
  };

  const reset = () => {
    const seed = PROTO.seed.map((file) => ({ ...file, size: bytes(file.text) }));
    S.files = S.scenario === "empty" ? [] : seed;
    if (S.scenario === "at-limit") {
      for (let n = S.files.length + 1; n <= limits.max_owner_files; n += 1) {
        const text = `Interview ${n}: the operator restarts the queue by hand.\n`;
        S.files.push({
          id: newId(), name: `Interview ${n}.txt`, kind: "transcript", format: "text/plain",
          text, size: bytes(text), added: "2026-10-29T10:00:00Z",
        });
      }
    }
    S.draft = null;          // a file read and checked, not yet saved to Files
    S.problem = null;        // { where: "files" | "form", text }
    S.notice = null;
    S.selected = S.files[0]?.id ?? null;  // variant B's detail panel
    S.confirm = null;        // the ID a delete waits on
    S.menu = false;          // variant A's picker menu
    S.dialog = S.view === "files" && S.variant === "C";
    S.form = { name: "", description: "", uploads: { transcript: null, diagram: null }, picked: [] };
    S.sent = null;
    if (S.scenario === "gone") {
      const gone = S.files.find((file) => file.kind === "diagram");
      S.form.picked = [S.files[0].id, gone.id];
      S.files = S.files.filter((file) => file !== gone);
      S.gone = { [gone.id]: gone.name };  // the page still holds the name it showed
      S.view = "form";
      S.dialog = false;
    } else {
      S.gone = {};
    }
  };

  const fileById = (id) => S.files.find((file) => file.id === id);

  // ---- rules: a Files upload ------------------------------------------------

  const atLimit = () => S.files.length >= limits.max_owner_files;
  const LIMIT_TEXT = () => `Files holds ${limits.max_owner_files} files, the most it can keep.`
    + " Delete a file before you add another.";

  // The browser checks of the direct upload, in the same order, after the row.
  const checkFile = async (file, reader) => {
    const row = rowForName(file.name);
    if (!row) return { error: `${reader} only ${supported} files. “${file.name}” is not one of them.` };
    if (file.size > limits.max_source_bytes) {
      return { error: `This file is larger than the ${limits.max_source_bytes.toLocaleString()} bytes that one analysis can carry.` };
    }
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
    } catch {
      return { error: "The page could not read this file as UTF-8 text. Upload a plain text file." };
    }
    if (CONTROL.test(text)) return { error: "This file holds control characters, so it is not a plain text file." };
    if (!text.trim()) return { error: "This file holds no text." };
    return { draft: { fileName: file.name, text, row, name: file.name.slice(0, limits.name_chars), kind: row.default_kind } };
  };
  const readFile = async (file) => (atLimit() ? { error: LIMIT_TEXT() } : checkFile(file, "Files keeps"));

  // A STUB of the server's answer to a Files save: the name rules of ADR 0118,
  // the page labels, and the flowchart check of ADR 0120.
  const refuseSave = (draft) => {
    const name = draft.name.trim();
    if (!name) return "Give the file a name.";
    if ([...draft.name].length > limits.name_chars) return `The name has more than ${limits.name_chars} characters. Shorten it.`;
    if (atLimit()) return LIMIT_TEXT();
    if (S.files.some((file) => file.name === draft.name)) {
      return `You already have a file named “${draft.name}”. Give this file a different name.`;
    }
    if (pageLabels.includes(draft.name)) {
      return `The page uses the name “${draft.name}” for its own sources. Give this file a different name.`;
    }
    if (!draft.row.kinds.includes(draft.kind)) return "This kind is not allowed for this format.";
    if (draft.kind === "diagram") {
      const first = draft.text.split("\n").map((line) => line.trim())
        .find((line) => line && !line.startsWith("%%") && line !== "---");
      if (!/^(flowchart|graph)\b/.test(first ?? "")) {
        return "This Mermaid file is not a flowchart. Its first line must start with “flowchart” or “graph”.";
      }
    }
    return null;
  };

  const choose = async (file, where = "files") => {
    if (!file) return;
    const result = await readFile(file);
    S.problem = result.error ? { where, text: result.error } : null;
    S.draft = result.draft ?? null;
    S.notice = null;
    render();
  };

  const save = (where = "files") => {
    const refusal = refuseSave(S.draft);
    if (refusal) {
      S.problem = { where, text: refusal };
    } else {
      const file = {
        id: newId(), name: S.draft.name, kind: S.draft.kind, format: S.draft.row.format,
        text: S.draft.text, size: bytes(S.draft.text), added: new Date().toISOString(),
      };
      S.files.unshift(file);
      S.selected = file.id;
      S.notice = `“${file.name}” is in Files.`;
      S.draft = null;
      S.problem = null;
    }
    render();
  };

  const DELETE_TEXT = (file) => [
    `Delete “${file.name}”?`,
    "This removes it from Files. Each analysis that already used it keeps its own copy,"
      + " and its reports do not change. You cannot undo a delete.",
  ];

  const remove = (id) => {
    const file = fileById(id);
    S.files = S.files.filter((each) => each.id !== id);
    S.confirm = null;
    if (S.selected === id) S.selected = S.files[0]?.id ?? null;
    if (S.form.picked.includes(id)) S.gone[id] = file.name;
    S.notice = `“${file.name}” is deleted. The analyses that used it keep their copy.`;
    render();
  };

  // ---- rules: a submission --------------------------------------------------

  const heldUploads = () => Object.entries(S.form.uploads).filter(([, upload]) => upload);
  const sources = () => {
    const uploads = heldUploads();
    const picked = S.form.picked.map((id) => fileById(id)).filter(Boolean);
    const describe = S.form.description.trim() || (!uploads.length && !picked.length);
    return [
      ...(describe ? [{ kind: "description", label: "Pasted description", text: S.form.description }] : []),
      ...uploads.map(([kind, upload]) => ({ kind, label: upload.label, text: upload.text })),
      ...picked.map((file) => ({ kind: file.kind, label: file.name, owner_file_id: file.id, text: file.text })),
    ];
  };
  const usedBytes = () => sources().reduce((sum, source) => sum + bytes(source.text), 0);

  // A STUB of the server's answer to a submission.
  const refuseSubmit = () => {
    const missing = S.form.picked.filter((id) => !fileById(id));
    if (missing.length) {
      const name = S.gone[missing[0]] ?? missing[0];
      return `The file “${name}” is no longer in Files, so this analysis cannot use it.`
        + " Remove it from the analysis to continue.";
    }
    if (!S.form.name.trim()) return "Give the analysis a name.";
    const all = sources();
    if (all.length > limits.max_sources) {
      const files = S.form.picked.length;
      return `An analysis can use at most ${limits.max_sources} sources, and this one has ${all.length},`
        + ` with ${files} from Files. Remove ${all.length - limits.max_sources} to continue.`;
    }
    const total = usedBytes();
    if (total > limits.max_source_bytes) {
      return `The description and the files total ${total.toLocaleString()} bytes, over the`
        + ` ${limits.max_source_bytes.toLocaleString()} bytes that one analysis can carry.`
        + " Remove a file or shorten the description.";
    }
    return null;
  };

  const submit = () => {
    const refusal = refuseSubmit();
    S.problem = refusal ? { where: "form", text: refusal } : null;
    S.sent = refusal ? null : sources().map(({ text, ...rest }) => ({ ...rest, bytes: bytes(text) }));
    S.notice = refusal ? null : "PROTOTYPE: the page sends these sources and opens the questions.";
    render();
  };

  const togglePick = (id) => {
    if (S.form.picked.includes(id)) {
      S.form.picked = S.form.picked.filter((each) => each !== id);
      delete S.gone[id];
    } else {
      S.form.picked.push(id);
    }
    S.problem = null;
    render();
  };

  // A direct upload, as today: the row's default kind, no choice, not stored.
  const directUpload = async (file) => {
    if (!file) return;
    const { error, draft } = await checkFile(file, "The page reads");
    if (error) {
      S.problem = { where: "form", text: error };
    } else {
      const kind = draft.row.default_kind;
      S.form.uploads[kind] = { label: `Uploaded ${kind}`, text: draft.text, fileName: draft.fileName };
      S.problem = null;
    }
    render();
  };

  // ---- shared pieces --------------------------------------------------------

  const fileInput = (onFile) => h("input", {
    type: "file", accept, hidden: true,
    on: { change: (event) => { const [file] = event.target.files; event.target.value = ""; onFile(file); } },
  });
  const chooseButton = (label, onFile, attrs = {}) => {
    const input = fileInput(onFile);
    return [h("button", { type: "button", ...attrs, on: { click: () => input.click() } }, label), input];
  };
  const problemBox = (where) => (S.problem?.where === where
    ? h("div", { class: "refusal", role: "alert" }, S.problem.text) : null);
  const noticeBox = () => (S.notice ? h("div", { class: "notice", role: "status" }, S.notice) : null);
  const meta = (file) => `${KIND_LABEL[file.kind]} · ${formatRow(file.format).label} · ${size(file.size)}`;
  const intro = () => h("p", { class: "muted" },
    "Keep transcripts and diagrams here, and add them to any of your analyses."
    + " An analysis keeps its own copy of each file it uses.");
  const countLine = () => h("p", { class: "hint" },
    `${S.files.length} of ${limits.max_owner_files} files`);
  const formatHint = () => h("p", { class: "hint" },
    `${supported}, up to ${limits.max_source_bytes.toLocaleString()} bytes.`);

  // The name field and the kind choice for a checked file.
  const draftFields = (where) => {
    const d = S.draft;
    const kinds = d.row.kinds.length > 1
      ? h("fieldset", { class: "kind-choice" }, h("legend", { class: "field-label" }, "Kind"),
        d.row.kinds.map((kind) => h("label", {},
          h("input", {
            type: "radio", name: `kind-${where}`, checked: kind === d.kind,
            on: { change: () => { d.kind = kind; } },
          }), ` ${KIND_LABEL[kind]}`)))
      : h("p", {}, h("span", { class: "field-label" }, "Kind "), `${KIND_LABEL[d.kind]} (a ${d.row.label} file is always a ${KIND_LABEL[d.kind].toLowerCase()})`);
    return [
      h("p", { class: "hint" }, `File: ${d.fileName} · ${d.row.label} · ${size(bytes(d.text))}`),
      h("label", { class: "field-label", for: `name-${where}` }, "Name"),
      h("input", {
        type: "text", id: `name-${where}`, maxlength: limits.name_chars, value: d.name, autocomplete: "off",
        on: { input: (event) => { d.name = event.target.value; } },
      }),
      h("p", { class: "hint" }, "An analysis shows this name as the source of each quote it takes from the file."),
      kinds,
    ];
  };

  const confirmDialog = () => {
    if (!S.confirm) return null;
    const file = fileById(S.confirm);
    const [question, detail] = DELETE_TEXT(file);
    const dialog = h("dialog", { "aria-labelledby": "confirm-q" },
      h("p", { id: "confirm-q" }, h("strong", {}, question)), h("p", {}, detail),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", on: { click: () => { S.confirm = null; render(); } } }, "Cancel"),
        h("button", { type: "button", class: "danger", on: { click: () => remove(file.id) } }, "Delete")));
    dialog.addEventListener("cancel", () => { S.confirm = null; });
    queueMicrotask(() => dialog.showModal());
    return dialog;
  };

  // The analysis form, as the workspace page shows it, with a slot for a picker.
  const analysisForm = ({ tools = [], below = [], beforeActions = [] }) => {
    const remaining = h("p", { class: "hint", role: "status" });
    const updateRemaining = () => {
      const left = limits.max_source_bytes - usedBytes();
      remaining.textContent = left < 0
        ? "The description and the files are too long. Remove a file or shorten the description."
        : `Up to ${left.toLocaleString()} bytes remaining · ${sources().length} of ${limits.max_sources} sources`;
      remaining.classList.toggle("field-error", left < 0);
    };
    updateRemaining();
    const uploadNotes = heldUploads().map(([kind, upload]) => h("p", { class: "hint" },
      `${KIND_LABEL[kind]}: ${upload.fileName} `,
      h("button", { type: "button", class: "quiet", on: { click: () => { S.form.uploads[kind] = null; render(); } } }, "Remove")));
    const [uploadButton, uploadInput] = chooseButton("+", directUpload, {
      class: "quiet upload-icon", "aria-label": "Upload a transcript or a Mermaid diagram", title: "Upload a transcript or a Mermaid diagram",
    });
    return h("form", { class: "entry", on: { submit: (event) => { event.preventDefault(); submit(); } } },
      h("h1", {}, "New analysis"),
      h("label", { class: "field-label", for: "analysis-name" }, "Analysis name"),
      h("input", {
        id: "analysis-name", value: S.form.name, placeholder: "For example, Customer portal", autocomplete: "off",
        on: { input: (event) => { S.form.name = event.target.value; } },
      }),
      h("label", { class: "field-label", for: "description" }, "What are you building?"),
      h("div", { class: "composer" },
        h("textarea", {
          id: "description", value: S.form.description, placeholder: "Describe each part of your system and how the parts connect.",
          on: { input: (event) => { S.form.description = event.target.value; updateRemaining(); } },
        }),
        h("div", { class: "composer-tools" }, uploadButton, uploadInput, tools)),
      remaining, uploadNotes, below, beforeActions,
      problemBox("form"), noticeBox(),
      h("div", { class: "entry-actions" }, h("button", { type: "submit", class: "primary" }, "Prepare analysis")));
  };

  const pickedLine = (id) => {
    const file = fileById(id);
    const label = file ? `${file.name}` : `${S.gone[id]} (deleted)`;
    return h("li", {}, h("span", { class: "tag" }, file ? KIND_LABEL[file.kind] : "Missing"),
      h("span", { class: "n" }, label), file ? h("span", { class: "hint" }, size(file.size)) : null,
      h("button", { type: "button", class: "quiet", on: { click: () => togglePick(id) } }, "Remove"));
  };

  // ---- variant A: a Files page, the upload form above a table ---------------

  const filesA = () => {
    const upload = S.draft
      ? h("div", { class: "upload-form" }, h("h2", {}, "Add a file"), draftFields("a"),
        h("div", { class: "line" },
          h("button", { type: "button", class: "primary", on: { click: () => save() } }, "Save to Files"),
          h("button", { type: "button", class: "quiet", on: { click: () => { S.draft = null; S.problem = null; render(); } } }, "Cancel")),
        problemBox("files"))
      : h("div", { class: "upload-form" }, h("h2", {}, "Add a file"),
        h("div", { class: "line" }, chooseButton("Choose a file", (file) => choose(file), { disabled: atLimit() })),
        formatHint(), atLimit() ? h("div", { class: "refusal" }, LIMIT_TEXT()) : problemBox("files"));
    const rows = S.files.map((file) => h("tr", {},
      h("td", { class: "name" }, file.name),
      h("td", {}, KIND_LABEL[file.kind]),
      h("td", {}, formatRow(file.format).label),
      h("td", { class: "num" }, size(file.size)),
      h("td", {}, h("time", { datetime: file.added }, day(file.added))),
      h("td", {}, h("button", {
        type: "button", class: "quiet", "aria-label": `Delete ${file.name}`,
        on: { click: () => { S.confirm = file.id; render(); } },
      }, "Delete"))));
    return [
      h("div", { class: "count-line" }, h("h1", {}, "Files"), countLine()),
      intro(), noticeBox(), upload,
      S.files.length
        ? h("div", { class: "analysis-table-wrap" }, h("table", { class: "files-table" },
          h("thead", {}, h("tr", {}, ["Name", "Kind", "Format", "Size", "Added", ""].map((t) => h("th", { scope: "col" }, t)))),
          h("tbody", {}, rows)))
        : h("p", { class: "muted" }, "No files yet. Add a transcript or a Mermaid diagram above."),
      confirmDialog(),
    ];
  };

  const formA = () => {
    const search = h("input", { type: "search", placeholder: "Find a file", "aria-label": "Find a file" });
    const items = S.files.map((file) => h("button", {
      type: "button", disabled: S.form.picked.includes(file.id), "data-name": file.name.toLowerCase(),
      on: { click: () => { S.menu = false; togglePick(file.id); } },
    }, h("strong", {}, file.name), h("br"), h("small", { class: "hint" }, meta(file))));
    search.addEventListener("input", () => items.forEach((item) => {
      item.hidden = !item.dataset.name.includes(search.value.toLowerCase());
    }));
    const menu = S.menu ? h("div", { class: "picker-menu", role: "dialog", "aria-label": "Add from Files" },
      S.files.length ? [search, items] : h("p", { class: "hint" }, "Files is empty."),
      h("p", { class: "hint" }, h("a", { href: link({ view: "files" }) }, "Open Files to add or delete a file"))) : null;
    if (menu) queueMicrotask(() => search.focus());
    const button = h("button", {
      type: "button", class: "quiet", "aria-expanded": String(S.menu), title: "Add from Files",
      on: { click: () => { S.menu = !S.menu; render(); } },
    }, "Add from Files");
    return analysisForm({
      tools: [button, menu],
      below: S.form.picked.length ? h("ul", { class: "picked", "aria-label": "Files in this analysis" }, S.form.picked.map(pickedLine)) : null,
    });
  };

  // ---- variant B: a list with a detail panel, upload in a dialog ------------

  const uploadDialogB = () => {
    if (!S.draft && S.problem?.where !== "dialog") return null;
    const close = () => { S.draft = null; S.problem = null; render(); };
    const dialog = h("dialog", { class: "files-dialog", "aria-labelledby": "upload-b" },
      h("h2", { id: "upload-b" }, "Upload a file"),
      S.draft ? draftFields("b") : null,
      problemBox("dialog"),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", on: { click: close } }, "Cancel"),
        S.draft ? h("button", { type: "button", class: "primary", on: { click: () => save("dialog") } }, "Save to Files") : null));
    dialog.addEventListener("cancel", close);
    queueMicrotask(() => dialog.showModal());
    return dialog;
  };

  const filesB = () => {
    const file = fileById(S.selected);
    const detail = file
      ? h("aside", { class: "detail", "aria-label": "File details" },
        h("h2", {}, file.name),
        h("dl", {},
          h("dt", {}, "Kind"), h("dd", {}, KIND_LABEL[file.kind]),
          h("dt", {}, "Format"), h("dd", {}, `${formatRow(file.format).label} (${file.format})`),
          h("dt", {}, "Size"), h("dd", {}, `${file.size.toLocaleString()} bytes`),
          h("dt", {}, "Added"), h("dd", {}, day(file.added))),
        h("pre", {}, file.text.slice(0, 4000)),
        S.confirm === file.id
          ? h("div", { class: "refusal" }, DELETE_TEXT(file).map((line) => h("p", {}, line)),
            h("div", { class: "dialog-actions" },
              h("button", { type: "button", on: { click: () => { S.confirm = null; render(); } } }, "Cancel"),
              h("button", { type: "button", class: "danger", on: { click: () => remove(file.id) } }, "Delete")))
          : h("button", { type: "button", class: "danger", on: { click: () => { S.confirm = file.id; render(); } } }, "Delete"))
      : null;
    const list = h("ul", { class: "lib-list" }, S.files.map((each) => h("li", {},
      h("button", {
        type: "button", class: "row", "aria-pressed": String(each.id === S.selected),
        on: { click: () => { S.selected = each.id; S.confirm = null; render(); } },
      }, h("span", { class: "n" }, each.name), h("span", { class: "hint" }, day(each.added)),
      h("span", { class: "m" }, meta(each))))));
    return [
      h("div", { class: "count-line" }, h("h1", {}, "Files"),
        h("span", {}, chooseButton("Upload a file", (f) => choose(f, "dialog"), { class: "primary", disabled: atLimit() }))),
      intro(), countLine(),
      atLimit() ? h("div", { class: "refusal" }, LIMIT_TEXT()) : null,
      noticeBox(), problemBox("files"),
      S.files.length ? h("div", { class: "lib" }, list, detail) : h("p", { class: "muted" }, "No files yet. Upload a transcript or a Mermaid diagram."),
      uploadDialogB(),
    ];
  };

  const formB = () => {
    const used = usedBytes();
    const missing = S.form.picked.filter((id) => !fileById(id));
    const checklist = h("fieldset", { class: "checklist" }, h("legend", {}, "Files for this analysis"),
      S.files.length
        ? S.files.map((file) => h("label", {},
          h("input", { type: "checkbox", checked: S.form.picked.includes(file.id), on: { change: () => togglePick(file.id) } }),
          h("span", { class: "n" }, file.name), h("span", { class: "hint" }, meta(file))))
        : h("p", { class: "hint" }, "Files is empty. ", h("a", { href: link({ view: "files" }) }, "Upload a file to Files")),
      missing.length ? h("ul", { class: "picked" }, missing.map(pickedLine)) : null,
      h("p", { class: "hint" }, h("a", { href: link({ view: "files" }) }, "Manage files")));
    const meter = h("div", {},
      h("label", { class: "field-label", for: "bytes-meter" }, `Size: ${used.toLocaleString()} of ${limits.max_source_bytes.toLocaleString()} bytes`),
      h("meter", { id: "bytes-meter", min: 0, max: limits.max_source_bytes, high: limits.max_source_bytes * 0.9, value: used }));
    return analysisForm({ below: [checklist, meter] });
  };

  // ---- variant C: no Files page, one dialog from the form -------------------

  const filesDialogC = () => {
    if (!S.dialog) return null;
    const close = () => { S.dialog = false; S.draft = null; S.confirm = null; if (S.problem?.where === "dialog") S.problem = null; render(); };
    const input = fileInput((file) => choose(file, "dialog"));
    const drop = h("div", {
      class: "drop",
      on: {
        dragover: (event) => { event.preventDefault(); drop.classList.add("over"); },
        dragleave: () => drop.classList.remove("over"),
        drop: (event) => { event.preventDefault(); choose(event.dataTransfer.files[0], "dialog"); },
      },
    }, atLimit()
      ? LIMIT_TEXT()
      : ["Drop a file here, or ", h("button", { type: "button", class: "quiet", on: { click: () => input.click() } }, "choose a file"), input,
        h("br"), h("small", { class: "hint" }, `${supported}, up to ${limits.max_source_bytes.toLocaleString()} bytes.`)]);
    const pending = S.draft ? h("div", { class: "upload-form" }, draftFields("c"),
      h("div", { class: "line" },
        h("button", { type: "button", class: "primary", on: { click: () => save("dialog") } }, "Save to Files"),
        h("button", { type: "button", class: "quiet", on: { click: () => { S.draft = null; S.problem = null; render(); } } }, "Cancel"))) : null;
    const rows = S.files.map((file) => h("tr", {},
      h("td", { class: "pick" }, h("input", {
        type: "checkbox", checked: S.form.picked.includes(file.id), "aria-label": `Use ${file.name} in this analysis`,
        on: { change: () => togglePick(file.id) },
      })),
      h("td", { class: "name" }, file.name, h("br"), h("small", { class: "hint" }, `${meta(file)} · ${day(file.added)}`)),
      h("td", {}, S.confirm === file.id
        ? h("span", {}, h("small", {}, DELETE_TEXT(file)[1]), " ",
          h("button", { type: "button", class: "danger", on: { click: () => remove(file.id) } }, "Delete"),
          h("button", { type: "button", class: "quiet", on: { click: () => { S.confirm = null; render(); } } }, "Keep"))
        : h("button", { type: "button", class: "quiet", "aria-label": `Delete ${file.name}`, on: { click: () => { S.confirm = file.id; render(); } } }, "Delete"))));
    const dialog = h("dialog", { class: "files-dialog", "aria-labelledby": "files-c" },
      h("div", { class: "count-line" }, h("h2", { id: "files-c" }, "Files"), countLine()),
      intro(), drop, pending, problemBox("dialog"), noticeBox(),
      S.files.length ? h("div", { class: "analysis-table-wrap" }, h("table", { class: "files-table" }, h("tbody", {}, rows))) : null,
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", class: "primary", on: { click: close } },
          `Done · ${S.form.picked.length} in this analysis`)));
    dialog.addEventListener("cancel", close);
    queueMicrotask(() => { if (!dialog.open) dialog.showModal(); });
    return dialog;
  };

  const formC = () => analysisForm({
    tools: h("button", { type: "button", class: "quiet", on: { click: () => { S.dialog = true; render(); } } }, "Files"),
    below: S.form.picked.length
      ? h("ul", { class: "picked", "aria-label": "Files in this analysis" }, S.form.picked.map(pickedLine))
      : h("p", { class: "hint" }, "Use the Files button to add a stored transcript or diagram."),
    beforeActions: filesDialogC(),
  });

  // ---- switcher and state ---------------------------------------------------

  const link = (change) => {
    const next = new URLSearchParams({ variant: S.variant, view: S.view, scenario: S.scenario, ...change });
    return `/files?${next}`;
  };

  const VIEWS = {
    A: { files: filesA, form: formA },
    B: { files: filesB, form: formB },
    C: { files: formC, form: formC },
  };

  const render = () => {
    history.replaceState(null, "", link({}));
    document.getElementById("variant").replaceChildren(...[VIEWS[S.variant][S.view]()].flat().filter(Boolean));
    document.getElementById("variant-label").textContent = `${S.variant} (${VARIANTS[S.variant]})`;
    document.getElementById("view").textContent = S.view === "files" ? "View: Files" : "View: analysis form";
    // The sidebar keeps the variant and the scenario.
    const filesNav = document.getElementById("files-nav");
    filesNav.href = link({ view: "files" });
    if (S.view === "files") filesNav.setAttribute("aria-current", "page");
    else filesNav.removeAttribute("aria-current");
    document.querySelector("#sidebar-navigation .new-analysis").href = link({ view: "form" });
    document.getElementById("proto-state").textContent = JSON.stringify({
      variant: S.variant, view: S.view, scenario: S.scenario,
      owner_files: S.files.slice(0, 6).map(({ text, ...row }) => row),
      owner_files_count: S.files.length,
      submission_sources: S.sent,
    }, null, 2);
  };

  const go = (change) => {
    const keepForm = !("scenario" in change);
    const form = S.form;
    Object.assign(S, change);
    if (keepForm && S.files) {
      S.dialog = S.view === "files" && S.variant === "C";
      S.draft = null; S.problem = null; S.notice = null; S.confirm = null; S.menu = false;
    } else {
      reset();
    }
    if (keepForm && form) S.form = form;
    render();
  };

  const keys = Object.keys(VARIANTS);
  const step = (by) => go({ variant: keys[(keys.indexOf(S.variant) + by + keys.length) % keys.length] });
  document.getElementById("prev").addEventListener("click", () => step(-1));
  document.getElementById("next").addEventListener("click", () => step(1));
  document.getElementById("view").addEventListener("click", () => go({ view: S.view === "files" ? "form" : "files" }));
  const scenario = document.getElementById("scenario");
  scenario.replaceChildren(...Object.entries(SCENARIOS).map(([key, label]) => h("option", { value: key }, `Scenario: ${label}`)));
  scenario.value = S.scenario;
  scenario.addEventListener("change", () => go({ scenario: scenario.value }));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
  // Sidebar links change the view in place, so the stub state stays.
  document.getElementById("files-nav").addEventListener("click", (event) => { event.preventDefault(); go({ view: "files" }); });
  document.querySelector("#sidebar-navigation .new-analysis").addEventListener("click", (event) => { event.preventDefault(); go({ view: "form" }); });

  reset();
  render();
})();
