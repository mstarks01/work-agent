# 113. A shared answer covers the parts a person ticks

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029) (package 2, first step)
- **Amends**: [ADR 0081](0081-a-question-kind-is-asked-once-for-the-system.md),
  whose shared answer covered every part its box listed

## Context

ADR 0081 asks a box of same-shape questions once: two or more rows of one
attribute or kind, with one form and one set of choices. The shared answer
covered every part the box listed, and a person opted a part out. ADR 0081
said that how often an owner over-claims this way was unmeasured.

`run.py shared-answers` measures it over the corpus. It makes each stated
attribute that the early list can ask unknown, builds the boxes that the paused
list asks once, and reads each blessed value as the true answer. Over 16 cases
with STRIDE selected, 36 shared boxes have a true value to check, with 87
parts. 34 of those parts (39%) have a true value other than the most common
one in their box, and only 16 of the 36 boxes have one true value. With ASVS
selected, 23 of 47 parts (49%) differ. The differences are real, not wording:
in case 02 one box holds "company SSO", "fleet-wide pre-shared key" and "none;
the bucket is public read".

The corpus cases are written to hold varied controls, and a real description
states many of these attributes, so these are upper bounds for this corpus. A
box of question kinds has no blessed truth and is not in these figures.

## Decision

**Every part of a shared box starts unticked** (maintainer's decision of
2026-10-31). A person ticks each part that the shared answer covers, and the
answer is saved as each ticked part's own answer, through the save that
exists. A facets row has a tick too. The list of parts stays open under the
shared question.

**Unticking a part takes back only what the shared answer wrote.** A part
that took the shared answer and is then unticked returns to blank. A part that
a person answered by hand is an exception: it is unticked, and the shared
answer leaves it alone.

**The page's `shareable` rule has a second reader**, the instrument, and a
test puts each pair of served questions to both.

## Consequences

An owner spends one choice for each part that the answer covers. Over the
STRIDE boxes with a truth, an owner who shares the most common value and opens
no exception spends 36 choices and over-claims 34 parts. An owner who ticks
the parts spends 123 choices and over-claims none. An owner who shares and
then corrects each part that differs would spend 70. These are offline counts
over a corpus; the live effect on owners is unmeasured.

The service still stores each part's answer as its own answer. A reload, a
resume, the API and the export do not keep which parts a shared answer
covered. The next step of package 2 stores the coverage, its origin and its
exceptions.

## Evidence

`tests/test_evals_shared_answers.py`,
`test_one_shared_answer_fills_only_the_parts_a_person_ticks` and
`test_unticking_a_part_takes_back_only_the_shared_answer` in
`tests/test_webapp_questions.py`, and
`test_one_shared_answer_saves_the_part_for_every_element_ticked` in
`tests/test_browser.py`.
