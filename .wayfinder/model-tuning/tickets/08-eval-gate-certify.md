---
id: 08
title: "Eval-gate mechanism — certify() + blessed-fingerprint manifest + sweep parametrization"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
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

## Answer

Shipped `evals/harness/certify.py`, the checked-in `evals/blessed-fingerprints.toml`,
the eval-path wiring in `evals/harness/run.py` + `modes.py`, and
`tests/test_evals_certify.py` (18 tests). Whole suite **546 pass / 1 skip** (07's
528 + 18), zero Vertex — the gate is exercised entirely offline.

**`certify(node_fingerprints, manifest) -> CertifyResult`** — a pure function
(ticket 03 §4). A node is uncertified when its fingerprint is absent from that
node's blessed set, so an **empty set fails closed** — an unblessed node never
certifies. `CertifyResult.uncertified` names each offending node **and the
fingerprint it presented**, so override-drift and served-build-drift are reported
identically (both are simply absent from the set — indistinguishable to the gate,
which is the point). Sorted output → deterministic verdict.

**Blessed manifest** (`BlessedManifest`, pydantic, `extra="forbid"`, frozen):
node → `frozenset` of blessed fingerprints (a node accumulates several blessed
served-builds). `load_manifest` **fails closed** (OWASP A02/A10): unreadable file,
invalid TOML, unsupported version (hard cutover to `version = 1`, no shim per
[ticket 02](02-config-schema-migration.md)), non-hex fingerprint, or stray key
all raise `CertificationError` rather than certifying against a silently-empty
set. The shipped manifest ships **all sets empty** — the honest pre-baseline
state (no live sweep has run; out of scope), so every real run reads uncertified
until a sweep promotes one.

**`promote(sampling, served_models, resolve_tier)`** — the single-sourced write
path: one `SamplingConfig` both re-pins `sampling.toml` **in place** (comments
preserved — the "why-absent" record is the file's point) *and* derives the
fingerprints written to the manifest, so file and manifest cannot drift. Both
writes happen only after the in-place rewrite succeeds → a rejected promotion
touches neither file. Promoting a param the file leaves **unset raises** — turning
an UNVERIFIED param ([ticket 04](04-per-class-decoding-defaults.md)) into a pinned
one is a human decision owing a rationale, not a silent sweep write. Manifest
serialization re-checks node keys against a slug shape as defence-in-depth against
a crafted key (OWASP A05).

**Eval-path wiring** (`run.py`): `command_run` **always** runs `certify` against
`load_manifest()` and surfaces the verdict (`_print_certification`), and the JSON
aggregate carries `certification` + `node_fingerprints` + `trusted` — nothing
downstream folds an uncertified run into a trusted number unaware ("never silently
trust"). Hard-fail is the CI knob `--require-certified` (off by default: a gate
firing before a baseline exists just trains people to bypass it). Sweep
parametrization falls out of `build_eval_pipeline(sampling=…)` + `STRIDE_SAMPLING_*`
overrides — each variant's `pipeline.node_fingerprints` is a property of its
config, certified per run.

**Resumed a prior session's WIP:** the artifacts existed uncommitted but the test
module failed collection — it imported two factory helpers (`make_report`,
`minimal_report_kwargs`) that were never created. Fixed by dropping the dead
`make_report` import and building the test report from the existing `sample_report`
factory via `model_copy`. No product code touched.

[Ticket 09 (docs)](09-docs-configuration.md) is now unblocked — the frontier.
