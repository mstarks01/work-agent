# 79. A new analysis can carry an earlier job's answers

- **Status**: accepted
- **Date**: 2026-10-12
- **Relates to**: [ADR 0072](0072-a-paused-job-s-description-can-be-amended.md),
  whose carried answers this reuses;
  [ADR 0065](0065-a-job-record-splits-into-rows-and-two-objects.md), whose
  durable store will keep the earlier jobs this reads
- **Amended by**: [#1672](https://github.com/mstarks01/work-agent/issues/1672),
  under which the carried answers survive a restart where
  `ANALYSIS_JOB_STORE` is `sql`

## Context

An owner who analyses the same system again, or a second system that shares
its platform, answers the same questions again. An amendment already carries a
paused job's answers to the job it starts, and that job's own questions take
each answer they still ask (ADR 0072). Nothing let a new submission do the
same.

## Decision

**A submission can name one earlier job of its owner, and the new job carries
every answer that job holds.** `POST /v1/jobs` takes `answers_from`, and the
first-run app's `/analyze` takes the same field. `JobRecord.held_answers` is the
one reader of what a job holds: a final report's corrections over a report's
draft, the draft over the answers its run read or its pause saved, and those
over what it carried. The new job keeps them in `JobRecord.carried`, apart
from its own answers, so its extraction and its catalog pass never read them.

**The new job's own questions decide which answers it takes.** Each carried
answer passes the new job's answer check, as an amendment's carried answer
does. An answer about an element the new model does not have, or about a fact
it states, is dropped, and the questions route lists it in `carried_dropped`.
An answer names an element by its ID, and an ID follows the element's name, so
an answer carries where the new description gives the element the same name.

**The rules are fail-closed.** Another owner's job reads as missing (`404`). A
submission without `questions` is refused, because a job that does not pause
never reads what it carries. A job that holds no answer is refused, and so is
a set over the held limits.

**The owner names one job.** The service does not merge the latest answer to
each question over every earlier job. One job is simpler to state, and it
needs no rule for two jobs that disagree.

## Consequences

The earlier job does not change: nothing locks it, and it can be answered,
followed up or deleted as before. The new job does not carry skips, as an
amended job does not.

Jobs are held in memory, so an owner can carry only from a job of the same
server session. A durable job store (ADR 0065) extends this to every job the
store keeps, with no change to the rule.

> **Amended by [#1672](https://github.com/mstarks01/work-agent/issues/1672).**
> The job store holds the jobs. Where `ANALYSIS_JOB_STORE` is `sql`, an owner
> can carry from every job the store keeps, and the carried answers survive a
> restart.

## Evidence

`tests/test_reuse_answers.py` (what a job holds, the route, the owner check,
the refusals, an answer taken and an answer dropped), the `/analyze` tests in
`tests/test_webapp.py`, the submit test in `tests/test_webapp_questions.py`
and `test_a_new_analysis_reuses_the_answers_of_an_earlier_one` in
`tests/test_browser.py`.
