---
id: 014
title: "Implement report schema in stride_service"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Graduate the decided report schema (see [STRIDE report schema and severity model](005-report-schema.md), prototype on branch `prototype/report-schema`) into production code: a `report` module in `stride_service` with the Threat/Severity/Verdict/StrideReport models, the fixed severity matrix with derived band, per-threat confidence, `rejected_threats` array, embedded System Model + boundary crossings, and report metadata — plus tests (matrix derivation, verdict/unknown-ref shapes, serialization round-trip). The prototype is the spec; production code may restructure freely.

## Resolution

Resolved 2026-07-19. Shipped `src/stride_service/report.py` + `tests/test_report.py` (31 tests; suite 60 green). Graduated from the ticket-005 prototype with production restructures:

- All prototype models (`Severity`, `Mitigation`, `UnknownRef`, `Verdict`, `Threat`, `NodeRun`, `Job`, `InputRef`, `Summary`, `StrideReport`) with the fixed severity matrix; `derive_severity_level()` exposed so evals can check the arithmetic; asserted `level` that contradicts the matrix is rejected.
- `Job.status` renamed `complete` → `completed` to match the ticket-008 lifecycle contract.
- Verdict shape enforced: `needs-info` requires ≥1 `related_unknowns` + a reason; `rejected` requires a reason; `related_unknowns` illegal on other statuses. Threat IDs must carry their category letter (`S-01` ⇒ spoofing).
- Self-containment enforced in `StrideReport`: every element ref (threats + unknown-refs) must resolve in the embedded System Model; `boundary_crossings` must equal the derived set; threat IDs unique; rejected verdicts only in `rejected_threats` (and vice versa); `summary` must match `build_summary()` of the report's own contents.
- `SCHEMA_VERSION = "1.0"`, `DEFAULT_DISCLAIMER` (AI-generated label per scope decision); all names exported from the package root.

Unblocks [Implement job API in stride_service](018-implement-job-api.md).
