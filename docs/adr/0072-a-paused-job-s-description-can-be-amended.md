# 72. A paused job's description can be amended

- **Status**: accepted
- **Date**: 2026-10-08
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F)
- **Relates to**: [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md),
  whose rounds the amended job asks again

## Context

A paused job asks about the model that its extraction built. An owner can
answer the facts that the service offers, but cannot correct the model
itself. The audit of 7 October 2026 named three cases: a component that the
model misses, a flow that it misses, and a stated fact that the extraction
read wrongly. An answer cannot add an element or a flow, and the answer check
refuses an answer to a fact the model states.

## Decision

**An amendment is extracted again** (maintainer's decision of 2026-10-08). A
person who waits on a paused job writes what is true, in their own words. The
service starts a new job from the paused job's sources, with the amendment
added as one more description, labelled `Amendment 1`, `Amendment 2` and so
on. The number is the lowest that no source label of the job already uses,
because a caller names its own sources. The new job runs extraction and the assertion pass again, and it pauses
again. It never resumes at `prepare`, because a new element or flow changes
the model that every later step reads.

**The amended job carries the paused job's answers, and its own questions
decide which to take.** It holds them apart from its own answers
(`JobRecord.carried`), so its extraction and its catalog pass
never read them. Its question set takes each carried answer that passes its
own answer check, as a submitter's answer would. An answer about a part that
the amended model no longer has, or about a fact that it now states, is not
taken. The first-run page lists those answers, and the questions route
returns them as `carried_dropped`. An answer that the new rounds give
replaces the carried one.

**The amendment holds the paused job.** The amended job holds its parent as a
resumed job does (`jobs.held_parent`). While it is in flight or has a report,
the paused job takes no answer and no second amendment. Where it fails, the
paused job takes answers again. An amendment names the round revision that it
read, as a save does, and the amended sources must fit the source limits.

## Consequences

An amendment costs one extraction and one assertion pass. A saved answer
costs nothing, so the page offers the amendment for a change that an answer
cannot make.

The amended job does not carry skips. A question that the owner skipped on the
old model comes back on the new one.

The routes are `POST /v1/jobs/{id}/amendments` and the first-run app's
`POST /amend/{run}`.

## Evidence

`tests/test_amendments.py` (the route, the hold, a failed amended job, the
revision, the source limits, the carried answers taken and dropped, and an
extraction that reads no carried answer) and `TestTheAmendment` in
`tests/test_webapp_questions.py`.
