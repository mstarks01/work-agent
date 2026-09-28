# 49. A SQLite job store keeps jobs across a restart

- **Status**: accepted
- **Date**: 2026-09-28
- **Effort**: [#1282](https://github.com/mstarks01/work-agent/issues/1282)
- **Relates to**: [ADR 0045](0045-a-job-that-asks-pauses-after-the-assertion-pass.md),
  whose pause this makes durable; [ADR 0007](0007-per-caller-concurrency-ceiling.md),
  whose ceiling the store enforces

## Context

A job that asks questions pauses and waits for a person with no time limit.
The only store was `memory`, so a restart lost every paused job, its model, its
catalog and its extraction. Every deployment can pause since #1271. No
deployment target is chosen yet: container and hosting packaging are out of
scope, so nothing yet needs a store that several hosts share.

## Decision

**A `sqlite` store, in one file, for one process.** `ANALYSIS_JOB_STORE=sqlite`
selects it and `ANALYSIS_JOB_STORE_PATH` names the file; an unset path stops
startup. SQLite is in the standard library, so the store adds no dependency.

**One process at a time.** The store holds an exclusive lock on a file beside
the database, and a second process that opens the same path stops at startup.

**A restart ends the jobs it interrupted.** When the store opens, a job it
holds as `queued` or `running` belonged to a process that stopped, and no task
will run it. It ends as `failed` through `JobRecord.interrupt`, keeps its
token reservation, and tells the caller to submit it again. The lifecycle
gains the one edge that needs, `queued -> failed`. A paused, completed, failed
or rejected job stays as it was.

**One reader of the admission rules.** The bounds move from the `memory`
store's `reserve` into `jobs.admit`, and both stores call it inside their own
atomic step. The `sqlite` store's step is one `BEGIN IMMEDIATE` transaction.
`tests/test_jobs.py` runs the whole store contract on both stores.

**The file holds what callers submitted.** Sources, answers and reports rest
in it, so the store creates it readable and writable by its owner only, and
every statement binds its values.

## Consequences

A deployment with more than one host still needs a shared backend, such as
Postgres, as a new registry entry that calls `jobs.admit` inside its own
transaction.

The service neither encrypts the file nor deletes old jobs. The operator's
storage policy protects it, and a retention rule is a separate decision.

A job's whole record is read to answer `events_after`, without its report,
which the store keeps in its own column. That costs one parse a poll.
