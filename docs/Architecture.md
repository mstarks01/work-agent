# Architecture

Both entry points — the in-process [engine](Integration-Guide.md) and the
[`/v1` API](HTTP-API.md) — drive one Google ADK Workflow graph and shape its
outcome into a [`Report`](Report-Schema.md). This page is the map of what
runs between text in and report out.

## The pipeline

A static ADK Workflow with deterministic `FunctionNode` bookends around the
model calls:

```mermaid
flowchart TD
    start([text in]) --> extract["extract<br/>(base)"]
    extract --> validate{{validate}}
    validate -- valid --> prepare[prepare]
    validate -- invalid --> repair["repair<br/>(base)"]
    repair --> revalidate{{revalidate}}
    revalidate -- valid --> prepare
    revalidate -- invalid --> reject([rejected])

    prepare --> analyze["lane agents, in parallel<br/>one per lane of each framework<br/>(strong)"]
    analyze --> merge["merge<br/>(per framework)"]
    merge --> critic["critic<br/>(per framework, strong)"]
    critic --> router{{route_review}}

    router -- accept --> assemble[assemble]
    router -- revise --> recritic["recritic<br/>(per framework, strong)"]
    recritic --> rereview{{rereview}}
    rereview -- accept --> assemble
    rereview -- revise --> failed([failed])
    assemble --> report([Report])

    classDef llm fill:#ede9fe,stroke:#7c3aed,stroke-width:1.5px,color:#2e1065
    classDef code fill:#e0f2fe,stroke:#0284c7,stroke-width:1.5px,color:#082f49
    classDef gate fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#451a03
    classDef good fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#052e16
    classDef bad fill:#fee2e2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a
    classDef io fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#0f172a

    class extract,repair,analyze,critic,recritic llm
    class prepare,merge,assemble code
    class validate,revalidate,router,rereview gate
    class report good
    class reject,failed bad
    class start io
```

Purple nodes are model calls. Everything else is a deterministic `FunctionNode`:
blue ones do work, amber ones only choose an edge, and the rounded ends are the
run's three outcomes.

- **extract** turns the untrusted text into a canonical system model (five DFD
  element types: external entity, process, data store, data flow, trust
  boundary).
- **validate** is a mechanical gate. Failures route to **repair** (one bounded
  pass over the original text) and revalidate; a model that still fails, or is
  over the [150-element cap](Configuration.md), ends as a **rejection**.
- **prepare** derives the per-analysis context, all of it a pure function of the
  validated model: the boundary crossings; the **deterministic candidates** for
  each lane; the **domain packs** this system earns; and the system model as the
  agents will see it — with `source_excerpt`, `source_label` and
  `source_speaker` stripped, so the only submitter words downstream of here are
  the ones a finding chose to quote.
- **lane agents** (`analyze_<framework>_<lane>`) run in parallel — one per lane
  of each framework the job selected, which for STRIDE is its six categories.
  Each drafts claims in its own lane, and each claim cites at least one
  **ground**: a quote from the submitted text, an `unknown` attribute, or a
  boundary crossing. A **candidate is never one of those** — it is a structural
  lead code found, which an agent may investigate and reject, and which nothing
  downstream of the prompt reads.
