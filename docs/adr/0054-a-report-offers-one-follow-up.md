# 54. A report offers one follow-up

- **Status**: accepted
- **Date**: 2026-09-30
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Supersedes**: the limit of three rounds in
  [ADR 0052](0052-the-answer-rounds-end.md). Its rule that a fact answered
  once is not asked again stands.
- **Relates to**: [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md)
- **Evidence**: `QA-2026-09-26-03-E22` in `evals/experiments/`

## Context

A job lineage took up to three rounds of answers, counted across the start of
the analysis and each report. So a submitter could meet questions at the
pause, then on a report, then on another, and nothing said how many to
expect. Each report round runs the whole analysis again.

## Decision

**A report offers one follow-up.** The report lists every open fact its
conditional findings wait on, so one follow-up can answer them all. Answering
it runs the analysis once more, and the report that run writes is final: it
asks nothing and admits no answer. `Resumption.follow_up` records that a job
came from a report's answers, and `QuestionSet.final` reads it.

A lineage therefore runs the analysis at most twice: once when it starts and
once for the follow-up. The rounds at the pause run no model.

**The follow-up must add information.** A submission whose every answer is
"I don't know", or repeats an earlier answer, is refused, and the follow-up is
still available. Only a new or moved link, or an answer whose known content
changes, counts. Without this, one uninformed submission spent the only rerun
on a run that read nothing new (#1289).

**The follow-up is optional and says so.** The report shows it as a closed
section: how many questions, how many conditional findings wait on them, and
that answering runs the analysis once more. Each question says why it is
there. "You skipped this before the analysis" marks a question the pause
showed and got no answer to, from `JobRecord.shown_early`. "New from the
analysis" marks one that the findings' evidence raised. The reviewer's own
questions are headed apart, because they can change from run to run. A
question no conditional finding waits on comes last, in a closed section of
its own, because its answer cannot move a finding of this report (#1289).

**The form and the pause page state the whole plan**: facts, free and in
rounds; the analysis; and one optional follow-up.

## Why one

In E22, after E15's answers, 15 of the 16 questions that a follow-up report
newly asked came from the reviewer alone, and the reviewer's run-to-run
variation is as large. The questions from the findings' evidence were the
same in every run. So a second follow-up would mostly answer the reviewer's
sampling.

## Consequences

A final report still lists its open facts under its conditional findings, so
nothing a reader needs is hidden. A submitter who wants a second follow-up
submits the description again.

E22 is one case, five runs an arm, and three answers. The equal-effort paid
run on #1289 would measure a second follow-up directly.
