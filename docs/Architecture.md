# Architecture

Both entry points — the in-process [engine](Integration-Guide.md) and the
[`/v1` API](HTTP-API.md) — drive one Google ADK Workflow graph and shape its
outcome into a [`StrideReport`](Report-Schema.md). This page is the map of what
runs between text in and report out.

## The pipeline

A static ADK Workflow with deterministic `FunctionNode` bookends around the
model calls:

```
extract -> validate -> prepare -> [6 analysts] -> merge -> critic -> route_review
                |  ^                                                      |
             (repair)                                            accept / revise
                                                                   |        |
                                                              assemble   recritic -> rereview
                                                                              accept / revise
                                                                                |        |
                                                                           assemble  critic_failed
```

- **extract** turns the untrusted text into a canonical system model (five DFD
  element types: external entity, process, data store, data flow, trust
  boundary).
- **validate** is a mechanical gate. Failures route to **repair** (one bounded
  pass over the original text) and revalidate; a model that still fails, or is
  over the [150-element cap](Configuration.md), ends as a **rejection**.
- **prepare** derives the per-analysis context.
- **six analysts** run in parallel, one per STRIDE category, each drafting
  threats in its lane.
- **merge** joins the drafts; the **critic** rules on all of them in one pass —
  verdicts, dedupe, severity calibration.
- **route_review** runs the mechanical checks the assembler depends on. If the
  critic's output fails them, one bounded **recritic** re-ask runs; a second
  failure is a `failed` job, not a rejection.
- **assemble** builds the final report.

Untrusted input never becomes an instruction: it enters session state as fenced
data for the extraction prompt (OWASP LLM01). Mechanical checks live in code;
the models are asked only for judgement.

## Models

Every LLM node runs on one of two **model tiers** — named for the job they do,
not for any vendor's product line:

- **`base`** — the workhorse: `extract` and `repair`.
- **`strong`** — judgement: the six analysts, the `critic`, and the `recritic`.

[`config/model_tiers.toml`](Configuration.md) maps nodes to tiers and each tier
to a `(vendor, model)` pair. Deterministic `FunctionNode`s carry no model. The
`strong` tier is where the token budget goes — the eight-way fan-out plus the
critic.

The two tiers choose their vendor **independently**, so `base` and `strong` can
run different vendors at the same time. Every vendor is reached through one
adapter (LiteLLM), and the ten LLM nodes share **two** adapter instances — one
per tier — so the startup checks on credentials and decoding parameters run
twice rather than ten times.

## Provenance and certification

Each node records what actually answered it: the **served** model build (the
build string the provider reported, prefixed with its vendor), the **requested**
route, and a **fingerprint** — a hash of the served route plus the tier's
decoding parameters. These land in the report's `nodes` array, so a report
explains its own origin without trusting an outside record.

**Certification** compares those fingerprints against the ones this deployment
has **blessed** — approved by a sanctioned measured run and recorded in
`config/blessed-fingerprints.toml`. It has three results: `certified` (every
fingerprint observed is blessed), `uncertified` (at least one is not), and
`unexercised` (a tier the graph declares produced no fingerprint at all).

