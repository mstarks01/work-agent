# Configuration

The service reads its behaviour from versioned files in `config/` and from a set
of environment variables. Loaders **fail closed**: a missing or invalid file
stops startup rather than silently falling back to a default model or sampling.

Both [`StrideEngine.from_config(env=...)`](Integration-Guide.md) and the
[HTTP app](HTTP-API.md) take the same environment; the tables below apply to both.

## Config files

| File | Purpose |
| --- | --- |
| `config/model_tiers.toml` | Maps each LLM node to a tier, and each tier to a `(vendor, model)` pair. |
| `config/sampling.toml` | Decoding parameters, shared by production and evals. |
| `config/resilience.toml` | Retry attempts and per-request timeout for LLM calls. |
| `config/blessed-fingerprints.toml` | The generation identities this deployment has blessed. |
| `skills/` | The per-category STRIDE skill Markdown baked into the image. |
| `prompts/` | The agent prompt and exemplar Markdown. |

### Models and vendors

The service runs on **any supported vendor, with no privileged default**. Two
tiers named on a capability axis — `base` (extraction, repair) and `strong` (the
six analysts, the critic, the re-ask) — each select a `(vendor, model)` pair
**independently**, so the two tiers may run different vendors at once.

```toml
version = 3

[tiers.base]
vendor = "vertex"
model = "gemini-2.5-flash"

[tiers.strong]
vendor = "anthropic"
model = "claude-sonnet-4-5-20250929"
```

Supported vendors are `vertex`, `anthropic` and `openai`. Every one is reached
through a single adapter (LiteLLM); there is no per-vendor code path, and Gemini
reaches Vertex the same way everything else does.

**Auth is derived from the vendor, never configured alongside it.** Each vendor
owns its credential mode, so an unrepresentable pairing like `vertex` + an API
key cannot be written down at all:

| Vendor | Credential mode | Required environment |
| --- | --- | --- |
| `vertex` | ADC | `STRIDE_VERTEX_PROJECT`, `STRIDE_VERTEX_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` |
| `anthropic` | API key | `STRIDE_ANTHROPIC_API_KEY` |
| `openai` | API key | `STRIDE_OPENAI_API_KEY` |

