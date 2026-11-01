// The browser checks of an uploaded text file, for the direct upload of the
// form page and for the upload to Files. Only text leaves the page: the server
// never receives a file, a file name or a content type.
//
// The file's extension finds its row in the format table that the server
// renders into the page (ADR 0120). The checks then run cheapest first: the
// size before a byte is read, then a strict UTF-8 decode, then no control
// character other than a tab or a line break, then some text. The server holds
// the same control class in analysis_service.owner_files.CONTROL_CHARACTER,
// and it renders the refusal of each of its own checks into the page.
const textFile = (() => {
  const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/;
  const formats = JSON.parse(document.getElementById("source_formats").textContent);
  const refusals = JSON.parse(document.getElementById("file_refusals").textContent);
  const rows = Object.entries(formats).map(([format, row]) => ({ format, ...row }));
  const rowOf = (name) => rows.find((row) => row.extensions.some((extension) => name.toLowerCase().endsWith(extension)));
  const supported = rows.map((row) => `${row.label} (${row.extensions.join(", ")})`).join(" or ");
  // `reader` starts the refusal of a file with no row: "The page reads" or
  // "Files keeps". The result is { row, text } or { error }.
  const read = async (file, maxBytes, reader) => {
    const row = rowOf(file.name);
    if (!row) return { error: `${reader} only ${supported} files.` };
    if (file.size > maxBytes) return { error: refusals.too_large };
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
    } catch {
      return { error: "The page could not read this file as UTF-8 text. Upload a plain text file." };
    }
    if (CONTROL.test(text)) return { error: refusals.not_plain_text };
    if (!text.trim()) return { error: refusals.no_text };
    return { row, text };
  };
  const kindLabel = (kind) => kind.charAt(0).toUpperCase() + kind.slice(1);
  const size = (bytes) => (bytes < 1024 ? `${bytes} bytes` : `${(bytes / 1024).toFixed(1)} KB`);
  return { CONTROL, rows, refusals, read, kindLabel, size, formatLabel: (format) => formats[format].label };
})();
