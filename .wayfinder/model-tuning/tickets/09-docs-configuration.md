---
id: 09
title: "Docs — docs/Configuration.md for per-tier sampling, overrides, and the eval gate"
label: wayfinder:task
status: open
assignee: ""
blocked-by: [05, 06, 07, 08]
---

## Task

Document the shipped mechanism end-to-end (the destination requires "docs").
Update `docs/Configuration.md` to cover:

- The v2 tier-keyed `config/sampling.toml`: the full commented decoding surface,
  what is pinned vs left-unset-by-design (cite [ticket 04](04-per-class-decoding-defaults.md)
  for why `top_p`/`top_k`/penalties are unset), and the `thinking`
  off/auto/int semantics (auto = explicit `-1`, **not** the unset default).
- The `STRIDE_SAMPLING_{TIER}_{PARAM}` override surface: offered params only,
  fail-closed, reserved/forbidden raise; framed as a **recorded, eval-gated
  escape hatch**, not the tuning path (tuning is a file diff + sweep).
- Provenance: the per-tier clear block + per-node generation-identity
  fingerprint, and what makes a result defensible (the fingerprint, not the
  seed — seed is best-effort).
- The eval gate: the blessed-fingerprint manifest, `certify`, and that an
  uncertified run is never silently trusted. Note the live sweep + tuned numbers
  are out of scope (`temperature = 0` remains the shipped default).

Keep it consistent with the existing `STRIDE_MODEL_*` documentation it
parallels.

**Also (surfaced resolving [ticket 07](07-provenance-stamping.md)):** the report
schema gained `StrideReport.sampling` (per-tier clear block) and
`NodeRun.sampling_fingerprint`. Update **`docs/Report-Schema.md`** and the
**`docs/example-report.html`** sample so the documented report shape matches
what ships — the provenance is only self-defending if the schema doc describes
it.
