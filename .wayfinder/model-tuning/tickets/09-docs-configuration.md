---
id: 09
title: "Docs — docs/Configuration.md for per-tier sampling, overrides, and the eval gate"
label: wayfinder:task
status: resolved
assignee: "github@michaelstarks.com"
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

## Answer

Documented the shipped mechanism end-to-end across three docs; docs-only change,
no code touched. Validated the example against the real schema (JSON parses; every
LLM node's fingerprint recomputes from the recorded clear block + served model).

**`docs/Configuration.md`** — rewrote the **Sampling** section for the v2
tier-keyed `sampling.toml`: a per-param table of pinned (`temperature = 0.0`,
`candidate_count = 1`) vs left-unset-by-design (`top_p`/`top_k`/penalties/`seed`/
`max_output_tokens`/`thinking`), citing ticket 04 for why the model-dependent
params are unset; the `thinking` off/auto/int semantics spelled out (auto =
explicit `-1`, unset = the model preset, **not** dynamic); the contract-breaking
absent set named. Added a **Sampling overrides** table
(`STRIDE_SAMPLING_{TIER}_{PARAM}`, offered params only — temperature/top_p/seed/
thinking, reserved/forbidden raise), framed as a recorded, eval-gated escape
hatch, sited next to the parallel `STRIDE_MODEL_*` table. Added **The eval gate
and provenance** section: per-tier clear block + per-node generation-identity
fingerprint (defensibility is the fingerprint, not the best-effort seed), the
blessed-fingerprint manifest + always-certify + never-silently-trust + empty
sets ship + `--require-certified` off by default + `promote` single-sources the
file and manifest; live sweep and tuned numbers out of scope, `temperature = 0`
remains the default.

**`docs/Report-Schema.md`** — added `sampling` to the top-level shape, expanded
the `NodeRun` line, and added a **Provenance** section (anchor `#provenance`)
documenting `StrideReport.sampling` (per-tier clear block, plain scalars, `null`
= model default) and `NodeRun.sampling_fingerprint` (64-hex, recomputable, absent
on FunctionNodes / stub reports), with the raw-fingerprint-only / certification-
in-the-eval-layer split and a cross-link to Configuration.

**`docs/example-report.html`** — added the `sampling` clear block (flash + pro)
and a real `sampling_fingerprint` on each LLM node (`assemble` FunctionNode left
without one), computed from the shipped config via `sampling_fingerprint`
(`407d58f1…` flash / `85b42a65…` pro). Pipeline view now renders a truncated
`fp …` per node (full hash + formula in the tooltip) and a per-tier resolved-
sampling line.

Destination reached: mechanism shipped end-to-end with offline tests and docs.
