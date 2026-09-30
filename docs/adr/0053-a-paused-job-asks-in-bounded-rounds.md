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

**A paused job asks in rounds.** A round asks up to 10 choices of capability
questions and up to 10 choices of questions about the model's elements (field
questions). A question with facets costs one choice a facet it leaves open,
and any other question costs one. A round of 10 questions asked 17 to 24
choices on the 13 Baseline models, so the unit is the choice, not the
question (#1289). A question wider than the round is still asked alone.
Link questions are outside the round limit, and each round shows every one
still open. On the six archived reports with a catalog, a pause shows 1 to 3,
each one choice that places 1 to 4 stated facts, so they need no place under
the limit; the page counts them in the choices it says a round asks. A submitter saves a round, which writes the answers onto the job
and runs no model. The next round is built from the model with every saved
answer in it, so an answer hides the parts of a capability it rules out, and
a named mechanism lowers the questions that rested on its lead. "Start the
analysis" is available in every round.

**Each kind has a floor and a limit.** `answer_round.EARLY_RULES` is the table.

| Kind | Floor | Limit | Why |
| --- | --- | --- | --- |
| Field | 1 | 30 | The score is a ranking heuristic, the prior's citation rate times one plus the candidates, and 1 is where the ranking cuts. No measurement says how many findings an answer changes, so the floor is not a count of findings. The top 30 hold 99% of a STRIDE list's ranking score at the floor (E20), which is not a share of the report's value. |
| Capability | 2 | 30 | A capability's score counts the units it could settle, not findings, so it takes its own floor. Under 2, it settles one unit. At ASVS level 2 the floor left 28 of 51, and dropped about 6 a job that settle a level 1 requirement while it kept 13 that settle only level 2; a question in the highest band now passes the floor where bands differ (ADR 0055), so 34 pass and the limit asks 30. |

Two limits, not one: capability questions come first, so one shared limit
would give a job that selects both frameworks no field question.

**A limit bounds the questions not yet answered at all.** Every saved answer
counts toward its kind's limit. A question with facets that a round answered
in part comes back first, and takes no place under the limit. Where the limits
hold questions back, the question set says so: `stop` is `budget-exhausted`,
not `nothing-left`, and the page states how many questions will not be asked.

**A submitter can skip a question for now.** A save may name questions of its
round to skip. A skip is not a fact, not an absence and not an "I don't
know": the fact stays open, and the analysis treats it as open. No later
round shows a skipped question, and a skip takes no place under a limit, so
a submitter who cannot answer reaches the next useful questions. The skipped
questions stay listed, and an answer to one is still admitted. A question the
submitter leaves blank without a skip comes back, as before: only an explicit
skip sets a question aside (#1289). A question with facets answered in part
may be skipped with that answer: the facets given are kept, and the rest are
set aside. Without this, such a question came back first in every round and
had no way out; an offline replay asked one fifty rounds over (E26).

**A waiting job counts its saved rounds.** Every answer to a waiting job names
the round revision it was read against, and a save checks it and writes the
next one in one step. Without it, a page left open on an earlier round saved
its answers over a later round's, and nothing said so (#1289).

**A save never starts the analysis.** A save that leaves no question shows
that nothing is left, with the saved answers, which the submitter can still
change. Only a continue starts the analysis. A person who saves progress does
not expect a run to start, least of all where the limits ended the questions
(#1289). A pause that has no question at all, before any answer, starts the
analysis with no page between, because nobody saved anything.

**A saved round does not count toward the three rounds of ADR 0052**, because
it runs no analysis. Starting the analysis with answers counts, as before.

**The page shows what is left and what was answered.** Each round states how
many questions of each kind are likely to remain. That is an estimate: in E20
the count at the start was an upper bound in 202 of 217 STRIDE models, and
answers added at most 9. "Your answers" lists every saved answer, and each can
be changed. A question with facets comes back while a facet has no answer,
with its answered facets filled in.

## Consequences

In E21, with rounds of 10 questions, every replay ended in three rounds. A
STRIDE job asks at most 30 questions, and the limit cuts at most 12. An ASVS
job asks 26 yes/no questions.

In E23 the round size in choices moved only how the work is split. A STRIDE
pause answered in full asks a median of 58 choices at every budget, because
the limit of 30 questions sets the total. At 10 choices a round it takes 7
rounds (5 when every control is named), and an ASVS pause takes 3. A
submitter can stop at any round, and skip, so a smaller page costs a person
who answers everything more rounds, and nobody else anything.

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

**One answer reaches several elements only where the owner ticks them.**
Where a round asks one attribute or question kind about two or more elements
in one form, the page offers a "Same for all" row that copies its answer into
each ticked row. An unticked row is an exception, and every row is still sent
as its own answer, so no answer reaches an element the owner did not choose
(#1289).
