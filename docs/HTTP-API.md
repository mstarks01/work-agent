# HTTP API

The `/v1` job API is the decoupled surface for a front end. It is async:
submit text, get a job handle, poll or stream until the report is ready. For an
in-process integration prefer [`StrideEngine`](Integration-Guide.md) — it drives
the same pipeline without the job/auth/polling machinery.

Build the app with `create_app()`; every seam is injectable, defaulting to
production wiring.

```python
from stride_service import create_app

app = create_app()   # configured store, real pipeline, configured JWT verifier
```

See [Configuration](Configuration.md#bearer-auth-http-surface-only) for the
required `STRIDE_AUTH_PROVIDER` / `STRIDE_OIDC_*`, the
[`STRIDE_JOB_STORE`](Configuration.md#job-storage-http-surface-only) backend
selection, and the Vertex environment.

## Auth

Every `/v1` route requires a bearer JWT (RS256, verified against the configured
issuer, audience, and JWKS) from the selected auth provider — see
[Configuration](Configuration.md#bearer-auth-http-surface-only) for supported
identity providers and setup. Job reads are **owner-only** and return
`404` — not `403` — for a non-owner, so job IDs cannot be enumerated. Rejection
detail is generic on purpose; the reason is logged, never returned.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/jobs` | Submit a description; returns a job handle. |
| `GET` | `/v1/jobs/{id}` | Poll: status, per-node progress, timestamps. Never the report. |
| `GET` | `/v1/jobs/{id}/events` | The same progression as Server-Sent Events; resumable via `Last-Event-ID`. |
| `GET` | `/v1/jobs/{id}/report` | The full [report](Report-Schema.md) once completed; `409` before. |
| `GET` | `/healthz` | Unauthenticated liveness probe. |

Errors are RFC 9457 `application/problem+json`.

## Lifecycle

```
queued -> running -> completed | failed | rejected
```

- `completed` — the report is available at `/v1/jobs/{id}/report`.
- `rejected` — the input failed the validity gate; the poll response carries the
  `validation_issues` (see [Report-Schema](Report-Schema.md)).
- `failed` — an internal error; only a generic message is exposed.

This mirrors the engine's three outcomes; the HTTP layer adds the queue,
ownership, and delivery around them.

## Submit and poll

```http
POST /v1/jobs
Authorization: Bearer <ping-jwt>
Content-Type: application/json

{"description": "Customers sign in and place orders...", "system_name": "Orders"}
```

```json
201 Created
Location: /v1/jobs/job-ab12...
{"job_id": "job-ab12...", "status": "queued"}
```

Then `GET /v1/jobs/job-ab12...` until `status` is terminal, or subscribe to
`GET /v1/jobs/job-ab12.../events`. The description is capped at 100 KiB (see
[Configuration](Configuration.md)); an oversized body is rejected before parsing.
