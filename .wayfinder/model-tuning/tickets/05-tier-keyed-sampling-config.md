---
id: 05
title: "Tier-keyed SamplingConfig + resolve_sampling + STRIDE_SAMPLING_* overrides + v2 sampling.toml"
label: wayfinder:task
status: open
assignee: ""
blocked-by: [02, 04]
---

## Task

The config layer for per-tier sampling: author the v2 file first, then reshape
the loader to fit it (per the effort's "author content before loader code"
note). Spec is fully decided by [ticket 02](02-config-schema-migration.md)
(schema) and [ticket 04](04-per-class-decoding-defaults.md) (default numbers).

**Author `config/sampling.toml` (v2), content-first:**

- `version = 2` — **hard cutover**, no v1 shim (the loader fail-closes on any
  other version).
- Key by tier: `[tiers.flash]` / `[tiers.pro]`, reusing `model_tiers.toml`'s
  node→tier map.
- Carry the **full commented decoding surface** (temperature / top_p / top_k /
  seed / max_output_tokens / candidate_count / penalties / thinking), each with
  a one-line comment. `temperature = 0.0` pinned; `candidate_count = 1`.
- **Reconcile 02 with 04:** ticket 02 said "every param pinned to its default,"
  but ticket 04 found `top_p` / `top_k` / `presence_penalty` /
  `frequency_penalty` have **no publishable per-class default** (UNVERIFIED).
  Resolve by keeping every param **present in the commented surface** but
  **left unset with a rationale comment** ("model default; no published
  constant — see ticket 04") rather than inventing a number. Pin only what 04
  verified: `temperature = 0.0`, `candidate_count = 1`, and
  `max_output_tokens` intent against the **65,536** ceiling.
- `thinking` is a per-tier mixed scalar (`"off"` | `"auto"` | int). **Fix the
  comment per ticket 04:** unset applies the model's preset per-class budget;
  `"auto"`/dynamic is **not** the unset default and maps to explicit
  `thinking_budget = -1`. Resolved class-legal in the loader (flash 0–24,576,
  0=off; pro 128–32,768, 0 is a 400).
- A "why-absent" block naming the contract-breaking params kept out
  (`response_schema`, `response_mime_type`, `stop_sequences`, `http_options`).

**Reshape `stride_service/sampling.py` to fit:**

- `SamplingConfig` → tier-keyed; add a `TierSampling` type for one tier's
  resolved params.
- Add `resolve_sampling(node) -> TierSampling`, the injected **sibling of
  `resolve_model`** (`model_tiers.py:116`), using the same node→tier map.
- `candidate_count` loader **fail-closes if ≠ 1** (the reserved Self-MoA lever).
- `thinking` resolved to a class-legal value in the loader; illegal per-class
  values raise.
- **Env overrides** `STRIDE_SAMPLING_{TIER}_{PARAM}` for the **offered** params
  only (`temperature`, `top_p`, `seed`, class-guarded `thinking`), applied
  before validation exactly like `load_model_tiers` applies `STRIDE_MODEL_*`
  (`model_tiers.py:123-155`): validated identically, fail-closed, set-but-empty
  raises. An env var naming a **reserved** (incl. `candidate_count`) or
  **forbidden** param **raises** ("not overridable"). See
  [ticket 03](03-provenance-and-override-policy.md) §3.

**Tests:** offline, fail-closed coverage mirroring `test_model_tiers` — bad
version, unknown key/tier, out-of-range value, `candidate_count ≠ 1`, illegal
per-class `thinking`, each override path incl. reserved/forbidden-var rejection.
Consult `python-style` and `owasp-security`.
