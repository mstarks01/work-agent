---
id: 005
title: "STRIDE report schema and severity model"
label: wayfinder:prototype
status: closed
assignee: claude.ai@michaelstarks.com
blocked-by: [003]
---

## Question

Define the structured JSON report the front-end consumes: threat entry shape (STRIDE category, affected element reference, description, severity, mitigations, confidence?), the severity model (likelihood×impact matrix vs DREAD-style vs qualitative bands), and report-level metadata (job id, model versions, timings). Prototype a sample report against a realistic input so the front-end team can react to it.

## Resolution

Resolved 2026-07-18 via prototype. Prototype (draft Pydantic models + generated sample) on branch `prototype/report-schema`: `prototypes/report_schema_prototype.py`, `prototypes/sample_report.json`. Sample system model passes the real `stride_service` validator; boundary crossings derived, all six STRIDE categories and all three verdict states demonstrated.

1. **Severity model** — qualitative likelihood x impact (each low|medium|high) with the band (low|medium|high|critical) **derived by a fixed matrix, never asserted** by a model, plus a `justification` string. Critic calibrates two narrow judgments; evals check the arithmetic. DREAD rejected (deprecated; numeric sub-scores are false precision from an LLM). Bands-only rejected (no calibration signal).
2. **Confidence** — per-threat qualitative `confidence: low|medium|high`, critic-calibrated grounding in model facts. Orthogonal to the verdict (the inclusion ruling).
3. **Threat entry shape** — `id` (`<STRIDE letter>-NN`, per-category sequence), `category`, `title`, `description`, `affected_element_ids` (>=1 typed-slug refs), `severity`, `confidence`, `mitigations` (list of `{summary, detail}`), `verdict` (`{status: confirmed|needs-info|rejected, reason, related_unknowns: [{element_id, attribute}]}`). needs-info verdicts point at the unknown attributes that caused them.
4. **Rejected threats** — ride in the report in a separate `rejected_threats` array with rejection reasons: visible audit trail, cleanly split from actionable threats.
5. **Self-contained payload** — report embeds the full validated System Model plus derived `boundary_crossings`; every element ref resolves inside one payload (tens of KB).
6. **Report metadata** — `schema_version`, `disclaimer` (AI-generated label per scope decision), `job` (`id`, `status`, `created_at`, `completed_at`, `revise_rounds`), `input` (`system_name`, `source_sha256`), `nodes` (per-node `{node, model|null, duration_ms}` — model versions + timings), `summary` (counts by category/severity, needs-info, rejected, elements analyzed).
