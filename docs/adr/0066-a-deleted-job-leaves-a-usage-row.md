# 66. A deleted job leaves a usage row

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1530](https://github.com/mstarks01/work-agent/issues/1530), on the
  persistent storage map [#1522](https://github.com/mstarks01/work-agent/issues/1522)
- **Relates to**: [ADR 0065](0065-a-job-record-splits-into-rows-and-two-objects.md),
  which sets the order of the writes this ADR reverses

## Context

An owner can hard-delete a job, and a retention period expires jobs. But
admission reads job records. The per-owner rate limit and both token budgets
count the jobs inside the current window, and the rule that a job takes one
follow-up (ADR 0054) reads the child jobs. If a delete removed the whole
record, an owner could delete jobs to reset the limits or to get a second
follow-up.

## Decision

**A delete or an expiry removes all content and keeps a usage row.** The
content is the sources, the report, the checkpoint, the answers, the
corrections and the events. The usage row holds only what admission reads: the
owner ID, the creation time, the reserved and measured tokens, the status and
the parent job ID. No route reads a usage row, and the job reads as 404. The
service removes the usage row when the budget window has passed and no parent
job still exists.

The service turns the row into a usage row in one transaction, then deletes the
objects. A sweep removes an object whose delete failed. Only a job in a
terminal status can be deleted.

## Considered options

- **Remove the whole record.** Rejected: it resets the rate limit, the token
  budgets and the one-follow-up rule.
- **A soft delete that hides the record.** Rejected: it keeps the content that
  the owner asked to remove.
