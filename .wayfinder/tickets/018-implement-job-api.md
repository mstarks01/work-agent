---
id: 018
title: "Implement job API in stride_service"
label: wayfinder:task
status: open
assignee:
blocked-by: [014]
---

## Question

Implement the front-end API contract per the resolution of [Front-end API contract (async jobs, Ping auth)](008-api-contract.md): FastAPI `/v1` routes (`POST /v1/jobs` with the 100 KB cap, `GET /v1/jobs/{id}` poll, `GET /v1/jobs/{id}/events` SSE, `GET /v1/jobs/{id}/report`, unauthenticated `/healthz`), the job lifecycle state machine (`queued → running → completed | failed | rejected`, with `ValidationIssue`s embedded on rejection), RFC 9457 problem+json error responses, and the Ping JWT dependency (configurable issuer/audience/JWKS; owner-only reads returning 404 for non-owners). Job persistence goes behind a small job-store interface with an in-memory implementation — the real backend is a deferred storage decision, not this ticket. The ADK Runner is not built yet: run the pipeline behind a runner interface with a stub. Plus tests (route contracts, lifecycle transitions, auth/ownership including the 404-not-403 rule, SSE event sequence). AFK task: pure code, no decisions left. Consult `python-style` and `owasp-security` skills.
