# 56. The findings that wait decide the follow-up order

- **Status**: accepted
- **Date**: 2026-10-01
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Amends**: the order of [ADR 0046](0046-every-open-fact-is-asked-and-code-writes-the-answer.md) and [ADR 0055](0055-a-follow-up-asks-first-what-the-most-important-findings-wait-on.md)
- **Evidence**: `QA-2026-09-26-03-E34` and `-E35` in `evals/experiments/`

## Context

A report's follow-up asked its facts in two sections. The first ranked the
open facts in every draft's grounds, rejected and confirmed drafts included,
so that a critic sampled again could not move it (ADR 0046, E7). The facts
only the critic named followed. Inside each section the next question was the
one that completed the most findings, highest band first (ADR 0055).

So a low finding that waits on one fact came before a critical finding that
waits on two, and a critical finding whose fact only the critic named came
after every evidence question. The review of 2026-10-01 asked what "most
important first" means.

## Decision

**"Most important first" means the most important waiting finding first.**
The conditional findings decide the order, the highest band first. Where a
fact completes findings of the highest band left, the next question is the
one that completes the most of them, then the most in each band below. Where
none does, the next questions are the facts of that band's finding with the
fewest choices left, asked together. A lower band never comes before a higher
one that still waits. `questions.follow_up_order` is the one reader.

**The critic's facts take their place by the finding they serve.** A question
keeps its basis: `evidence` where a draft's grounds cite the fact, `critic`
where only a verdict names it. The page labels a critic question in its own
row, because critic and evidence questions now mix.

**The facts no waiting finding needs come last**, in their own section of the
page: the facts of confirmed and rejected drafts, and of a draft that waits
on a fact answered "I don't know", which no answer in the list can complete.

**Bands across frameworks stay as ADR 0055 set them.** Every package's
highest band ranks equal, and a finding counts as one whatever its framework.
That is a stated policy: three level 1 requirements that wait on one fact
come before one critical threat that waits on another. It is not a measured
equivalence of a requirement and a threat.

## Consequences

The order now reads the critic's verdicts, so a critic sampled again moves
it. E35 priced that: each order was built from one of two critic samples of
Baseline 6bff717's drafts and scored on the other. Critical and high findings
completed at 5 choices rose from 42 to 54, and at 10 from 50 to 71. Findings
completed in all fell at 2 to 5 choices (75 to 59 at 5), and recovered by 10.
On the archive, ASVS level 1 requirements completed at 5 choices rose from 73
to 138.

Band-first ranking with the evidence section kept first gained nothing (E34).
The gain comes from ranking over the findings that wait.

The crossed measurement covers STRIDE only, on 13 tuned cases, because only
STRIDE has two critic replays of the same drafts.