Keys are read **only** from these vendor-scoped variables. LiteLLM's ambient
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` pickup is deliberately unused, so a
credential this deployment did not declare cannot authenticate a run. Keys are
never logged, never in the report, and never in a fingerprint; errors name the
variable, never its value.

**Pinning** means naming a model specifically enough that it won't quietly
change under you. The check is per-vendor and deliberately loose: it rejects
names that openly float (`-latest`, `-preview`, `-exp`) and, where a vendor
publishes a dated form, requires it (`claude-...@YYYYMMDD` on Vertex,
`claude-...-YYYYMMDD` when called directly). Gemini 2.5 and later ship no
numbered builds, so there the bare name is the most specific identifier
available.

A loose rule is the right one here because it runs against three vendors'
catalogs at once, and its predecessor — an allowlist of numbered Gemini builds —
broke outright when Google retired them. The name check is only a proxy. The
real guarantee is the **served build read back from every response** and
recorded for each node execution, described under
[Architecture → Provenance and certification](Architecture.md#provenance-and-certification).

### Sampling

`config/sampling.toml` (`version = 3`) pins decoding parameters **per tier**, in
`[tiers.base]` and `[tiers.strong]` tables that reuse the node→tier map from
`model_tiers.toml`. The eval harness and production read this same file, on
purpose: grading a configuration you don't actually ship is how a test suite
stays green while production quietly drifts.

The file lists **every** decoding parameter the surface admits, each either
pinned or left as a *commented* line explaining why. An omitted key is a typo the
loader rejects, never a silent fallback.

| Param | Shipped state | Notes |
| --- | --- | --- |
| `temperature` | pinned `0.0` | Greedy decoding; the model's own default is `1.0`. |
| `max_output_tokens` | pinned `8192` | Must be pinned: silence means a *vendor-derived* cap. |
| `candidate_count` | pinned `1` | Reserved; the loader **rejects any value ≠ 1**. |
| `top_p`, `presence_penalty`, `frequency_penalty` | **unset** | No verified per-tier constant to pin. |
| `seed` | **unset** | Buys consistency, not reproducibility — and Anthropic does not accept it at all. |
| `thinking` | **unset** | Leaves the model's own preset. |

`thinking` is a uniform `"low"` / `"medium"` / `"high"` enum. It reaches all
three vendors, which is why there are no longer per-tier legal ranges: LiteLLM
maps it to `budget_tokens` on Anthropic, `thinkingConfig` on Gemini, and passes
it through on OpenAI o-series. `"auto"` and `"off"` are **not** accepted —
`"auto"` raises on two vendors, and `"off"` is worse than unportable, since
Gemini accepts it at build time and then fails the request.

> **`top_k` is gone from the surface** (it was removed in version 3). It is the
> one parameter the build-time check provably cannot cover — LiteLLM re-injects
> it into the request *after* validation — so a wrong value would be silent while
> the fingerprint attested to it.

Parameters that break the structured-output contract are **never** in the file
and never overridable: `response_schema` (the SDK *raises*), `response_mime_type`
(silently discarded), `stop_sequences` (would truncate mid-token), and
`http_options` (owned by `resilience.toml`).

### The startup parameter check

Vendors do not accept the same decoding parameters. At startup, every tier's
`(vendor, model, sampling)` combination is run through the provider library's
own check, so **an unsupported parameter stops startup rather than failing the
first request** — otherwise it would raise partway through a job, after earlier
nodes had already been paid for. For example:

- `seed` on Anthropic, or on Vertex-hosted Claude, is a startup error — but the
  same `seed` on Vertex-hosted Gemini is fine.
- `temperature = 0.0` on an OpenAI o-series model is a startup error, because
  o-series models constrain temperature to exactly `1`.

The check asks the library itself rather than consulting a table this repo
maintains, so it cannot drift away from the behaviour that actually fires at
request time. The library's model data is read from the installed copy, so the
answer never depends on a network fetch during startup.

### Resilience

`attempts = 3`, `timeout_ms = 300000` (`version = 2`). On library defaults the
LLM nodes never retry and never time out, so a single 429 kills a paid-for job.
Unlike sampling, these **are** environment-overridable — they change how hard the
service tries, never which answer it gets.

`attempts` is a **total** count and is converted for the provider, which counts
retries *after* the first try. Version 2 **removed** the four backoff knobs
(`initial_delay`, `max_delay`, `exp_base`, `jitter`): the adapter picks its
backoff curve internally from the exception type, so as configuration they read
as a knob and connected to nothing.

## Environment variables

### Config paths (override where files are read from)

| Variable | Overrides |
| --- | --- |
| `STRIDE_SKILLS_DIR` | `skills/` |
| `STRIDE_PROMPTS_DIR` | `prompts/` |
| `STRIDE_MODEL_TIERS` | `config/model_tiers.toml` |
| `STRIDE_SAMPLING` | `config/sampling.toml` |
| `STRIDE_RESILIENCE` | `config/resilience.toml` |
| `STRIDE_BLESSED_FINGERPRINTS` | `config/blessed-fingerprints.toml` |

A variable **picks which file is read**; it never layers a second file over the
first. A set-but-empty value is a deploy mistake and raises rather than falling
back to the repo copy.

These apply to the **whole deployment**, not only the service: an eval sweep
reads the same files, and promoting a sweep winner re-pins the same
`sampling.toml` and blesses into the same manifest. Redirect a path and
everything follows it — which is what makes grading a configuration you do not
run impossible rather than merely discouraged.

### Model overrides (deploy-time, no image rebuild)

| Variable | Effect |
| --- | --- |
| `STRIDE_MODEL_BASE_VENDOR` / `STRIDE_MODEL_BASE_MODEL` | Overrides the `base` tier's pair. |
| `STRIDE_MODEL_STRONG_VENDOR` / `STRIDE_MODEL_STRONG_MODEL` | Overrides the `strong` tier's pair. |

`_MODEL` **alone** is the ordinary case — retune a tier's model on a deployed
revision. `_VENDOR` alone is a **startup error**: a mismatched pair such as
`anthropic` + `gemini-2.5-pro` passes every downstream check and would only die
on the first node of a paid-for job.

Any unrecognised `STRIDE_MODEL_*` variable also raises. That is deliberate: a
deployment still carrying version 2's `STRIDE_MODEL_FLASH` must fail loudly
rather than have it silently ignored while the tier quietly runs the file's
model.

### Sampling overrides

`STRIDE_SAMPLING_{TIER}_{PARAM}` retunes one tier's decoding at deploy time,
validated **identically** to a file value. `{TIER}` is `BASE` or `STRONG`.

| Variable | Effect |
| --- | --- |
| `STRIDE_SAMPLING_{TIER}_TEMPERATURE` | Overrides the tier's `temperature`. |
| `STRIDE_SAMPLING_{TIER}_TOP_P` | Overrides the tier's `top_p`. |
| `STRIDE_SAMPLING_{TIER}_SEED` | Overrides the tier's `seed`. |
| `STRIDE_SAMPLING_{TIER}_THINKING` | Overrides the tier's `thinking` (`low`/`medium`/`high`). |
| `STRIDE_SAMPLING_{TIER}_MAX_OUTPUT_TOKENS` | Overrides the tier's `max_output_tokens`. |

Only these are overridable. A variable naming a reserved (`candidate_count`),
removed (`top_k`) or forbidden param raises `not overridable`. Treat this as a
temporary escape hatch: an override changes the run's fingerprint, so a run using
one reads as **uncertified**. To change sampling for real, edit the file and back
it with a measurement — see [Tuning the models](../evals/TUNING.md).

### Resilience overrides

| Variable | Effect |
| --- | --- |
| `STRIDE_RETRY_ATTEMPTS` | Total attempts per LLM call. |
| `STRIDE_TIMEOUT_MS` | Per-request timeout, milliseconds. |

### Provider environment

Each tier's vendor determines what credentials it needs; see
[Models and vendors](#models-and-vendors) for the table. If a selected vendor's
credentials are missing, startup stops with an error, so a misconfigured
deployment never reaches its first request. Offline tests and the in-memory
[stub runner](Integration-Guide.md) need none of this.

## Input limits

Bounds enforced before or during analysis:

| Limit | Value | Where |
| --- | --- | --- |
| `MAX_DESCRIPTION_BYTES` | 100 KiB (UTF-8) | Rejected at both entry points. |
| `MAX_SYSTEM_NAME_CHARS` | 200 | Rejected by the engine / API. |
| `MAX_ELEMENTS` | 150 | A larger model is a `too-many-elements` [rejection](Report-Schema.md). |
