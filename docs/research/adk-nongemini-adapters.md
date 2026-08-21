# Non-Gemini models behind google-adk's `BaseLlm` (wayfinder #4)

**Question.** How does `google.adk.models` expose non-Gemini models behind
`BaseLlm`, and which path makes the STRIDE service's LLM provider pluggable —
with at least one non-Gemini vendor — end to end? Production currently hardwires
`Gemini(model=..., retry_options=...)` in `resilient_resolver`
([`src/stride_service/pipeline.py:249-252`](../../src/stride_service/pipeline.py)),
which the graph's `ModelResolver` (`Callable[[str], str | BaseLlm]`) accepts
([`src/stride_service/graph.py:112`](../../src/stride_service/graph.py)).

## Sources & method

Facts below are verified against **the exact wheel this repo pins** —
`google-adk==2.5.0` ([`pyproject.toml`](../../pyproject.toml),
[`uv.lock`](../../uv.lock)) — by reading the installed source under
`.venv/lib/python3.12/site-packages/google/adk/`. Installed-source citations are
the authoritative primary source for what *this* repo's API offers; upstream
project is <https://github.com/google/adk-python>. Vendor-doc URLs cited are the
ones the ADK source itself points callers to
(`lite_llm.py:2402`, `registry.py:180`).

**Availability check (verified):** `litellm`, `anthropic`, and `openai` are
**not** in `uv.lock` and **not** installed in `.venv`. All four non-Gemini paths
below are optional deps behind `google-adk[extensions]` (the lazy loaders raise
`ImportError` with that hint —
`.venv/.../google/adk/models/__init__.py`,
`registry.py:165-181`). Adopting any of them adds a dependency.

## The dispatch surface

`BaseLlm` is a Pydantic model with one required field `model: str`, one abstract
method `generate_content_async(llm_request, stream) -> AsyncGenerator[LlmResponse]`,
and a classmethod `supported_models() -> list[str]` of name regexes
(`base_llm.py:32-52`). Anything satisfying that contract is a valid node model;
the repo's `ModelResolver` may return either a string (resolved later by the
registry) or a ready `BaseLlm` (as `resilient_resolver` does).

`LLMRegistry.resolve(model)` maps a model string to a class two ways
(`registry.py`, `models/__init__.py:52-100`):

- **Prefix override** `class:model` — e.g. `"openai:gpt-4"` forces the class,
  bypassing regex (`registry.py:59-78, 132-144`).
- **Regex match** on the bare string. Registered patterns (v2.5.0,
  `models/__init__.py:52-90`):

  | Class | Module | Matches (selected) |
  | --- | --- | --- |
  | `Gemini` | `google_llm` | `gemini-.*`, `gemma-4.*`, `projects/.../publishers/google/models/gemini.*`, endpoint paths |
  | `Claude` (native) | `anthropic_llm` | `claude-3-.*`, `claude-.*-4.*` |
  | `LiteLlm` | `lite_llm` | `openai/.*`, `anthropic/.*`, `bedrock/.*`, `vertex_ai/.*`, `azure/.*`, `mistral/.*`, `cohere/.*`, `deepseek/.*`, `groq/.*`, `together_ai/.*`, `fireworks_ai/.*`, `databricks/.*`, `ai21/.*`, `ollama/(?!gemma3).*`, … |
  | `OpenAILlm` (labs) | `google.adk.labs.openai` | `gpt-.*`, `o1-.*`, `o3-.*` |
  | `Gemma` / `Gemma3Ollama` | `gemma_llm` | `gemma-.*`, `ollama/gemma3.*` |
  | `ApigeeLlm` | `apigee_llm` | `.*-apigee$` |

Note the **overlap**: `gpt-*`/`o1-*`/`o3-*` resolve to the experimental labs
`OpenAILlm` when bare, but `openai/gpt-*` routes to `LiteLlm`. `claude-*` bare →
native `Claude` (Vertex); `anthropic/claude-*` or `vertex_ai/claude-*` →
`LiteLlm`. The `class:model` prefix is the deterministic way to pin which
adapter runs.

---

## Path A — `LiteLlm` (wrapper over LiteLLM, 100+ providers)

