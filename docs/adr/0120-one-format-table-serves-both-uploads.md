# 120. One format table serves both uploads

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2061](https://github.com/mstarks01/work-agent/issues/2061), on the
  Owner File map [#2057](https://github.com/mstarks01/work-agent/issues/2057)
- **Relates to**: [ADR 0118](0118-an-owner-file-is-an-object-and-a-row.md), which
  stores the format of an **Owner File**; [ADR 0119](0119-a-source-names-the-owner-file-it-copies.md),
  which gives a **Source** no format field

## Context

The web app has two uploads. The direct upload adds a file to one analysis.
The Files upload stores an Owner File. Each upload needs one rule: which
extensions make which format, which kinds each format allows, and which kind
is the default. The browser needs the rule to choose the kind and to offer the
choice. The server needs the rule to refuse a kind that the format does not
allow.

No route takes a file, so no file name reaches the server. The name of an
Owner File comes from the file name by default, but the person can edit it.

On `main`, the rule is only in the browser. `first_run.js` makes a `.mmd` or
`.mermaid` file a `diagram` and each other file a `transcript`. The `accept`
attribute in `workspace.html` holds a second copy of the extensions. The
`accept` attribute only filters the file picker, so a person can upload
`notes.md` or a file with no extension, and the page makes it a transcript.

The `diagram` register in `sources.py` tells extraction to read nodes, arrows
and subgraphs. QA-2026-10-31-03 measured flowcharts only.

## Decision

**The table lives in `analysis_service/sources.py`, beside `SourceKind`.**
`SourceFormat` is a `Literal` of the format values. Each row holds the format
value, its extensions, the kinds it allows and its default kind:

| Format | Extensions | Kinds | Default kind |
| --- | --- | --- | --- |
| `text/plain` | `.txt`, `.vtt`, `.srt` | `description`, `transcript` | `transcript` |
| `text/vnd.mermaid` | `.mmd`, `.mermaid` | `diagram` | `diagram` |

A test checks that the default kind of each row is one of its kinds, that no
row allows `answers`, and that no extension is in two rows.

**The server renders the table into the page.** The web app renders the table
as JSON into the workspace page with `script_json`, the same as
`ANSWER_LIMITS`. It renders the `accept` attribute from the extensions in the
table. The JS reads the rendered table and keeps no copy.

**The browser sends the format of a Files upload.** The browser finds the row
from the file extension and sends the `format` value with the kind, the name
and the text. The request types `format` as `SourceFormat`, so the server
refuses a value that is not in the table. The server then refuses a kind that
the row does not allow. The direct upload sends a kind and no format, the same
as today.

**A `diagram` Source must be a Mermaid flowchart.** The check finds the first
line that is not blank, does not start with `%%` and is not in a leading `---`
front matter block. The first word of that line must be `flowchart` or
`graph`. The `Source` model runs the check for each `diagram` Source, so the
direct upload, the Files upload and `/v1` read one rule. The JS has no copy of
the check, and the page shows the server's message.

**The extension rule moves into the table.** The JS finds the row from the
file extension, without regard to case. The direct upload uses the default
kind of the row and offers no kind choice. The page refuses a file with no
row, and the message names the supported extensions. `DIAGRAM_FILE` and the
hand-written `accept` value go away.

## Considered options

- **A JS module holds a copy of the table, and a test compares it with the
  Python table.** Rejected: each copy is a second reader, and the `accept`
  attribute is then a third copy.
- **A new module for the table.** Rejected: the table reads `SourceKind`, and
  the flowchart check sits beside the `diagram` register.
- **The browser sends the extension, and the server finds the row.**
  Rejected: then the extension lookup runs in the JS and in the Python.
- **The format with no syntax check.** Rejected: the server then stores a
  `diagram` that the register cannot read.
- **Any Mermaid diagram type passes.** Rejected: the register and
  QA-2026-10-31-03 cover flowcharts only.
- **Only the Files upload runs the flowchart check.** Rejected: that is a
  second rule, and a direct upload then carries a text that Files refuses.
- **A file with no row falls back to `text/plain`.** Rejected: the table is
  then not a closed set, and each text file passes without a row.
- **A kind choice on the direct upload.** Rejected: #2058 keeps the direct
  upload as it is.

## Consequences

A new format is a new row in the table. The page gets the row with no JS edit.

The server cannot prove a format from the text, because both formats are
UTF-8 text. So the format check only finds a request where the kind and the
format disagree. A later image format needs a byte check on the server, and
that row adds it.

`/v1` refuses a `diagram` Source that is not a flowchart. The `diagram` kind
merged in #2056 on 2026-10-31, and the build changes `docs/HTTP-API.md`. A
stored `diagram` Source that fails the check also fails to parse when a job
reads it. Only a record from 2026-10-31 can hold such a Source. A test runs
the check on each file in `evals/diagrams/` and on the output of
`mermaid.py`.

The direct upload refuses a file that it accepts today, such as `notes.md` or
a file with no extension. The direct upload shows a flowchart error when the
person selects "Prepare analysis", not when the person selects the file. The
Files upload shows the error at the upload.
