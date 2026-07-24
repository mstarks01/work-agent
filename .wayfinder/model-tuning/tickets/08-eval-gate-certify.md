---
id: 08
title: "Eval-gate mechanism — certify() + blessed-fingerprint manifest + sweep parametrization"
label: wayfinder:task
status: open
assignee: ""
blocked-by: [07]
---

## Task

The offline gate that turns "green suite, drifting prod" into a visible,
gateable fact. Spec fully decided by
[ticket 03](03-provenance-and-override-policy.md) §4. The **live gate run stays
out of scope** (needs Vertex); this ticket ships only the offline-testable
mechanism.

- **Blessed-fingerprint manifest:** a checked-in file (`evals/blessed-
  fingerprints.toml`) keying **node → set of blessed fingerprints** (a node may
  accumulate several blessed served-builds over time). Sweep **promotion writes
  both** this manifest **and** `config/sampling.toml` — keep that write path
  single-sourced so they cannot drift.
- **Gate function:** a pure `certify(report_fingerprints, manifest) ->
  {certified: bool, uncertified_nodes: [...]}`. It lives in the **eval/CI
  layer**, not on the product report — the report carries raw fingerprints only
  ([ticket 07](07-provenance-stamping.md)); the verdict is computed here.
- **"Never silently trust":** the eval path **always** runs `certify` and
  surfaces the verdict, refusing to fold an uncertified run into trusted
  aggregates. Hard-fail-vs-warn is a CI policy knob left to the harness config;
  the mechanism only guarantees the verdict is computed and surfaced.
- **Sweep parametrization:** parametrize the eval harness so a sweep can vary
  per-tier params across runs, each run fingerprinted — exercised offline
  against scripted stand-ins (no live model).

**Tests:** offline — feed scripted report fingerprints + a stub manifest and
assert: exact-match → certified; a mismatched node → uncertified with that node
listed; an override-drifted fingerprint and a served-build-drifted fingerprint
both flagged. Consult `python-style` and `owasp-security`.
