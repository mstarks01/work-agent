# 110. The first-run app migrates a SQLite job store

- **Status**: accepted
- **Date**: 2026-10-30
- **Effort**: [#2027](https://github.com/mstarks01/work-agent/issues/2027)
- **Relates to**: [ADR 0077](0077-the-first-run-app-holds-job-records-in-the-job-store.md)

## Context

The `sql` job store refuses a schema that is not at the latest revision, and
the service never migrates. An operator runs
`python -m analysis_service.migrations` as a deploy step, under a role that
may change the schema, and the service runs under a role that may not.

The first-run app uses the same rule. So a person who selects a SQLite store
must remember the migration step, and must run it again after each upgrade
that adds a revision. The Settings page also refuses a save that names a
SQLite file that does not exist yet, because the store checks the file when
it is built. On one machine, one person owns the SQLite file and runs the
app, so the reason for the separate role does not apply.

## Decision

**The first-run app migrates a database whose row allows it.** The row of
each database in `DATABASES` has a field `first_run_migrates`. It is true for
SQLite and false for PostgreSQL. The row of each job store in `JOB_STORES`
has a field `first_run`, the step that the first-run app runs before the
store is built. `build_store(env, first_run=True)` runs it after it builds
the object store, so the startup refuses settings in the same order as
before.

**The step migrates a new file and an older file** (maintainer's decision,
2026-10-30). It makes a missing file with mode `0600`, as the migration step
does. It migrates a database at an older revision to the latest one. A
database at the latest revision stays as it is. A database at a revision
that the tree does not hold stays as it is, and the startup check refuses it.
A database that cannot be read or migrated stops the startup with a store
error, and the log holds the cause.

**A migration writes one log line** that names the two revisions
(maintainer's decision, 2026-10-30).

**The Settings page runs the same step** when it checks a save, so a save can
name a SQLite file that does not exist yet. A save that the check then
refuses for another setting can leave a new, migrated file.

**The `/v1` service keeps the manual step**, on SQLite too (maintainer's
decision, 2026-10-30). It calls `build_store` without `first_run`.

## Considered options

- **Migrate only a new, empty file.** Rejected: after an upgrade a person
  meets the refusal that this change removes.
- **A branch on the driver name in the app.** Rejected: a field on the row
  answers for each database, and a new database must state it.

## Consequences

A person who selects a SQLite store installs the `sql` extra and makes the
private directories. The app does the rest. A PostgreSQL store still needs
the migration step before the app starts.
