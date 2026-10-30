# 107. A delete keeps a job whose child is in flight

- **Status**: accepted
- **Date**: 2026-10-30
- **Relates to**: [ADR 0066](0066-a-deleted-job-leaves-a-usage-row.md),
  [ADR 0106](0106-a-workspace-title-is-a-label-and-the-report-keeps-its-system-name.md)

## Context

`JobStore.delete` deletes a job that has ended (ADR 0066). An answer to a
finished or paused job, or an amendment of a paused job, starts a child job.
Admission of the child locks the parent's row, and it refuses the child where
the parent is gone (`parent_missing`).

The web app's delete route deletes each job of an analysis. It checks that
each job has ended, and then it deletes the jobs one at a time. An answer in
another tab can start a child between that check and the delete of the
parent. The delete of the parent then succeeds, the child runs with no
parent, and the workspace lists the child as its own analysis.

## Decision

**`JobStore.delete` keeps a job that has a child that holds a slot.** A child
holds a slot while it has not ended (`holds_a_slot`), the rule that admission
counts.

- Both stores check the children in the step that deletes. The `sql` store
  checks them under the row lock that admission takes on a child's parent. So
  if the child is admitted first, the delete keeps the parent. If the delete
  commits first, the admission finds no parent.
- `DELETE /v1/jobs/{id}` answers `409` for such a job, with a message that
  names the child.
- The web app's delete route answers `409` and stops. The jobs that it
  deleted before the refusal stay deleted.
- The retention sweep and the workspace bound skip such a job until its child
  ends, and delete it on a later pass.

## Considered options

- **A store option that only the web app's route sets.** Rejected: two
  delete rules in one store, and `/v1` keeps the same race.
- **A second listing after the deletes.** Rejected: it makes the window
  smaller and does not close it, and a running child still survives.

## Consequences

An owner cannot delete a job while its follow-up runs, through either
surface. A partial delete of an analysis is possible: the web app deletes
the newest jobs first, so the jobs that stay are the oldest, with the child
that stopped the delete.
