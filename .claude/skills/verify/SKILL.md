---
name: verify
description: Launch and drive the stride_service /v1 job API locally to verify changes at the HTTP surface.
---

# Verifying stride_service

The runtime surface is the FastAPI app from `stride_service.api.create_app()`.
Production auth needs `STRIDE_PING_ISSUER`, `STRIDE_PING_AUDIENCE`, and
`STRIDE_PING_JWKS_URL`; `create_app()` fails closed without them.

## Launch with real auth (no Ping needed)

Run a throwaway JWKS: generate an RSA key, serve
`{"keys": [jwt.algorithms.RSAAlgorithm.to_jwk(pub)]}` (add `kid`/`alg`) from a
stdlib `HTTPServer` thread, point `STRIDE_PING_JWKS_URL` at it, mint RS256
tokens with matching `kid`, `iss`, `aud`, `exp`, `sub`. Then:

```bash
uv run --with uvicorn python <script that sets env, then uvicorn.run(create_app(), port=8470)>
```

uvicorn is not a project dependency — always `--with uvicorn`.
Set the env vars **before** importing `stride_service.api`.

## Flows worth driving

- `GET /healthz` unauthenticated → 200.
- `POST /v1/jobs` (Bearer token, `{"description": ...}`) → 201 + Location;
  the stub runner completes the job in the background almost instantly.
- `GET /v1/jobs/{id}` → completed + per-node progress; never contains the report.
- `GET /v1/jobs/{id}/report` → full report JSON; `/events` → SSE stream that
  replays and closes on terminal jobs (`curl -N`, `Last-Event-ID` resumes).
- Ownership: a second subject's token must get 404 (not 403) on all reads.
- Errors: no/expired/tampered token → 401 problem+json with
  `WWW-Authenticate: Bearer`; >100 KB description → 413; garbage JSON → 422.

## Gotchas

- Every error body must be `application/problem+json` — check `-i` output.
- Don't `pkill -f <script name>` from a command whose own command line
  contains that name; it kills your shell (exit 144).
