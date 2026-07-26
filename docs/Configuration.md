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

The file lists **every** decoding parameter the models accept, each either pinned
to a value or left as a *commented* line explaining why. An omitted key is a
typo the loader rejects, never a silent fallback to a model default. The shipped
values keep the models greedy and deterministic: `temperature = 0.0`, everything
else left to the model's own default.

| Param | Shipped state | Notes |
| --- | --- | --- |
| `temperature` | pinned `0.0` | Greedy decoding; the model's own default is `1.0`. |
| `candidate_count` | pinned `1` | Reserved for future multi-candidate sampling; the loader **rejects any value ≠ 1**. |
| `top_p`, `top_k`, `presence_penalty`, `frequency_penalty` | **unset** | No documented per-class default to pin; left to the model. |
| `seed` | **unset** | No numeric default; best-effort, **not** a reproducibility guarantee. |
| `max_output_tokens` | **unset** | Uncapped up to the model ceiling of `65,536`. |
| `thinking` | **unset** | Leaves the model's preset per-class budget. |

A parameter is left unset wherever the model gives no published per-class value
to pin — inventing one would bake in a number nobody measured. To find a better
value, measure it: see [Tuning the models](../evals/TUNING.md).

`thinking` is a per-tier scalar resolved to a class-legal budget:

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