- **merge** joins one framework's drafts and runs the mechanical half of the
  fan-in: every reference resolves, no two lanes reused a claim ID, and every
  quote ground is matched against the bytes of the source it names. A refused
  quote is rewritten to the source's own nearest span where one is near enough
  ([`repaired_quotes`](Report-Schema.md#repaired_quotes--quotes-rewritten-to-the-sources-own-span)).
  An unverifiable quote is marked and still renders; a claim where *nothing*
  verifies is dropped and marked as a
  [`groundless_claims`](Report-Schema.md#groundless_claims--claims-that-lost-every-ground)
  entry. It also computes the per-lane
  [`coverage`](Report-Schema.md#coverage--what-each-lane-was-offered) account
  over the drafts.
  Then that framework's **critic** rules on all of them in one pass — verdicts,
  dedupe, severity calibration — spending judgement only on what code cannot
  check. **Each package carries its own critic, blind to every other
  framework**: two frameworks' subgraphs never touch between `prepare` and
  `assemble`, because a critic rules its own framework's claims against its own
  framework's question.
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
- **`strong`** — judgement: every framework's lane agents, its `critic`, and the
  `recritic`.

[`config/model_tiers.toml`](Configuration.md) maps nodes to tiers and each tier
to a `(vendor, model)` pair. Deterministic `FunctionNode`s carry no model. The
`strong` tier does most of a job's model work — the six-way category fan-out
plus the critic and its re-ask.

The two tiers choose their vendor **independently**, so `base` and `strong` can
run different vendors at the same time. Every vendor is reached through one
adapter (LiteLLM), and the ten LLM nodes share **two** adapter instances — one
per tier — so the startup checks on credentials and decoding parameters run
twice rather than ten times.

## Provenance and certification

Three terms, defined once and used throughout:

- **Served build** — the model identifier the provider says actually answered a
  request, prefixed with its vendor (`vertex_ai/gemini-2.5-pro-002`). Not
  necessarily the one you asked for. Gemini is the worked example here because
  one had to be. It is also the one profiled family whose served build differs
  from the route you asked for, which is the distinction these three terms
  exist to draw. On Claude, both model fields hold the same string.
- **Fingerprint** (also *generation identity*) — `sha256` of the served route
  plus that tier's resolved decoding parameters. One value that identifies
  exactly how a node produced its output.
- **Blessed** — a fingerprint recorded in `config/blessed-fingerprints.toml`
  because a measured, sanctioned run produced it. The list is this deployment's
  own; nothing about it ships from this repo.

Every run is **self-describing**: each node records what it asked for, what
answered, and the fingerprint of the two together.

| Field | What it holds |
| --- | --- |
| `NodeRun.requested_model` | The configured route — what was asked for (`vertex_ai/gemini-2.5-pro`). |
| `NodeRun.model` | The served build — what actually answered (`vertex_ai/gemini-2.5-pro-002`). |
| `NodeRun.sampling_fingerprint` | The fingerprint of the served route and the tier's decoding params. |

The report records both model fields and **compares neither**. It doesn't need
to: if the build moves, the fingerprint changes too, and no blessed list
contains it — so the run reads as uncertified and the drift surfaces there
instead.

The fingerprint is computed **per node execution**, not once at startup. A build
that moves partway through a run therefore gives one node two different
identities, which is the signal you want rather than a defect. The vendor prefix
is part of the hash because a served identifier alone carries no vendor —
Vertex-hosted Claude and Anthropic-direct Claude return identical build strings.

`config/blessed-fingerprints.toml` records blessed fingerprints **per tier**,
not per node. A fingerprint contains no node name, and `critic` and `recritic`
run on the same tier, so they present a byte-identical hash; keying by node
would call that one hash blessed under `critic` and unblessed under `recritic`,
marking the first revise path in production uncertified on a technicality.

The list is **deployment-local**. This project can never ship a run that already
counts as certified, because a repo-level blessing plus a local one could only
resolve as one silently overriding the other.
`STRIDE_BLESSED_FINGERPRINTS` chooses *which* single file is read — it does not
layer a second one on top.

**The service certifies every job it completes**, not just the eval harness. The
result has three states and lives on the job record, never on the report: the
report is portable evidence that travels with the analysis, while a blessed list
is one deployment's claim about it.

| State | Meaning | Effect on `GET /v1/jobs/{id}/report` |
| --- | --- | --- |
| certified | Every observed fingerprint is blessed | Served |
| uncertified | At least one is not | Served **unless** `STRIDE_REQUIRE_CERTIFIED` |
| unexercised | A tier the graph declares presented no fingerprint at all | **Always** withheld |

The lists ship **empty**, so until you promote a measured baseline every run
reads as uncertified. That is recorded, not fatal — a gate that fires before
anyone knows the normal range just trains people to switch it off. Promoting one
is `python -m evals.harness.run promote <artifact> --yes`, which derives the
blessed fingerprints from the served builds the sweep observed rather than from
anything typed in by hand — see
[TUNING.md](../evals/TUNING.md#step-5--promote-the-winner).
`unexercised` is different: every tier has a node that always runs, so it cannot
happen on a run that produced a report at all. It is an internal assertion, and
enforcing it costs nothing.

Withholding refuses the *report*; it never fails the job. A failed job carries no
report at all, and the fingerprints that show what drifted live inside it.
Nothing about certification appears in the job status view — it is operator-only.

| Variable | Effect |
| --- | --- |
| `STRIDE_REQUIRE_CERTIFIED` | Withhold the report when the run is uncertified. Off by default. |

## Outcomes

The graph reaches one of three states, surfaced identically by both entry
points:

- **completed** — a `Report`.
- **rejected** — the input failed the validity gate; carries the
  `ValidationIssue`s.
- **failed** (raises) — an internal error. No partial report is ever produced:
  an empty Tampering section means "looked, found nothing", never "the Tampering
  agent errored".

That last guarantee is enforced, not assumed. An LLM node whose completion is
truncated writes no output key at all — ADK saves one only from a final event
carrying text — so "the agent errored" and "the agent found nothing" arrive
as an *absent* key and an *empty* one. `merge_drafts` distinguishes them and
fails the job on the first, naming the lanes and the knob; `validate_extraction`
does the same one node earlier. Read as equivalent, a truncated agent would
delete a sixth of the analysis and finish green, because the critic rules what
it is handed and `by_category` omits a lane with no threats rather than
carrying a zero.

## Resilience

Retry, timeout and the per-job deadline are configured in [`config/resilience.toml`](Configuration.md)
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
- **Within a single analysis**, the lane agents run in parallel but each writes
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
| `stride_service.sources` | `Source`: the untrusted text a job is built from, the per-deployment bounds both entry points enforce, and the fenced render that is the only way caller bytes reach a model (OWASP LLM01). |
| `stride_service.deployment` | One installation's config, resolved once: the files, the graph they configure, its runner and its certification gate. |
| `stride_service.pipeline` | `AdkPipelineRunner`: one job's identity, input digest and certification around a Graph Run. |
| `stride_service.execution` | Drives a built graph and stamps each node execution. Shared by the service and the eval harness. |
| `stride_service.graph` | Topology and node functions. |
| `stride_service.system_model` | Canonical model + validity helpers. |
| `stride_service.analysis` | Deterministic traversal of a validated model: flows, reachability, paths, unknown controls. No security claims. |
| `stride_service.candidates` | The rule table. Structural conditions an agent should investigate — leads, never findings, never evidence. |
| `stride_service.domains` | Which `skills/domains/` packs a model earns, decided from its own technology fields. |
| `stride_service.coverage` | Per-lane accounting: what each agent was offered, and what its drafts cite. |
| `stride_service.report` | The `Report` envelope, the neutral `Claim` and the severity model. |
| `stride_service.frameworks` | The framework-package contract, its registry and its deployment gate. |
| `stride_service.validation` | The mechanical validity gate. |
| `stride_service.critic` | The mechanical checks around the critic step — the ones no model should be asked to perform. |
| `stride_service.skills` / `.prompts` / `.markdown_loader` | Skill/prompt loading and composition. |
| `stride_service.model_tiers` / `.sampling` / `.resilience` | Config loaders. |
| `stride_service.vendors` | The vendor registry: each vendor's router prefix, credential mode, and model-name rules. |
| `stride_service.binding` | Builds one adapter per tier from `(vendor, model, sampling, resilience)`, and the `NodeBinding` the graph binds onto its LLM nodes. |
| `stride_service.model_gate` | The startup check that asks the provider library whether a tier's parameters are actually supported. |
| `stride_service.certification` | Compares a run's fingerprints against the deployment's blessed manifest. |
| `stride_service.auth` | Bearer-token (OIDC JWT) verification. |
| `stride_service.errors` | `ConfigError`, the base every fail-closed config loader raises — so a caller can handle "this deployment cannot run" without enumerating which knob was wrong. |
