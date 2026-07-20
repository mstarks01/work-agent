---
id: 022
title: "Author the phase-1 golden corpus and SME blessing workflow"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

Author the phase-1 golden-case corpus decided by [Golden-case eval suite design](009-eval-suite-design.md) — content first, harness code second (ticket 023 fits the loader and scorer to this layout, not the reverse).

**Six cases**, each 8–20 elements so its reference set is exhaustively enumerable by a human (decision 5):

- one payment/checkout system — the **control** case, near-identical in shape to `analyst.md`'s shared exemplar
- three structurally far from it — an IoT fleet, a batch data pipeline, an ML inference service
- two OWASP Threat Model Cookbook conversions

Without the payments control there is nothing to subtract from and the exemplar-domain-bias delta is unmeasurable, so it is not optional (decision 6).

**Two artifacts per case** (decision 1):

- `model.json` — a blessed `SystemModel`, **bootstrapped from a real `extract` run and then SME-corrected**, not hand-authored (decision 2). Must pass the shipped validator. Keep the bootstrap→blessed diff per case as recorded signal about what `extract` habitually gets wrong.
- `threats.*` — a list of `ReferenceThreat`: `category`, `affected_element_ids` (all must resolve in that case's `model.json`), `claim` (one sentence, attacker-action phrasing), `tier` (`must-find` | `expected`), `severity` (`likelihood`/`impact` only — no band, it derives), `notes` (SME rationale, never scored).

Plus the source input text per case, and its `source_sha256`.

**Blessing workflow** — one-time, offline, before a case merges; nothing interactive and nothing at runtime (per-analysis human review stays out of scope). An SME signs off on both artifacts in one reading session, one PR, one approval. The SME corrects the bootstrapped model by working a checklist against the **source text**, never against the candidate model — that is the whole mitigation for the anchoring risk in decision 2. Document the workflow itself as part of this ticket so the phase-2 expansion and the corpus feedback loop (decision 11) can follow it.

**Judge calibration fixtures, authored in the same session** (decision 13): ~100 candidate pairs hand-labelled match/no-match. These are the ground truth for the ≥90% judge–human agreement bar, and they persist as the fixtures that let ticket 023's scorer be unit-tested offline with zero Vertex calls.

Deliberately **not** in phase 1: the two adversarial/degenerate cases (sparse-input `needs-info`, and validity-gate failure exercising `repair` → `reject`) and the remaining six cases. Deferred to ticket 025 (decision 19) because the binding cost is serialized SME time.

Internal-system cases carry a sanitization obligation this ticket owns: no real hostnames, credentials, or customer data in a fixture that lives in the repo.
