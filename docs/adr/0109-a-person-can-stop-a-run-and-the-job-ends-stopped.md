# 109. A person can stop a run, and the job ends stopped

- **Status**: accepted
- **Date**: 2026-10-30
- **Effort**: [#2025](https://github.com/mstarks01/work-agent/issues/2025)
- **Relates to**: [ADR 0044](0044-a-resumed-job-starts-at-prepare.md),
  [ADR 0107](0107-a-delete-keeps-a-job-whose-child-is-in-flight.md)

## Context

A person cannot stop an analysis after it starts. The first-run app refuses
to delete a job that has not ended, so a run with a wrong description or a
wrong framework continues to its end or to the deadline. It spends model calls
in that time, and it holds the one slot of the app.

`execute_job` already ends a run early when the deadline expires. The
deadline scope cancels the graph, the provider calls in flight are dropped,
the job stores no report, and it keeps its token reservation.

## Decision

**A stop takes the path of the deadline.** A `StopSignal` ends the deadline
scope of one run at once, with `asyncio.Timeout.reschedule`. `execute_job`
reads the signal when the scope ends. A requested stop ends the job in the
new terminal status `stopped`, and a deadline still ends it `failed`. A stop
that arrives after the run returned changes nothing, and the job ends as its
run did.

**`stopped` means that a person stopped the job.** `failed` keeps its
meaning: a fault of the service. The job stores no error text. A stopped job
holds no slot, and it keeps its reservation, because nothing measured the
cancelled graph.

**A stopped Resumed Job does not lock its parent.** It read none of its
answers into a report, so `stopped` is in `NON_LOCKING_STATUSES`. The parent
takes answers again, and a paused parent waits again.

**Only the first-run app has a stop control** (maintainer's decision,
2026-10-30). The workspace keeps the task and the signal of each run that it
starts. `POST /workspace/runs/{run}/stop` stops the run of the analysis that
has not ended, and waits up to ten seconds for it to end. It answers `204`
when the run ended, `202` when the run still ends, and `409` when this
process runs no job of the analysis. The page labels `stopped` as "Stopped"
and `failed` as "Failed" (maintainer's decision, 2026-10-30).

## Considered options

- **Reuse `failed` with a new message.** Rejected: a count of failures could
  then not separate a fault of the service from a choice of a person.
- **A stop route on `/v1`.** Deferred: `/v1` runs each job in the process
  that admitted it, so a request to another process cannot reach the task. A
  stop there needs a stop flag in the store that the run reads between nodes.
- **A pause and a resume at any node.** Rejected for now: the run state is an
  in-memory session that ends with the run, and a job resumes only at
  `prepare` (ADR 0044).

## Consequences

`/v1` reports the `stopped` status, and its lifecycle lists it, but no `/v1`
route sets it.

A node body that runs in a thread continues for a short time after the stop,
as after a deadline.
