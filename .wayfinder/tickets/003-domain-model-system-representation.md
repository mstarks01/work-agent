---
id: 003
title: "Domain model: canonical system representation"
label: wayfinder:grilling
status: closed
assignee: claude.ai@michaelstarks.com
blocked-by: []
---

## Question

Define the canonical internal model the extraction agent produces and all downstream agents consume: what are the element types (components/processes, data flows, endpoints, data stores, external actors, trust boundaries, assets?), their attributes, and their identity scheme (threats must trace back to elements)? Resolve via /grilling + /domain-modeling; align with classic DFD-based STRIDE-per-element practice so skills/prompts can lean on established methodology.

## Resolution

Resolved 2026-07-18 via grilling + domain-modeling. Glossary terms captured in `CONTEXT.md`.

1. **Taxonomy** — classic five DFD element types (External Entity, Process, Data Store, Data Flow, Trust Boundary), no additions. Assets are tags on elements, not a sixth type. Leans on the established STRIDE-per-element applicability matrix.
2. **Identity** — typed human-readable slugs, unique per job: `<type>:<normalized-name>`; flows use `flow:<source>-to-<dest>:<label>`. Deterministic from type+name for eval comparability. No cross-job identity.
3. **Trust boundaries** — zone membership (pytm-style), flat zones for v1. Non-flow elements carry `trust_zone`; boundary crossings are **derived** (endpoints' zones differ), never extracted.
4. **Attributes** — fixed security-relevant set per type plus free-form `notes`; common: `id`, `name`, `description`, `assets`, `source_excerpt`.
   - External Entity: `kind` (human|external-system), `trust_zone`
   - Process: `technology`, `trust_zone`, `exposure` (internet-facing|internal)
   - Data Store: `technology`, `trust_zone`, `data_classification`, `encryption_at_rest`
   - Data Flow: `source`, `destination`, `protocol`, `authentication`, `data_description`, `encryption_in_transit`
   - Trust Boundary: `kind` (network|privilege|tenant|other)
5. **Unknowns/assumptions** — `unknown` is a legal value for security-relevant attributes (analysts treat as unverified, not absent/present). Inferred values are recorded plus an entry in a top-level `assumptions` list (assumption, element ID, basis). Never silent guesses; no assume-worst defaults.
6. **Flow directionality** — one flow per interaction, direction = initiation; response implicit in `data_description`. Independent reverse interactions (webhooks/push) are their own flows.
7. **Asset vocabulary** — controlled enum, config-extendable: `credentials`, `pii`, `financial`, `health`, `secrets`, `business-critical-data`, `availability-critical`, `reputation`.
8. **Validity gate** — mechanical rules (unique typed IDs, referential integrity of flow endpoints and `trust_zone`, ≥1 zone, legal enums, assumptions reference real elements). On failure: one repair pass feeding validator errors back to extraction, then fail the job with a structured error. Silent auto-repair rejected.
