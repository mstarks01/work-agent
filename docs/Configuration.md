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

The service runs on **any supported vendor, with no privileged default** — and
that holds for the shipped file, not only for the mechanism. `model_tiers.toml`
selects **nothing**: both tier tables are absent, and a run that has not chosen
stops at startup with an error naming the vendors and the two places a selection
can be made. There is no vendor you reach by doing nothing.

Two tiers named on a capability axis — `base` (extraction, repair) and `strong`
(the six analysts, the critic, the re-ask) — each select a `(vendor, model)` pair
**independently**, so the two tiers may run different vendors at once.

```toml
version = 3

[tiers.base]
vendor = "vertex"
model = "gemini-2.5-flash"

[tiers.strong]
vendor = "anthropic"
model = "claude-opus-5"
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
change under you. The check is per model *family* and deliberately loose: it
rejects names that openly float (`-latest`, `-preview`, `-exp`) and, where the
vendor publishes a canonical form, requires that shape. Gemini 2.5 and later
ship no numbered builds, so there the bare name is the most specific identifier
available; the same is true of OpenAI's o-series.

Claude is the family with a published form:

```text
claude-<name>-<major>[-<minor>]     e.g. claude-opus-5, claude-sonnet-4-6
```

Two things to know about it. The dateless ID is **not** an alias — from the 4.6
generation on it is the canonical pinned snapshot, and Anthropic ships a new ID
rather than moving weights under an existing one. And Vertex spells it the same
way, so `vendor = "vertex"` and `vendor = "anthropic"` take the identical model
string for the same model.

**This service runs Claude 4.6 and later only.** A pre-4.6 name is rejected —
either as unpinned, because those generations carry a snapshot date
(`claude-sonnet-4-5-20250929` direct, `claude-sonnet-4-5@20250929` on Vertex)
and their bare form really was a floating alias, or, when the shape is right
but the version is too old (`claude-haiku-4-5`), as a generation, with the
message naming the one it read. There is no mode that accepts both schemes.

A loose rule is the right one for the rest because it runs against three
vendors' catalogs at once, and its predecessor — an allowlist of numbered
Gemini builds — broke outright when Google retired them. Claude's half avoids
that trap by matching a shape rather than enumerating builds: a model released
tomorrow already satisfies it. The name check is only a proxy either way. The
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
| `temperature` | pinned `0.0` | Greedy decoding; the model's own default is `1.0`. **Must be unset on a tier running Claude 4.7 or later** — see below. |
| `max_output_tokens` | pinned `8192` | Must be pinned: silence means a *vendor-derived* cap. |
| `candidate_count` | pinned `1` | Reserved; the loader **rejects any value ≠ 1**. |
| `top_p`, `presence_penalty`, `frequency_penalty` | **unset** | No verified per-tier constant to pin. |
| `seed` | **unset** | Buys consistency, not reproducibility — and Anthropic does not accept it at all. |
| `thinking` | **unset** | Leaves the model's own preset. |

`thinking` is a uniform `"low"` / `"medium"` / `"high"` enum. It reaches all
three vendors, which is why there are no longer per-tier legal ranges: LiteLLM
maps it to adaptive `thinking` plus `output_config.effort` on Anthropic,
`thinkingConfig` on Gemini, and passes it through on OpenAI o-series. `"auto"`
and `"off"` are **not** accepted —
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

**Its one blind spot, and the check that covers it.** Asking the library means
inheriting the limits of the library's model data: a model released after the
pinned copy is unknown to it and falls back to the provider's *base* config,
where anything the provider generally accepts passes. That is usually harmless
— it is a name check, not an existence check — but one case is not. Anthropic
removed `temperature` from **Claude 4.7 onward**: only the model's own default
is accepted, and a request carrying the parameter is rejected. Since the shipped
sampling pins `temperature = 0.0`, a tier naming a Claude newer than the pinned
library would sail through startup and die on the first node of a paid job.

So a second startup check runs beside the first: a tier on Claude 4.7 or later
with `temperature` set is a startup error naming the tier and the file to edit.
Two deliberate limits on it:

- It keys on the **model**, not the vendor. Vertex-hosted Claude is the same
  model under the same removal, and `vendor = "vertex"` must not be a way around
  it.
- The floor is **4.7, not 4.6**. Claude 4.6 still accepts `temperature`, so
  pinned greedy decoding survives there rather than being swept up by a
  vendor-wide ban.

Unset the parameter for that tier and the model runs on its own default, which
is the only value these generations serve. This is a floor, not a re-introduced
support table: when the pinned library's model data catches up, the first check
starts catching the same case and this one becomes redundant rather than
contradictory.

### The startup schema check

Every LLM node in the graph binds an output schema, so a third check runs per
tier: **can this `(vendor, model)` be constrained to a schema *natively*?**

Where a provider cannot, the library does not fail — it *emulates* the
constraint by synthesising a single tool whose input schema is the response
schema and forcing a call to it. The two paths are not equivalent. The native
path resolves `$ref`/`$defs` before sending, because providers do not resolve
external schema references; the emulated path forwards the schema as-is. A
schema with nested types — which every Pydantic model here produces — therefore
arrives unusable, and the model answers in a shape of its own invention.

That failure is the most expensive one available: the request is well-formed,
the response is well-formed, and the job dies at the node's own output
validation partway through. Neither of the other two checks can see it. So a
tier whose model would take the emulated path is a startup error naming the
tier.

Like the supported-param check, this is asked as a **call**, not a table — the
check inspects whether the library had to synthesise its internal
response-format tool for this pair. That matters more than it sounds: under the
pinned library, the same Claude generation can be native on one vendor and
emulated on another, so a rule keyed on the model alone would pass a
configuration that does not work.

`supports_structured_output` is a **different and weaker question** — whether a
schema is honoured at all — and answers yes for models on both paths. It cannot
substitute for this check. The eval judge runs both at config-load time for the
same reason (`evals/harness/judge.py`).

### Resilience

`attempts = 3`, `timeout_ms = 300000`, `max_source_bytes = 102400`,
`max_sources = 10` (`version = 3`). On library defaults the LLM nodes never
retry and never time out, so a single 429 kills a paid-for job; the other two
bound what one job may carry. Unlike sampling, all four **are**
environment-overridable — none of them can move an eval score, because retry
and timeout change how hard the service tries and the input bounds decide only
whether a submission is accepted at all.

`attempts` is a **total** count and is converted for the provider, which counts
retries *after* the first try.

`max_source_bytes` is the **total across all of a job's sources**, not a bound
on any one of them: there is deliberately no per-source cap, since it would
forbid only shapes the total already permits. Both bounds are in UTF-8 bytes
rather than tokens, so the public contract does not change when a deployment
changes vendor.

Version 3 **added** the two input bounds. Version 2 **removed** version 1's four
backoff knobs (`initial_delay`, `max_delay`, `exp_base`, `jitter`): the adapter
picks its backoff curve internally from the exception type, so as configuration
they read as a knob and connected to nothing. Both are hard cutovers — a file on
an older version fails to load, so every deployment edits its file rather than
inheriting a default for a contract its callers can see.

## Environment variables

### Config paths (override where files are read from)

| Variable | Overrides |
| --- | --- |
| `STRIDE_SKILLS_DIR` | `skills/` |
| `STRIDE_PROMPTS_DIR` | `prompts/` |
| `STRIDE_TIERS_FILE` | `config/model_tiers.toml` |
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

### How strictly each override family is checked

The three families do **not** treat an unrecognised variable the same way, and
the difference is worth knowing before you debug a setting that appears to have
no effect.

| You set | Result |
| --- | --- |
| `STRIDE_MODEL_BASE_MODLE` (typo) | **Startup fails**: `unrecognised model override(s)` |
| `STRIDE_MODEL_FLASH` (stale v2 name) | **Startup fails**: same check |
| `STRIDE_SAMPLING_BSAE_SEED` (typo'd tier) | **Startup fails**: `unknown tier 'BSAE'` |
| `STRIDE_SAMPLING_BASE_TOP_K` (removed param) | **Startup fails**: `TOP_K is not overridable` |
| `STRIDE_RETRY_ATEMPTS` (typo) | **Silently ignored** — the file's value stands |
| `STRIDE_TIMEOUT_MSEC` (typo) | **Silently ignored** — the file's value stands |

The reason is naming, not intent. `STRIDE_MODEL_*` and `STRIDE_SAMPLING_*` are
namespaces those two loaders own, so each can enumerate every variable it
accepts and reject anything else in its namespace. The resilience knobs are bare
`STRIDE_`-prefixed names, and `STRIDE_` belongs to the whole application — it
also holds the config paths, the provider credentials and the job-store
selector. No single loader can claim it, so the resilience loader reads the two
names it knows and cannot see that you meant a third.

What makes the weaker guarantee tolerable is the **consequence**, which is also
why the two families differ in the first place:

- Model and sampling change *what* the model produces. A silently missed
  override there means a run recorded as using one configuration actually used
  another, which invalidates every measurement taken against it — so those
  fail closed, hard.
- Retry and timeout change only *how hard we try*, never which answer comes
  back. A silently missed override costs you resilience, never correctness, and
  cannot move an eval score.

So: **when a retry or timeout change appears to do nothing, suspect the variable
name first.** Nothing echoes these values back, and a set-but-empty value *is*
caught (`STRIDE_RETRY_ATTEMPTS=` raises `is set but empty`) — it is only a
misspelled *name* that passes unnoticed.

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
| `max_source_bytes` | 100 KiB (UTF-8), total across all sources | Rejected at both entry points; deployment config. |
| `max_sources` | 10 | Rejected at both entry points; deployment config. |
| Source `label` | 200 characters, single-line, unique per job | Rejected as a malformed source. |
| `MAX_SYSTEM_NAME_CHARS` | 200 | Rejected by the engine / API. |
| `MAX_ELEMENTS` | 150 | A larger model is a `too-many-elements` [rejection](Report-Schema.md). |