`class LiteLlm(BaseLlm)`, `lite_llm.py:2659`. `__init__(self, model, **kwargs)`
stores extra kwargs and forwards them to `litellm.acompletion`
(`lite_llm.py:2689-2709, 2750`).

1. **Model identifier / pinnability.** `provider/model` strings passed straight
   to LiteLLM (`lite_llm.py:2744-2750`); the wrapper adds no rewriting. Provider
   list: <https://docs.litellm.ai/docs/providers> (cited by `registry.py:180`).
   A **stable/pinnable** identifier is the vendor's dated snapshot inside the
   string — e.g. `anthropic/claude-sonnet-4-5-20250929`,
   `openai/gpt-4o-2024-11-20`, `vertex_ai/claude-sonnet-4@20250514`. Auto-updating
   aliases are the undated forms (`anthropic/claude-sonnet-4-5-latest`,
   `openai/gpt-4o`); distinguished only by the presence of the dated suffix, same
   discipline as the repo's current "no `-latest`/`-preview`" rule
   ([`config/model_tiers.toml`](../../config/model_tiers.toml)).
2. **Auth.** Per-provider env vars, set *before* instantiation (class docstring,
   `lite_llm.py:2662-2675`). `anthropic/*` → `ANTHROPIC_API_KEY`; `openai/*` →
   `OPENAI_API_KEY`; **`vertex_ai/*` → `VERTEXAI_PROJECT` / `VERTEXAI_LOCATION`
   over ADC** (docstring example, `lite_llm.py:2668-2669`). So a `vertex_ai/…`
   model reuses the **same ADC/Vertex credentials the repo already requires**
   ([`docs/Configuration.md` §Vertex environment](../Configuration.md)); other
   providers need a new API-key secret, unlike today's keyless ADC posture.
3. **Sampling surface.** `_get_completion_inputs` reads the node's
   `GenerateContentConfig` and maps (`lite_llm.py:2404-2419`):
   `temperature`, `top_p`, `top_k`, `presence_penalty`, `frequency_penalty`
   pass through by name; `max_output_tokens → max_completion_tokens`;
   `stop_sequences → stop`; `response_schema → response_format`
   (`lite_llm.py:2390-2395`). This covers **every param the repo's
   `config/sampling.toml` offers** (`temperature`, `top_p`, `top_k`,
   `max_output_tokens`; `candidate_count` is pinned to 1 and not forwarded, fine).
   **Gap:** `seed` is **not** in the mapping — dropped for LiteLlm. The repo
   leaves `seed` unset and treats it as best-effort/no-guarantee
   ([`docs/Configuration.md` §Sampling](../Configuration.md)), so this is
   tolerable, not a regression of a relied-on feature. Param names follow
   <https://docs.litellm.ai/docs/completion/input> (cited by `lite_llm.py:2402`).
4. **Retry / timeout.** Read from the **per-request**
   `GenerateContentConfig.http_options` (`lite_llm.py:2763-2785`):
   `http_options.retry_options.attempts → num_retries` (top-level LiteLLM param),
   `http_options.timeout → timeout`. Also any `num_retries`/`timeout`/`api_base`
   passed as constructor kwargs are forwarded. **This is the key
   compatibility point:** the repo already builds the per-request http_options
   from `resilience.toml`
   ([`graph.py:447-462`](../../src/stride_service/graph.py),
   [`resilience.py:93-99`](../../src/stride_service/resilience.py)) — but today
   `to_http_options()` carries only `timeout`, not `retry_options`, because
   Gemini takes retries via its constructor
   ([`pipeline.py:249-252`](../../src/stride_service/pipeline.py)). For LiteLlm
   to inherit retries, `to_http_options()` must also set `retry_options` on the
   per-request http_options (a small, additive change). **Semantic caveat:** ADK
   maps `attempts → num_retries` verbatim (`lite_llm.py:2782`), but LiteLLM
   `num_retries` counts *retries* (extra attempts), whereas genai
   `HttpRetryOptions.attempts` counts *total attempts*; `attempts=3` would mean 4
   total tries under LiteLlm — an off-by-one to reconcile.
