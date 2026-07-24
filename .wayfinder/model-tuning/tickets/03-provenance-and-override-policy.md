---
id: 03
title: "Decide provenance stamping and the env-override / eval-gate policy"
label: wayfinder:grilling
status: resolved
assignee: "github@michaelstarks.com"
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

## Answer (grilled 2026-07-24)

The three sub-questions are settled. The mechanics below are unambiguous enough
to build against once ticket 04's default numbers land.

### 1. Provenance recording — clear values **and** a derived fingerprint

- Every run records **both** the resolved per-tier sampling values *in the
  clear* **and** a fingerprint **derived from those recorded values** (so it is
  recomputable from the artifact, not hashed independently upstream). This
  mirrors the existing precedent — served `model` is already recorded in the
  clear per node (`report.py` `NodeRun`, ticket 026).
- The fingerprint is **generation identity**: `sha256(served model, resolved
  tier sampling)`, computed **per node** and stamped on `NodeRun` beside the
  served `model`. Model + sampling are bound into one hash because the thing the
  eval gate certifies is a tier's *generation behaviour*, which is model and
  sampling jointly — splitting them lets a mismatched pair pass two green
  half-checks. It keys on the **served** model (per node, per ticket 026, not
  the requested string), so a pro node served a new build gets a different hash
  than its siblings and that drift is *visible* — which is the point.
- The clear per-tier sampling values live **once** in a **top-level per-tier
  block** on the report (no duplication across same-tier nodes); only the small
  per-node hash repeats on each `NodeRun`.
- *Mechanical:* sha256 (matches `InputRef.source_sha256`); canonical
  serialization = sorted keys over the *resolved* values.

### 2. The `http_options` / resilience gap — fenced out

- The fingerprint stays **model + sampling**. The override surface **forbids
  `http_options`** (already on ticket 01's forbid list), so this effort
  introduces **no** new un-fingerprinted path — the ticket's "a timeout override
  cannot slip in un-fingerprinted" worry is closed at the surface, not by
  folding resilience into the hash.
- The pre-existing `RunConfig.http_options → config/resilience.toml` path
  (ticket 01 research, `basic.py:82-83`) is **out of scope**: it belongs to the
  resilience config's own future provenance, and `resilience.toml` is a separate
  pinned config the map deliberately keeps apart. Recorded in the map's
  Out-of-scope section.

### 3. Env override — v1 ships the eval-gated escape hatch

- v1 **does** ship an env override, reintroducing what decision 15 forbade —
  *because* the thing that made it unsafe (invisible eval-only drift) is now
  fixed by the fingerprint + gate. Decision 15 is not violated; it is superseded
  on its own terms. This generalizes the `STRIDE_MODEL_*` precedent to sampling.
- Naming: **`STRIDE_SAMPLING_{TIER}_{PARAM}`** (e.g.
  `STRIDE_SAMPLING_PRO_TEMPERATURE`, `STRIDE_SAMPLING_FLASH_TOP_P`) — per-tier,
  per-param, category-namespaced to parallel `STRIDE_MODEL_` and leave room for
  future `STRIDE_RESILIENCE_*`.
- Scope = **offered params only** (`temperature`, `top_p`, `seed`, per-tier
  class-guarded `thinking`), each validated **identically to the file value**
  (fail-closed, same ranges, class-legal `thinking`). An env var naming a
  **reserved** param (incl. `candidate_count`) or a **forbidden** one **raises**
  ("not overridable") — the live-knob surface equals the offered surface, no
  wider. `candidate_count` stays file-pinned at 1 and cannot be pushed off 1 by
  env at all.
- Overrides flow into the *resolved* sampling, so they are **captured by the
  per-node fingerprint automatically** — an override is defensible precisely
  because it is fingerprinted and gateable.

### 4. Eval-gate mechanism — manifest + offline `certify`

- The blessed baseline is a **checked-in manifest** (mechanical:
  `evals/blessed-fingerprints.toml`) holding the per-node fingerprints a
  sanctioned baseline sweep recorded. Sweep **promotion writes both**
  `sampling.toml` *and* the manifest — this is the "sweep promotes a winner"
  loop the destination describes. A manifest (not derive-from-file) is required
  because the served-model half of the fingerprint cannot be recomputed from the
  pinned file. It catches override-drift and served-build drift uniformly (both
  yield fingerprints absent from the set). Mechanical: manifest keys node → set
  of blessed fingerprints (a node may have several blessed served-builds).
- The gate is a **pure, offline-testable function**
  `certify(report_fingerprints, manifest) → {certified, uncertified_nodes}`,
  exercised by the offline suite with scripted fingerprints + a stub manifest
  (the live gate *run* stays out of scope — no Vertex).
- The **report carries raw fingerprints only**; certification is computed in the
  **eval/CI layer**, never stamped on the product report (a production report
  should not need the eval manifest to describe itself). "Never silently trust"
  = the eval path **always** runs `certify` and surfaces the verdict, refusing
  to fold an uncertified run into trusted aggregates. Hard-fail-vs-warn is a CI
  policy knob; the mechanism guarantees the verdict is computed and surfaced.

**Reconcile with decision 15:** provenance is exactly what lets an override
coexist with honest evals — the fingerprint + manifest gate convert "green
suite, drifting prod" from an invisible risk into a visible, gateable fact.

On close, this ticket plus [the schema ticket](02-config-schema-migration.md)
graduate the implementation build-out from the fog; the sole remaining blocker
is [ticket 04's](04-per-class-decoding-defaults.md) default numbers.
