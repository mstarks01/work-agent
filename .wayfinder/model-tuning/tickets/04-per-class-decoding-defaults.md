---
id: 04
title: "Research: authoritative Gemini 2.5 per-class decoding defaults (flash vs pro)"
label: wayfinder:research
status: resolved
assignee: "claude (research subagent)"
blocked-by: [02]
---

## Question

Ticket 02 decided the v2 `sampling.toml` enumerates the **full decoding
surface** with every param pinned to its default value. Ticket 01 established
the param *surface* and *legality ranges* but **not the numeric defaults** —
in the installed `google-genai`, `top_p` / `top_k` / `max_output_tokens` /
`presence_penalty` / `frequency_penalty` are all `Optional[...] = None`, so the
SDK sends nothing and Vertex applies its own default. To author the file
without guessing, we need those actual numbers, **per class**.

For **`gemini-2.5-flash`** and **`gemini-2.5-pro`** on Vertex, source from
official Google documentation (cite each as a URL, no guessing — leave a value
`UNVERIFIED` rather than invent it):

1. **`top_p`** — default value, each class.
2. **`top_k`** — default value, each class (note: typed `float` in installed
   genai, `types.py:6054`).
3. **`max_output_tokens`** — default / model maximum, each class.
4. **`presence_penalty`** — default value and valid range, each class (ticket
   01 flagged the Vertex range as a doc gap: commonly cited `[-2.0, 2.0]`,
   unconfirmed).
5. **`frequency_penalty`** — same.
6. **`seed`** — reconfirm the best-effort guarantee already in ticket 01; no
   numeric default expected (unset = no seed), but confirm.
7. **`thinking_budget` per-class defaults** — ticket 01 has the *ranges*
   (flash 0–24,576, 0=off; pro 128–32,768, no off). Capture the **default
   behavior** when unset (dynamic/`-1`?) for each class, so the file's
   `thinking = "auto"` comment is accurate.

**Deliverable:** a table `param → { flash default, pro default, source URL }`
plus any value that could not be verified, flagged `UNVERIFIED`. Capture the
findings with a context pointer here. On close, the implementation build-out can
author `sampling.toml` with cited numbers.

**Scope guard:** these are the *neutral model defaults*, not tuned values. The
tuned per-tier values and any live sweep remain out of scope (live Vertex);
this ticket only sources what the model already does so the config can pin it
honestly.

## Context pointer

Findings captured during research; the full write-up is summarized in the Answer
below.

## Answer

Full findings and citations were captured during research. Summary:

| param | flash default | pro default | source |
|---|---|---|---|
| `top_p` | **UNVERIFIED** — model-dependent, no published per-class number | **UNVERIFIED** | Firebase AI Logic "Model parameters" lists topP as "Depends on the model"; Vertex per-model table is JS-rendered/unreachable |
| `top_k` (typed `float`) | **UNVERIFIED** — model-dependent | **UNVERIFIED** | same; genai `GenerationConfig` ref: default is "the Model's `top_k` attribute" |
| `max_output_tokens` | model max **65,536** (no smaller default; unset = uncapped to max) | model max **65,536** | ai.google.dev model pages (flash/pro): output limit 65,536, input 1,048,576 |
| `presence_penalty` | default **0.0 / none** (SDK sends `None`); **range UNVERIFIED** (`[-2.0,2.0]` unconfirmed) | same | installed `types.py:6091`; Vertex range table JS-rendered/unreachable |
| `frequency_penalty` | default **0.0 / none**; **range UNVERIFIED** | same | installed `types.py:6098`; same doc gap |
| `seed` | **no numeric default** (unset = no seed); best-effort, not guaranteed — reconfirmed | same | `types.py:6105-6110`; Vertex REST `GenerationConfig` |
| `thinking_budget` (unset) | range **1–24,576**, `0` disables; unset ⇒ model preset default, **not** auto-dynamic; `-1` = dynamic (explicit) | range **128–32,768**, cannot disable; unset ⇒ preset default; `-1` = dynamic (explicit) | Firebase AI Logic "Thinking" |

**Key result for the `sampling.toml` `thinking = "auto"` comment:** dynamic
allocation is **not** the unset default — the model applies a preset per-class
budget when the field is unset; dynamic must be requested explicitly with
`thinking_budget = -1`. (The exact unset preset *number* is itself UNVERIFIED —
the rendered table was not retrievable — but the `-1`/`0`/range semantics are
confirmed.)

**UNVERIFIED gaps (do not invent; leave unset or confirm live):**
1. `top_p` / `top_k` per-class numeric defaults — Google documents these as
   model-dependent and publishes no rendered constant; also now deprecated &
   ignored by gemini-3.x (still apply to 2.5).
2. `presence_penalty` / `frequency_penalty` valid range (`[-2.0, 2.0]` remains
   unconfirmed from a server-rendered official page); effective default `0.0`.
3. `thinking_budget` exact unset preset number.

**Method note:** the authoritative Vertex per-model parameter tables are
JS-rendered SPAs; server-side fetch returns only nav (same wall ticket 01 hit).
Aggregator/SEO results were rejected (they conflated Gemini 1.5 values). Where a
number was not obtainable from a server-rendered official Google page it is
`UNVERIFIED` rather than guessed. Recommendation for the `sampling.toml`
build-out: pin `max_output_tokens` intent to the 65,536 ceiling, leave
`top_p`/`top_k`/penalties unset (model default) rather than inventing numbers,
and express thinking as explicit `-1`/per-class budget.
