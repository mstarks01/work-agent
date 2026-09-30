# 48. A paused job ranks its open facts before any finding

- **Status**: accepted
- **Date**: 2026-09-27
- **Effort**: [#1225](https://github.com/mstarks01/work-agent/issues/1225),
  [#1252](https://github.com/mstarks01/work-agent/issues/1252)
- **Relates to**: [ADR 0045](0045-a-job-that-asks-pauses-after-the-assertion-pass.md),
  whose pause this adds to; [ADR 0047](0047-the-critic-asks-a-kind-of-question-about-an-element.md),
  whose kinds it asks early
- **Evidence**: `QA-2026-09-26-03-E5` and `-E13` in `evals/experiments/`
- **Amended by**: [ADR 0049](0049-an-answer-binds-to-an-open-fact-and-the-order-completes-findings.md),
  which keeps the prior as the eligibility rule and shares the open-fact rule
  with the answer check

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

**Every deployment can pause.** An early fact question needs no catalog, so a
deployment that builds none runs the head without an assertion pass, stops at
the `pause` node after the validity gate, and asks its early questions and no
link question. A link answer still needs a catalog, and it is refused where
there is none. The eval's heads mode scores a catalog, so it refuses such a
deployment before it runs.

The STRIDE row counts E11's two terra replays of Baseline 6bff717. The ASVS
row counts the archived sweep `20260906T234806Z-asvs-two-question-t1` (11
cases, #1284). That sweep is older than the question kinds, so the row holds
attribute rates only: an ASVS job asks its capability questions and the open
attributes the rates name, and no question kind until a critic replay with
today's prompt counts kind rates.
