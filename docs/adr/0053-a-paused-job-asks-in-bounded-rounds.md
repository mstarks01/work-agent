# 53. A paused job asks in bounded rounds

- **Status**: accepted
- **Date**: 2026-09-29
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Relates to**: [ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md),
  whose ranking the rounds keep, and
  [ADR 0052](0052-the-answer-rounds-end.md), whose limit of three rounds a
  saved round does not count toward
- **Evidence**: `QA-2026-09-26-03-E20` and `-E21` in `evals/experiments/`

## Context

A paused job showed its whole early list in one page: 89 questions for the
median STRIDE model, 126 at most, and 51 capability questions at ASVS level 2.
Any answer started the analysis, so a submitter had one chance to answer.

## Decision

**A paused job asks in rounds.** A round shows at most 10 capability
questions and at most 10 questions about the model's elements (field
questions). A submitter saves a round, which writes the answers onto the job
and runs no model. The next round is built from the model with every saved
answer in it, so an answer hides the parts of a capability it rules out, and
a named mechanism lowers the questions that rested on its lead. "Start the
analysis" is available in every round.

**Each kind has a floor and a limit.** `answer_round.EARLY_RULES` is the table.

| Kind | Floor | Limit | Why |
| --- | --- | --- | --- |
| Field | 1 | 30 | Under 1, a question is expected to change less than one finding. The top 30 hold 99% of a STRIDE list's score at the floor (E20). |
| Capability | 2 | 30 | A capability's score counts the units it could settle, not findings, so it takes its own floor. Under 2, it settles one unit. At ASVS level 2 the floor leaves 28 of 51. |

Two limits, not one: capability questions come first, so one shared limit
would give a job that selects both frameworks no field question.

**When nothing is left, the analysis starts.** A save that leaves no question
starts the analysis, as a continue with no new answers does. A pause that has
no question at all starts it too, with no page between.

**A saved round does not count toward the three rounds of ADR 0052**, because
it runs no analysis. Starting the analysis with answers counts, as before.

**The page shows what is left and what was answered.** Each round states how
many questions of each kind are likely to remain. That is an estimate: in E20
the count at the start was an upper bound in 202 of 217 STRIDE models, and
answers added at most 9. "Your answers" lists every saved answer, and each can
be changed. A question with facets comes back while a facet has no answer,
with its answered facets filled in.

## Consequences

In E21 every replay ended in three rounds. A STRIDE job asks at most 30
questions, and the limit cuts at most 12. An ASVS job asks 26 yes/no
questions.

A saved round is kept on the waiting job, through `JobStore.save_round`, which
writes the answers and nothing else. A first-run app restart still loses a
paused run (#1282).

**A capability and an attribute can ask about related facts, and neither
answer writes the other.** A flow's authentication and the `oauth`,
`authentication` or `mutual-tls` capabilities are examples, as are
`encryption_at_rest` and the `encryption` capability. No pair has a safe
mechanical link: a capability is about the whole application, an attribute is
about one element, and a free-text mechanism is not a capability code can
read. The analysis sees both answers in the answers Source.
