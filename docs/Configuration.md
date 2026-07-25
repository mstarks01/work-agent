# Configuration

The service reads its behaviour from versioned files in `config/` and from a set
of environment variables. Loaders **fail closed**: a missing or invalid file
stops startup rather than silently falling back to a default model or sampling.

Both [`StrideEngine.from_config(env=...)`](Integration-Guide.md) and the
[HTTP app](HTTP-API.md) take the same environment; the tables below apply to both.

## Config files

| File | Purpose |
| --- | --- |
| `config/model_tiers.toml` | Maps each LLM node to a tier, and each tier to a pinned Vertex model string. |
| `config/sampling.toml` | Decoding parameters, shared by production and evals. |
| `config/resilience.toml` | Retry attempts and per-request timeout for LLM calls. |
| `skills/` | The per-category STRIDE skill Markdown baked into the image. |
| `prompts/` | The agent prompt and exemplar Markdown. |

### Models

Two tiers, pinned to stable-GA identifiers (no `-latest`, `-preview`, `-exp`):
`flash` for extraction/repair, `pro` for the six analysts, the critic, and the
critic re-ask. Upgrade a string only after the eval suite passes on the
candidate. The pinned defaults are `gemini-2.5-flash` and `gemini-2.5-pro`; the
project targets Gemini 2.x only.

### Sampling

`config/sampling.toml` (`version = 2`) pins the decoding parameters **per model
class**, in `[tiers.flash]` and `[tiers.pro]` tables that reuse the node→tier map
from `model_tiers.toml`. Eval and production read this same file — grading a
configuration you do not ship is how a suite goes green while production drifts,
so there is no eval-only copy.

The file lists **every** decoding parameter the surface admits, each either
pinned to a value or left as a *commented* line with a reason — an omitted key is
a rejected typo, never a silent "model default". What ships is
behaviour-unchanged from before the effort: `temperature = 0.0` (greedy), every
other param the model's own default.

| Param | Shipped state | Why |
| --- | --- | --- |
| `temperature` | pinned `0.0` | Greedy decoding; the model's own default is `1.0`. |
| `candidate_count` | pinned `1` | Reserved Self-MoA lever; the loader **rejects any value ≠ 1**. |
| `top_p`, `top_k`, `presence_penalty`, `frequency_penalty` | **unset** | Model-dependent with no published per-class constant — pinning a guess would claim a decision nobody made. |
| `seed` | **unset** | No numeric default; best-effort, **not** a reproducibility guarantee. |
| `max_output_tokens` | **unset** | Uncapped up to the model ceiling of `65,536`. |
| `thinking` | **unset** | Leaves the model's preset per-class budget. |

The unset states are sourced from research
([ticket 04](../.wayfinder/model-tuning/tickets/04-per-class-decoding-defaults.md)):
where Google documents a value as model-dependent with no rendered per-class
number, the file leaves it unset rather than inventing one.

`thinking` is a per-tier mixed scalar resolved to a class-legal budget:

- **unset** → the model's preset per-class budget (today's default — **not**
  dynamic allocation).
- `"auto"` → dynamic allocation (an explicit `thinking_budget = -1`).
- `"off"` → disabled. `flash` only (range `0–24,576`); `pro`'s floor is `128`
  and `0` is a 400, so `"off"` on `pro` is rejected.
- an **int** → a fixed budget, checked against the class range (`flash`
  `0–24,576`, `pro` `128–32,768`).

Parameters that break the structured-output contract are **never** in the file
and never overridable: `response_schema` (ADK *raises*), `response_mime_type`
(silently discarded), `stop_sequences` (would truncate mid-token), and
`http_options` (owned by `resilience.toml`).

