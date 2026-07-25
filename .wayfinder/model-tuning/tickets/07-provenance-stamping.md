---
id: 07
title: "Provenance stamping — per-node fingerprint on NodeRun + top-level per-tier clear block"
label: wayfinder:task
status: resolved
assignee: "github@michaelstarks.com"
blocked-by: [05, 06]
---

## Task

Make every run self-describing about the sampling it ran on. Spec fully decided
by [ticket 03](03-provenance-and-override-policy.md) §1.

- **Clear values, top-level per tier:** add a per-tier block to `StrideReport`
  recording the **resolved** sampling values (`temperature`, `top_p`, `seed`,
  `thinking`, …) in the clear, **once per tier** (not duplicated per node).
- **Per-node fingerprint:** add to `NodeRun` (`report.py:188`, beside the
  existing served `model`) a `sha256(served model, resolved tier sampling)` —
  the **generation-identity** hash, keyed on the **served** model (per node,
  per ticket 026) so a node served a new build gets a different hash than its
  siblings and that drift is visible.
- **Canonical serialization** (so the hash is recomputable from the artifact):
  sorted keys over the resolved values; sha256 to match
  `InputRef.source_sha256`. The fingerprint must be **derivable from the
  recorded clear block + served `model`**, not hashed from some upstream state.
- Fingerprint covers **model + sampling only** — `http_options`/resilience is
  deliberately **not** folded in (see the map's Out-of-scope note); the override
  surface already forbids `http_options`, so no un-fingerprinted path exists.

**Tests:** offline — given a scripted run, assert the clear block matches the
resolved config, the per-node fingerprint recomputes from (clear block, served
model), and two nodes with different served models on the same tier get
different fingerprints. Consult `python-style` and `owasp-security`.

## Answer

Every completed run is now self-describing about the sampling it ran on: a
top-level per-tier clear block plus a per-node generation-identity fingerprint.
Whole suite green (528 pass, +8).

**Fingerprint (`sampling.py`):** `sampling_fingerprint(served_model, sampling:
TierSampling) -> str` = `sha256` over a canonical JSON payload
`{"model": served_model, **sampling.model_dump()}` with `sort_keys=True`. Model
and resolved sampling bound into one hash (ticket 03 §1), keyed on the **served**
model (ticket 026) so a node served a different build diverges from its
same-tier siblings — and recomputable from the recorded clear values + served
model alone.

**Schema (`report.py`):**
- `NodeRun.sampling_fingerprint: str | None` (64-hex), with a validator that a
  fingerprint requires a served `model` on the same node — deterministic
  FunctionNodes carry neither.
- `StrideReport.sampling: dict[str, dict[str, float | int | None]]` — tier name
  → the tier's resolved values (serialized `TierSampling`), once per tier.
  **Recorded as plain scalars, not the `TierSampling` model, to avoid an import
  cycle:** `report.py` is a low-level schema module that `skills` imports, and
  `sampling`/`model_tiers` import back through `skills` → `report`. Both new
  fields default (empty / `None`) so stub-runner and eval-synthetic reports stay
  valid; the production runner always populates them.

**Static provenance on `Pipeline` (`graph.py`):** `build_pipeline` gains a
`tier_sampling` param (the clear block, = `SamplingConfig.tiers`) and now also
computes `node_fingerprints` (graph node → hash) from the very `node_models`
source and `resolve_sampling` the graph binds — so a node's recorded served
model and its fingerprint can never describe different generations. Both ride on
the frozen `Pipeline` (config-derived, fixed for its life, computed once).

**Stamping (`pipeline.py`):** the runner copies `tier_sampling` (as `model_dump`
per tier) into `StrideReport.sampling` and each finished node's fingerprint from
`node_fingerprints` onto its `NodeRun`. `build_default_pipeline` and the eval
harness pass `tier_sampling=sampling.tiers`.

**`http_options` stayed out** of the hash (ticket 03 §2 / map Out-of-scope): the
fingerprint is model + sampling only; the override surface already forbids
`http_options`, so no un-fingerprinted path was added.

**Tests:** 5 unit (`test_sampling.py`) — sha256 shape, determinism,
model-sensitivity, sampling-sensitivity, recompute-from-clear-values; 3
integration (`test_pipeline.py`) — the clear block equals the resolved config,
every LLM node's fingerprint recomputes from `(clear block, served model)` while
FunctionNodes have none, and flash≠pro nodes get distinct identities.

**Follow-on for ticket 09 (docs):** the report gained `sampling` +
`NodeRun.sampling_fingerprint`, so **`docs/Report-Schema.md` and
`docs/example-report.html` are now stale** — ticket 09 (currently scoped to
`docs/Configuration.md`) should widen to cover the report-shape additions too.
[Ticket 08 (eval-gate `certify` + manifest)](08-eval-gate-certify.md) was
blocked by 07 — now the frontier; it consumes these raw per-node fingerprints.
