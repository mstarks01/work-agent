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
> `analysis_service.frameworks.widest_fan_out`, 23 today — so the burst this ADR
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
`JobStore` query, or enforcement pushed to the edge — the store seam wins on the
argument `build_store` already makes.

`build_store` refuses to default to the `memory` backend on the stated grounds
that per-instance storage "loses every job on restart and isolates jobs behind a
load balancer." A counter held in process state has exactly that defect, and
worse for being invisible: behind two instances the effective ceiling is double
the configured number, and it resets on every deploy. Asked of the store, the
ceiling is precisely as shared as the deployment's storage is, and registering a
shared backend turns it into a shared ceiling with no change at the call site.

The protocol therefore carries the atomicity requirement in its **signature**.
`JobStore.reserve(record, *, ceiling)` counts the owner's jobs in flight and
inserts the record as one operation, and the protocol exposes neither a bare
count nor an unconditional create beside it. A caller holding both would write
the check-then-act race by hand: two submissions that each read the count before
either inserts both pass a ceiling of one. `InMemoryJobStore` satisfies the
requirement by never awaiting inside `reserve`, so asyncio cannot interleave a
second reservation with it; a networked backend needs the count and the insert
in one transaction or one conditional write.

> **Amended by [#505](https://github.com/mstarks01/work-agent/issues/505).**
> The pair above was `active_for` then `create`, with the atomicity stated in
> prose and nothing enforcing it. A backend author could satisfy the type and
> miss the paragraph. One method cannot be used non-atomically.

Edge enforcement was not chosen *against* — a deployment behind a gateway that
already meters per caller should keep doing that. It was rejected as the only
answer, because it makes the bound invisible to the service and unavailable to
anyone running it any other way.

### It is a version 5 resilience knob, and the cutover is hard

`max_active_jobs` goes in `config/resilience.toml` beside the other six. It
meets that file's stated criterion exactly: an operational bound that cannot
change *which* answer a job produces, only whether the submission is accepted —
so it is env-overridable (`ANALYSIS_MAX_ACTIVE_JOBS`) and can be turned down
mid-incident without an image rebuild.

Per the repo's no-shim rule, `SUPPORTED_VERSION` moves 4 → 5 and a version-4
file fails the check rather than inheriting a default. There is deliberately no
value meaning "unlimited": that was version 4's behaviour, and it is the defect
this version exists to end. A deployment that has not chosen a ceiling does not
start. `0` is refused for the same reason from the other direction — a
deployment that accepts no jobs should not be running — while `1` is legal and
makes the service strictly serial per caller, matching the web app's gate.

### The refusal is `429`, after the input ladder, with no `Retry-After`

The ceiling is checked *after* the source ladder. `reserve` enforces it and
`reserve` needs the record, and the record is built from the resolved framework
selection and the validated sources — so the ladder has to run first.

This reverses the original ordering, which put the ceiling first on the grounds
that it is a fact about the caller rather than about the submission, so a caller
at their ceiling should get the same answer whatever they sent. That property is
real and it is now gone: a submission which breaches a rung *and* sits on the
ceiling hears about the rung. The trade is deliberate. A count taken before the
ladder is a count another submission can land behind, and an ordering preference
does not outrank a race that lets a burst overshoot the number. Both answers
refuse the request and neither runs a model, so nothing the ceiling exists to
stop gets through the new ordering.

Auth stays outermost — the ceiling is per subject and cannot be consulted before
there is one.

A submission past the ceiling is refused rather than queued, for the reason
`Analyses.claim`'s docstring already gives: a queued job holds the caller's place
in the provider quota just as a running one does, so only a refusal sheds load.
No `Retry-After` is sent, because what clears the ceiling is a job of the
caller's reaching a terminal state, not the passage of time.

## Consequences

- One token can no longer run more than `max_active_jobs` jobs **at once**. The
  bound is the deployment's to set and is visible in its config rather than
  implied by its infrastructure. It bounds concurrency, not cumulative spend: a
  caller who submits serially, letting one job finish before the next, stays
  inside the ceiling while spending without limit over time. The unbounded-
  consumption half of OWASP LLM10 is therefore **not** closed here.

  > **Closed by [#503](https://github.com/mstarks01/work-agent/issues/503).**
  > It was left to the integrator, with a per-caller rate limit at the edge. It
  > is now enforced by the service: `analysis_service.budgets` adds a per-subject
  > job rate, a per-subject token budget and a deployment-wide token budget over
  > a rolling window, all decided inside the same `reserve` call as the ceiling.
  > An edge that meters per caller should keep doing so, and a provider-side
  > spend limit is still the backstop behind both — that part of the reasoning
  > holds.
- **The ceiling is only as shared as the configured store.** With the shipped
  `memory` backend behind more than one instance, the effective ceiling is the
  sum across instances. This is stated in `config/resilience.toml` and in
  `docs/Configuration.md` rather than left for an operator to discover, and it
  is an argument for a shared backend, not a caveat that weakens the bound.
- A new `JobStore` backend must implement `reserve` as one transaction or one
  conditional write. The protocol offers no second way to admit a job, so an
  implementer cannot satisfy the type and miss the requirement. `reserve`
  distinguishes `at_ceiling` from `duplicate`, and raises on a backend failure
  rather than returning an outcome — a store that cannot answer must not read as
  a store that said no.
- **Not addressed here**: jobs still run in-process via FastAPI
  `BackgroundTasks`, so accepted work is still work inside the serving
  container, and on Cloud Run CPU outside a request is not guaranteed unless
  CPU-always-allocated is set. The ceiling bounds how much of that can pile up
  per caller, which is the half that was unbounded; where jobs *execute* is a
  separate decision this one does not make.
