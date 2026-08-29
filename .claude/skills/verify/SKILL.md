---
name: verify
description: Launch and drive the analysis_service /v1 job API locally to verify changes at the HTTP surface.
---

# Verifying analysis_service

The runtime surface is the FastAPI app from `analysis_service.api.create_app()`.
Production auth needs `ANALYSIS_AUTH_PROVIDER=oidc` plus `ANALYSIS_OIDC_ISSUER`,
`ANALYSIS_OIDC_AUDIENCE`, and `ANALYSIS_OIDC_JWKS_URL`; `create_app()` fails closed
without them.

## Launch with real auth (no IdP needed)

Run a throwaway JWKS: generate an RSA key, serve
`{"keys": [jwt.algorithms.RSAAlgorithm.to_jwk(pub)]}` (add `kid`/`alg`) from a
stdlib `HTTPServer` thread, set `ANALYSIS_AUTH_PROVIDER=oidc`, point
`ANALYSIS_OIDC_JWKS_URL` at it, mint RS256
tokens with matching `kid`, `iss`, `aud`, `exp`, `sub`. Then:

```bash
uv run python <script that sets env, then uvicorn.run(create_app(), port=8470)>
```

uvicorn is a `web` dependency-group member, not a wheel dependency. The group is
in `[tool.uv] default-groups`, so `uv sync` installs it and no `--with uvicorn`
is needed; passing it anyway is harmless.
Set the env vars **before** importing `analysis_service.api`.

`create_app()` defaults to the real ADK graph — the **Deployment** builds a
`analysis_service.pipeline.AdkPipelineRunner` per framework selection
(`analysis_service.deployment.Deployment.runner_for`) — so a submitted job calls
Vertex and fails without credentials. To drive the HTTP surface offline, pass
`create_app(runner=StubPipelineRunner())`; to exercise the real graph with
canned model output, build a pipeline with a `resolve_model` returning a
`BaseLlm` stand-in, as `tests/test_pipeline.py` does.

## Flows worth driving

- `GET /healthz` unauthenticated → 200.
- `POST /v1/jobs` (Bearer token, `{"description": ...}`) → 201 + Location;
  with the stub runner the job completes in the background almost instantly.
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
