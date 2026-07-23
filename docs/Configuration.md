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

`temperature = 0.0`, deliberately **not** environment-overridable — grading a
configuration you do not ship is how a suite goes green while production drifts.
Change sampling with a reviewed edit to the file.

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
