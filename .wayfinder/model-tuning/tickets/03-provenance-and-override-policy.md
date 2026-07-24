---
id: 03
title: "Decide provenance stamping and the env-override / eval-gate policy"
label: wayfinder:grilling
status: open
assignee: ""
blocked-by: [01, 02]
---

## Question

The high-level policy is settled (grilled 2026-07-24, on the map): pinned file canonical; every run records the effective per-tier params; tuning is an eval sweep that promotes a winner into the file; env override is a recorded, eval-gated escape hatch, not the tuning path. This ticket nails the mechanics so the implementation is unambiguous:

1. **Where the effective params are recorded.** Extend the report's existing provenance (ticket 026's `models` / `nodes` block already records the served `model_version` per node) with the effective per-tier tuning params, and/or a **config fingerprint** (a hash). Decide exactly what the fingerprint covers (which params, per tier) and where it lives on the report/run artifact so any produced result is self-describing.
2. **Env-override surface for v1.** Whether v1 ships *any* env override and, if so, the naming and granularity — per-tier (`STRIDE_PRO_TEMPERATURE`, `STRIDE_FLASH_TOP_P`, …) vs none — given the intent is repeatable/defensible-by-default with tuning via the file + sweep, not live twiddling. The `STRIDE_MODEL_*` model-string override is the precedent to follow or consciously decline. If overrides ship, they must validate exactly like the file values (fail-closed) and be captured by the fingerprint in (1).
3. **Eval-gate treatment of an unblessed config.** How the harness/CI treats a run whose fingerprint no baseline sweep covered — flag / mark uncertified, never silently trust. This decides the *mechanism* the gate enforces; the actual gate **run** is out of scope (live Vertex), so it must be offline-testable (e.g. a fingerprint-mismatch check the offline suite exercises).

**Reconcile with decision 15** (eval and prod read one file): provenance is precisely what lets an override coexist with honest evals — it converts "green suite, drifting prod" from an invisible risk into a visible, gateable fact. On close, this ticket plus [the schema ticket](02-config-schema-migration.md) graduate the implementation build-out from the fog.

## Research input (from ticket 01, 2026-07-24)

- **`response_schema` override is a hard crash, not a silent override** — ADK
  *raises* (`llm_agent.py:1078-1087`) if it appears on the config, so the
  override layer must **forbid** it outright, not merely deprioritize.
  `response_mime_type` overrides are silently discarded — also forbid.
- **`RunConfig.http_options` wins over the node's `http_options`**
  (`basic.py:82-83`) — a latent path to silently override
  `config/resilience.toml`. The override/provenance design must account for it
  so a timeout override cannot slip in un-fingerprinted.
- **`seed` is best-effort**, and `gemini-2.5-pro` is reported non-deterministic
  even with fixed seed + `temperature = 0`. Provenance framing: `seed` reduces
  variance, it does not certify reproducibility — the **fingerprint**, not the
  seed, is what makes a result defensible.
- **`candidate_count` is exactly Self-MoA** (Vertex 1–8); reserved, not offered.
  If ever exposed it must route through ticket 009's union/dedupe path, never
  the plain tuning knob.