Tuning is a reviewed edit to the file, promoted by an eval sweep (see
[The eval gate](#the-eval-gate-and-provenance) below) — not an ad-hoc production
change. The `STRIDE_SAMPLING_*` env vars exist only as a recorded, eval-gated
escape hatch, documented [below](#sampling-overrides-deploy-time-recorded).

### Resilience

`attempts = 3`, `timeout_ms = 300000`. On SDK defaults the LLM nodes never retry
and never time out, so a single 429 kills a paid-for job; these two numbers fix
both. Unlike sampling, these **are** environment-overridable — they change how
hard the service tries, never which answer it gets, so they are safe to turn
down mid-incident without an image rebuild.

## Environment variables

### Config paths (override where files are read from)

| Variable | Overrides |
| --- | --- |
| `STRIDE_SKILLS_DIR` | `skills/` |
| `STRIDE_PROMPTS_DIR` | `prompts/` |
| `STRIDE_MODEL_TIERS` | `config/model_tiers.toml` |
| `STRIDE_SAMPLING` | `config/sampling.toml` |
| `STRIDE_RESILIENCE` | `config/resilience.toml` |

### Model overrides (deploy-time, no image rebuild)

| Variable | Effect |
| --- | --- |
| `STRIDE_MODEL_FLASH` | Overrides the `flash` tier string. |
| `STRIDE_MODEL_PRO` | Overrides the `pro` tier string. |

These override the tier strings only; the node-to-tier mapping stays in the
file. The same pinned-string rule applies — an alias or preview build is
rejected.

### Sampling overrides (deploy-time, recorded)

`STRIDE_SAMPLING_{TIER}_{PARAM}` retunes one tier's decoding at deploy time
without an image rebuild, validated **identically** to a file value (an
out-of-range override fails closed exactly like one in the file). `{TIER}` is
`FLASH` or `PRO`; `{PARAM}` is one of the **offered** params below.

| Variable | Effect |
| --- | --- |
| `STRIDE_SAMPLING_{TIER}_TEMPERATURE` | Overrides the tier's `temperature`. |
| `STRIDE_SAMPLING_{TIER}_TOP_P` | Overrides the tier's `top_p`. |
| `STRIDE_SAMPLING_{TIER}_SEED` | Overrides the tier's `seed`. |
| `STRIDE_SAMPLING_{TIER}_THINKING` | Overrides the tier's `thinking` (`off`/`auto`/int). |

Only these four params are overridable. A var naming a **reserved**
(`candidate_count`) or **forbidden** param raises `not overridable`; an unknown
tier or a set-but-empty value also raises. This is deliberately a **recorded,
eval-gated escape hatch**, not the tuning path: overrides flow into the resolved
values, so the run's provenance fingerprint captures them, and a run on
un-blessed sampling reads as uncertified (see below). Retune for real with a
reviewed file diff plus a sweep, not a standing override.

### The eval gate and provenance

Every run is **self-describing**. The report carries, in the clear, the resolved
per-tier params it ran on (`StrideReport.sampling`) and a per-node
generation-identity fingerprint (`NodeRun.sampling_fingerprint`) —
`sha256(served model, resolved tier sampling)`, binding model and sampling into
one hash keyed on the *served* model. What makes a result defensible is that
fingerprint, recomputable from the recorded clear block; the `seed` is
best-effort and guarantees nothing. See [Report Schema](Report-Schema.md#provenance).

`evals/blessed-fingerprints.toml` records, per node, the fingerprints a
sanctioned baseline sweep blessed. The eval path **always** certifies a run's
fingerprints against it: a fingerprint absent from its node's set is
*uncertified*, and an uncertified run is never silently folded into a trusted
aggregate. Override-drift and served-build-drift are caught identically — both
just produce a fingerprint that is not in the set. The sets ship **empty** (no
live sweep has run — out of scope, no Vertex here), so every real run reads as
uncertified until a baseline sweep promotes one; hard-fail is an off-by-default
CI knob (`--require-certified`). A sweep's `promote` step is the single write
path: one winning config both re-pins `sampling.toml` in place and derives the
blessed fingerprints, so the two cannot drift. The live sweep and the tuned
numbers themselves are out of scope — `temperature = 0` remains the shipped
default.

### Resilience overrides

| Variable | Effect |
| --- | --- |
| `STRIDE_RETRY_ATTEMPTS` | Total attempts per LLM call. |
| `STRIDE_TIMEOUT_MS` | Per-request timeout, milliseconds. |
| `STRIDE_RETRY_INITIAL_DELAY` / `_MAX_DELAY` / `_EXP_BASE` / `_JITTER` | Optional backoff-curve knobs; unset means the SDK default. |

### Ping auth (HTTP surface only)

Required by the [`/v1` API](HTTP-API.md); the in-process engine does not use them.

| Variable | Purpose |
| --- | --- |
| `STRIDE_PING_ISSUER` | Expected token issuer. |
| `STRIDE_PING_AUDIENCE` | Expected audience. |
| `STRIDE_PING_JWKS_URL` | JWKS endpoint for signature verification. |

### Vertex environment (out of scope for this repo)

Reaching the models needs a configured Google Vertex environment — Application
Default Credentials plus the project and location the `google-adk` client reads
(typically `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and
`GOOGLE_GENAI_USE_VERTEXAI=1`). Provisioning that environment is deliberately out
of scope here; the code assumes it is correctly configured. Offline tests and
the in-memory [stub runner](Integration-Guide.md) need none of it.

## Input limits

Bounds enforced before or during analysis:

| Limit | Value | Where |
| --- | --- | --- |
| `MAX_DESCRIPTION_BYTES` | 100 KiB (UTF-8) | Rejected at both entry points. |
| `MAX_SYSTEM_NAME_CHARS` | 200 | Rejected by the engine / API. |
| `MAX_ELEMENTS` | 150 | A larger model is a `too-many-elements` [rejection](Report-Schema.md). |