5. **Served-model observability.** `LlmResponse.model_version` **is populated**
   from the LiteLLM `ModelResponse.model` in both non-streaming
   (`lite_llm.py:2112, 2119`) and streaming (`:2926-2985`) paths. So the actual
   served build is readable per call — compatible with the repo's
   served-model fingerprint (`sampling_fingerprint`,
   [`sampling.py:203-218`](../../src/stride_service/sampling.py)) and the eval
   harness's per-run served-model recording
   ([`model_tiers.py:53`](../../src/stride_service/model_tiers.py)).

---

## Path B — native `Claude` on Vertex (`anthropic_llm.Claude`)

`class Claude(AnthropicLlm)`, `anthropic_llm.py:898-943`; base `AnthropicLlm`
at `:598`. Requires the `anthropic` package (`registry.py:165-170`).

1. **Model identifier / pinnability.** Default `claude-3-5-sonnet-v2@20241022`
   (`anthropic_llm.py:915`); regex `claude-3-.*`, `claude-.*-4.*` (`:619-620`).
   Vertex uses **`@YYYYMMDD` dated snapshots** — a genuine GA-pin analogue.
   `_resolve_model_name` also accepts full
   `projects/.../publishers/anthropic/models/<id>` paths and extracts the id
   (`:622-632`). Undated aliases would be the auto-updating form.
2. **Auth.** `AsyncAnthropicVertex(project_id, region, …)` built from
   **`GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` over ADC**
   (`anthropic_llm.py:919-942`) — i.e. the **identical credential path the repo
   already configures for Gemini/Vertex**
   ([`docs/Configuration.md` §Vertex environment](../Configuration.md)); no new
   secret. (The base `AnthropicLlm` uses `AsyncAnthropic()` → `ANTHROPIC_API_KEY`
   for the direct API, `:894-895`.)
3. **Sampling surface.** `_build_anthropic_kwargs` (`:634-708`):
   `temperature`, `top_p`, `top_k`(→int), `stop_sequences`, and
   `max_output_tokens → max_tokens` (default **8192** when unset, `:701-706, 615`).
   **No `presence_penalty`/`frequency_penalty`/`seed`** (not in the Anthropic
   API). **Behavioral gap:** sampling params are silently **ignored when
   thinking/effort is enabled** (`:673-696`), and the default `max_tokens=8192`
   caps output far below the repo's assumed Gemini ceiling of 65,536
   ([`docs/Configuration.md`](../Configuration.md)) unless `max_output_tokens`
   is set. Anthropic thinking uses `effort` via
   `AnthropicGenerateContentConfig`, not the standard `thinking_level` (`:143-213`).
4. **Retry / timeout.** **None exposed through ADK.** The adapter constructs the
   client with defaults and never reads `http_options`
   (`anthropic_llm.py:917-942`, `:711-746`). Resilience would fall to the
   `anthropic` SDK's own defaults (client `max_retries`, default 2); expressing
   `resilience.toml` requires subclassing `Claude` to build the client with
   `max_retries`/`timeout`. No `retry_options` path exists.
5. **Served-model observability.** **Not populated.**
   `message_to_generate_content_response` builds `LlmResponse` **without**
   `model_version` (`anthropic_llm.py:478-504` — the finish/version handling is a
   `TODO`), and the streaming aggregator likewise omits it (`:882-891`). The
   Anthropic `Message.model` field exists but is discarded. **This breaks the
   repo's served-model reproducibility record** — the fingerprint would have only
   the pinned string, never a served-build readback, so a moved build cannot be
   caught as evidence (the property `model_tiers.toml` explicitly relies on).

---

## Path C — labs `OpenAILlm` (experimental)

`class OpenAILlm(BaseLlm)`, `.venv/.../google/adk/labs/openai/_openai_llm.py:326`.
Under `google.adk.labs` (experimental); needs the `openai` package. Default
`gpt-4o`, `max_tokens=4096`, regex `gpt-.*`/`o1-.*`/`o3-.*` (`:334-340`). Auth via
`AsyncOpenAI()` → `OPENAI_API_KEY` (`:495-496`). Sampling: `temperature` etc.
mapped (`:412-413`). **`model_version` not populated** (absent from the file).
Overlaps LiteLlm's `openai/*` route with fewer providers, experimental status,
and no served-version readback — **not recommended** over LiteLlm.

