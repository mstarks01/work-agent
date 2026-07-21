---
id: 007
title: "Per-agent Vertex model tier assignment"
label: wayfinder:grilling
status: closed
assignee: github@michaelstarks.com
blocked-by: [004]
---

## Question

For each node in the decided topology, which Vertex model tier does it get (Flash-class for extraction/formatting, Pro-class for analysis/critique?), how is the assignment configured (per-node config file so ops can retune without code changes?), and what are the fallback/upgrade rules?

## Resolution

Resolved 2026-07-18 via grilling, grounded in tickets 001 (quality patterns: recall lives in the proposal stage, don't put a cheap tier there; Self-MoA — use your best model, don't mix), 002 (ADK: per-`LlmAgent` model string; regional Vertex endpoints may reject `-latest` aliases), and 004 (topology).

1. **Tier map** — `flash` → extract, repair (formatting/normalization work); `pro` → all six STRIDE-category analysts + critic (analysts are the only stage that can find threats; critic drives precision). Deterministic FunctionNodes (validate, prepare, join, router, assemble) carry no model. Fits the agreed ~2-4x token budget since the canonical System Model is small.
2. **Two named tiers, shared** — config defines exactly two tiers, each a single **pinned Vertex model version string** (no `-latest` aliases: regional-endpoint compatibility + eval reproducibility); nodes reference a tier name. No per-node model diversity or override keys.
3. **Configuration** — a versioned config file is the source of truth (tier → model string, node → tier). Two env vars (`STRIDE_MODEL_FLASH`, `STRIDE_MODEL_PRO`) override the tier strings at deploy time so ops can retune on Cloud Run without an image rebuild; the node→tier mapping is file-only.
4. **Fallback/upgrade** — never auto-degrade across tiers. Retries stay on the pinned model; a run that can't reach its tier fails the job cleanly with a clear error (retry mechanics belong to the future graph-error-handling ticket). Outage response is an ops action: flip the env var to another same-tier version. Upgrades change the pinned string only after the golden-case eval suite passes on the candidate model.

Graduated: [Implement model-tier config in stride_service](017-implement-model-config.md).

**Decision 2 amended** by [Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md) (2026-07-21): "pinned Vertex model version string" assumed numbered stable builds, which Gemini 2.5 and later do not ship — the lint it produced rejected every string that resolves. "Pinned" now means the most specific *stable GA* identifier (no `-latest`, no `-preview`/`-exp`), and eval reproducibility is carried by recording the served model version per run rather than by the string alone. Decisions 1, 3 and 4 stand.
