# 125. A Control fills only an open fact

- **Status**: accepted
- **Date**: 2026-11-03
- **Effort**: [#2116](https://github.com/mstarks01/work-agent/issues/2116), on the
  Controls map [#2111](https://github.com/mstarks01/work-agent/issues/2111)
- **Relates to**: [ADR 0079](0079-a-new-analysis-can-carry-an-earlier-job-s-answers.md),
  which lays carried answers under own answers;
  [ADR 0082](0082-a-platform-default-that-cannot-be-disabled-is-recorded.md) and
  [ADR 0083](0083-a-vendor-default-is-asked-and-a-protocol-definition-is-recorded.md),
  whose stated defaults stand

## Context

Three writers can reach one fact on one element: the extraction, a carried
answer and the owner's own answer. `open_attribute` is the one reader of "may
this fact be written" for an answer, and a stated value stands, "none"
included, as does a protocol default's value. A Control adds a fourth writer
(ADR 0123), and two Controls can write one fact.

## Decision

**A Control fills only an open fact, through `open_attribute`.** A stated
value stands. A Control's fact on a stated attribute is recorded as `stated`,
with the stated value beside it, and is not written. A `stated` fact never
blocks: the Control applies, the fact is left out with no click, and the other
facts and the rows still apply. A Control whose every fact is `stated` still
applies, because its rows and documents go to the critic. The owner edits the
description or the Control.

**The rung**: own answers lie over a Control, and a Control lies over carried
answers. A pause answer or a follow-up correction can change a value a
Control wrote. An `unknown` answer at the pause can take a Control's fact back
for this job, and the follow-up cannot. A carried answer to a fact a Control
writes is dropped and listed with the reason `control-states`.

**A submission with Controls asks questions.** A job that does not pause
shows no model before its lanes run, so it has no point where a Control's
facts and their lines of the answers Source can be written. The submission
refuses `controls` without `questions`, as it refuses `answers_from` without
them.

**Two Controls that write one fact with two values refuse the save.** The
refusal names the two Controls, the fact and the two values. The check runs at
submission on the resolved elements, after the zone fan-out and the ticks. Two
Controls with one value pass, and the job records both as writers. Facets merge
as `merged_facts` merges them, and only a facet stated twice with two values is
refused.

**The job keeps `control_facts`, and `fact_origin(key)` is the one reader of
a fact's rung.** `control_facts` holds one entry per applied Control: the
Control's ID and the fact answers it wrote after scope resolution.
`held_answers` lays the list on its rung. `fact_origin` returns `correction`,
`draft`, `answer`, `control` with the Control's ID, or `carried`. The answers
Source carries one line per Control fact, and the line names the Control. A
fact a Control wrote and an owner later changed has two lines, and the model
holds the owner's value.

**A fact a Control writes is not asked.** It is listed under "Your answers"
with the mark "from Control <name>" through `fact_origin`. The owner may change
the value there at the pause, and the mark then reads as an own answer with
the Control's value as "was". A fact no question of the job asks is still
written and listed.

**The report has a Controls section, and the job's copy is its one reader.**
One entry per applied Control holds its name, its resolved scope, its documents
with their class, and each fact with a state from the closed set `written`,
`changed`, `stated`, stored in the job row's `control_facts` at each pause
save and on the Resumed Job, because the copy is written once (ADR 0127).
Nothing recomputes a state from the model. The rows and their outcomes join
the entry (ADR 0126). A Control the owner left out is listed with its reason
(ADR 0124). The Markdown and the JSON export carry the section.

## Considered options

- **A Control over a stated value.** Rejected: a configuration file beats a
  transcript, but a stale Control then hides a change the description reports.
- **A Control over own answers.** Rejected: a typo in a Control then blocks a
  job until a resubmission.
- **The narrower scope wins between two Controls.** Rejected: "internet-facing"
  and "behind WAF" are both true, and one attribute cannot hold both.
- **An `origin` field on `FactAnswer`.** Rejected: 37 readers see a field most
  ignore, and the lists would be a second reader.
- **Each Control-written question asked pre-filled.** Rejected: a zone Control
  over twelve elements costs twelve confirmations.
- **A refusal on a `stated` fact.** Rejected: blocks a job on a fact that
  already has a value.

## Consequences

The change review gains the count "answered by a Control". A capability fact an
application-scoped Control writes takes the stated check and the lineage check
in the same save.
