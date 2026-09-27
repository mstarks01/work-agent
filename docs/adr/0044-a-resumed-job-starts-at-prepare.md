# 44. A resumed job starts at prepare

- **Status**: accepted
- **Date**: 2026-09-27
- **Effort**: [#1252](https://github.com/mstarks01/work-agent/issues/1252),
  step 1
- **Relates to**: [ADR 0043](0043-a-link-answer-is-closed-and-code-writes-it.md),
  whose answers this carries into the analysis
- **Evidence**: `QA-2026-09-26-03-E3` and `-E4` in `evals/experiments/`

## Context

An answer to a report's question reached the analysis only through a new
full submission. That job extracted, asserted and analysed from the start
again. It paid for every node again, and a run that spelled a principal
differently placed nothing (`unmatched-link`).

## Decision

**A caller answers against the finished job** with
`POST /v1/jobs/{id}/answers`, and the service starts a **new** job that
resumes from the finished one.

**The new job starts at `prepare`**, on a graph entry of its own (`resume`).
The finished job's **Valid System Model** and assertion record are copied onto
the new job at submission, and seeded at the state keys a full run writes them
to. No reading node and no assertion pass runs. `prepare` writes the answers
into that catalog and gates it, as it gates every catalog. The builder refuses
an assertion pass or a source review on this entry.

**The answers carry forward.** A new answer about a principal replaces the
finished job's answer about the same one, and the others stay. The composed
answers Source is written again from the merged set.

**A resumed job is a new job** (maintainer's decision, 2026-09-27). It is
admitted through the same helper as a submission, against the in-flight ceiling
and the token budget, because its lanes and critics spend model calls. The
finished job and its report do not change.

**A report the gate withholds cannot be answered**, because the route reads it
through the same rule that serves it.

## Consequences

The runner is memoized per selection and entry, so a deployment holds at most
one resumed graph per selection.

The token reservation for a resumed job uses the same estimate as a
submission, which includes extraction that does not run. The reservation is
replaced by the measured tokens when the job ends, so the over-reservation
lasts only while the job runs.

This is the capability the pause after extraction needs (#1252, step 3): a job
that stops after the assertion pass resumes here when its answers arrive.
