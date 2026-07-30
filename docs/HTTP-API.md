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

See [Configuration](Configuration.md) for the required
[`STRIDE_AUTH_PROVIDER` / `STRIDE_OIDC_*`](#bearer-auth),
the [`STRIDE_JOB_STORE`](#job-storage) backend
selection, and the
[credentials](Configuration.md#provider-environment) for whichever model vendor
each tier uses.

## Auth

Every `/v1` route requires a bearer JWT (RS256, verified against the configured
issuer, audience, and JWKS) from the selected auth provider — see
[Bearer auth](#bearer-auth) below for supported identity providers and setup.
Job reads are **owner-only**: a request from
anyone but the job's owner gets `404`, not `403`, so job IDs cannot be probed
for existence. Rejection detail is generic on purpose — the real reason is
logged, never returned.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/jobs` | Submit a description; returns a job handle. |
| `GET` | `/v1/jobs/{id}` | Poll: status, per-node progress, timestamps. Never the report. |
| `GET` | `/v1/jobs/{id}/events` | The same progression as Server-Sent Events; resumable via `Last-Event-ID`. |
| `GET` | `/v1/jobs/{id}/report` | The full [report](Report-Schema.md) once completed; `409` before, and `409` if the report is withheld (below). |
| `GET` | `/healthz` | Unauthenticated liveness probe. |

Errors are RFC 9457 `application/problem+json`.

### When the report is withheld

A completed job's report can still be refused. Before serving it, the service
checks the run's **fingerprints** — a per-node hash of the model build that
answered plus that tier's decoding parameters — against the list this deployment
has **blessed** (approved by a measured run, in
`config/blessed-fingerprints.toml`). Two cases refuse with `409`:

- the run is **uncertified** (a fingerprint isn't blessed) *and*
  `STRIDE_REQUIRE_CERTIFIED` is set — off by default;
- the run is **unexercised** (a tier the graph declares produced no fingerprint
  at all) — always refused, and not reachable on a run that produced a report.

The problem body names the unblessed nodes and their hashes, and the tiers that
went unexercised. It never includes the analysis. The job itself stays
`completed` — withholding refuses the delivery, not the work, because the
fingerprints that show what drifted live inside the report. Nothing about this
appears in `GET /v1/jobs/{id}`; it is operator-facing only. See
[Architecture](Architecture.md#provenance-and-certification).

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
Authorization: Bearer <jwt>
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

## Bearer auth

These apply to the `/v1` API only; the in-process engine uses none of them.
Every `/v1` route requires a valid bearer token; the verifier returns the
token's `sub`, and job ownership binds to that subject.

### How the provider is chosen

`STRIDE_AUTH_PROVIDER` selects the backend at deploy time and **fails closed** —
an unset or unknown value stops startup rather than weakening or skipping the
check. The value is never read from the request, so a token can't pick its own
verifier.

| Variable | Purpose |
| --- | --- |
| `STRIDE_AUTH_PROVIDER` | Auth backend to use. Today: `oidc`. |

Each backend reads its own prefixed settings. The `oidc` backend is a standard
**OIDC JWT verifier**, configured through `STRIDE_OIDC_*`:

| Variable | Purpose |
| --- | --- |
| `STRIDE_OIDC_ISSUER` | Expected `iss` claim — your IdP's issuer URL. |
| `STRIDE_OIDC_AUDIENCE` | Expected `aud` claim — the API's identifier at the IdP. |
| `STRIDE_OIDC_JWKS_URL` | JWKS endpoint the IdP publishes its signing keys at. |

Tokens must be RS256-signed and carry `exp`, `iss`, `aud`, and `sub`; anything
else is rejected with a single generic error (the real reason is logged, never
returned).

### Supported identity providers

The `oidc` backend speaks plain OIDC, so it works with **any OIDC-compliant
identity provider**. For each, the three settings come from the IdP's OIDC
discovery document (`<issuer>/.well-known/openid-configuration` → `issuer` and
`jwks_uri`); the audience is the API/resource identifier you register for this
service. Switching providers is a values change only — the variable names stay
`STRIDE_OIDC_*`.

| Provider | Typical issuer (`STRIDE_OIDC_ISSUER`) |
| --- | --- |
| Ping (PingOne / PingFederate) | `https://auth.pingone.com/<env-id>/as` |
| Okta | `https://<org>.okta.com/oauth2/<auth-server-id>` |
| Auth0 | `https://<tenant>.auth0.com/` |
| Microsoft Entra ID (Azure AD) | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| AWS Cognito | `https://cognito-idp.<region>.amazonaws.com/<pool-id>` |
| Keycloak | `https://<host>/realms/<realm>` |

### Example: Okta

1. In the IdP, register this service as an API/resource and note the **audience**
   (resource identifier) clients will request tokens for — e.g. `stride-service`.
2. Fetch the discovery document to read the issuer and JWKS URL:
   ```bash
   curl -s https://<org>.okta.com/oauth2/<auth-server-id>/.well-known/openid-configuration \
     | jq '{issuer, jwks_uri}'
   ```
3. Set the environment:
   ```bash
   export STRIDE_AUTH_PROVIDER=oidc
   export STRIDE_OIDC_ISSUER="https://<org>.okta.com/oauth2/<auth-server-id>"
   export STRIDE_OIDC_AUDIENCE="stride-service"
   export STRIDE_OIDC_JWKS_URL="https://<org>.okta.com/oauth2/<auth-server-id>/v1/keys"
   ```
4. Start the app. Callers pass `Authorization: Bearer <token>` on every `/v1`
   request; see the [HTTP API](HTTP-API.md) for the routes.

Pointing at a different OIDC IdP (Ping, Auth0, Entra, …) is the same three
settings from that IdP's discovery document — no code change.

### Adding a new backend

A backend with a distinct name (or a non-OIDC mechanism — opaque-token
introspection, mTLS, an API key) is a new entry in the `_FACTORIES` registry in
[`src/stride_service/auth.py`](../src/stride_service/auth.py). Reuse
`OidcJwtVerifier` for another OIDC issuer, or implement the `TokenVerifier`
protocol (`verify(token) -> str`) for anything else; the API layer is unchanged.

## Job storage

Required by the [`/v1` API](HTTP-API.md); the in-process engine keeps no jobs.
The API only ever talks to the `JobStore` interface, so the backend is a
deploy-time choice.

`STRIDE_JOB_STORE` selects the backend at startup and **fails closed** — an
unset or unknown value stops startup rather than silently falling back to
non-durable storage. The value is never read from the request.

| Variable | Purpose |
| --- | --- |
| `STRIDE_JOB_STORE` | Job-store backend to use. Today: `memory`. |

The `memory` backend is a per-instance, in-process dict: fast and dependency-free,
but jobs are lost on restart and are not shared across instances, so it suits
single-instance or development deployments only. Durable, multi-instance
deployments need a shared backend (see below).

### Adding a new backend

A durable or shared backend (Redis, Postgres, …) is a new entry in the
`_FACTORIES` registry in [`src/stride_service/jobs.py`](../src/stride_service/jobs.py).
Implement the `JobStore` protocol (`create`, `get`, `save`) and read any
connection settings from its own prefixed env vars; the API layer is unchanged.
