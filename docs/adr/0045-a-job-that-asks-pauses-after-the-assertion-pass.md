# 45. A job that asks pauses after the assertion pass

- **Status**: accepted
- **Date**: 2026-09-27
- **Effort**: [#1252](https://github.com/mstarks01/work-agent/issues/1252),
  step 3
- **Relates to**: [ADR 0044](0044-a-resumed-job-starts-at-prepare.md), whose
  resumed job continues a paused one
- **Evidence**: `QA-2026-09-26-03-E4` in `evals/experiments/`

## Context

An answer that arrives after the report never reaches the lanes that wrote
the conditional findings. An answer before the analysis changes what the lanes
read: a principal's facts reach the rules once it is linked. The maintainer
decided on 2026-09-27 that a job should wait for a person indefinitely when a
toggle that raises questions is on, and never wait in an autonomous run.

## Decision

**A per-job toggle, `questions`, off by default.** A submission that sets it
runs the head-only graph: extraction, the validity gate, the bounded repair
and the assertion pass, then the catalog. An autonomous caller that does not
set it runs the full graph and never waits.

**The run ends in `awaiting-answers`**, holding a `Checkpoint`: the **Valid
System Model** and the gated assertion record. The wait sits between two runs,
never inside one, because every run is bounded by the job deadline and the
wait is not.

**`awaiting-answers` is terminal** for the lifecycle and for admission. A job
waiting on a person takes no in-flight slot, so a person may leave several
waiting, and its tokens settle as a rejected job's do, from the node runs its
run returned.

**Answering it starts a resumed job** (ADR 0044) from the checkpoint it holds.
`{"links": []}` is legal only here, and means "continue without answers".

**The questions a paused job asks are the link questions** from the catalog
it holds, and the early fact questions of
[ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md).

## Consequences

The job store is still in memory, and the choice of a persistent store is
still deferred. A restart loses every waiting job and its extraction. The
pause ships behind the toggle with that limit stated in the API document.

A resumed job carries the parent's ID, so a paused job and its continuation
are linked, but the paused job's own status does not change when it is
answered: it can be answered again, and each answer starts a new job.
