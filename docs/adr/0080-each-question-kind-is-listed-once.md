# 80. Each question kind is listed once

- **Status**: accepted
- **Date**: 2026-10-13
- **Amends**: [ADR 0078](0078-a-paused-job-asks-one-adaptive-list.md), whose
  rule that a group heading never moves a low-priority row ahead of the
  intended choice this replaces for the question kinds
- **Evidence**: `QA-2026-10-13-02-E1` to `-E3` in `evals/experiments/`, and
  `TestTheFrameworksTakeTurns::test_each_question_kind_is_listed_once_at_its_first_row`
  in `tests/test_answer_admission.py`

## Context

The page draws a box for each run of rows that share a group. The list ranks
each question by its own element, and the frameworks take turns one question
at a time, so the rows of one kind sit apart. On the head runs of the example
description the page drew 17, 23 and 18 boxes for 9, 8 and 6 kinds: "Who may
do what on…" appeared in several boxes, and the "Same for all" control, which
needs a kind's rows in one box, rarely appeared. The maintainer asked on
2026-10-13 that a group never appear twice.

ADR 0078 forbade it: a group heading must not move a low-priority row ahead of
the intended choice. The maintainer had accepted that rule's cost on
2026-10-12.

## Decision

**A question kind's rows follow its first row** (`answer_round.grouped`, the
last step of `recommended`). The kind takes the place of its highest-ranked
row, and its other rows keep the list's order inside it. A lower row of one
kind can so come before a higher row of another kind.

**A capability question keeps its own place.** Every capability shares one
group, "About the application". Gathering them put every capability ahead of
STRIDE's questions when both frameworks were selected, and STRIDE completed 60
findings at five choices where the list completed 1,009
(`QA-2026-10-13-02-E2`). So with ASVS selected the capability box can still
appear more than once.

The maintainer chose this on 2026-10-13 against the measured cost below.

## Consequences

`run.py early-policies` walks down the list as a page shows it
(`QA-2026-10-13-02-E3`). STRIDE alone completes 19.7% more findings at five
choices and 2% to 5% more from ten to thirty. With both frameworks selected,
STRIDE moves between −3.9% and +4.6%, and ASVS settles 6.9% to 21.8% fewer
units. The walk charges each row its own choices; one "Same for all" answer
for a kind costs fewer, so the walk understates the grouped order.

`run.py early-answerers` answers only the first row of each save, and the
first row never moves, so it reads the same with or without this rule
(`QA-2026-10-13-02-E1`). It cannot price a change to the later rows.
