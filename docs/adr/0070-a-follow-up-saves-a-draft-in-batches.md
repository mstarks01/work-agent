# 70. A follow-up saves a draft in batches

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F3)
- **Relates to**: [ADR 0054](0054-a-report-offers-one-follow-up.md), whose one
  follow-up this keeps, and [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md),
  whose revision check a draft save reuses

## Context

A report's follow-up asks every open fact that its conditional findings wait
on, with no cap. One answer request carries at most 200 fact answers and 50
link answers. The two bounds did not agree.

The audit of 7 October 2026 built 201 schema-valid conditional threats with
distinct open facts. The follow-up asked 201 questions, and a request with
one answer for each was refused. A report could not save a batch without
running, because only a waiting job saved, and the first run spent the one
follow-up. Changes to earlier answers took places in the same request, so the
failure did not need 201 new questions.

The job record had the same mismatch. Its answer lists used the request
ceiling of 200 as their own bound, so a job that gathered more answers across
its saves broke its own record.

## Decision

**A report's follow-up keeps a draft.** A save to a finished, not final,
report keeps the answers as a draft on the job (`JobRecord.draft`). It runs no model and does not spend the follow-up. A later
batch replaces an earlier draft answer to the same fact or principal.

**The run composes the draft with what it is sent.** A run without `"save"`
merges the draft with its own answers and admits the result as one follow-up,
under every existing rule: the answer check, the source limits and the rule
that a follow-up must add information. An empty run uses the draft alone.

**A save names its revision.** A draft save carries the revision it read, as
a waiting job's save does, and the store checks it and writes the next
revision in one step. Of two saves that read one revision, only the first
lands. A run may name its revision, and is checked where it does. A second
run is refused while the first holds the report. Where the first run fails,
the report asks again and its draft is still there.

**The limits are published, and each one has its own job.** One request
keeps its ceilings of 200 facts and 50 links. A job holds at most five times
those across every save (`MAX_HELD_FACTS`, `MAX_HELD_LINKS`), and the answer
check refuses a save or a run that would pass them. No schema bound limits how
many facts a model or report can leave open, so the held ceiling is a refusal
point, not a measured maximum. The deployment's source limits bound the
answers' text as well. The questions route publishes all four as
`answer_limits`, and the report page reads them.

**The report page saves in batches.** "Save progress" splits the answers into
requests within the limits, each naming the revision that the last one
returned. "Run the follow-up" saves first where the answers pass one request,
and then runs on the draft. Every answer stays on the page after a refusal,
an interrupted connection or an unreadable reply, and the buttons come back.
A saved draft fills its questions when the page opens.

## Consequences

The first-run app keeps the draft on the run in memory, as it keeps every
other answer. The durable store of #1282 and ADR 0065 holds it with the rest
of the job record, so this ADR adds no second store.

A draft of answers that add nothing is kept, because a save is not a run. The
run that would read nothing new is refused, and the follow-up stays available.

## Evidence

The reproduction in #1542 (F3). The regression tests are in
`tests/test_follow_up_drafts.py` (199, 200 and 201 facts; 49, 50 and 51
links; earlier-answer edits; two batches and one run; a stale save; a second
run; a failed run run again) and `TestTheFollowUpSavesInBatches` in
`tests/test_webapp_questions.py`.