The service certifies every job it completes — this is not the eval harness's
job alone. The result lands on the job record, never on the report: the report
is portable evidence that travels with the analysis, while a match against a
blessed list is one deployment's claim about it. See
[Configuration](Configuration.md#provenance-and-certification) for the full
rules and the environment variables that control them.

## Outcomes

The graph reaches one of three states, surfaced identically by both entry
points:

- **completed** — a `StrideReport`.
- **rejected** — the input failed the validity gate; carries the
  `ValidationIssue`s.
- **failed** (raises) — an internal error. No partial report is ever produced:
  an empty Tampering section means "looked, found nothing", never "the analyst
  errored".

## Resilience

Retry and timeout are configured in [`config/resilience.toml`](Configuration.md)
and attached to the adapter itself, so a retry is invisible to the graph and the
report's `nodes` array is unchanged by one. A per-request timeout turns a hang
into an error the retry can act on. Three attempts by default.

## Concurrency and isolation

Concurrent analyses are independent. Each `analyze()` call runs one job in its
own ADK session, created fresh with a unique session id and seeded with only that
job's own input text; the graph's per-run data lives entirely in that session's
state, read back by the same session id. The engine, runner, and compiled
pipeline hold no per-run state — they carry only read-only configuration (the
node→model map, the loaded prompts and skills), so one engine is safe to share
across every call.

- **Isolation is per session, not per caller.** Two analyses submitted at the
  same time by the same caller still get separate sessions, so they can never
  read or overwrite each other's state.
- **Within a single analysis**, the six analysts run in parallel but each writes
  to its own category-keyed slot in the session, so the parallel branches don't
  clobber one another before the merge.
- **Untrusted input stays contained.** Because a job's text lives only in its own
  session (as fenced data — see [The pipeline](#the-pipeline)), a prompt-injection
  attempt in one submission cannot reach another running analysis.

This guarantee assumes the intended concurrency model: `async` calls on a single
event loop. The default `InMemorySessionService` is an in-process store — safe
for cooperative async concurrency with distinct session ids, but not a
thread-safe store to share across OS threads. Scaling across processes keeps
analyses isolated (nothing is shared), but then each worker has its own in-memory
job and session state, so a job must be routed to the worker that holds it —
which is what the persistent backends below are for.

## Seams

The pipeline is reached through interfaces, so backends are swappable and the
whole graph runs offline against scripted models:

| Seam | Interface | Default | Status |
| --- | --- | --- | --- |
| Pipeline execution | `PipelineRunner` | `AdkPipelineRunner` (real graph) / `StubPipelineRunner` (tests) | Complete |
| Job persistence | `JobStore` | `InMemoryJobStore` (`memory`) | Backend selected by `STRIDE_JOB_STORE` via a fail-closed registry; only the non-durable `memory` backend ships — a durable one is a new registry entry |
| ADK sessions | `BaseSessionService` | `InMemorySessionService` | In-memory only; a `session_service_uri` backend is unwired |

The in-memory defaults are enough to get a report in process. Choosing a backend
is already wired for the `JobStore` (`STRIDE_JOB_STORE`, which stops startup on
an unset or unknown value rather than quietly falling back). Still out of scope
for the current work: a durable `JobStore` implementation, a session backend,
deployment packaging (container, Cloud Run), and observability. The interfaces
and the selection seam are in place for all of them.

## Where the code lives

| Module | Responsibility |
| --- | --- |
| `stride_service.engine` | In-process `StrideEngine` facade. |
| `stride_service.api` | The `/v1` FastAPI app. |
| `stride_service.jobs` | Job lifecycle, `JobStore`, `PipelineRunner` seams. |
| `stride_service.pipeline` | `AdkPipelineRunner`, production graph wiring. |
| `stride_service.graph` | Topology and node functions. |
| `stride_service.system_model` | Canonical model + validity helpers. |
| `stride_service.report` | `StrideReport` and the severity model. |
| `stride_service.validation` | The mechanical validity gate. |
| `stride_service.critic` | The mechanical checks around the critic step — the ones no model should be asked to perform. |
| `stride_service.skills` / `.prompts` / `.markdown_loader` | Skill/prompt loading and composition. |
| `stride_service.model_tiers` / `.sampling` / `.resilience` | Config loaders. |
| `stride_service.vendors` | The vendor registry: each vendor's router prefix, credential mode, and model-name rules. |
| `stride_service.binding` | Builds one adapter per tier from `(vendor, model, sampling, resilience)`. |
| `stride_service.model_gate` | The startup check that asks the provider library whether a tier's parameters are actually supported. |
| `stride_service.certification` | Compares a run's fingerprints against the deployment's blessed manifest. |
| `stride_service.auth` | Bearer-token (OIDC JWT) verification. |
