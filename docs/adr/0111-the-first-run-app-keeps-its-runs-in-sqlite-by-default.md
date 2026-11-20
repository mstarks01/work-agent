# 111. The first-run app keeps its runs in SQLite by default

- **Status**: accepted
- **Date**: 2026-10-30
- **Relates to**: [ADR 0077](0077-the-first-run-app-holds-job-records-in-the-job-store.md),
  which this amends; [ADR 0110](0110-the-first-run-app-migrates-a-sqlite-job-store.md),
  whose migration this default needs

## Context

Where no store was set, the first-run app used the `memory` store, so a
first run needed no setting. A restart lost every run. To keep the runs, a
person installed the `sql` extra, made two private directories and set four
variables, each with a command line. ADR 0110 removed the migration step, and
these steps stayed.

## Decision

**With no store setting, the app keeps its runs in SQLite** (maintainer's
decision, 2026-10-30). `store_defaults` gives `ANALYSIS_JOB_STORE=sql`, a
SQLite database `jobs.db` and a `filesystem` object root `objects` in the
data directory, `$XDG_DATA_HOME/work-agent` or `~/.local/share/work-agent`.
A default applies only where the selected rows read it, so an explicit
`memory` store or another object store gets no setting that its row refuses.

**The app makes the default directories.** `build_app_store`, the one builder
of the app's store, makes the data directory and the object root with mode
`0700` where the store uses them and they do not exist. It never makes a
directory that a setting names. It never changes the mode of a directory
that exists, and the private-file check then refuses one that a group or
others can open.

**`uv run` installs the SQLite packages.** The `web` dependency group carries
the `sql` extra, and the extra stays the one statement of its ranges.

**`memory` stays a choice.** A person who wants nothing kept on disk selects
it on the Settings page.

## Considered options

- **Keep `memory` as the default, and offer one choice on the Settings page
  that fills in the SQLite settings.** Rejected: the person must still
  restart the app after the save (ADR 0093), and a first run still loses its
  runs.

## Consequences

The app keeps each description, answer and report on disk by default, in a
directory that only its user can open, until the bound of 20 runs or a
delete removes it.

The tests run the app on `memory` where a test selects no store, because a
`TestClient` outside its `with` block stops a job task on the default store
when the request ends. A test of the default store asks for it.
