# 55. A follow-up asks first what the most important findings wait on

- **Status**: accepted
- **Date**: 2026-09-30
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Amends**: the order of [ADR 0049](0049-an-answer-binds-to-an-open-fact-and-the-order-completes-findings.md)
- **Relates to**: [ADR 0054](0054-a-report-offers-one-follow-up.md)
- **Evidence**: `QA-2026-09-26-03-E25` in `evals/experiments/`

## Context

A report's follow-up asked first the question that completes the most
findings. A question that completes one critical finding therefore came after
one that completes three low ones. The owner answers from the top and can stop
at any point, so the order decides which findings an owner's first few answers
can settle.

## Decision

**Each Framework Package declares how important one of its claims is.** The
package member `rank` returns a `Band`: an order, the higher first, and a label
a page shows. STRIDE reads the severity band the lane agent rated. ASVS grades
no harm, and 5.0 defines a level as a requirement's priority, so ASVS reads the
level, with level 1 first. A package that grades nothing returns one band for
every claim. Importance is judgement about a framework's own method, which is
why a package declares it: a new package fails to construct until it answers.

**The order is lexicographic by band, with no weights.** The next question
completes the most findings in the highest band, then in the next band down,
and so on. Where none completes a finding, the next are the facts of the
highest-band finding with the fewest left. Weights would be numbers nobody
measured.

**Every draft ranks by its band, whatever its verdict.** The evidence section
still does not depend on the critic (ADR 0046): a band is the lane's rating or
the catalog's, and a critic ruling sets neither.

**Each question names the highest band that waits on it**, as `band`, read
from the conditional findings it can settle.

**The early questions at the pause keep their ranking.** No finding exists
before the analysis, so there is nothing to rank by.

## Consequences

In E25, over the 52 reports of the four Baselines, the first 3 questions cover
202 critical and high findings where they covered 173, and 295 findings in all
where they covered 310. At 10 questions both orders cover 613. An owner who
stops early settles more of what matters and slightly fewer findings in all.
