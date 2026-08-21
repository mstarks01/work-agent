# Can `LiteLlm` be the SOLE model adapter across all target vendors? (wayfinder #8)

**Question.** Can ADK's `LiteLlm` (the `google.adk.models.lite_llm.LiteLlm`
wrapper behind `BaseLlm`) be the *sole* model adapter across every target vendor
— (a) an OpenAI-spec vendor (OpenAI itself), (b) Anthropic, (c) Vertex-hosted
models — or does some vendor force a bespoke native adapter? This **extends**
[`adk-nongemini-adapters.md`](adk-nongemini-adapters.md) (wayfinder #4), which
proved the reproducibility invariants only for Claude-on-Vertex, to the full
target set.

The test is the four repo invariants from ticket #8, applied *per vendor,
through `LiteLlm` behind `BaseLlm`*:

1. **`model_version` readback** — the *served* model build is recoverable per
   response (the reproducibility invariant; `sampling_fingerprint`,
   [`sampling.py:203-218`](../../src/stride_service/sampling.py)).
2. **`drop_params`** — whether LiteLLM *silently* drops an unsupported sampling
   param, and whether it can be forced fail-closed. A silent drop would make a
   `sha256(served model, sampling)` fingerprint describe a request that was
   never sent.
3. **API-key auth incl. Vertex** — whether `LiteLlm` accepts raw API-key auth
   for each vendor, *including* Vertex (historically ADC / service-account).
4. **`thinking_budget` / reasoning params** — passed through faithfully per
   vendor (`reasoning_effort`, `thinking`).

---

## TOP-LINE VERDICT

**Yes — `LiteLlm` can be the sole model adapter across OpenAI, Anthropic, and
Vertex-hosted Claude for the three reproducibility-critical invariants (1
`model_version`, 2 `drop_params` fail-closed, 4 reasoning passthrough). No
vendor forces a bespoke *native* adapter.** On invariant 1 `LiteLlm` is
*strictly better* than the native `Claude`-on-Vertex adapter, which silently
drops `model_version` (wayfinder #4, `adk-nongemini-adapters.md` Path B).

**The single exception is invariant 3 (API-key auth) for VERTEX.** Vertex-hosted
models via `LiteLlm`'s `vertex_ai/` route **cannot** authenticate with a raw API
key — they require ADC / service-account. The API-key `gemini/` route reaches
only the **Gemini Developer API**, not Vertex-hosted Claude/partner models.
**However, this is a platform/auth constraint, not a native-adapter trigger:**
the native `Claude`-on-Vertex adapter *also* requires ADC (and additionally
breaks invariant 1). No adapter — native or wrapper — delivers raw-API-key auth
for Vertex-hosted models. So the exception is "Vertex must use ADC/SA," **not**
"Vertex forces a native adapter."

**Exact vendor + invariant forcing the only exception:** **Vertex × invariant 3
(API-key auth)** — and it forces an *auth-mode* exception (ADC), not a native
adapter.

---

## Sources & method

- **ADK behaviour** is verified against the exact wheel this repo pins,
  **`google-adk==2.5.0`** ([`pyproject.toml`](../../pyproject.toml),
  [`uv.lock`](../../uv.lock)), by reading the installed source under
  `.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py`. Upstream:
  <https://github.com/google/adk-python>.
- **LiteLLM behaviour** is verified against **official LiteLLM docs**
  (<https://docs.litellm.ai>) and the **`BerriAI/litellm` `main`-branch source**
  (line numbers below are from `main` as read 2026-07-27). `litellm` is **not
  installed** in this repo's `.venv` (confirming #4), so its source is consulted
  upstream. **Latest published stable at time of research: `litellm==1.93.0`**
  (PyPI `info.version`; `main` is ahead at `1.95.0.dev*`). `docs.litellm.ai`
  tracks `main`.
- **Vendor facts** are from first-party docs: OpenAI API reference, Anthropic
  Messages API, Google Cloud Vertex AI docs, plus LiteLLM's own issue tracker.

---

## How `LiteLlm` handles the request/response (vendor-agnostic seams)

These paths are the *same code* regardless of vendor — establishing them once
lets each invariant be reasoned per vendor.

**Constructor `**kwargs` forwarded to `litellm.acompletion`.**
`LiteLlm.__init__(self, model, **kwargs)` stores all extra kwargs in
`self._additional_args` (`lite_llm.py:2689-2709`) and merges them into the
completion call (`completion_args.update(self._additional_args)`,
`lite_llm.py:2744-2761`). So any LiteLLM param — `drop_params`,
`reasoning_effort`, `thinking`, `api_key`, `vertex_project` — can be pinned on
the adapter instance. `drop_params` is special-cased:
`drop_params = kwargs.pop("drop_params", None); if drop_params is not None:
self._additional_args["drop_params"] = drop_params` (`lite_llm.py:2696-2709`)
— ADK sets **no** default, so LiteLLM's own default governs.

**Request-side sampling mapping (`_get_completion_inputs`).**
`lite_llm.py:2404-2419` maps only: `temperature`, `max_output_tokens →
max_completion_tokens`, `top_p`, `top_k`, `stop_sequences → stop`,
`presence_penalty`, `frequency_penalty`. **`thinking_config` / reasoning is NOT
in this map** — see invariant 4 caveat.

**Response-side `model_version`.** Non-streaming sets
`model_version=response.model` (`lite_llm.py:2112, 2119`); streaming sets
`model_version=part.model` (`lite_llm.py:2926, 2934, 2978, 2985`). This is
vendor-agnostic: it echoes whatever LiteLLM put in `ModelResponse.model`.

---

## Per-vendor × per-invariant

| Invariant | (a) OpenAI (`openai/…`) | (b) Anthropic (`anthropic/…`) | (c) Vertex-hosted (`vertex_ai/…`) |
| --- | --- | --- | --- |
| **1. `model_version` served readback** | **yes** | **yes** | **yes** |
| **2. `drop_params` forceable fail-closed** | **yes** (default) | **yes** (default) | **yes** (default) |
| **3. Raw API-key auth** | **yes** (`OPENAI_API_KEY`) | **yes** (`ANTHROPIC_API_KEY`) | **NO** — ADC/SA required |
| **4. Reasoning passthrough (`reasoning_effort`/`thinking`)** | **yes** (`reasoning_effort`) | **yes** (`thinking` + `reasoning_effort`) | **yes** (Anthropic-on-Vertex) |
| Forces a *native* adapter? | no | no | **no** (auth is a platform limit, not adapter-fixable) |

### Invariant 1 — `model_version` served readback: **holds for all three**

LiteLLM populates `ModelResponse.model` from the **provider's returned model
field** (the served build), and ADK reads it into `LlmResponse.model_version`:

- **OpenAI / OpenAI-spec:** the generic converter sets
  `model_response_object.model = response_object["model"]`
  (`litellm/litellm_core_utils/llm_response_utils/convert_dict_to_response.py`,
  `main`, lines **245, 338, 742, 776**). OpenAI's API returns the resolved dated
  snapshot in the response `model` field, so the served build is recovered.
- **Anthropic (direct):** the Anthropic handler sets
  `model_response.model = completion_response["model"]`
  (`litellm/llms/anthropic/chat/transformation.py`, `main`, line **2417**) —
  i.e. the Anthropic Messages API `model` (served) field.
- **Vertex-hosted Claude:** same Anthropic transformation path (verified for
  Vertex Claude in wayfinder #4). ADK reads `response.model`
  (`lite_llm.py:2112`).

This is the invariant the **native `Claude` adapter breaks** (no `model_version`
populated — #4 Path B), so `LiteLlm` is the *only* adapter that preserves it
across all three. **Pinning discipline unchanged:** you still pin the dated
snapshot in the request string (`openai/gpt-4o-2024-11-20`,
`anthropic/claude-sonnet-4-5-20250929`, `vertex_ai/claude-…@YYYYMMDD`); the
readback lets drift be *detected*.

### Invariant 2 — `drop_params` fail-closed: **holds for all three (default)**

LiteLLM's default is **fail-closed (raise)**, not silent-drop:

- Global default: `drop_params = bool(os.getenv("LITELLM_DROP_PARAMS", False))`
  (`litellm/__init__.py`, `main`) → **`False`** unless the env var is set.
- Docs: *"By default, LiteLLM raises an exception if you send a parameter to a
  model that doesn't support it."*
  (<https://docs.litellm.ai/docs/completion/drop_params>).
- ADK adds no default (`lite_llm.py:2696-2709`), so an unsupported sampling
  param **raises** unless the caller explicitly opts into dropping.

This is vendor-independent and satisfies the fingerprint requirement: leave
`drop_params` at its default `False` and a sampling param the served model
doesn't support **fails the request** rather than silently vanishing.
`additional_drop_params` (a list of OpenAI params to drop) and per-call
`drop_params=True` are the *opt-in* escape hatches — the repo simply must **not**
set them (and must not set `LITELLM_DROP_PARAMS`). No vendor forces a silent
drop.

### Invariant 3 — API-key auth: **holds for OpenAI + Anthropic; FAILS for Vertex**

- **OpenAI:** `OPENAI_API_KEY` env var, or `api_key=` constructor kwarg
  forwarded via `_additional_args`. **Raw API key — yes.**
- **Anthropic (direct):** `ANTHROPIC_API_KEY`, or `api_key=` kwarg. **Raw API
  key — yes.**
- **Vertex-hosted (`vertex_ai/…`): NO raw-API-key path.** LiteLLM's Vertex
  provider authenticates with `vertex_credentials` (service-account JSON /
  filepath), `vertex_project`, `vertex_location`, or
  `GOOGLE_APPLICATION_CREDENTIALS` (ADC) — the same posture ADK's docstring
  shows (`VERTEXAI_PROJECT` / `VERTEXAI_LOCATION` over ADC,
  `lite_llm.py:2668-2669`). LiteLLM's Vertex docs explicitly redirect API-key
  users elsewhere: *"If you just want to use an API key (like OpenAI), use the
  `gemini/` prefix instead."*
  (<https://docs.litellm.ai/docs/providers/vertex>) — but `gemini/` is the
  **Gemini Developer API (Google AI Studio)**, *not* Vertex-hosted, and covers
  **Gemini only**, never Vertex-hosted Claude/partner models.
- **Vertex Express / global endpoint (`aiplatform.googleapis.com`) does issue
  API keys** at the *vendor* level — Google's express-mode docs state *"You can
  use your API key to authenticate with the … API"* and a raw
  `…:generateContent?key=<KEY>` call succeeds. **But LiteLLM does not honour it**
  for the `vertex_ai/` route: `BerriAI/litellm#21036` (opened **2026-02-12**)
  reports LiteLLM forces service-account credential loading
  (`_credentials_from_default_auth` in `vertex_llm_base.py`) and raises
  `DefaultCredentialsError` even against the global/express endpoint that needs
  only a token/key. So **Vertex-API-key-via-`LiteLlm` is currently unsupported**.
  (<https://github.com/BerriAI/litellm/issues/21036>)

**Crucially, this does *not* force a native adapter.** The native
`Claude`-on-Vertex adapter (`anthropic_llm.Claude`) *also* authenticates over
ADC / `GOOGLE_CLOUD_*` (`AsyncAnthropicVertex`, #4 Path B) — it offers **no**
raw-API-key path either, and it *loses* invariant 1. So switching to a native
adapter would not buy Vertex API-key auth; it would only cost `model_version`
readback. The honest conclusion: **for Vertex-hosted models, ADC/service-account
is mandatory regardless of adapter choice** — `LiteLlm` remains the best adapter.

> **Unverified (stated honestly):** whether Vertex *partner* models
> (Anthropic Claude / Model Garden MaaS) are reachable *specifically* under an
> express-mode **API key** (as opposed to Gemini-only) — Google's express-mode
> docs confirm API-key auth and separately list Claude/Mistral as Model Garden
> partner models, but do **not** state that partner models are in the
> express-mode API-key scope. Moot for this repo regardless, since LiteLLM's
> `vertex_ai/` route rejects API-key auth (issue #21036) and the repo already
> runs on Vertex ADC.

### Invariant 4 — reasoning / `thinking_budget` passthrough: **holds for all three, with an ADK caveat**

- **Anthropic (direct + Vertex-hosted):** LiteLLM's `AnthropicConfig` supports
  **both** `thinking={"type":"enabled","budget_tokens":N}` (passed straight
  through) **and** `reasoning_effort` (mapped to `budget_tokens` via
  `AnthropicConfig._map_reasoning_effort`, e.g. low/medium/high/xhigh/max →
  `DEFAULT_REASONING_EFFORT_*_THINKING_BUDGET`;
  `litellm/llms/anthropic/chat/transformation.py`, `main`, ~line **1188**;
  both listed as supported params, ~line **473-474**).
- **OpenAI (o-series):** `reasoning_effort` is a native OpenAI param passed
  through.
- LiteLLM lists **Anthropic API, Vertex AI (Anthropic), and OpenAI Responses
  API** among reasoning-capable providers
  (<https://docs.litellm.ai/docs/reasoning_content>), and standardises the
  output as `reasoning_content` + `thinking_blocks` — which ADK 2.5.0 already
  parses back into thought parts (`lite_llm.py:479-626, 2107-2138`).

> **ADK caveat (applies to all vendors).** ADK 2.5.0's request-side mapping
> (`_get_completion_inputs`, `lite_llm.py:2404-2419`) does **not** translate a
> node's `GenerateContentConfig.thinking_config` (the Gemini-style
> `thinking_budget` / `include_thoughts`) into LiteLLM reasoning params. To use
> reasoning through `LiteLlm` you must pass `reasoning_effort=` or `thinking=`
> as **constructor kwargs** on the `LiteLlm` instance (they flow via
> `_additional_args` → `acompletion`, `lite_llm.py:2700, 2750`), **not** via the
> node's `thinking_config`. This is uniform across vendors and is a small
> adapter-construction detail, not a native-adapter trigger.

---

## Conclusion

`LiteLlm` is a viable **sole** adapter for OpenAI, Anthropic, and Vertex-hosted
Claude. It preserves the three reproducibility-critical invariants (served
`model_version` readback, fail-closed `drop_params`, faithful reasoning
passthrough) across **all** target vendors, and does so *better* than the native
`Claude` adapter (which forfeits `model_version`). **No vendor forces a bespoke
native adapter.**

The only cross-vendor asymmetry is **auth**: Vertex-hosted models must use
ADC / service-account (invariant 3) because neither `LiteLlm`'s `vertex_ai/`
route nor the native Vertex adapter accepts a raw API key. That is a
Google-platform constraint, already matched by this repo's existing Vertex ADC
posture ([`docs/Configuration.md` §Vertex environment](../Configuration.md)) —
not a reason to add a second adapter.

### Adoption notes (carried from #4, unchanged)

- `litellm` / `google-adk[extensions]` is **not** installed; adopting `LiteLlm`
  adds the dependency.
- Pin dated snapshots per vendor; rely on `model_version` readback to *detect*
  drift.
- Keep `drop_params` unset (and `LITELLM_DROP_PARAMS` unset) to stay
  fail-closed.
- Supply reasoning via `LiteLlm(reasoning_effort=…)` / `LiteLlm(thinking=…)`
  constructor kwargs, not the node `thinking_config`.
- For non-Vertex vendors (OpenAI, Anthropic direct) each introduces an API-key
  secret the repo's keyless-ADC posture doesn't have today.
