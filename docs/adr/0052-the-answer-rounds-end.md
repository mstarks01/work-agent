# 52. The answer rounds end

- **Status**: accepted
- **Date**: 2026-09-29
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Relates to**: [ADR 0046](0046-every-open-fact-is-asked-and-code-writes-the-answer.md),
  whose list this shortens after the first round, and
  [ADR 0049](0049-an-answer-binds-to-an-open-fact-and-the-order-completes-findings.md),
  whose `unknown` answer this makes final for its fact

## Context

A submitter answers questions at the pause and after each report. Each round
after a report runs the analysis again, and the reviewer can name new facts in
each run. Nothing ended the rounds. A fact answered "I don't know" also stayed
open, so every later round asked it again.

## Decision

**A later round does not ask a fact that an earlier round answered.** An
"I don't know" answer counts. A question with facets is asked again only while
a facet has no answer. `questions.answered_keys` is the one rule, and the early
list and the report list both read it. A submitter can still send a new answer
to an answered fact, to change it.

**A finding that waits on an "I don't know" fact is not counted.** No answer
in the list can cover it, so the count leaves it out.

**A job lineage takes at most three rounds of answers.** The rounds at the
pause and after each report are counted together. `Resumption.round` holds
the count, and each resumed job adds one. A job that reached the limit asks
nothing and admits no answer, so its report is final. The questions payload
carries `answer_rounds_left`, and the report page says how many remain.

Three is a judgement, not a measurement. The limit is `MAX_ANSWER_ROUNDS` in
`analysis_service.answer_round`.

## Consequences

A submitter knows how many rounds remain, and a round never repeats a
question. A reviewer that names new facts in every run cannot keep the rounds
going.

A lineage that reaches the limit with open facts ends with those facts open.
The report still lists them under its conditional findings.

The rounds at the pause that the bounded pre-analysis plan adds will count
against the same limit only if they run the analysis. A saved round that runs
no model is a separate question for that plan's ADR.
