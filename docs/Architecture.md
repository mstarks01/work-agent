# Architecture

Both entry points — the in-process [[Integration-Guide|engine]] and the
[[HTTP-API|`/v1` API]] — drive one Google ADK Workflow graph and shape its
outcome into a [[Report-Schema|`StrideReport`]]. This page is the map of what
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
  over the [[Configuration|150-element cap]], ends as a **rejection**.
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

Per-node model selection through [[Configuration|`config/model_tiers.toml`]]:
`flash` for `extract`/`repair`, `pro` for the six analysts, the `critic`, and
the `recritic`. Deterministic `FunctionNode`s carry no model. Each `pro` call on
the eight-way fan-out plus critic is where the token budget goes.

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

Retry and timeout are configured in [[Configuration|`config/resilience.toml`]]
and bound onto each model at the SDK level, so the report's `nodes` array is
unchanged by a retry. A per-request timeout turns a hang into a retryable error.
Three attempts by default.

## Seams

The pipeline is reached through interfaces, so backends are swappable and the
whole graph runs offline against scripted models:

| Seam | Interface | Default | Status |
| --- | --- | --- | --- |
| Pipeline execution | `PipelineRunner` | `AdkPipelineRunner` (real graph) / `StubPipelineRunner` (tests) | Complete |
| Job persistence | `JobStore` | `InMemoryJobStore` | In-memory only; persistent backend is a deferred choice |
| ADK sessions | `BaseSessionService` | `InMemorySessionService` | In-memory only; a `session_service_uri` backend is unwired |

The in-memory defaults are enough to get a report in process. A persistent
`JobStore` and session backend, deployment (container, Cloud Run, Ping
middleware), and observability are out of scope for the current work — the
interfaces are in place for them.

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
| `stride_service.skills` / `.prompts` / `.markdown_loader` | Skill/prompt loading and composition. |
| `stride_service.model_tiers` / `.sampling` / `.resilience` | Config loaders. |
| `stride_service.auth` | Ping JWT verification. |
