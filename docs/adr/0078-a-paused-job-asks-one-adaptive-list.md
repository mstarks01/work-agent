# 78. A paused job asks one adaptive list, and says what an answer settles

- **Status**: accepted
- **Date**: 2026-10-12
- **Effort**: [#1632](https://github.com/mstarks01/work-agent/issues/1632)
- **Supersedes**: the rounds of
  [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md): the fixed number
  of questions a round shows, the next-round step, the box order and the
  rule that a part takes a place beside its parent. Its save that runs no
  model, its explicit skips, its revision check, its floors, its limits and
  its framework turns stay.
- **Amends**: [ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md)
  and [ADR 0049](0049-an-answer-binds-to-an-open-fact-and-the-order-completes-findings.md),
  whose order within a framework's turn now follows what an answer settles,
  with the prior as the tiebreak; their eligibility rule is unchanged.
  [ADR 0051](0051-a-requirement-applies-by-a-rule-over-capabilities.md) and
  [ADR 0055](0055-a-follow-up-asks-first-what-the-most-important-findings-wait-on.md),
  whose early capability order now reads the effect first and the band and
  the units as the tiebreak; a parent still comes before its parts.
  [ADR 0068](0068-a-pause-says-what-it-leaves-open.md), whose counts gain
  the list's own and whose copy says "the list", not "a round".
  [ADR 0069](0069-a-skip-is-of-a-question-the-page-presented.md), whose
  shown record the page now reports and the service checks.
  [ADR 0074](0074-each-framework-has-its-own-question-limit.md), whose
  entitlement is unchanged and whose round wording is not.
  [ADR 0054](0054-a-report-offers-one-follow-up.md), whose workflow reads
  "the list before the analysis" for "rounds".
- **Relates to**: [ADR 0067](0067-a-paused-job-asks-what-decides-a-framework-first.md),
  whose gate questions still lead; [ADR 0050](0050-a-compound-question-kind-is-answered-in-facets.md)
  and [ADR 0073](0073-a-finding-can-wait-on-some-facets-and-an-answer-can-carry-a-detail.md),
  whose facets and details are unchanged; [ADR 0056](0056-the-findings-that-wait-decide-the-follow-up-order.md),
  whose post-report order this does not touch.
- **Evidence**: `QA-2026-10-12-02-E1` to `-E3` in `evals/experiments/`,
  `tests/test_answer_admission.py::TestTheListRanksByWhatAnAnswerSettles`
  and `tests/test_pause_summary.py`.

## Context

A paused job asked in rounds of 10 capability questions and 5 field
questions (ADR 0053). The page showed a round number, a "Save and show more"
button and an estimate of the rounds left. Each save replaced the whole form.
The order within a round read the band and the unit count of a capability
question, and the prior per choice of a field question; no rule read what an
answer would do to the other questions.

The maintainer asked on 2026-10-12 for one page that lists the recommended
questions at once, with no visible round, and that asks first the questions
whose answer settles the most other questions, so an owner resolves the broad
dependencies early and spends less on the details. The safeguards that make
an answer trustworthy stay: a save runs no model, a skip is explicit, the
limits and the floors hold, the frameworks take fair turns, and a question
hidden from the owner is never recorded as seen or skipped.

## Decision

**A paused job asks one adaptive list.** `QuestionSet.early` holds every
recommended question of the pause at once: each question at or above its
kind's floor, up to each framework's limit of each kind, with the gate
questions first (ADR 0067) and the frameworks in turn (`answer_round.by_turn`).
There is no `per_round`, no round number and no box order. A save writes the
answers onto the job and runs no model, and the list is read again off the
model with the saved answers in. The response carries `policy_version`, which
is `adaptive-1`, so a client knows what the list's order and counts mean.

**A part waits on its parent, apart from the list.** A capability question
whose parent is still open is in `QuestionSet.blocked` with the reason
`parent-undecided`. It takes no place under a limit and the page does not
show it. It joins the list once the parent is answered "yes", and leaves once
the parent is "no". So the page needs no rule of its own for which part shows.
A question of a framework whose precondition a reopened answer left
undecidable waits the same way, with `gate-undecided`.

**A question an answer made unnecessary is listed with the rule that removed
it.** `QuestionSet.unnecessary` holds each question the pause first listed
that a saved answer proved settled or unnecessary, with one of
`parent-absent`, `gate-satisfied`, `gate-refuted`, `fact-settled` or
`no-longer-needed-by-selected-units`. The questions a limit holds back carry
`answer-limit`, those under a floor `below-floor`, and the skipped ones
`skipped`. A client maps each reason to its own words.

**Each listed capability question says what each of its answers would
settle.** For each one and each of its answers, the service writes the
capability statement the answer would write onto a copy of the model, through
the one writer (`fact_writes.capability_statements`), and reads each runnable
framework's open capabilities again. The questions that leave the pool are
what the answer settles; the questions that join the list are what it unlocks
(`answer_effects.capability_effects`). A question's potential is the most it
settles over its answers; its least is the fewest. The row publishes `rank`
with both bounds and each answer's effect, so a page can say "a 'no' may
remove 19 questions" and "a 'yes' lists 5 more", with the service's own
figures. The ranking does not favour either answer.

**Within a framework's turn, the list's own order holds, and the effects break
a tie.** A capability ranks by its band and the units it could settle (ADR
0055), a field question by the prior per choice (ADR 0049). Where two
capabilities tie on both, the one with the higher potential per choice comes
first, then the one with the higher least. The maintainer chose this on
2026-10-12 after `QA-2026-10-12-02-E1` measured the order #1632 first asked
for, the potential first: on 32 archived ASVS reports it decided 13% to 37%
fewer units at five choices for every answerer, on 18 to 30 of the 32
reports, and more units only past ten choices and only for an owner who
answers "no" often. A "no" to a broad parent removes many questions and
decides few units; the band-first order asks the capability that decides the
most. The questions an order removes and the units it decides are two
measures, and the second is what the analysis reads.

**Only a rule that removes a question earns credit.** An absent parent makes
its parts absent (`capabilities.resolve`), and a decided unit no longer needs
the other capabilities its rule reads. No other rule removes a question: a
named mechanism lowers a field question's score, and a move under a floor is
not a removal, so a field question has no effect and keeps the prior's order.
The ranking infers nothing from prose, from a detail, or from one element's
answer about another, and treats no silence as "no".

**The page reports what it presented, and the service checks it.** A save
or a start names the early questions the page showed (`presented`). The
service refuses a key the list does not hold, or a part hidden under its
parent's answer, and records only the reported questions as shown. A skip is
taken only of a reported question. So a question the service returned and the
page never rendered is not in the shown history, and no hidden part is
recorded as seen or skipped (ADR 0069).

**The counts say what the list holds and what an answer did.**
`PauseSummary` gains `recommended`, `recommended_choices`, `blocked` and
`unnecessary` beside the counts of ADR 0068. `introduced` counts the
questions the page reported, not the questions the service returned. An
answer can raise `recommended`: a "yes" lists the parts that waited on it,
and a gate answer lists a framework's questions. The page says so; it does
not claim that the work only shrinks.

**The limits and the floors are unchanged.** Each framework has its own limit
of each kind (ADR 0074), and a limit counts saved answers, not requests,
edits, facets or rendered rows. A skip spends no place. A read or a save with
unchanged inputs changes nothing.

## Consequences

**The turns carry over the saves.** The frameworks' turns start from what
the saved answers charged each framework, not from zero on each build. Before
that, an owner who answered the first question and saved met the same
framework's next question every time, and STRIDE completed 96 of 2,796
findings in ten choices with ASVS beside it, against 1,488 alone
(`QA-2026-10-12-02-E2`).

**A read is kept by its content.** `early_questions` and
`capability_effects` keep their results by a digest of what they read, so a
read that changes nothing costs about 13 ms, and a read after a save builds
only the list with the answers in. With nothing kept, the set builds in a p95
of 180 ms (no answers) to 332 ms (thirty answers) on the largest corpus
model with STRIDE and ASVS level 3, on an idle machine, under the 500 ms the
issue proposed (`QA-2026-10-12-02-E3`).

**The `/v1` questions payload changes shape.** `early_questions` holds the
whole list, `early_blocked` and `early_unnecessary` are new, each row
carries `reason` and `rank`, and `early_remaining` and `early_withheld` are
gone. There is no round-shaped payload beside it: the first-run page and the
eval replay are the callers, and both read the list.

**The first-run page still saves by a button.** With this record the page
shows the whole list, sends what it presented, and names no round. Keyed
state, autosave, conflict handling and incremental rendering are the next
change of #1632.

**The box order's evidence no longer applies.** `QA-2026-10-09-02-E2` measured
the box order against the question turn order inside rounds. The list has no
round to box, and a group heading must not move a low-priority block ahead of
the intended choice, so the page shows each group's rows in the list's order.
