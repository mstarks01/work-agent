# Per-vendor sampling support sets for the build-time check (wayfinder #12)

**Question.** For each vendor in the provider registry (`vertex`, `anthropic`,
`openai`), which of this repo's sampling params does the vendor actually accept
**through LiteLLM**, in *genai* names: `temperature`, `top_p`, `top_k`, `seed`,
`max_output_tokens`, `presence_penalty`, `frequency_penalty`, `thinking_budget`?

The consumer is [#6](https://github.com/mstarks01/work-agent/issues/6)
decision 3: an unsupported param must fail the **build**, not the first request,
because `drop_params` stays fail-closed ([#8](https://github.com/mstarks01/work-agent/issues/8))
and a mid-job raise happens after earlier pipeline nodes are already paid for.
The check is only as good as the sets behind it — too strict refuses a working
config, too loose makes the check decorative.

---

## TOP-LINE VERDICT

**The `#6` prototype's shape is wrong, in two independent ways.**

1. **`Vendor.supported: frozenset[str]` cannot be static.** Every one of the
   three vendors resolves its supported set as a **function of `(vendor,
   model)`**, not of vendor. This is not a corner case on one vendor — it is the
   normal path on all three, and on `openai` it is severe enough that two models
   from the same vendor disagree about `top_p`, `presence_penalty` and
   `frequency_penalty`. A per-vendor `frozenset` is guaranteed to be wrong for
   some model of that vendor.

2. **`vertex` is not one provider.** LiteLLM's `vertex_ai/` route **dispatches
   on the model-string prefix** to four different config classes
   (`gemini*` → `VertexGeminiConfig`, `claude*` → `VertexAIAnthropicConfig`,
   `mistral*`, `codestral*`, else Llama3). `vertex_ai/claude-*` and
   `vertex_ai/gemini-*` have materially different sets — Claude-on-Vertex has
   **no `seed`** and **no penalties**, Gemini-on-Vertex has both. A single
   `vertex` registry entry describes neither.

**Two params escape the mechanism entirely and need separate treatment:**

- **`top_k` is never validated by LiteLLM on any vendor.** It is not an
  OpenAI-spec param, so it is absent from `DEFAULT_CHAT_COMPLETION_PARAM_VALUES`,
  never enters `non_default_params`, and is therefore never seen by
  `_check_valid_arg`. It is instead swept up as a *provider-specific extra* and
  injected verbatim into the request body. `get_supported_openai_params` is
  **not the authority for `top_k`** — `VertexGeminiConfig` listing it is
  unreachable on this path.
- **`temperature` on OpenAI o-series is value-constrained, not
  presence-constrained.** `temperature` is in the supported list, but any value
  other than exactly `1` raises `UnsupportedParamsError`. A set-membership check
  cannot express this.

**`seed` and `thinking_budget` do NOT bypass validation.** Both are *named
parameters* of `litellm.completion`, so a constructor-passed value (#6 decision
1) lands in `non_default_params` and is checked exactly like a request-mapped
param. This is the good news: the fail-closed guarantee #6 relies on holds for
them, and the build-time check is therefore both necessary and sufficient for
these two.

---

## Sources & method

- **LiteLLM** is read from the **`BerriAI/litellm` `main`** tree pinned at commit
  **`0cd588ad10beaca4b5d37d9272f3fe73d92aca4f`** (committed 2026-07-24, read
  2026-07-27). All line numbers below are from that commit. `litellm` is **not**
  installed in this repo's `.venv` (#4, #8), so the source is read upstream
  rather than introspected.
- **ADK** is read from the exact wheel this repo pins, **`google-adk==2.5.0`**,
  at `.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py`.
- Vendor API docs are deliberately **not** treated as authority. What decides
  whether a param raises is LiteLLM's own `get_supported_openai_params` plus the
  `_check_valid_arg` gate, which can and does disagree with the vendor's API.

### The genai → LiteLLM name mapping

The check must compare *LiteLLM* names, so the repo's genai names have to be
translated first. ADK does this in `_get_completion_inputs`
(`lite_llm.py:2404-2419`, google-adk 2.5.0):

| repo / genai name | name on the wire to `acompletion` | how it travels |
| --- | --- | --- |
| `temperature` | `temperature` | ADK `generation_params` |
| `top_p` | `top_p` | ADK `generation_params` |
| `top_k` | `top_k` | ADK `generation_params` |
| `max_output_tokens` | **`max_completion_tokens`** | ADK `generation_params` |
| `presence_penalty` | `presence_penalty` | ADK `generation_params` |
| `frequency_penalty` | `frequency_penalty` | ADK `generation_params` |
| `seed` | `seed` | **`LiteLlm` constructor** (#6 dec. 1) |
| `thinking_budget` | `thinking` or `reasoning_effort` | **`LiteLlm` constructor** (#6 dec. 1) |

Two things to note. **`max_output_tokens` becomes `max_completion_tokens`, not
`max_tokens`** — a check written against `max_tokens` would be testing a name
this repo never sends. (Harmless in practice: every config below lists both.)
And **`seed` / `thinking_config` are absent from ADK's loop**, which is the whole
reason #6 moved them onto the constructor.

### Where the fail-closed gate actually fires

`litellm.utils.get_optional_params` (`utils.py:3762`) builds `non_default_params`
via `pre_process_non_default_params` (`utils.py:3609`) →
`PreProcessNonDefaultParams.base_pre_process_non_default_params`
(`utils.py:3543-3585`), then calls the nested `_check_valid_arg`
(`utils.py:3833-3866`), which raises `UnsupportedParamsError` for any
`non_default_params` key absent from the provider's supported list — unless
`litellm.drop_params` / `drop_params` is true, which this repo never sets (#8).

Two filters upstream of that gate matter:

1. **`base_pre_process_non_default_params` keeps a key only if
   `k in default_param_values`** (`utils.py:3571`), where `default_param_values`
   is `DEFAULT_CHAT_COMPLETION_PARAM_VALUES` (`constants.py:640-686`). That dict
   contains `temperature`, `top_p`, `seed`, `max_tokens`,
   `max_completion_tokens`, `presence_penalty`, `frequency_penalty`,
   `reasoning_effort`, `thinking` — and **not `top_k`**.
2. Anything left over is swept into the request *after* the gate by
   `add_provider_specific_params_to_optional_params`
   (`utils.py:4336`, defined `utils.py:4361-4397`): for OpenAI-compatible
   providers into `optional_params["extra_body"]`, for everything else straight
   onto `optional_params[k]` — which `AnthropicConfig.transform_request`
   documents as becoming "the JSON body via `{**optional_params}`"
   (`llms/anthropic/chat/transformation.py:1854-1855`).

**Consequence for `seed` and `thinking_budget` (question 4).** Both `seed` and
`thinking` are declared parameters of `litellm.completion`
(`main.py:4835`, `main.py:4856`), as is `reasoning_effort` (`main.py:4832`).
`locals().copy()` at `utils.py:3806` therefore captures them into
`passed_params`, they survive the `default_param_values` filter, and
`_check_valid_arg` sees them. **A constructor-passed `seed` on Anthropic raises
`UnsupportedParamsError` — it is not silently passed through.** ADK's request map
is bypassed; LiteLLM's validation is not. The reproducibility hazard #6 worried
about does not exist for these two.

**Consequence for `top_k`.** `top_k` is not a declared parameter of
`litellm.completion`, so it arrives through `**kwargs`, is collected by
`get_non_default_completion_params` (`utils.py:9182-9189`, comment: *"model-specific
params - pass them straight to the model/provider"*), reaches
`get_optional_params` as `**kwargs` → `special_params`, is merged into
`passed_params` (`utils.py:3565`) — and is then **discarded from
`non_default_params`** by the `k in default_param_values` filter. It is never
checked, never mapped, and is finally re-injected raw by
`add_provider_specific_params_to_optional_params`. See the per-param note below.

---

## The table

Read as: *does LiteLLM accept this param for this vendor/model, without
`drop_params`?* **Y** = in the supported list. **N** = absent → raises
`UnsupportedParamsError` at request time. **model** = the answer depends on which
model of that vendor.

| genai param | `anthropic` | `vertex_ai/gemini-*` | `vertex_ai/claude-*` | `openai` (gpt-4/4o class) | `openai` (o-series) | `openai` (gpt-5) |
| --- | --- | --- | --- | --- | --- | --- |
| `temperature` | Y | Y | Y | Y | **Y, value must be `1`** | Y |
| `top_p` | Y | Y | Y | Y | **N** | **model** ¹ |
| `top_k` | — see note | — see note | — see note | — see note | — see note | — see note |
| `seed` | **N** | Y | **N** | Y | Y | Y |
| `max_output_tokens` → `max_completion_tokens` | Y | Y | Y | Y | Y | Y |
| `presence_penalty` | **N** | **model** ² | **N** | Y | **N** | **N** |
| `frequency_penalty` | **N** | **model** ² | **N** | Y | **N** | **N** |
| `thinking_budget` → `thinking`/`reasoning_effort` | **model** ³ | **model** ⁴ | **model** ³ | **N** ⁵ | Y (`reasoning_effort`) | Y (`reasoning_effort`) |

¹ `top_p` (with `logprobs`, `top_logprobs`) is restored only when
`_supports_reasoning_effort_level(model, "none")` — i.e. gpt-5.1/5.2 class
(`llms/openai/chat/gpt_5_transformation.py:185-189`).
² Penalties are dropped for Gemini 3-or-newer and for
`gemini-2.5-pro-preview-06-05` (`_supports_penalty_parameters`,
`vertex_and_google_ai_studio_gemini.py:303-310`, gated at `:339-340`).
³ Added only when the model is `claude-3-7-sonnet`, an adaptive-thinking model,
or `supports_reasoning(model, provider)` is true
(`llms/anthropic/chat/transformation.py:465-475`).
⁴ Added only when `supports_reasoning(model)` is true
(`vertex_and_google_ai_studio_gemini.py:342-345`).
⁵ `reasoning_effort` is absent from `OpenAIGPTConfig`'s base list
(`llms/openai/chat/gpt_transformation.py:142-173`); the o-series and gpt-5
configs each append it themselves.

### Per-cell evidence

- **`anthropic`** — `AnthropicConfig.get_supported_openai_params`,
  `litellm/llms/anthropic/chat/transformation.py:445-476`. Base list: `stream`,
  `stop`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `tools`,
  `tool_choice`, `extra_headers`, `parallel_tool_calls`, `response_format`,
  `user`, `web_search_options`, `speed`, `context_management`, `cache_control`;
  plus `thinking` and `reasoning_effort` under the condition at `:465-472`.
  **No `seed`, no `top_k`, no penalties — the prior session's claim is
  confirmed against the source, at the pinned commit.**
- **`vertex_ai/gemini-*`** — `VertexGeminiConfig.get_supported_openai_params`,
  `litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py:312-345`.
  Includes `seed` (`:327`) and `top_k` (`:316`); penalties and reasoning are
  conditional as footnoted.
- **`vertex_ai/claude-*`** — `VertexAIAnthropicConfig`
  (`litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py:25`)
  **subclasses `AnthropicConfig`** and does not override
  `get_supported_openai_params`, so it inherits the `anthropic` column verbatim.
- **The `vertex_ai` dispatch** — `litellm/litellm_core_utils/get_supported_openai_params.py:185-196`:
  `mistral*` → `MistralConfig`, `codestral*` → `CodestralTextCompletionConfig`,
  `claude*` → `VertexAIAnthropicConfig`, `gemini*` → `VertexGeminiConfig`,
  **else** → `VertexAILlama3Config`. Note the `else` branch: an unrecognised
  Vertex model string falls through to the Llama config rather than erroring.
- **`openai` base** — `OpenAIGPTConfig.get_supported_openai_params`,
  `litellm/llms/openai/chat/gpt_transformation.py:141-187`. Includes
  `frequency_penalty`, `presence_penalty`, `seed`, `temperature`, `top_p`,
  `max_tokens`, `max_completion_tokens`. **No `top_k`, no `reasoning_effort`.**
- **`openai` o-series** — `OpenAIOSeriesConfig.get_supported_openai_params`,
  `litellm/llms/openai/chat/o_series_transformation.py:45-88`: takes the base
  list, appends `reasoning_effort`, then removes `logprobs`, `top_p`,
  `presence_penalty`, `frequency_penalty`, `top_logprobs`. The
  temperature-must-be-1 raise is in `_map_openai_params`, same file `:96-114`.
- **`openai` gpt-5** — `OpenAIGPT5Config.get_supported_openai_params`,
  `litellm/llms/openai/chat/gpt_5_transformation.py:148-189`: base list plus
  `reasoning_effort`/`verbosity`, minus `presence_penalty`, `frequency_penalty`,
  `stop`, `logit_bias`, `modalities`, `prediction`, `audio`,
  `web_search_options` — and minus `top_p`/`logprobs`/`top_logprobs` except on
  the 5.1/5.2 class. A gpt-5 *search* model returns an entirely different,
  much smaller list (`:149-164`), with **no `temperature` and no `seed`**.

### The `top_k` row, in full

`top_k` cannot be given a Y/N per vendor because **LiteLLM's supported-param
mechanism does not govern it at all**. Traced end to end:

1. ADK puts `top_k` into `generation_params` (`lite_llm.py:2412`), so it reaches
   `acompletion(**generation_params)` as a bare kwarg.
2. `top_k` is not a declared parameter of `litellm.completion`
   (`main.py:4811-4862` — it is not in the signature), so it lands in `**kwargs`.
3. `get_non_default_completion_params` (`utils.py:9182`) collects it as a
   "model-specific param".
4. It reaches `get_optional_params` as `**kwargs` → `special_params`, is merged
   into `passed_params` (`utils.py:3565`), then **filtered out** of
   `non_default_params` because `top_k` is absent from
   `DEFAULT_CHAT_COMPLETION_PARAM_VALUES` (`constants.py:640-686`;
   filter at `utils.py:3578`).
5. `_check_valid_arg` therefore never sees it — **no raise, on any vendor,
   regardless of `drop_params`.**
6. `add_provider_specific_params_to_optional_params` (`utils.py:4361-4397`) then
   re-adds it: for `openai` and openai-compatible providers into
   `optional_params["extra_body"]["top_k"]` (`:4374-4392`); for `anthropic` and
   `vertex_ai` straight onto `optional_params["top_k"]` (`:4392-4396`), which
   becomes the JSON body.

What each vendor then does with it is a **vendor-API** question, not a LiteLLM
one, and is where this ticket's evidence stops:

- **Anthropic** — the Messages API does accept `top_k`, and the param is placed
  directly in the body, so it plausibly *works* despite being absent from
  `get_supported_openai_params`.
- **OpenAI** — chat completions has no `top_k`; sending it inside `extra_body`
  should draw a `400 unknown parameter` from the API itself.
- **Vertex Gemini** — `top_k` is in `VertexGeminiConfig`'s supported list, but
  that listing is **unreachable on this path** (step 5 removed it before
  `map_openai_params` ran), so it arrives as an unmapped extra rather than as
  `generationConfig.topK`.

These three outcomes are marked **UNVERIFIED** below. What *is* verified, and
what the design needs, is that **the build-time check cannot cover `top_k` by
consulting `get_supported_openai_params`**, and that a wrong `top_k` is a
*silent* wrong — the exact failure mode the fingerprint work exists to prevent.
Note this repo can set `top_k` in the tier config
([`sampling.py:94`](../../src/stride_service/sampling.py)) even though it is not
in `OFFERED_PARAMS` (`sampling.py:74`).

---

## Static frozenset, or `(vendor, model)` lookup?

**`(vendor, model)`. Unambiguously, on all three vendors.** The
`Vendor.supported: frozenset[str]` field in the #6 prototype
(`_prototype_tier_provider_registry.py:121`) cannot hold a correct answer.

The evidence is not a single conditional but four independent ones:

| vendor | what makes the set model-dependent |
| --- | --- |
| `anthropic` | `thinking` / `reasoning_effort` gated on reasoning capability (`transformation.py:465-472`) |
| `vertex` | the **route itself** branches on model prefix to four different config classes (`get_supported_openai_params.py:185-196`) — *before* any per-model conditional inside them |
| `vertex_ai/gemini-*` | penalties gated on `_supports_penalty_parameters` (`:303-310`); reasoning gated on `supports_reasoning` (`:342`) |
| `openai` | three different config classes by model family, disagreeing on `top_p`, both penalties, and `reasoning_effort` |

The `vertex` row is the structurally interesting one: it is not a refinement
within a vendor, it is a **different provider config entirely**. Under the
registry's `vertex` entry, `vertex_ai/claude-sonnet-4-5` and
`vertex_ai/gemini-2.5-pro` disagree on `seed` and on both penalties — and `seed`
is precisely the param #6 decision 1 went to some length to keep honest in the
fingerprint. A `vertex` entry carrying `_ALL_PARAMS` (as the prototype has it)
is **too loose** for Claude-on-Vertex: it would pass the build and then raise on
the first request, which is the exact failure #6 decision 3 exists to prevent.

Two further consequences for whatever replaces the frozenset:

- **Set membership is not the whole predicate.** The o-series
  `temperature == 1` rule (`o_series_transformation.py:99-114`) is a *value*
  constraint enforced at request time. A `supported: frozenset[str]` check
  passes a config that then raises. If the build-time check is to be
  non-decorative for OpenAI reasoning models, it needs to admit value
  predicates, not just names.
- **The condition functions read LiteLLM's model-cost registry.**
  `supports_reasoning` (`utils.py:2498-2502`) resolves through
  `_supports_factory` against LiteLLM's model metadata, so the answer for a
  given model string depends on the installed `litellm` version's model map.
  Any local mirror of these sets is a **snapshot that drifts** — which argues
  for calling `litellm.get_supported_openai_params(model=..., custom_llm_provider=...)`
  at build time rather than hand-maintaining a table. Note the whole dispatcher
  is a pure function of `(model, custom_llm_provider)` with no network and no
  credentials, so it is callable at build time. That is a design option this
  ticket surfaces, not a decision it makes.

---

## UNVERIFIED

Recorded rather than invented, per #4's precedent.

1. **What each vendor's API does with an unvalidated `top_k`** (all three
   cells). The LiteLLM path is fully traced and verified; what is *not* verified
   is the resulting HTTP outcome — Anthropic accepting it, OpenAI 400-ing on
   `extra_body.top_k`, and whether Vertex Gemini honours an unmapped `top_k` or
   ignores it. Establishing this needs a live request per vendor, which this
   repo cannot make until `litellm` is installed and credentials exist. **Until
   then `top_k` should be treated as unrepresentable in the registry**, not
   given a guessed Y/N.
2. **Which concrete model strings this repo will actually name.** Every "model"
   cell above is a *rule*, not a value. The rules are verified; resolving them to
   Y/N requires the specific model ids the config will carry, which the
   config-schema work has not fixed yet (map §"Not yet specified"). In
   particular `supports_reasoning` for a given id is a lookup into the installed
   `litellm` model map and cannot be settled from source alone.
3. **`vertex_ai/` model strings matching none of `gemini*`/`claude*`/`mistral*`/`codestral*`**
   fall through to `VertexAILlama3Config` (`get_supported_openai_params.py:195-196`).
   That config was not read, and whether the fall-through is ever reachable for
   this repo's vendor set is undetermined.
4. **Non-chat request types.** The dispatcher branches on
   `request_type == "chat_completion"` for `vertex_ai`
   (`get_supported_openai_params.py:186`). Only the chat path was traced; this
   repo uses only chat, but that was assumed, not verified against every node.
5. **`AnthropicConfig._is_adaptive_thinking_model`** (`transformation.py:467`)
   was not read. It is one of three disjuncts gating `thinking`, and the other
   two already make the cell model-dependent, so it does not change any verdict
   here — but it is not evidence, it is an unopened box.
