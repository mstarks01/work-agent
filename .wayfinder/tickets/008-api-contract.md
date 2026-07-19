---
id: 008
title: "Front-end API contract (async jobs, Ping auth)"
label: wayfinder:grilling
status: closed
assignee: github@michaelstarks.com
blocked-by: [005]
---

## Question

Define the REST contract the front-end calls on Cloud Run: submit endpoint (input payload shape), job handle + status/progress mechanism (polling vs SSE streaming of node-level progress), result retrieval, error surface, and where Ping JWT validation sits (middleware; follows existing org patterns).

## Resolution

Resolved 2026-07-19 via grilling, grounded in tickets 002 (ADK serving/streaming), 005 (report schema), and 012 (ValidationIssue shape).

1. **Surface** — custom job-oriented REST API (`/v1`) wrapping the ADK Runner internally. ADK's stock `get_fast_api_app()` routes are disabled in production (kept for local `adk web` debugging): stock routes couple the front-end to ADK's Event schema (broken once already in 2.0), lack job semantics, widen the attack surface (session CRUD with client-asserted `user_id` — BOLA), and give no place to bind the Ping identity server-side.
2. **Submit** — `POST /v1/jobs`, JSON `{"description": "<semi-structured text>", "system_name": optional}`; if `system_name` omitted, extraction infers it. ~100 KB server-side cap (413 over). Returns `201` with `{job_id, status}`. No client-controlled knobs in v1 (pack selection is mechanical, tiers are ops-owned — prior decisions).
3. **Progress** — poll + SSE. `GET /v1/jobs/{id}` is the canonical, disconnect-proof poll: status, per-node progress, timestamps, error info — lightweight, never embeds the report. `GET /v1/jobs/{id}/events` streams the same progression as SSE (state changes + node completions, ending with a completion event); backed by the same shared job state so reconnects resume cleanly.
4. **Result** — `GET /v1/jobs/{id}/report` returns the full self-contained report JSON (tens of KB, per ticket 005) once `status=completed`; `409` before completion; `404` after expiry.
5. **Lifecycle & errors** — `queued → running → completed | failed | rejected`. `rejected` = input failed the validity gate after the repair pass; response embeds the structured `ValidationIssue`s (ticket 012) so the user can fix their description. `failed` = internal error (node crash, timeout); detail logged, not leaked. All HTTP errors are RFC 9457 `application/problem+json`.
6. **Auth** — Ping JWT validated by a FastAPI dependency on all `/v1` routes (org-standard issuer/audience/JWKS checks). Job records store the token subject; every read is owner-only, returning `404` (not 403) for non-owners to prevent job-id enumeration. Report payload never includes the subject. Unauthenticated `/healthz` for Cloud Run probes.
7. **Deferred** — job store backend and retention TTL are storage/deployment concerns, out of this ticket: the contract only reserves 404-after-expiry semantics. Recorded in the map's fog.
