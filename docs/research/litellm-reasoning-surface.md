# Is `reasoning_effort` a uniform reasoning surface across vendors?

Evidence for wayfinder ticket [#15](https://github.com/mstarks01/work-agent/issues/15),
question 3: what replaces `THINKING_RANGE` (`sampling.py:62`), the per-tier
integer budget range whose limits are Gemini's and whose keys are tier names.

Ticket [#13](https://github.com/mstarks01/work-agent/issues/13) established that
the three vendors' reasoning surfaces differ in *shape* — OpenAI has no
integer-budget form, Gemini derives its budget from an enum, Anthropic takes a
`thinking` dict and injects a `max_tokens`. It did **not** ask whether Anthropic
*also* accepts the enum. If it does, the config surface carries one spelling for
every vendor; if it does not, `thinking` becomes vendor-shaped.

## Method

`litellm==1.93.0` (the pin from #13 decision 1) in a throwaway venv.
`litellm.utils.get_optional_params` called as the #13 gate calls it. Network
blocked by a socket subclass that refuses to connect, every `API_KEY` / `GOOGLE`
/ `AWS` / `AZURE` variable stripped before import, and
`LITELLM_LOCAL_MODEL_COST_MAP=True` set before import per #13 decision 4. Every
result below is a pure build-time call — no request was issued.

Reproduce with `probe_litellm_reasoning_surface.py` and
`probe_litellm_reasoning_scalars.py` in this directory.

## Finding 1: `reasoning_effort` reaches all three vendors

| case | outcome |
| --- | --- |
| `anthropic` + `low` | PASS → `thinking={type: enabled, budget_tokens: 1024}`, `max_tokens: 5120` |
| `anthropic` + `medium` | PASS → `budget_tokens: 2048`, `max_tokens: 6144` |
| `anthropic` + `high` | PASS → `budget_tokens: 4096`, `max_tokens: 8192` |
| `vertex_ai` + `claude-sonnet-4-5@20250929` + `low` | PASS → identical to anthropic-direct |
| `vertex_ai` + `gemini-2.5-pro` + `low` | PASS → `thinkingConfig={thinkingBudget: 1024, includeThoughts: true}` |
| `vertex_ai` + `gemini-2.5-pro` + `high` | PASS → `thinkingBudget: 4096` |
| `openai` + `o3` + `high` | PASS → `reasoning_effort: high` (passthrough) |
| `openai` + `gpt-4o` + `low` | **RAISE** `UnsupportedParamsError` |

So the enum is a genuine uniform surface for `low`/`medium`/`high`, and the #13
gate covers *availability* — a non-reasoning model is caught at build time.

Note `includeThoughts: true` on the Gemini path: setting any effort turns on
thought summaries in the response, which the unset default does not.

## Finding 2: OpenAI does not validate the enum's *values*

| case | outcome |
| --- | --- |
| `vertex_ai` + `gemini-2.5-pro` + `"banana"` | RAISE `ValueError: Invalid reasoning effort: banana` |
| `anthropic` + `"banana"` | RAISE `BadRequestError: Unmapped reasoning effort` |
| `openai` + `o3` + `"banana"` | **PASS** → `reasoning_effort: banana` |

A garbage effort on an OpenAI tier passes the gate and reaches the request. This
is a second `top_k`-class hole — silently wrong, and the sampling fingerprint
would attest to it. The mitigation is to validate the value in *this repo's*
schema (a pydantic `Literal`) rather than relying on LiteLLM to do it.

## Finding 3: `"auto"` has no enum spelling

`sampling.toml`'s `thinking = "auto"` means Gemini dynamic allocation
(`thinking_budget = -1`).

| case | outcome |
| --- | --- |
| `vertex_ai` + `gemini-2.5-pro` + `"auto"` | RAISE `ValueError: Invalid reasoning effort: auto` |
| `anthropic` + `"auto"` | RAISE `BadRequestError: Unmapped reasoning effort: 'auto'` |
| `openai` + `o3` + `"auto"` | PASS (but see finding 2 — OpenAI passes anything) |
| `vertex_ai` + `gemini-2.5-pro` + `thinking={budget_tokens: -1}` | PASS → `thinkingBudget: -1` |

Dynamic allocation is reachable only through the raw dict form, so adopting the
uniform enum drops `auto`.

`"minimal"` is accepted on Gemini (→ `thinkingBudget: 128`) and OpenAI, but not
probed as a candidate surface value.

## Finding 4: `"off"` is representable everywhere and wrong on `gemini-2.5-pro`

| case | outcome |
| --- | --- |
| `vertex_ai` + `gemini-2.5-pro` + `"none"` | **PASS** → `thinkingConfig={thinkingBudget: 0, includeThoughts: false}` |
| `vertex_ai` + `gemini-2.5-flash` + `"none"` | PASS → `thinkingBudget: 0` (legal on flash) |
| `anthropic` + `"none"` | PASS → `{}` (reasoning simply absent, its default) |
| `openai` + `o3` + `"none"` | PASS → passthrough |

`thinkingBudget: 0` on `gemini-2.5-pro` is a 400 at request time — exactly the
legality `THINKING_RANGE`'s per-class floor encoded (`pro: (128, 32768)`, with
`"off"` rejected outright). The gate does **not** catch it, and #13 decision 1
removed per-`(vendor, model)` sampling data from the registry, so nothing else
would either.

## Finding 5: Anthropic's `max_tokens` injection only fires when you are silent

| case | outcome |
| --- | --- |
| `anthropic` + `low` + `max_tokens=8192` | PASS → `max_tokens: 8192`, `budget_tokens: 1024` |
| `anthropic` + `thinking` dict + `max_tokens=8192` | PASS → `max_tokens: 8192`, `budget_tokens: 2048` |
| `anthropic` + `max_tokens=8192` alone | PASS → `max_tokens: 8192` |
| `vertex_ai` + `gemini-2.5-pro` + `low` + `max_tokens=8192` | PASS → `max_output_tokens: 8192` |

An explicit cap survives intact — there is no clobber. The bite is the *unset*
case, which is the shipped file on both tiers today: `max_output_tokens` is
commented-unset with the rationale "uncapped to the model ceiling of 65,536".
On Anthropic with any reasoning set, that rationale is false — the request
carries a LiteLLM-derived cap of 5,120–8,192, a plausible truncation point for a
STRIDE analyst's structured JSON.

## Finding 6: the four backoff knobs have nowhere to land

Not a reasoning question, but probed in the same session for #15's
`resilience.toml` bullet, which assumed the file's shape was unchanged.

- `initial_delay`, `max_delay` and `exp_base` appear **nowhere** in the
  `litellm` package; `jitter` appears only in unrelated constants.
- The backoff curve is selected internally from the exception type
  (`litellm/utils.py:1829-1831`): `exponential_backoff_retry` on a
  `RateLimitError`, `constant_retry` on a generic `APIError`.
- `num_retries` is not in `litellm.completion`'s signature at all — it is read
  out of `**kwargs` (`litellm/main.py`, `num_retries = kwargs.get(...)`). So
  #6 decision 2's `attempts - 1` arrives unvalidated by signature; a misspelling
  silently reverts retry to a single try.

So `ResilienceConfig`'s four optional backoff fields (`resilience.py:73-76`)
cannot be honoured under `LiteLlm`, and the `num_retries` kwarg warrants a
build-time assertion rather than trust.

## What this leaves UNVERIFIED

- Request-time behaviour of every case above. These are `get_optional_params`
  results only; no call was issued to any vendor.
- Whether `gemini-2.5-pro` in fact 400s on `thinkingBudget: 0`. That is #13's
  and ticket 04's prior claim, restated here, not re-measured.
- Whether OpenAI's API rejects `reasoning_effort: "none"` / `"auto"` at request
  time. LiteLLM passes both through unvalidated.
- `"minimal"` as a surface value on Anthropic (not probed).
- `get_optional_params` is not a documented public API (#13 decision 4's
  caveat). A `litellm` version bump must re-run these probes.
