# 88. Field questions have no limit

- **Status**: accepted
- **Date**: 2026-10-15
- **Amends**: [ADR 0074](0074-each-framework-has-its-own-question-limit.md),
  which gives each framework a limit of each kind
- **Relates to**: [ADR 0081](0081-a-question-kind-is-asked-once-for-the-system.md),
  under which a group of field questions takes one place
- **Evidence**: `tests/test_answer_admission.py`
  (`test_field_questions_have_no_limit`)

## Context

A paused job had two limits for each selected framework: 30 places for
capability questions and 30 for field questions. A group of field questions
takes one place (ADR 0081), and there is one group for each attribute or
question kind that the question prior names. The prior names 18 for STRIDE and
5 for ASVS, so no list can hold 30 field places.

An offline count on 2026-10-15 showed the limit has no effect. On 186
archived STRIDE reports, a first list holds a median of 9 field places and at
most 10, and at most 18 with no floor. On 32 ASVS reports, it holds at most 3.
The floor decides which field questions a pause asks: without it, the STRIDE
list doubles.

Saved answers cannot reach the limit either. A job admits an answer only to a
question it lists, a carried answer included (ADR 0079), so each saved field
answer takes a place in one of the same groups.

## Decision

**Field questions have no limit.** `EARLY_RULES["field"]` has the floor of 1
and the limit `None`. The list holds every field question at or above the
floor.

**Capability questions keep their limit of 30 for each framework**, as ADR 0074
states. On archived ASVS models, it cuts the first list from a median of 37
capability questions to 30.

## Consequences

`early_held_back` holds only capability questions. `policy_version` stays
`adaptive-1`: a list built from today's prior is the same with or without the
field limit.

A prior that names more than 30 attributes and kinds for one framework would
list them all. A limit can come back with that prior, if the list needs one.