Tuning is a reviewed edit to this file, backed by an eval measurement (see
[The eval gate and provenance](#the-eval-gate-and-provenance) below) — not an
ad-hoc production change. The `STRIDE_SAMPLING_*` environment variables exist
only as a recorded escape hatch, documented [below](#sampling-overrides).

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

### Sampling overrides

`STRIDE_SAMPLING_{TIER}_{PARAM}` retunes one tier's decoding at deploy time
without an image rebuild, validated **identically** to a file value (an
out-of-range override fails closed exactly like one in the file). `{TIER}` is
`FLASH` or `PRO`; `{PARAM}` is one of the four **offered** params below.

| Variable | Effect |
| --- | --- |
| `STRIDE_SAMPLING_{TIER}_TEMPERATURE` | Overrides the tier's `temperature`. |
| `STRIDE_SAMPLING_{TIER}_TOP_P` | Overrides the tier's `top_p`. |
| `STRIDE_SAMPLING_{TIER}_SEED` | Overrides the tier's `seed`. |
| `STRIDE_SAMPLING_{TIER}_THINKING` | Overrides the tier's `thinking` (`off`/`auto`/int). |

Only these four params are overridable. A variable naming a reserved
(`candidate_count`) or forbidden param raises `not overridable`; an unknown tier
or an empty value also raises. Treat this as a temporary escape hatch, not the
way you tune: an override is recorded in the run's provenance fingerprint, so a
run using one reads as **uncertified** (see below). To change sampling for real,
edit the file and back it with a measurement — see
[Tuning the models](../evals/TUNING.md).

### The eval gate and provenance

Every run is **self-describing**. The report carries, in the clear, the resolved
per-tier params it ran on (`StrideReport.sampling`) and a per-node
generation-identity fingerprint (`NodeRun.sampling_fingerprint`) —
`sha256(served model, resolved tier sampling)`, binding model and sampling into
one hash keyed on the *served* model. What makes a result defensible is that
fingerprint, recomputable from the recorded clear block; the `seed` is
best-effort and guarantees nothing. See [Report Schema](Report-Schema.md#provenance).

`evals/blessed-fingerprints.toml` records, per node, the fingerprints of
configurations a measured run has blessed. The eval harness certifies every run
against it: a fingerprint that isn't in a node's blessed set marks the run
**uncertified**, and an uncertified run's scores are never treated as a trusted
baseline. This catches both an unexpected sampling override and an unannounced
model-build change — either one produces a fingerprint that isn't on the list.

The blessed sets start **empty**, so until you promote a measured baseline every
run reads as uncertified; that's surfaced, not fatal, unless you pass
`--require-certified`. Promoting a winner updates the config and the blessed
list together (one step, so they can't disagree) — see
[Tuning the models](../evals/TUNING.md#step-5--promote-the-winner). The shipped
default is `temperature = 0` until a measurement replaces it.

### Resilience overrides

| Variable | Effect |
| --- | --- |
| `STRIDE_RETRY_ATTEMPTS` | Total attempts per LLM call. |
| `STRIDE_TIMEOUT_MS` | Per-request timeout, milliseconds. |
| `STRIDE_RETRY_INITIAL_DELAY` / `_MAX_DELAY` / `_EXP_BASE` / `_JITTER` | Optional backoff-curve knobs; unset means the SDK default. |

### Bearer auth (HTTP surface only)

Required by the [`/v1` API](HTTP-API.md); the in-process engine does not use them.
Every `/v1` route requires a valid bearer token; the verifier returns the
token's `sub`, and job ownership binds to that subject.

#### How the provider is chosen

`STRIDE_AUTH_PROVIDER` selects the backend at deploy time and **fails closed** —
an unset or unknown value stops startup rather than weakening or skipping the
check. The value is never read from the request, so a token can't pick its own
verifier.

| Variable | Purpose |
| --- | --- |
| `STRIDE_AUTH_PROVIDER` | Auth backend to use. Today: `oidc`. |

Each backend reads its own prefixed settings. The `oidc` backend is a standard
**OIDC JWT verifier**, configured through `STRIDE_OIDC_*`:

| Variable | Purpose |
| --- | --- |
| `STRIDE_OIDC_ISSUER` | Expected `iss` claim — your IdP's issuer URL. |
| `STRIDE_OIDC_AUDIENCE` | Expected `aud` claim — the API's identifier at the IdP. |
| `STRIDE_OIDC_JWKS_URL` | JWKS endpoint the IdP publishes its signing keys at. |

Tokens must be RS256-signed and carry `exp`, `iss`, `aud`, and `sub`; anything
else is rejected with a single generic error (the real reason is logged, never
returned).

#### Supported identity providers

The `oidc` backend speaks plain OIDC, so it works with **any OIDC-compliant
identity provider**. For each, the three settings come from the IdP's OIDC
discovery document (`<issuer>/.well-known/openid-configuration` → `issuer` and
`jwks_uri`); the audience is the API/resource identifier you register for this
service. Switching providers is a values change only — the variable names stay
`STRIDE_OIDC_*`.

| Provider | Typical issuer (`STRIDE_OIDC_ISSUER`) |
| --- | --- |
| Ping (PingOne / PingFederate) | `https://auth.pingone.com/<env-id>/as` |
| Okta | `https://<org>.okta.com/oauth2/<auth-server-id>` |
| Auth0 | `https://<tenant>.auth0.com/` |
| Microsoft Entra ID (Azure AD) | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| AWS Cognito | `https://cognito-idp.<region>.amazonaws.com/<pool-id>` |
| Keycloak | `https://<host>/realms/<realm>` |

#### Example: Okta

1. In the IdP, register this service as an API/resource and note the **audience**
   (resource identifier) clients will request tokens for — e.g. `stride-service`.
2. Fetch the discovery document to read the issuer and JWKS URL:
   ```bash
   curl -s https://<org>.okta.com/oauth2/<auth-server-id>/.well-known/openid-configuration \
     | jq '{issuer, jwks_uri}'
   ```
3. Set the environment:
   ```bash
   export STRIDE_AUTH_PROVIDER=oidc
   export STRIDE_OIDC_ISSUER="https://<org>.okta.com/oauth2/<auth-server-id>"
   export STRIDE_OIDC_AUDIENCE="stride-service"
   export STRIDE_OIDC_JWKS_URL="https://<org>.okta.com/oauth2/<auth-server-id>/v1/keys"
   ```
4. Start the app. Callers pass `Authorization: Bearer <token>` on every `/v1`
   request; see the [HTTP API](HTTP-API.md) for the routes.

Pointing at a different OIDC IdP (Ping, Auth0, Entra, …) is the same three
settings from that IdP's discovery document — no code change.

#### Adding a new backend

A backend with a distinct name (or a non-OIDC mechanism — opaque-token
introspection, mTLS, an API key) is a new entry in the `_FACTORIES` registry in
[`src/stride_service/auth.py`](../src/stride_service/auth.py). Reuse
`OidcJwtVerifier` for another OIDC issuer, or implement the `TokenVerifier`
protocol (`verify(token) -> str`) for anything else; the API layer is unchanged.

### Vertex environment

Reaching the models needs a configured Google Vertex environment — Application
Default Credentials plus the project and location the `google-adk` client reads
(typically `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and
`GOOGLE_GENAI_USE_VERTEXAI=1`). The code assumes this is correctly configured.
Offline tests and the in-memory [stub runner](Integration-Guide.md) need none of
it.

## Input limits

Bounds enforced before or during analysis:

| Limit | Value | Where |
| --- | --- | --- |
| `MAX_DESCRIPTION_BYTES` | 100 KiB (UTF-8) | Rejected at both entry points. |
| `MAX_SYSTEM_NAME_CHARS` | 200 | Rejected by the engine / API. |
| `MAX_ELEMENTS` | 150 | A larger model is a `too-many-elements` [rejection](Report-Schema.md). |
