# 106. A workspace title is the workspace's own name, and the report keeps its system name

- **Status**: accepted
- **Date**: 2026-10-30
- **Ticket**: [#2008](https://github.com/mstarks01/work-agent/issues/2008)
- **Relates to**: [ADR 0066](0066-a-deleted-job-leaves-a-usage-row.md)

## Context

The web app's workspace lists each analysis by the `system_name` of its jobs.
The submitter types that name, and the **Report** embeds it. The operator
needs to rename an analysis after the run.

An analysis is a chain of jobs: the first job, and each **Resumed Job** and
**Amendment** after it. The workspace deletes the oldest job that waits for
nothing when a new job needs a place, so it can delete the first job of a
chain and keep the others.

A runner holds a copy of the job record while the job runs, and its save
writes the whole record back.

## Decision

**The rename writes a `title` field on each job of the analysis, and the
report never reads it.**

- `JobRecord.title` is a nullable field with its own column. The workspace
  shows `title`, then `system_name`, then the default name. `_name_of` is the
  one reader of that order.
- `JobStore.set_title` is the one writer after admission. It does not change
  the revision or `updated_at`, so a rename makes no saved answer stale. A
  save keeps the stored title, so a runner that read the record before the
  rename does not undo it.
- The rename writes the title on every job of the analysis. A Resumed Job
  and an Amendment copy the title of their parent when they are created. So
  the title stays when the workspace deletes the first job of the chain.
- `clean_system_name` reads each title, as it reads each system name. A blank
  title removes the title.
- The report and its `system_name` do not change. `/v1` does not show the
  title.

## Considered options

- **Rewrite `system_name` on each job and in each stored report.** Rejected:
  the report is written once (ADR 0065), and the report would then state a
  name that the submitter did not give.
- **Keep the title only on the first job of the chain.** Rejected: the
  workspace can delete that job and keep the others, and the title then
  disappears.
- **Keep the title in browser storage.** Rejected: a second browser and a
  cleared profile lose it, and a `sql` store keeps the runs across a restart.

## Consequences

A delete clears the title with the other content of the job, and the usage
row keeps no title (ADR 0066). A title is visible only in the workspace.