## Path D — subclass `BaseLlm` directly

Always available (`base_llm.py`): implement `generate_content_async` +
`supported_models`. Full control over auth, params, retries, and — crucially —
you can populate `LlmResponse.model_version` yourself. This is the fallback if
you need a vendor none of the above cover, or to *fix* Path B's missing
`model_version` (subclass `Claude`, wrap `message_to_generate_content_response`,
inject a configured `AsyncAnthropicVertex(max_retries=…, timeout=…)`). Cost:
you own the mapping and its maintenance.

---

## Comparison against the repo's invariants

| Concern (repo invariant) | `LiteLlm` (`vertex_ai/claude-*`) | native `Claude` (Vertex) | labs `OpenAILlm` |
| --- | --- | --- | --- |
| Pinnable GA-style id | dated suffix in string | `@YYYYMMDD` | dated `gpt-*` |
| Auth = existing ADC/Vertex (no new secret) | **yes** (`VERTEXAI_*`) | **yes** (`GOOGLE_CLOUD_*`) | no (`OPENAI_API_KEY`) |
| Sampling params (`temperature/top_p/top_k/max_tokens`) | full (no `seed`) | full (no `seed`; ignored under thinking; 8192 cap) | partial |
| `resilience.toml` expressible | **yes** via `http_options` (attempts↔retries caveat) | **no** (needs subclass) | no |
| Served-model readback (`model_version`) | **yes** | **no** (breaks fingerprint) | **no** |
| New dependency | `litellm` | `anthropic` | `openai` |

## Recommendation

**First non-Gemini provider: Anthropic Claude via `LiteLlm` with a
`vertex_ai/claude-…@YYYYMMDD` model string.** It is the only path that preserves
*all three* of the repo's load-bearing invariants at once:

1. **Auth reuses the existing keyless Vertex ADC path** (`VERTEXAI_PROJECT` /
   `VERTEXAI_LOCATION`, `lite_llm.py:2668-2669`) — no new secret-manager surface,
   matching today's "assumes a correctly configured Vertex environment" posture.
2. **Served-model version is readable** (`LlmResponse.model_version` from
   `response.model`, `lite_llm.py:2112`), so `sampling_fingerprint` and the eval
   harness's served-build drift detection keep working — the invariant the
   native `Claude` class silently breaks.
3. **`resilience.toml` maps onto it** through the per-request `http_options` the
   graph already assembles (`retry_options.attempts → num_retries`,
   `timeout → timeout`, `lite_llm.py:2763-2785`).

The native `Claude` class is *closer* on auth (same `GOOGLE_CLOUD_*` vars, no
extra provider prefix) but fails invariants (2) and (3): no `model_version`
readback and no resilience surface — disqualifying under this repo's
reproducibility-and-resilience design without extra subclassing. Non-Vertex
LiteLLM providers (`openai/*`, `anthropic/*` direct, `bedrock/*`) are equally
viable mechanically but each introduce an API-key secret the repo doesn't have
today.

### Work implied (not in scope of this note)

- Add `google-adk[extensions]` (or `litellm>=1.75.5`) to
  [`pyproject.toml`](../../pyproject.toml)/lock; it is **not currently
  installed**.
- Make `resolve_model` provider-aware: return `LiteLlm(model=…)` for non-Gemini
  tiers instead of `Gemini(...)`; `_model_name` already unwraps any `BaseLlm`
  back to its pinned string for the report (`graph.py:705-707`), so the nodes
  array is unaffected.
- Fold `retry_options` into `ResilienceConfig.to_http_options()` so LiteLlm
  inherits retries (today only `timeout` is carried there,
  `resilience.py:93-99`), and reconcile the **`attempts` (total) vs LiteLLM
  `num_retries` (extra)** off-by-one (`lite_llm.py:2782`).
- Accept that `seed` is dropped for LiteLlm (already best-effort in this repo).
