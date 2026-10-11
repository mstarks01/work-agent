# 77. The first-run app holds job records in the job store

- **Status**: accepted
- **Date**: 2026-10-11
- **Effort**: the architecture review of 2026-10-11, candidate 1
- **Relates to**: [ADR 0045](0045-a-job-that-asks-pauses-after-the-assertion-pass.md),
  which states what a job waiting on answers is;
  [ADR 0066](0066-a-deleted-job-leaves-a-usage-row.md), whose delete the
  app's retention cap uses; [ADR 0070](0070-a-follow-up-saves-a-draft-in-batches.md),
  whose consequences said the app keeps a draft on its run in memory

## Context

The first-run app held one `Run` record per analysis and a registry of them.
`Run` carried the same fields as a `JobRecord`, and the app re-derived the
status, the lock and the revision rules that `JobStore` already implements.
Every answer route existed twice, once in `analysis_service.api` and once in
`webapp/main.py`, with the same steps in the same order, and every ADR since
ADR 0053 cited two test suites for one rule. The two files changed in the
same commit 34 times in the 300 commits before this one, and a test existed
only to keep the two implementations agreeing.

## Decision

**The first-run app holds `JobRecord`s in an `InMemoryJobStore`, and one
module writes and starts a job for both surfaces.**

- `analysis_service.job_answers` reads a job from the store, builds its
  Answer State, stores a round, a draft or a correction, or creates the
  Resumed Job, admits it and starts it. It raises a typed refusal; the `/v1`
  routes map each to a problem response, the app to a page message.
- The app admits a job through `JobStore.reserve` with a ceiling of one and
  the deployment's own budget policy. "One run at a time" is that ceiling.
- The app's retention cap stays the app's policy: it holds at most twenty
  jobs, deletes the oldest finished job first through `JobStore.delete`,
  never deletes a job that waits for answers, and refuses a new job where
  every held job waits. A deleted job leaves its usage row (ADR 0066).
- The page's event stream is the store's event log for the job, followed by
  one frame for how the run ended: the questions a pause asks, where the
  report is, why the description was refused, or why the run failed. The
  store is the one record of the run.
- An analysis is the root of a job's parent chain, read through the
  resumption and amendment parents. No field names it.
- Every job the app holds names the in-process caller as its owner.
- `Engine.resume` is removed: the app was its only caller, and it built a
  record with no parent and no certification.

## Considered options

- **Keep `Run` and share only the submission operation.** Rejected: the
  lifecycle copy is what drew the churn and the parity test.
- **Move the retention cap into the store.** Rejected: the cap is one
  surface's policy, and a durable store has its own retention (ADR 0066).
- **A per-run event queue beside the store's log.** Rejected: it needs a
  second driver of the runner beside `execute_job`.

## Consequences

- A rule about a job's lifecycle has one implementation and one test file.
  The app's question tests test its pages, not the rules.
- The app's jobs carry their certification, and a resumed job combines its
  certification with its parent's, as a `/v1` job does.
- A restart still loses every job. The durable store of ADR 0065 would give
  the app persistence with no change to its routes.
- ADR 0070's statement that the app keeps a draft on its run in memory now
  reads: the app keeps it on the job record, in the in-memory store.
