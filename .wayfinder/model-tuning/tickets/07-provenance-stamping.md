---
id: 07
title: "Provenance stamping — per-node fingerprint on NodeRun + top-level per-tier clear block"
label: wayfinder:task
status: open
assignee: ""
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
