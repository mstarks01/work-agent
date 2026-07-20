---
id: 023
title: "Implement the eval harness, ReferenceThreat, and scorer"
label: wayfinder:task
status: open
assignee:
blocked-by: [022]
---

## Question

Build the phase-1 eval spine decided by [Golden-case eval suite design](009-eval-suite-design.md), fitted to the corpus layout ticket 022 authored.

**`ReferenceThreat`** — an eval-side Pydantic model, deliberately **not** `DraftThreat` and not in the shipped service package (decision 3). Fields per ticket 022. Import `StrideCategory`, `Rating`, and severity derivation from `report.py` so the vocabularies cannot drift. Lint: every `affected_element_ids` entry resolves in its case's blessed model, mirroring ticket 020's exemplar guard.

**Corpus loader** — reads cases from the ticket-022 layout, fail-closed in the shape `MarkdownLoader` established.

**Three eval modes over one corpus** (decision 1): extraction (text vs blessed model), analysis (blessed model injected at `prepare`, vs reference threats), end-to-end (text → report).

**Scorer**, in the order the standing principle demands — mechanical first, judgement only where nothing else will do:

1. mechanical lane prefilter — candidates must share a STRIDE category (decision 7a)
2. per-pair LLM judge on `claim` equivalence within lane; binary match + one-line rationale; **randomized pair order** (decisions 7b, 12)
3. mechanical one-to-one bipartite assignment (decision 7c)
4. unmatched threats adjudicated into `ungrounded` / `valid-unlisted` / `noise` (decision 9)
5. mechanical severity-calibration confusion over matched pairs via `derive_severity_level` — no judge (decision 8)

Reported metrics: must-find recall, expected-tier recall, lane accuracy, element accuracy, ungrounded rate, `needs-info` bucket. Element agreement is **scored, never used as a prefilter** (decision 8).

**Judge prompt lives in the eval tree, not `prompts/`** (decision 14) — it satisfies none of ticket 020's lints and must not ship in the production image. Judge model string is **pinned and versioned separately from the tiers** (decision 12).

**Pinned sampling config** (decision 15): temperature becomes a versioned config value in the `config/` tree alongside `model_tiers.toml`, **shared by eval and production**, defaulting to `0` — a knob, not a hardcoded constant, so the Self-MoA path stays open.

**Gating**: Tier 1 structural only — report parses as `StrideReport`, refs resolve, IDs unique with correct category letters, severity bands match `derive_severity_level`, summary counts consistent. Must-find recall computes and reports but does **not** block (decisions 16, 19).

**Offline-testable by construction**: the scorer must be unit-testable against ticket 022's ~100 labelled pairs with zero Vertex calls, since that is what the credential-free PR job runs (decision 17).

Verify the ≥90% judge–human agreement bar against those fixtures (decision 13). Failing it means the judge prompt needs work, not ship-anyway.
