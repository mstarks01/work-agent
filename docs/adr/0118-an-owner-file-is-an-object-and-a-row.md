# 118. An Owner File is an object and a row

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2059](https://github.com/mstarks01/work-agent/issues/2059), on the
  Owner File map [#2057](https://github.com/mstarks01/work-agent/issues/2057)
- **Relates to**: [ADR 0065](0065-a-job-record-splits-into-rows-and-two-objects.md),
  which sets the same split for a job;
  [ADR 0066](0066-a-deleted-job-leaves-a-usage-row.md), whose usage row an
  Owner File does not get

## Context

An **Owner File** is a supporting file that an **Owner** keeps in the Files
section of the web app. It does not expire, and it stays until the owner
deletes it. Today each format is text, and one Owner File is at most
`max_source_bytes`. Each kind belongs to one row of the format table
(ADR 0120), so the kind of an Owner File gives its format.

The store keeps a job in rows and two objects (ADR 0065). `object_key` is the
one rule for a key, and the orphan sweep reads each key with `key_parts`.

## Decision

**The text is an object, and one row in `owner_files` names it.** The row
holds the ID, the owner ID, the name, the kind, the SHA-256 of the object,
the size in bytes and the time of the upload. The row stores no format,
because the kind gives it. An upload writes
the object first and the row second, and a read checks the digest. The store
checks the owner on the row before it reads the object.

**The ID is `file-<32 hex>`, and the key is `<owner-id>/<file-id>`.** The ID
carries its type, as a job ID does. A job key has three parts and a file key
has two, so the two shapes cannot match. `object_key` and `key_parts` accept
both shapes, and `key_parts` returns the type of each key.

**The name is a Source label.** The upload checks the name with the Source
label validator, because a job uses the name as the label. Two names are the
same only where the strings are equal. `UNIQUE (owner_id, name)` is the one
reader of uniqueness, and the memory store holds the same check.

**`max_owner_files` in `resilience.toml` bounds the count, and the insert
checks it.** The default is 100. The store counts the owner's rows and
inserts in one transaction. On SQLite, `BEGIN IMMEDIATE` serialises two
uploads. On PostgreSQL, the transaction takes a per-owner advisory lock
first.

**A delete removes the row, then the object, and keeps nothing.** No budget
counts Owner Files, and a `uuid4` ID is never given again, so no usage row
and no tombstone stay. A job keeps its own copy of the text as a Source, so
a delete is allowed while a job uses the Owner File.

**One orphan sweep covers both key types.** A refused insert deletes its
object at once. The sweep keeps a file key only where a row has that ID under
the same owner ID, and it uses the same grace period as a job key.

**The methods are on `JobStore`.** Both stores implement them, and the store
contract suite runs on both. One class then holds the database, the object
store and the sweep.

## Considered options

- **The text in a row only.** Rejected: a later format of bytes then moves
  every stored text into objects, or keeps a second store for one concept.
- **The key `<owner-id>/files/<file-id>`.** Rejected: no reader lists the
  Owner Files by prefix, and the key then has the same number of parts as a
  job key.
- **A name compare that ignores case or Unicode form.** Rejected: a job
  compares labels as exact strings, so a second rule would disagree with it.
- **A tombstone row after a delete.** Rejected: it keeps a record that the
  owner asked to remove, which ADR 0066 also rejects.
- **A separate `OwnerFileStore` protocol and a separate sweep.** Rejected:
  each sweep pass then lists the whole object store twice.

## Consequences

The schema change edits `r0001`, so a database made before it has no
`owner_files` table and must be made again. The orphan sweep reads two
tables.
