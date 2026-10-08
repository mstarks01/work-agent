# 68. A pause says what it leaves open

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F2, F5)
- **Relates to**: [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md),
  whose floors and limits this keeps and whose `nothing-left` stop it
  narrows, and [ADR 0067](0067-a-paused-job-asks-what-decides-a-framework-first.md)

## Context

A paused job stopped with `budget-exhausted` or `nothing-left`. The page then
said "No question is left", and the workspace said "Question review
complete". Neither was true in general.

The audit of 7 October 2026 answered every offered capability "yes" at ASVS
level 1, on the shared test model. The pause stopped with `nothing-left` and
zero questions withheld, but 22 of 70 requirements still had unknown
applicability. Twenty-four capability questions were under the floor, and
`withheld` counted only the questions that a limit held back. So the stop hid
the floor's effect.

The same audit skipped every round of a STRIDE pause. The rounds showed 40
distinct field questions under a field limit of 30, because a skip spends no
limit. The limit counts answers, not the questions a person sees.

## Decision

**No stop says that every fact is settled.** `QuestionSet.stop` has four
values:

| Stop | Meaning |
| --- | --- |
| `budget-exhausted` | A limit holds questions back from every round. |
| `below-floor` | No question passes its floor, and questions under a floor are left. |
| `skipped` | Every open question left was skipped, and each one still takes an answer. |
| `nothing-left` | No open question is left to ask. |

The page words each one plainly: "The question limit is reached", "No
recommended question is left", "You skipped every question that is left" or
"No question is left to ask". It then lists
what stays open. The workspace label is "No more questions in a round".

**The set names the questions that no round shows.** `held_back` holds the
questions that a limit holds back, and `below_floor` the open questions under
a floor. The answer check admits an answer to each of them. The page lists
them under "More questions", collapsed, and each one opens with "Answer it".
So a submitter can choose to answer more before the analysis. A pause that has
optional questions does not start the analysis unseen. Rounds do not grow to
include these questions on their own.

**The set carries a summary of what stays open** (`PauseSummary`, and
`early_summary` on `/v1`). It keeps different counts apart:

- `introduced` and `choices`: the distinct early questions that the rounds
  showed, and the choices that they ask. A limit does not bound these, because
  a limit counts answers.
- `settled`, `partial` and `unknown`: the saved early answers, by whether
  every facet is known, some facets are known, or the answer is "I don't
  know".
- `skipped`, `held_back` and `below_floor`: the open questions that no round
  shows, by the reason.
- `applicability`: for each framework that will run and whose units apply by
  a rule over capabilities, the units in each state, by band. `unaskable`
  counts the unknown units that no open question can settle any more. These
  are units, not findings. The count reads the package's own `applicability`
  hook and a new `unit_band` hook, so a level 2 job still shows how many level
  1 requirements are unknown.

**This ADR does not change a floor or a limit.** The floors, the limits and
the highest-band exception of ADR 0053 stay as they are. The exception lets a
single-unit level 1 question pass the floor in a level 2 job, but not in a
level 1 job. The summary now shows the effect of that rule, and the questions
under the floor can be answered. A change to the rule needs its own decision.

## Consequences

The level 1 reproduction now stops with `below-floor`. It lists 24 questions
under the floor and reports 22 of 70 level 1 requirements as unknown. A level
2 job reports level 1 and level 2 apart: 7 of 70 level 1 requirements stay
unknown after 30 "yes" answers.

On the 15 corpus models with STRIDE, a pause holds 37 to 87 field questions
under the floor. Before this ADR, the page did not mention them. They now
appear under "More questions", collapsed.

An answer to a held-back or below-floor question counts toward its kind's
limit, as every saved answer does.

## Evidence

The reproduction in #1542, at `60340fe4` before the change and after it. The
regression tests are in `tests/test_pause_summary.py`, and the page test
`test_a_finished_pause_lists_what_stays_open_and_takes_a_held_back_answer` in
`tests/test_webapp_questions.py`.
