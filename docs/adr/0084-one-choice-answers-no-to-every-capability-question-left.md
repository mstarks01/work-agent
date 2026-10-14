# 84. One choice answers "no" to every capability question left

- **Status**: accepted
- **Date**: 2026-10-13
- **Relates to**: [ADR 0051](0051-a-requirement-applies-by-a-rule-over-capabilities.md),
  where only an answer states a capability;
  [ADR 0080](0080-each-question-kind-is-listed-once.md), where a capability
  question keeps its own place; and
  [ADR 0081](0081-a-question-kind-is-asked-once-for-the-system.md), where one
  answer covers a group of field questions
- **Evidence**: `QA-2026-10-13-09-E1` in `evals/experiments/`,
  `test_no_to_the_rest_answers_every_capability_left_in_one_choice` in
  `tests/test_evals_early_policies.py`,
  `test_no_to_the_rest_answers_only_the_blank_capability_questions` in
  `tests/test_webapp_questions.py`, and
  `test_no_to_the_rest_answers_each_blank_capability_question` in
  `tests/test_browser.py`

## Context

A job that selects ASVS asks a capability question for each capability that
its applicability rule needs and no answer states. On the example description,
level 2 asks 30 of them and level 1 asks 10. Only an answer states a
capability (ADR 0051), so a description cannot close one. Most of them ask
about something the application does not have, such as WebSockets or XML,
and each such "no" costs the owner one choice.

## Decision

**One choice answers "no" to every capability question left blank.** The
button **Answer “no” to every question about the application left blank**
writes "no" into each listed capability question that has no answer, and
sends each one as that question's own answer, through the save that exists.
A question with an answer, a question skipped for now, and a question that
is not listed stay as they are. Each answer stays in its place and can be
changed. A hint under the button says that a "no" removes checks from the
analysis, and that a question the owner is not sure of stays blank or takes
"I don't know".

**This sets aside one rule of
[#1632](https://github.com/mstarks01/work-agent/issues/1632)**: that the page
does not favour an answer because the answer makes the interview shorter. The
button favours "no" for that reason. The list's ranking stays neutral between
the answers (ADR 0078).

**Each capability question keeps its place in its framework's turn.** The
questions are not moved into one box. The maintainer chose this on
2026-10-13 from three layouts the walk measured.

## Consequences

`run.py early-policies` reads the `no-to-the-rest` policies: the "yes"
answers one at a time at their places, then one choice for every capability
question of the list left. The walk does not know which capabilities an
application has, so it reads three shares of "yes" answers
(`QA-2026-10-13-09-E1`). Against `group-question`, no reading loses at any
budget. With a third answered "yes":

- With ASVS alone, 924 units are settled at five choices where 780 were, and
  all 1,884 at fifteen where 1,574 were. The whole list costs 364 choices
  where it cost 790.
- With both frameworks, ASVS settles 912 units at ten choices where it
  settled 768, and STRIDE completes as many findings or more.
- With no "yes", ASVS alone settles 1,860 units at five choices, and the
  whole STRIDE list with both frameworks costs 3,658 choices where it cost
  7,004.

With every answer "yes", the button saves nothing, and the list costs what
it cost before.

One box at the first capability's place cost STRIDE 59% of its completed
findings at five choices with both frameworks, as gathering the capabilities
did in ADR 0080. One box at the last capability's place cost ASVS most of its
settled units below thirty choices. Both are recorded in
`QA-2026-10-13-09`.

A "no" rules out every requirement that needs the capability. One choice
makes a wrong "no" as easy as a right one. The walk answers truthfully, so
how often an owner presses the button over a capability the application has
is unmeasured.
