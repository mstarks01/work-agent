# 65. A job record splits into rows and two objects

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1528](https://github.com/mstarks01/work-agent/issues/1528), on the
  persistent storage map [#1522](https://github.com/mstarks01/work-agent/issues/1522)
- **Relates to**: [ADR 0064](0064-an-owner-id-is-a-random-surrogate-for-a-principal.md),
  which defines the owner ID in each key
- **Amended by**: [ADR 0066](0066-a-deleted-job-leaves-a-usage-row.md),
  under which a delete or an expiry removes the content and keeps a usage
  row; [ADR 0095](0095-a-stored-part-that-does-not-parse-makes-a-job-unreadable.md),
  under which a job whose stored part does not parse is unreadable

## Context

A durable `JobStore` keeps job state in a database and large data in an object
store. A median record is 86 KB, and the report is 95% of it. A job has
exactly one report, which the service writes once. Corrections sit beside it,
and a follow-up is a new job (ADR 0054). An attestation signs the canonical
form of the parsed report (`docs/Report-Attestation.md`), so a report whose
content changes no longer verifies.
Object stores differ: conditional writes and object lock are not on every
provider.

## Decision

**The sources and the report are objects. Everything else is in rows.**

- The keys are `<owner-id>/<job-id>/sources.json` and
  `<owner-id>/<job-id>/report.json`. There is no revision segment and no
  version prefix.
- The checkpoint and the resumption are JSON text in the job row. The events
  are rows in their own table, keyed by the job ID and a sequence number.
- A JSON part is stored as text, never as `jsonb`. One column type then
  serves PostgreSQL and SQLite, and no query reads inside the JSON.
- Each job row stores the `SCHEMA_VERSION` that wrote the record. One
  function, `same_schema`, accepts only the exact current version, because a
  record of any other version can hold a shape that this service cannot
  parse ([#1708](https://github.com/mstarks01/work-agent/pull/1708)). A route
  that must parse a record of another version refuses it. A read of the job
  row parses no part that the version covers until the function accepts the
  version. These parts are the objects, the checkpoint, the resumption, the
  answers, the fact keys, the framework selection and the node runs.
  `RECORD_FIELDS` states for each field of a record whether the version
  covers it, and `envelope_parts` reads that table. The version does not
  cover the events, the validation issues, the error or the
  certification, because the status route and the report gate read them
  for a record of any version. A read of a stored answer keeps the value
  that the service wrote when it admitted the answer, so a change to the
  wording of a question never makes a record unreadable. No migration rewrites a stored report, checkpoint or sources
  object ([#1532](https://github.com/mstarks01/work-agent/issues/1532)).
- The row holds the SHA-256 of each object. A read checks it and fails closed
  on a mismatch.
- The service writes the object first and the row second. It deletes the
  object when `reserve` refuses the job. A sweep deletes each object that no
  row names and that is older than a grace period.
- The service checks the owner on the row before it reads an object, and it
  builds each key only from the row.

## Considered options

- **The whole record in the database.** Rejected: the report would make the
  database large and its backups slow, and it gives no named files.
- **The whole record as objects.** Rejected: admission and answer rounds need
  transactions, and object stores have no transaction across two objects.
- **The row first, then the object.** Rejected: a reader could then find a row
  that names a missing object. An orphan object harms no reader.

## Consequences

The service, not the object store, enforces write-once. A bucket lifecycle
rule cannot remove orphans, because it does not know the rows.
