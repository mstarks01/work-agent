---
id: 015
title: "Author v1 STRIDE category skills and severity rubric"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: [006]
---

## Question

Author the seven v1 skill files per the design in [Skills-as-SME design and injection](006-skills-sme-design.md): six STRIDE-category skills in `skills/stride/` (five fixed sections each — scope boundary, per-element-type applicability, attribute-triggered threat patterns keyed to the System Model attribute vocabulary, failure-mode guardrails, mitigation guidance) and `skills/shared/severity_rubric.md`. Respect the CI token caps (category ≤ 3K, rubric ≤ 1K) and the fixed section headings the critic's boundary digest extraction depends on. No few-shot exemplars — those are prompt material (ticket 013). Ground pattern content in the research findings on `research/quality-patterns` and standard catalogs (CAPEC/ATT&CK-derived, condensed).

## Resolution

Resolved 2026-07-19. Authored the seven v1 skill files plus the empty domain-pack directory:

- `skills/stride/spoofing.md`, `tampering.md`, `repudiation.md`, `information-disclosure.md`, `denial-of-service.md`, `elevation-of-privilege.md` — filenames match the `StrideCategory` literals in `stride_service.report` so loader mapping is mechanical.
- `skills/shared/severity_rubric.md` — reproduces the exact `SEVERITY_MATRIX` from `stride_service.report` (cross-checked cell-by-cell); band always derived, never asserted.
- `skills/domains/.gitkeep` — empty v1 pack set per ticket 006.

**Fixed section headings** (facts ticket 016's CI lints and digest extraction depend on): exactly five H2 sections per category file, in order — `## Scope` (the critic-digest section), `## Applicability`, `## Threat Patterns`, `## Guardrails`, `## Mitigations`. Identical across all six files (verified by grep).

Content decisions:
- Every `## Scope` section defines the category and its lane boundary against the other five — the cross-category dedupe lanes the critic digest is assembled from.
- `## Applicability` follows the classic STRIDE-per-element matrix (S → entities+processes; T → processes+stores+flows; R → entities+processes+stores; I → processes+stores+flows; D → processes+stores+flows; E → processes) and explains *how* to analyze each admitted type; flows/boundaries are cited as evidence where they aren't targets.
- `## Threat Patterns` are trigger-keyed to the fixed attribute vocabulary (`exposure`, `authentication`, `encryption_in_transit`, `encryption_at_rest`, `data_classification`, `protocol`, `technology`, `kind`, asset tags, derived boundary crossings) with explicit `unknown`-sentinel handling; condensed from CAPEC/ATT&CK-style catalogs and OWASP Top 10:2025 / LLM Top 10 / ASI where apt.
- Every `## Guardrails` section carries the two research-documented failure modes (second-order reach, attacker-vs-observation perspective) plus unknown-discipline and stay-in-the-model grounding rules.
- No few-shot exemplars anywhere — prompt material, ticket 013.
- Token estimates (words × 4/3): category files ~1.5–1.7K (cap 3K), rubric ~0.7K (cap 1K) — headroom for pattern growth after evals.
