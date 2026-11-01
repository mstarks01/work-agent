// The Files section: the operator's Owner Files (ADR 0118). A file name and
// each value the server sends reach the page as text nodes, never as markup.
(() => {
  const el = (id) => document.getElementById(id);
  const limits = JSON.parse(el("files_limits").textContent);
  const cell = (text, className) => {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  };
  let files = [];
  let pending = null;
  let deleting = null;

  const show = (box, text) => {
    el(box).textContent = text;
    el(box).hidden = !text;
  };
  const atLimit = () => files.length >= limits.max_owner_files;

  // The filters, as on the Analyses page: the search reads the name, and
  // each select lists the values of the format table.
  const option = (value, text) => {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = text;
    return item;
  };
  el("files-kind").append(...[...new Set(textFile.rows.flatMap((row) => row.kinds))]
    .map((kind) => option(kind, textFile.kindLabel(kind))));
  el("files-format").append(...textFile.rows.map((row) => option(row.format, row.label)));

  const draw = () => {
    el("files-count").textContent = `${files.length} of ${limits.max_owner_files} files`;
    el("upload-file-button").disabled = atLimit();
    if (atLimit()) show("files-problem", textFile.refusals.full);
    const text = el("files-search").value.toLocaleLowerCase();
    const kind = el("files-kind").value;
    const format = el("files-format").value;
    const shown = files.filter((file) => file.name.toLocaleLowerCase().includes(text)
      && (!kind || file.kind === kind)
      && (!format || file.format === format));
    el("files-table").hidden = !shown.length;
    el("files-empty").hidden = Boolean(shown.length);
    el("files-empty").textContent = files.length
      ? "No files match your filters."
      : "No files yet. Upload a transcript or a Mermaid diagram.";
    el("files-list").replaceChildren(...shown.map((file) => {
      const row = document.createElement("tr");
      const added = document.createElement("time");
      added.dateTime = file.created_at;
      added.textContent = new Date(file.created_at).toLocaleDateString(undefined, { dateStyle: "medium" });
      const when = document.createElement("td");
      when.append(added);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "quiet";
      remove.textContent = "Delete";
      remove.setAttribute("aria-label", `Delete ${file.name}`);
      remove.addEventListener("click", () => {
        deleting = file;
        el("delete-question").textContent = `Delete “${file.name}”?`;
        el("delete-dialog").showModal();
      });
      const actions = document.createElement("td");
      actions.append(remove);
      row.append(
        cell(file.name, "name"),
        cell(textFile.kindLabel(file.kind)),
        cell(textFile.formatLabel(file.format)),
        cell(textFile.size(file.size_bytes), "size"),
        when,
        actions,
      );
      return row;
    }));
  };

  const load = async () => {
    try {
      const listed = await fetch("/workspace/files");
      if (!listed.ok) throw new Error();
      files = await listed.json();
    } catch {
      show("files-problem", "Could not load Files. Reload the page.");
      return;
    }
    draw();
  };

  // A chosen file is read and checked in the browser, then named and kinded
  // in the dialog. The server checks it again before it keeps it.
  el("upload-file-button").addEventListener("click", () => el("file-input").click());
  el("file-input").addEventListener("change", async () => {
    const [file] = el("file-input").files;
    el("file-input").value = "";
    if (!file) return;
    show("files-notice", "");
    if (atLimit()) {
      show("files-problem", textFile.refusals.full);
      return;
    }
    const read = await textFile.read(file, limits.max_bytes, "Files keeps");
    if (read.error) {
      show("files-problem", read.error);
      return;
    }
    show("files-problem", "");
    pending = { row: read.row, text: read.text, kind: read.row.default_kind };
    el("upload-facts").textContent = `File: ${file.name} · ${read.row.label} · ${textFile.size(new TextEncoder().encode(read.text).length)}`;
    el("upload-name").value = file.name.slice(0, limits.name_chars);
    el("upload-name").maxLength = limits.name_chars;
    const choices = read.row.kinds.map((kind) => {
      const label = document.createElement("label");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "upload-kind";
      radio.value = kind;
      radio.checked = kind === pending.kind;
      radio.addEventListener("change", () => { pending.kind = kind; });
      label.append(radio, ` ${textFile.kindLabel(kind)}`);
      return label;
    });
    const legend = el("upload-kinds").querySelector("legend");
    el("upload-kinds").replaceChildren(legend, ...choices);
    el("upload-kinds").hidden = read.row.kinds.length < 2;
    el("upload-fixed-kind").hidden = read.row.kinds.length > 1;
    el("upload-fixed-kind").textContent = `Kind: ${textFile.kindLabel(pending.kind)}. A ${read.row.label} file is always a ${pending.kind}.`;
    show("upload-problem", "");
    el("upload-dialog").showModal();
  });
  el("upload-cancel").addEventListener("click", () => {
    pending = null;
    el("upload-dialog").close();
  });
  el("upload-save").addEventListener("click", async () => {
    if (!pending) return;
    const name = el("upload-name").value;
    if (!name.trim()) {
      show("upload-problem", "Give the file a name.");
      return;
    }
    el("upload-save").disabled = true;
    try {
      const saved = await fetch("/workspace/files", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, kind: pending.kind, format: pending.row.format, text: pending.text }),
      });
      const body = await saved.json();
      if (!saved.ok) {
        show("upload-problem", body.message);
        return;
      }
      pending = null;
      el("upload-dialog").close();
      show("files-notice", `“${body.name}” is in Files.`);
      await load();
    } catch {
      show("upload-problem", "The connection was interrupted. Check Files before you upload again.");
    } finally {
      el("upload-save").disabled = false;
    }
  });

  el("delete-cancel").addEventListener("click", () => {
    deleting = null;
    el("delete-dialog").close();
  });
  el("delete-go").addEventListener("click", async () => {
    const file = deleting;
    deleting = null;
    el("delete-dialog").close();
    if (!file) return;
    try {
      const removed = await fetch(`/workspace/files/${encodeURIComponent(file.id)}`, { method: "DELETE" });
      if (!removed.ok && removed.status !== 404) {
        show("files-problem", (await removed.json()).message);
        return;
      }
    } catch {
      show("files-problem", "The connection was interrupted. Check Files before you delete again.");
      return;
    }
    show("files-problem", "");
    show("files-notice", `“${file.name}” is deleted. The analyses that used it keep their copy.`);
    await load();
  });

  el("files-search").addEventListener("input", draw);
  el("files-kind").addEventListener("change", draw);
  el("files-format").addEventListener("change", draw);

  load();
})();
