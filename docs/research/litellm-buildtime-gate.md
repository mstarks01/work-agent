# The build-time supported-param gate, verified empirically (wayfinder #13)

**Question.** [#6](https://github.com/mstarks01/work-agent/issues/6) decision 3
fails the *build* when a tier's resolved sampling names a param its vendor
cannot take. [#12](https://github.com/mstarks01/work-agent/issues/12) falsified
the mechanism #6 assumed (`Vendor.supported: frozenset[str]`) but could not test
its replacement, because `litellm` was not installed. This resolves that: what
is the source of truth for the check, and does it cover value constraints?

**Method.** `litellm==1.93.0` installed into a throwaway `uv` venv (Python
3.12, matching the project). The project's `.venv` and `pyproject.toml` were
**not** touched — adopting the dependency is implementation, not planning. The
probe (`probe_litellm_buildtime_gate.py`, this directory) monkeypatches
`socket.socket` to raise on any `connect`, and strips every `*API_KEY*` /
`GOOGLE*` / `AWS*` / `AZURE*` variable from the environment **before** importing
`litellm`, so a passing run is proof of a pure call rather than a lucky one.

---

## TOP-LINE VERDICT

**`litellm.utils.get_optional_params` is usable as a build-time gate, and it
subsumes the value-constraint problem.** It is callable standalone, offline,
with no credentials; only `model` is a required argument. It catches both name
violations and the o-series value constraint, because it *is* the gate the
request-time path runs — `_check_valid_arg` **and** `_map_openai_params`.

Consequently the registry needs **no** per-`(vendor, model)` sampling data at
all: `Vendor.supported` is deleted rather than repaired.

**One finding invalidates the naive "just pin `litellm`" plan.** On import,
`litellm` fetches its model-cost map over the network from
`BerriAI/litellm@main`. That map backs the conditionals (`supports_reasoning`,
`_supports_penalty_parameters`) the gate consults, so an exact version pin does
**not** by itself make the check deterministic.

---

## 1. The gate is callable at build time

`inspect.signature(litellm.utils.get_optional_params)` — 40 parameters, of which
only **`model`** and `**kwargs` are required. `custom_llm_provider`,
`temperature`, `top_p`, `seed`, `max_completion_tokens`, `presence_penalty`,
`frequency_penalty`, `reasoning_effort` and `thinking` are all named parameters.

**`top_k` is absent from the signature entirely** — confirming #12's trace
against the installed library rather than against upstream source.

`litellm.drop_params` is `False` by default, confirming the fail-closed premise
[#8](https://github.com/mstarks01/work-agent/issues/8) relies on.

## 2. It catches name violations, per `(vendor, model)`

| case | outcome |
| --- | --- |
| `anthropic` + `seed` | **RAISE** `anthropic does not support parameters: ['seed']` |
| `anthropic` + `presence_penalty` | **RAISE** |
| `anthropic` + `temperature=0.0` | pass |
| `vertex_ai` + `claude-sonnet-4-5@20250929` + `seed` | **RAISE** |
| `vertex_ai` + `gemini-2.5-pro` + `seed` | pass |
| `openai` + `gpt-4o` + `seed` | pass |
| `openai` + `o3` + `top_p` | **RAISE** |

Rows 4 and 5 are #12's central claim, reproduced: **one `vertex_ai` provider,
two different answers, selected by model prefix.** The prototype's
`vertex: _ALL_PARAMS` would have passed the build on row 4 and raised on the
first request — the exact failure #6 decision 3 exists to prevent.

## 3. It catches the value constraint a set cannot express

```
openai / o3 / temperature=0.0  -> RAISE  UnsupportedParamsError:
    O-series models don't support temperature=0.0. Only temperature=1 is supported.
openai / o3 / temperature=1.0  -> pass
```

This is the raise in `o_series_transformation.py:96-114`. A `frozenset[str]`
membership check passes `temperature=0.0` here and then raises mid-job. Calling
the gate needs no separate value-predicate mechanism — it runs the real one.

**This repo pins `temperature = 0.0` on both tiers today** (`config/sampling.toml`),
so an o-series model is not merely a hypothetical failure: it is an immediate,
build-time-detectable one under greedy decoding. It is also why
[#10](https://github.com/mstarks01/work-agent/issues/10) decision 3's
greedy-decoding requirement means **o-series cannot judge** — that now fails
closed here rather than at request time.

## 4. It does NOT become a de-facto existence check

| case | outcome |
| --- | --- |
| `anthropic` + `claude-nonexistent-9` + `seed` | **RAISE** (family rule still applies) |
| `vertex_ai` + `some-future-model` + `temperature` | pass |
| `openai` + `gpt-9-turbo` + `top_p` | pass |

An unrecognised model string resolves to its provider's **base** config rather
than erroring, so a valid-but-unreleased model is not rejected. This is
consistent with [#7](https://github.com/mstarks01/work-agent/issues/7)
decision 6 (no build-time existence check), and it is what makes the gate safe
to run against a model catalog that lags vendor releases.

**Residual, accepted:** because the fallback is the *base* family config, a
future o-series-class model under an unrecognised naming pattern would be
checked against the permissive base list — passing the build and raising at
request time. Narrow, and strictly no worse than the status quo.

## 5. The import-time network fetch

Run **without** `LITELLM_LOCAL_MODEL_COST_MAP`:

```
LiteLLM:WARNING: get_model_cost_map.py:290 - LiteLLM: Failed to fetch remote model
cost map from https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json:
NETWORK ACCESS ATTEMPTED. Falling back to local backup.
```

The fetch is attempted **at import**, unprompted, and targets the `main` branch —
not the pinned release. The conditionals the gate consults (`supports_reasoning`,
`_supports_penalty_parameters`) read that map. So without the flag:

- the build's pass/fail depends on the live contents of a GitHub URL, meaning a
  config could build today and fail tomorrow with **no change to the lockfile**;
- a service that otherwise makes no outbound calls at startup makes one.

Setting `LITELLM_LOCAL_MODEL_COST_MAP=True` **before the first `litellm` import**
removes the fetch entirely (warning gone) and yields **identical results** on
every case above. Verified working via `os.environ.setdefault` in-process, so it
does not have to be a deploy-time environment variable someone can forget.

## 6. Reasoning surfaces differ in shape, not just availability

Not #13's question, but it falls out of the probe and
[#15](https://github.com/mstarks01/work-agent/issues/15) owns the slot
("per-tier thinking legality"):

| vendor | accepted form | what the gate returns |
| --- | --- | --- |
| `anthropic` | `thinking={"type": "enabled", "budget_tokens": 2048}` | `{'thinking': {...}, 'max_tokens': 6144}` |
| `vertex_ai` gemini | `reasoning_effort="low"` | `{'thinkingConfig': {'thinkingBudget': 1024, 'includeThoughts': True}}` |
| `openai` o-series | `reasoning_effort="high"` | `{'reasoning_effort': 'high'}` |

Three consequences for #15:

1. **OpenAI has no integer-budget form at all** — only the `reasoning_effort`
   enum. `THINKING_RANGE` (`sampling.py:62`) is an int range per tier; that
   surface is not merely mis-ranged for a non-Gemini vendor, it may not survive
   as an int.
2. **Gemini derives a budget from the enum** (`"low"` → `1024`), so the number
   actually sent is LiteLLM's, not the config's — a fingerprint recording a
   configured budget would attest to a value the request did not carry.
3. **Anthropic's `thinking` injects `max_tokens: 6144` unbidden**, derived from
   the budget. That collides with `max_output_tokens`, which the config sets
   independently.

---

## UNVERIFIED

1. **What each vendor's API does with an unvalidated `top_k`** — unchanged from
   #12, and now moot: #13 removes `top_k` from the config surface.
2. **Concrete model strings.** `claude-sonnet-4-5`, `o3`, `gemini-2.5-pro` were
   used as representative ids; the real ids arrive with the config schema (#15).
3. **Whether `get_optional_params` is stable across `litellm` releases.** It is
   not a documented public API (`litellm.utils`, not `litellm`). The exact pin
   is what makes depending on it defensible; a version bump must re-run this
   probe.
4. **Non-chat request types** — only the default chat-completion path was
   exercised.
