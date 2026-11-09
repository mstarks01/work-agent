# 127. A Control is a row with evidence objects, and a job copies it

- **Status**: accepted
- **Date**: 2026-11-03
- **Effort**: [#2119](https://github.com/mstarks01/work-agent/issues/2119), on the
  Controls map [#2111](https://github.com/mstarks01/work-agent/issues/2111)
- **Relates to**: [ADR 0065](0065-a-job-record-splits-into-rows-and-two-objects.md),
  which splits a job into rows and objects;
  [ADR 0118](0118-an-owner-file-is-an-object-and-a-row.md) and
  [ADR 0119](0119-a-source-names-the-owner-file-it-copies.md), whose store,
  delete and copy rules this decision follows

## Context

A Control is an owner's durable record (ADR 0123). It carries a scope with
aliases, a list of facts, a list of rows and a list of evidence files, and one
file can be a configuration file with a credential in it. The `jobs` row keeps
lists of that size as JSON text columns, and a job's large text lives in two
objects, `<owner-id>/<job-id>/sources.json` and
`<owner-id>/<job-id>/report.json`. An Owner File row holds a Source
kind only, and the Files page lists Sources.

## Decision

**A Control is one row in a `controls` table of its own.** The row holds the
ID, the owner ID, the name, the time of the last save, and the authored
document as JSON text. The ID is `control-<32 hex>`. `UNIQUE (owner_id, name)`
is the one reader of the name rule, and the memory store holds the same check.
The Files page never lists an evidence file.

**Each evidence file is one object under the Control.** The key is
`<owner-id>/<control-id>/<evidence-id>`, with the evidence ID
`evidence-<32 hex>`. A `ControlKey` joins `JobKey` and `FileKey`, and
`key_parts` tells a Control key from a job key by the second part's prefix. An
upload writes the object first and the row second, and a read checks the
digest. The document lists each file's name, class, SHA-256 and size. The
orphan sweep keeps an evidence key only where the owner's row lists its ID.

**Three limits in `resilience.toml`.** `max_controls` (default 50) bounds the
owner's rows, `max_control_evidence` (default 10) bounds the evidence files on
one Control, and one evidence file is at most `max_source_bytes`. The insert
counts and writes in one transaction, as `add_file` does. The facts, the rows
and the aliases take fixed code bounds.

**An evidence file is plain text with a stated class, and no credential.**
The text passes the plain text rules an Owner File passes, through the same
`CONTROL_CHARACTER` check, and no format table row exists, because nothing
renders the file as a Source. The name passes `plain_name` and is unique within
the Control. The owner states the class. One neutral declaration,
`EvidenceClass`, a Literal of `config`, `code`, `people` beside
`CARRIED_EVIDENCE_KINDS`, feeds the ASVS `needs_evidence` Literal, so the two
cannot drift. One table in code holds the credential shapes the service knows:
a private key block, a cloud access key ID, a vendor token prefix, and an
assignment of a password, key, secret or token name to a literal value. In a
settings file, such as a `.env` or an `.ini` file, such a name at the start of
a line counts with any plain value; an empty value, a reference, a mask and a
comment do not, and a quoted value counts as it does in any file.
`credential_hit` reads the table and the settings rule. A hit refuses the
upload and names the line. The table is a list of shapes and does not find
every credential, so the page asks the owner to remove each one before an
upload, and a refusal says that the file looks like it holds one. The service
redacts nothing, because a redacted file is a file the owner did not upload.
The #484 code scan reuses `credential_hit` or replaces it in that one place.

**A job copies each applied Control into a third object,
`<owner-id>/<job-id>/controls.json`.**
The name joins `ObjectName`, and the row gets a nullable `controls_sha256`. A
job with no Control writes no object. The object holds one entry per Control
that the submission named: the document as copied, each evidence file's name,
class, SHA-256 and text, and each row with its version check. The store
writes the object once, at the submission, as it writes the sources. What
the job derives later, the resolution of each scope (ADR 0124) and the facts
with their state (ADR 0125), is a column of the job row: a rewrite of the
object must delete it before the put, and a read during the save would see
a digest failure. The rulings of the rows stay in the report (ADR 0126). The
critic's excerpts are copied from this
object, never from the owner's row. A Resumed Job and an Amendment copy the
parent's Controls object with no store read; a follow-up job with
`answers_from` reads the store again.

**An edit replaces in place, a delete keeps nothing, and nothing expires.** An
edit replaces the row's document and `updated_at`, adds or removes evidence
objects, and keeps no earlier version. An edit names the `updated_at` of the
version it read, and the store refuses it as `stale` where a save changed
the Control since. A reopened analysis lists "Controls used" from the job's
copy. A delete removes the row first and then each
evidence object, keeps no tombstone and no usage row, and is allowed while a
job uses the Control. A Control stays until the owner deletes it, and
`max_controls` keeps the store finite.

**`/v1` refuses a Control.** `JobSubmission` gets no `controls` and no
`control_scopes` key, so a `/v1` body that sends one gets `422`. No `/v1`
route lists, creates or deletes a Control. A `/v1` job writes no
Controls object, and its report carries no Controls section.

**The methods are on `JobStore`**: `add_control`, `list_controls`,
`get_control`, `replace_control`, `delete_control` and `read_evidence`, each
taking the owner ID and checking it on the row before any object read. The
`controls` table and the `controls_sha256` column join `r0001` in place.

## Considered options

- **A second kind set on `owner_files`.** Rejected: `_uploaded_kind`,
  `KIND_FORMATS` and the Files checklist each gain a second meaning.
- **The document as a third object beside the evidence objects.** Rejected: a list page then
  reads an object per row for a document the size of `answers`.
- **The copy as a JSON column on the `jobs` row.** Rejected: a megabyte read on
  every list of analyses, which ADR 0065 moved to objects.
- **A version row on an edit.** Rejected: a record the owner never asked for;
  the copy holds the digests a later compare would read.
- **A tombstone after a delete.** Rejected: the report reads the copy, and
  ADRs 0066 and 0118 reject a record the owner asked to remove.
- **Redaction in place.** Rejected: the owner cannot see what was cut.
- **Any bytes with a MIME type.** Rejected: the excerpt copier reads lines.
- **A separate `ControlStore` and an `r0002`.** Rejected: two sweep listings,
  and a shim for a database that no deployment holds.

## Consequences

A database made before the change must be made again, the first-run SQLite
file included. The store contract suite gains the Control cases and runs on
both stores. Who classifies a document when the #484 role table and the owner
disagree, the authority of a `config` document, and what a `people` document
holds stay in the map's fog.
