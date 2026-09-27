# 48. A paused job ranks its open facts before any finding

- **Status**: accepted
- **Date**: 2026-09-27
- **Effort**: [#1225](https://github.com/mstarks01/work-agent/issues/1225),
  [#1252](https://github.com/mstarks01/work-agent/issues/1252)
- **Relates to**: [ADR 0045](0045-a-job-that-asks-pauses-after-the-assertion-pass.md),
  whose pause this adds to; [ADR 0047](0047-the-critic-asks-a-kind-of-question-about-an-element.md),
  whose kinds it asks early
- **Evidence**: `QA-2026-09-26-03-E5` and `-E13` in `evals/experiments/`

## Context

A paused job asked only link questions. A fact question was ranked by the
findings that wait on it, and a paused job has no findings. An answer before
the analysis is the one the lanes read, so an early fact question is worth
more than the same question after the report, if the list asks the right
facts.

The question kinds make that possible. They are 45% of what the critic cites
(261 of 577 on two terra passes), and they name an element, so a job can ask
them before any finding exists.

## Decision

**A paused job asks its open facts, ranked before any finding.** For each
element, a paused job asks each attribute its model holds as `unknown`, its
zone where the service inferred it, and each question kind.

**The rank is a prior times the candidates.** The prior says how often each
framework's findings in earlier runs cite that attribute or kind, per element
of that type. The candidates are the framework's rules that fire on the
element. A question's score is the prior times one plus the candidates,
summed over the job's frameworks. On 13 tuned cases, with the prior read
from the other 12 cases, the first six questions a case settled 59% to 64% of
what the report's own six settled, and the first ten 62% to 63%.

**The prior is a table with its provenance.** `question_prior.json` holds one
row per framework, and each row names the runs it counted, the commit and the
number of tuned cases. `run.py question-prior` writes a row from a sweep's
reports, or from critic replays of its drafts. A holdout case is never
counted. A framework whose row counted no run asks nothing early.

**Each question gives its reasons.** They are the questions of the rules that
fire on the element, because no finding exists to cite.

**An early answer takes the answer path that already exists.** The answers
route checks it against the paused model, and the resumed job writes it: an
attribute or a zone onto the model, a kind as a line of the answers Source.

**A candidate still writes no finding.** The ranking reads candidates only to
order questions, so a fired rule cannot become a claim through it.

## Consequences

The count measures findings that the lanes wrote without answers. It cannot
see an early answer change what the lanes write, which is the reason to ask
early. The paid pair on #1225, with signed answers at the pause, is the
measurement for that.

A case gets 60 to 126 early questions. The pages show the first ten and put
the rest one click away.

Only a deployment that builds an assertion catalog can pause, as ADR 0045
decided. An early fact question needs no catalog, so a pause for a deployment
without one is the next change to decide.

The STRIDE row counts E11's two terra replays of Baseline 6bff717. The ASVS
row counts no run, so an ASVS job asks nothing early until a row is counted
from ASVS runs.
