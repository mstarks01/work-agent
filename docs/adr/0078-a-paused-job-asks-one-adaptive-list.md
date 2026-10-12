# 78. A paused job asks one adaptive list, ranked by what an answer settles

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
- **Evidence**: `tests/test_answer_admission.py::TestTheListRanksByWhatAnAnswerSettles`
  and `tests/test_pause_summary.py`. The replay of the list against the
  archived baselines (`QA-2026-09-26-03-E33`, `-E35`, `QA-2026-10-09-01-E1`,
  `QA-2026-10-09-02-E1` and `-E2`) at equal choice budgets is not run yet;
  it is the evidence step of #1632, and until it runs the list's effect on
  coverage is unmeasured.

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

**Within a framework's turn, the question whose answer settles the most other
questions comes first.** For each listed capability question and each of its
answers, the service writes the capability statement the answer would write
onto a copy of the model, through the one writer
(`fact_writes.capability_statements`), and reads each runnable framework's
open capabilities again. The questions that leave the pool are what the
answer settles; the questions that join the list are what it unlocks
(`answer_effects.capability_effects`). A question's potential is the most it
settles over its answers; its least is the fewest. The order within a kind is
the potential per choice left to answer, then the least, then the list's own
order: the band and the units for a capability, the prior per choice for a
field question. The row publishes `rank` with both bounds and each answer's
effect, so a page can say "a 'no' may remove 19 questions" and "a 'yes' lists
5 more", with the service's own figures.

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

**The order trades band for effect.** On the shared test model at ASVS
level 2, thirty "yes" answers to the list leave 19 level 1 requirements
unknown, where the band-first order of ADR 0055 left 7: a "yes" to a broad
parent such as `authentication` settles no requirement, and the list asks
that parent first because its "no" would settle 19 questions. Thirty "no"
answers settle far more than the band-first order. Which owner the service
meets more often is not known, so the list's effect on coverage at equal
choice budgets is the next step of #1632, replayed per framework over every
answerer, against the archived baselines; a coverage loss that the replay
does not explain is a reason to change the order, not to keep it.

**A read costs the effects.** The question set builds in 310 ms at ASVS
level 2 and 458 ms with STRIDE and ASVS level 3 on the shared test model,
in one process on an idle machine with nothing cached; the effects are about
170 ms of that at level 3. The issue's target is 500 ms for a save and its recalculation on
the documented local setup. No cache is kept yet.

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
