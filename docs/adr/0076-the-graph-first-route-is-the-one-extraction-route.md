# 76. The graph-first route is the one extraction route

- **Status**: accepted
- **Date**: 2026-10-11
- **Effort**: [#1003](https://github.com/mstarks01/work-agent/issues/1003)
- **Relates to**: [ADR 0034](0034-an-assertion-is-a-scoped-fact-with-a-support-span.md),
  which states what an assertion row is, and
  [ADR 0038](0038-a-component-reaches-the-graph-only-in-a-zone.md), whose
  placement contract the removed resolver read
- **Evidence**: `QA-2026-10-10-01-E1` in `evals/experiments/`, and the
  comments on #1003 of 2026-09-17, 2026-09-24 and 2026-10-10

## Context

#1003 asked whether reading the source facts first, and binding them to the
System Model afterwards, recovers more stated facts than the graph-first route
with the assertion pass. It built four arms behind flags that were off by
default: a facts-first reading in one call and in two calls, and a bounded
source review pass over either head.

All arms ran once over the 13 tuning cases on one model. The graph-first route
scored 0.374 macro recall of stated required facts. The one-call facts-first
reading scored 0.290, and its interval against the graph-first route excludes
zero. The review pass moved neither route outside the spread: +0.013 on the
graph-first route and +0.029 on the facts-first route, with both intervals
including zero. On the facts-first route the review discarded 7 of 13 batches,
because the operations named endpoints and subjects the model did not hold.

## Decision

**The graph-first route is the one extraction route** (maintainer's decision
of 2026-10-11). The facts-first reading, the split reading and the source
review pass are removed from the tree, with their prompts, their resolver, their
patch applicator, their deployment flags and the eval instruments that compared
them. `execution.extraction_strategy` on a report keeps its field and admits
`graph-first` only, so every archived report still loads.

The head-only graph entry stays. A job that asks questions pauses on it, and the
heads eval mode scores a catalog on it.

## Consequences

- A deployment has one extraction order, selected by nothing.
- `evals/emissions/20260917T-arms-luna-pro/` keeps the graph-first sweep only.
  The other arms' sweeps are in git history, and the ledger rows that cite
  them stay as the record of what was measured.
- The oracle and bottleneck instruments are gone with the resolver they read.
  A perfect-reading ceiling is read through `lane-replay --prepare-today
  signed`, which puts the signed facts through the shipped route.
