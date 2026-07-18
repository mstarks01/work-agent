---
id: 014
title: "Implement report schema in stride_service"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

Graduate the decided report schema (see [STRIDE report schema and severity model](005-report-schema.md), prototype on branch `prototype/report-schema`) into production code: a `report` module in `stride_service` with the Threat/Severity/Verdict/StrideReport models, the fixed severity matrix with derived band, per-threat confidence, `rejected_threats` array, embedded System Model + boundary crossings, and report metadata — plus tests (matrix derivation, verdict/unknown-ref shapes, serialization round-trip). The prototype is the spec; production code may restructure freely.
