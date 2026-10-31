# 115. An early field question ranks by the rules that read its fact

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029) (package 5)
- **Amends**: [ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md),
  whose list ranked a field question by its score per choice
- **Relates to**: [ADR 0078](0078-a-paused-job-asks-one-adaptive-list.md),
  whose list this orders

## Context

Before the analysis, the early list scores a field question as the prior's
rate times one plus the candidates that name the element, summed over the
selected frameworks. The floor reads that score, and the list ranked by it per
choice. A candidate counted toward every field of its element, whether or not
its rule read that field.

On the 11 archived cases with reports of both frameworks, 29 of the 198 STRIDE
attribute questions and 91 of the 207 ASVS attribute questions that candidates
raised had no candidate that read the fact. No rule names a question kind,
such as `audit-evidence`, so each of the 782 STRIDE question-kind questions
was raised only by candidates that read other facts.

## Decision

**The floor and the order read two values.** `EarlyQuestion.score` stays as it
was, and the floor reads it, so the same questions are eligible.
`EarlyQuestion.relevance` is the same product with only the candidates whose
rule reads the field, and the list ranks a field question by it per choice.

**`early_questions.reads` is the one reader of the facts a candidate reads.**
A rule reads each fact it names, the `trust_zone` of a flow's end where it
names that end's zone, and the attribute it names where it reads a named
attribute. The reasons a question gives read the same function. No rule reads
a question kind, so a kind ranks by its prior alone.

## Consequences

`early-policies` reads the order that this replaces as `element-count`. On the
11 cases, with each question answered "I don't know", both orders ask the same
choices in total:

| Selection | Choices | `element-count` | Shipped |
|---|---|---|---|
| STRIDE alone, findings completed | 5 | 289 | 285 |
| | 10 | 383 | 429 |
| | 15 | 446 | 510 |
| | 20 | 510 | 521 |
| STRIDE with ASVS, findings completed | 10 | 367 | 399 |
| | 15 | 474 | 489 |
| ASVS with STRIDE, units settled | 15 | 148 | 143 |
| | 30 | 350 | 384 |
| | 45 | 435 | 481 |
| | 60 | 501 | 528 |

ASVS alone does not change. At 30 choices or more each order completes all 521
STRIDE findings. These are counts of dependency sets on archived reports, not
of findings that an answer changes, and the live effect is unmeasured.

Two other orders lost. With a question kind still counting every candidate,
the kinds came before the attribute questions, and STRIDE alone completed 260
findings at 10 choices. With the relevance also read by the floor, every
question kind fell under the floor and left the early list. A question with no
dependency metadata must stay eligible, so that order is refused.

## Evidence

`TestTheOrder` and `test_a_field_question_ranks_by_its_relevance_per_choice`
in `tests/test_early_questions.py`, and
`test_the_element_count_order_asks_the_same_questions` in
`tests/test_evals_early_policies.py`.
