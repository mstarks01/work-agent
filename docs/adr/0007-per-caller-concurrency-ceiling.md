# 7. The per-caller concurrency ceiling lives on the job store

- **Status**: accepted
- **Date**: 2026-08-09
- **Effort**: [#113 — POST /v1/jobs has no per-subject rate or concurrency
  limit](https://github.com/mstarks01/work-agent/issues/113)

## Context

`POST /v1/jobs` bounded three things: what one submission carries
(`max_source_bytes`, `max_sources`), what one request weighs (the body cap), and
how long one run takes (`job_deadline_ms`). All three are per *job*, and none
bounds the number of jobs, so nothing between the bearer check and the queued
run consulted how many jobs a subject already had going.

That gap is not cheap to leave open. Each accepted job runs five LLM stages on
the graph's longest path, and the widest of them fans the six category agents
out in parallel on the `strong` tier. Ten concurrent submissions is sixty
concurrent `strong`-tier requests against one shared per-minute quota — from one
valid token, without breaking a single documented bound.

> **Amended by [#286](https://github.com/mstarks01/work-agent/issues/286).** Six
> was the whole fan-out when this was written. It is now one `strong`-tier
> request per lane of every framework a job names —
> `stride_service.frameworks.widest_fan_out`, 23 today — so the burst this ADR
> sizes against is larger than the number above. The decision it argues for is
> unchanged and the arithmetic behind it moved, which is the reason that
> function exists rather than a number in prose. The byte cap already
cites OWASP LLM10; the unbounded-consumption half of LLM10 is the per-caller
budget, and that was the missing half.

The first-run web app has had this gate from the start: `Analyses.claim` refuses
a second concurrent run outright rather than queueing it. The production API,
the surface that actually has multiple callers, had nothing.

## Decisions

### A concurrency ceiling, not a rate limit

The knob counts jobs **in flight** — `queued` plus `running` — rather than
submissions per interval.

A concurrency ceiling is self-clearing: finishing a job is what buys the next
one. That needs no window, no timer, and no state the job store does not already
hold. It also bounds the thing that actually costs money, which is simultaneous
provider calls; a rate over a window bounds that only indirectly, and needs a
clock to do it. The shipped `3` is sized against the fan-out rather than against
demand — three jobs is eighteen concurrent `strong`-tier requests, which is the
burst a per-minute quota sees.

### The counter lives on the `JobStore` seam

Of the three candidates the issue set out — an in-process counter, a
`JobStore.active_for(subject)` query, or enforcement pushed to the edge — the
store seam wins on the argument `build_store` already makes.

`build_store` refuses to default to the `memory` backend on the stated grounds
that per-instance storage "loses every job on restart and isolates jobs behind a
load balancer." A counter held in process state has exactly that defect, and
worse for being invisible: behind two instances the effective ceiling is double
the configured number, and it resets on every deploy. Asked of the store, the
ceiling is precisely as shared as the deployment's storage is, and registering a
shared backend turns it into a shared ceiling with no change at the call site.

The protocol therefore carries a documented atomicity requirement, because the
API calls `active_for` and then `create`: an implementation must observe the
count and the create together, or a burst can overshoot the number.
`InMemoryJobStore` satisfies it by never awaiting inside either method, so
asyncio cannot interleave the pair; a networked backend needs the check and the
insert in one transaction.

Edge enforcement was not chosen *against* — a deployment behind a gateway that
already meters per caller should keep doing that. It was rejected as the only
answer, because it makes the bound invisible to the service and unavailable to
anyone running it any other way.

### It is a version 5 resilience knob, and the cutover is hard

`max_active_jobs` goes in `config/resilience.toml` beside the other six. It
meets that file's stated criterion exactly: an operational bound that cannot
change *which* answer a job produces, only whether the submission is accepted —
so it is env-overridable (`STRIDE_MAX_ACTIVE_JOBS`) and can be turned down
mid-incident without an image rebuild.

Per the repo's no-shim rule, `SUPPORTED_VERSION` moves 4 → 5 and a version-4
file fails the check rather than inheriting a default. There is deliberately no
value meaning "unlimited": that was version 4's behaviour, and it is the defect
this version exists to end. A deployment that has not chosen a ceiling does not
start. `0` is refused for the same reason from the other direction — a
deployment that accepts no jobs should not be running — while `1` is legal and
makes the service strictly serial per caller, matching the web app's gate.

### The refusal is `429`, before the input ladder, with no `Retry-After`

The ceiling is checked *before* the source ladder rather than after. It is a
fact about the caller, not about the submission, so a caller at their ceiling
gets the same answer whatever they sent; checking it second would let a
malformed body outrank it and make the ceiling probe-able through requests that
were never going to run. Auth stays outermost — the ceiling is per subject and
cannot be consulted before there is one.

A submission past the ceiling is refused rather than queued, for the reason
`Analyses.claim`'s docstring already gives: a queued job holds the caller's place
in the provider quota just as a running one does, so only a refusal sheds load.
No `Retry-After` is sent, because what clears the ceiling is a job of the
caller's reaching a terminal state, not the passage of time.

## Consequences

- One token can no longer spend the whole deployment's provider quota. The
  bound is the deployment's to set and is visible in its config rather than
  implied by its infrastructure.
- **The ceiling is only as shared as the configured store.** With the shipped
  `memory` backend behind more than one instance, the effective ceiling is the
  sum across instances. This is stated in `config/resilience.toml` and in
  `docs/Configuration.md` rather than left for an operator to discover, and it
  is an argument for a shared backend, not a caveat that weakens the bound.
- A new `JobStore` backend must implement `active_for` and must make the
  count-then-create pair atomic. The protocol says so; nothing enforces it at
  the type level.
- **Not addressed here**: jobs still run in-process via FastAPI
  `BackgroundTasks`, so accepted work is still work inside the serving
  container, and on Cloud Run CPU outside a request is not guaranteed unless
  CPU-always-allocated is set. The ceiling bounds how much of that can pile up
  per caller, which is the half that was unbounded; where jobs *execute* is a
  separate decision this one does not make.
