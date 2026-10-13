# 81. A question kind is asked once for the system

- **Status**: accepted
- **Date**: 2026-10-13
- **Amends**: [ADR 0074](0074-each-framework-has-its-own-question-limit.md),
  whose limit now counts groups of field questions rather than questions, and
  [ADR 0078](0078-a-paused-job-asks-one-adaptive-list.md), whose questions a
  page now asks once for each group
- **Relates to**: [ADR 0080](0080-each-question-kind-is-listed-once.md), which
  keeps a group's rows together
- **Evidence**: `QA-2026-10-13-04-E1` and `-E2` in `evals/experiments/`,
  `tests/test_evals_early_policies.py`, `TestTheAdaptiveList` in
  `tests/test_answer_admission.py`, and
  `test_one_shared_answer_saves_the_part_for_every_element_listed` in
  `tests/test_browser.py`

## Context

The list asks each question kind once for each element: "What limits bound
the requests…" for six services, "Who may do what…" for nine. On the example
description a STRIDE-only pause met about 75 choices in 26 to 30 questions,
and 23 of the questions were kinds asked again for another element. A field
for each kind would close few of them (`QA-2026-10-13-03-E3`): a description
states one part of a question for a few elements.

## Decision

**A group of field questions is one question.** Where a box holds two or more
rows of one attribute or kind, the page asks it once, lists the elements it
applies to, and sends one answer for each element through the save that
exists. A row the owner edits under "Answer each one separately", or unticks,
is an exception the shared answer leaves alone. A capability question stays
its own question.

**The limit counts places** (`answer_round.limit_place`): a group of field
questions takes one place, and a capability question one. A STRIDE list holds
fewer than 30 groups, so the floor and not the limit decides which field
questions a pause asks. The page's progress counts a shared group's parts
once.

**A shared answer covers the elements the box lists**, not every element of
that kind in the model. The box opens with the shared question first and the
elements closed under it. The maintainer chose these on 2026-10-13.

## Consequences

`run.py early-policies` with the `group-question` policy charges a group's
parts once for every element it lists (`QA-2026-10-13-04-E2`, read with the
group limit). Against the list before this change, STRIDE alone completes
80.7% more findings at five choices and 36.4% more at ten, and with ASVS
selected 99.3% and 38.1% more; ASVS settles as many units or more at every
budget. The whole STRIDE-only list costs 3,182 choices where it cost 8,739.

The walk counts a shared answer as right for every element. A shared answer
can be wrong for an element the owner did not think about, and the lanes then
read it as that element's fact. The box names every element and offers the
exceptions in one click; how often an owner over-claims is unmeasured.
