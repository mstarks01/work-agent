# 114. A shared answer is stored with the parts it covers

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029) (package 2, second step)
- **Relates to**: [ADR 0081](0081-a-question-kind-is-asked-once-for-the-system.md),
  [ADR 0113](0113-a-shared-answer-covers-the-parts-a-person-ticks.md), whose
  ticked parts this stores, and
  [ADR 0070](0070-a-follow-up-saves-a-draft-in-batches.md), whose draft holds
  shares too

## Context

A page asks a box of same-shape questions once, and a person ticks the parts
the answer covers (ADR 0113). The page sent one ordinary answer for each
ticked part. After a save, the service could not tell a shared answer from
separate answers, so a reload, a resume, the API and an export lost which
parts the answer covered and which parts were exceptions.

A job's fact answers have 37 readers in 8 files: admission, a report's
conditions, the answers Source, carried answers, the API and others.

## Decision

**A shared answer is a stored record** (maintainer's decision of
2026-10-31). `AnswerSet.shares` holds each `SharedAnswer`: the answer, whose
key is the question's shape with the element left empty, and `covers`, the
exact elements it covers. A request body carries `shares`, and the questions
route returns `answered_shares` and `draft_shares`.

**The service writes each covered part's answer when it admits the share.**
Admission expands each share into one fact answer for each covered element,
and checks each one as any other answer. So the 37 readers stay as they are,
and each reads a covered element's answer as that element's own. The other
design stores only the share and derives each element's answer when it is
read. Precedence and retraction would hold by construction, but every reader
would have to read the derived answers, and one missed reader would disagree
with the rest.

**The precedence is stated.** A covered element holds the share's answer and
nothing else:

- A submission that gives a covered element its own answer as well is
  refused, and so is a submission with two shares of one question.
- A later answer of an element's own takes it out of every earlier share.
- A share replaces the earlier share of its question. An element that leaves
  `covers` loses only the answer the share wrote, and an empty `covers` takes
  the share back from every element.

**Each covered element counts as a fact answer** toward the bound of one
request and toward the limit a job holds. A report's draft holds its shares as
sent; `AnswerSet.expanded` writes them out where a reader needs each element's
answer, such as `held_answers`.

## Consequences

The page still sends one answer for each ticked part. The next change makes it
send a share and restore the ticks from `answered_shares` and `draft_shares`.

A job that carries another job's answers (ADR 0079) or an amendment's new job
(ADR 0072) carries each covered element's answer, not the share. A correction
to a final report is an element's own answer, kept beside the report; it
leaves the stored shares as they were.

An enforcing element and a closed set of control categories are not part of
the record. Package 2 of #2029 takes them up next, with their own decision.

## Evidence

`tests/test_answer_shares.py`.
