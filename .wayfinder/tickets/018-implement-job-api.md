---
id: 018
title: "Implement job API in stride_service"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: [014]
---

## Question

Implement the front-end API contract per the resolution of [Front-end API contract (async jobs, Ping auth)](008-api-contract.md): FastAPI `/v1` routes (`POST /v1/jobs` with the 100 KB cap, `GET /v1/jobs/{id}` poll, `GET /v1/jobs/{id}/events` SSE, `GET /v1/jobs/{id}/report`, unauthenticated `/healthz`), the job lifecycle state machine (`queued → running → completed | failed | rejected`, with `ValidationIssue`s embedded on rejection), RFC 9457 problem+json error responses, and the Ping JWT dependency (configurable issuer/audience/JWKS; owner-only reads returning 404 for non-owners). Job persistence goes behind a small job-store interface with an in-memory implementation — the real backend is a deferred storage decision, not this ticket. The ADK Runner is not built yet: run the pipeline behind a runner interface with a stub. Plus tests (route contracts, lifecycle transitions, auth/ownership including the 404-not-403 rule, SSE event sequence). AFK task: pure code, no decisions left. Consult `python-style` and `owasp-security` skills.

## Resolution

Resolved 2026-07-19. Shipped the full ticket-008 contract as three new modules:

- `stride_service.jobs` — the `queued → running → completed | failed | rejected`
  state machine (`JobRecord.transition()` refuses illegal edges), an append-only
  per-job event log with dense 1-based `seq` (backs both poll progress and SSE
  resume), the `JobStore` persistence seam with `InMemoryJobStore` (storage
  backend stays deferred), and the `PipelineRunner` seam with
  `StubPipelineRunner` (walks named nodes, returns a minimal fully valid
  `StrideReport`; the ADK graph plugs in later). `execute_job()` fails closed:
  runner exceptions are logged, the job stores only a generic message.
- `stride_service.auth` — `PingJwtVerifier` (PyJWT + JWKS): configurable
  issuer/audience/JWKS via `STRIDE_PING_*` env vars, `RS256` only, required
  `exp/iss/aud/sub`; all rejections collapse to one generic
  `AuthenticationError` (reason logged, never surfaced).
- `stride_service.api` — `create_app()` with injectable store/runner/verifier
  seams: `POST /v1/jobs` (100 KB description cap + raw-body middleware guard,
  413), `GET /v1/jobs/{id}` poll (progress, `ValidationIssue`s on rejected,
  generic error on failed, never the report), `GET /v1/jobs/{id}/events`
  (SSE, `Last-Event-ID` resume, closes after terminal event),
  `GET /v1/jobs/{id}/report` (409 unless completed), unauthenticated
  `/healthz`. All errors RFC 9457 problem+json; owner-only reads return an
  identical 404 for missing and foreign jobs (no job-id enumeration).

54 new tests (lifecycle edges, store copy semantics, executor outcomes, JWT
verifier incl. HS256 alg-confusion, route contracts, ownership, SSE
sequence/resume); suite 183 green. Also verified live: uvicorn + a local JWKS
server through the real `PyJWKClient` path — full submit → poll → report → SSE
flow plus 401/404/409/413/422 probes; recipe captured in
`.claude/skills/verify/SKILL.md`. New deps: `fastapi`, `pyjwt[crypto]`; dev
`httpx`.
