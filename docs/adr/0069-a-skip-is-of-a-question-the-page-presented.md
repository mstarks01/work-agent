# 69. A skip is of a question the page presented

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F4)
- **Relates to**: [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md),
  which introduced the skip and the part that waits on its parent

## Context

A capability question that is a part of another one is hidden until its
parent is answered "yes". The first-run page kept the hidden row in the
round's rows. "Skip the rest" then sent the hidden row's key as a skip, and
the service admitted it. So the record said that the submitter skipped a
question that they never saw.

The audit of 7 October 2026 showed the effect. With the parent answered "I
don't know", the hidden part was skipped. When the submitter later changed
the parent to "yes", the part stayed in the skipped list and did not come to
a round.

The report had the same gap. It labelled every question that the pause
showed and that got no answer as "skipped", even where the submitter left it
blank, and even where the "shown" record held a hidden part.

## Decision

**The service decides what a round presented.** `QuestionSet.presented` is
the one reader. A part whose parent the round also asks is presented only
when the parent's answer, with the submission in, is "yes". A part of a
hidden part stays hidden. The page hides the same rows by the same rule.

**A skip of a part that was not presented is refused.** The message says that
the part is asked once it shows. "Skip the rest" on the page skips only the
rows that show.

**Only a presented question is recorded as shown.** So the history of a
question holds only what the page presented. A part that the round asked but
the page hid behind its parent is not in it, and no state records it.

**The report reads one history rule.** `fact_answers.fact_status` gives
each fact one of six states: `answered`, `partial`, `unknown`, `skipped`
(the submitter's skip), `unanswered` (presented and left blank) or `open`
(never presented). The conditions beside a finding and the follow-up's
labels both read it. A resumed job now carries the pause's skips
(`ResumedJob.skipped`, `skipped_early` on the job record), so the report can
tell a skip from a blank. The follow-up question's `asked_before` flag is
replaced by `history`.

## Consequences

A part whose parent is answered "I don't know" or "no", or left blank, is not
skipped. Where the parent later becomes "yes", the part comes to a round as an
ordinary question.

The report says "shown before the analysis and left blank" for a question
that the submitter did not skip, and "you answered part of this before the
analysis" for a partial answer.

## Evidence

The reproduction in #1542 (F4a and F4b). The regression tests are in
`tests/test_presented_history.py`. They drive the shipped page script and
production admission over the same payload.
