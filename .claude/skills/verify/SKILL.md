---
name: verify
description: Launch and drive the analysis_service /v1 job API locally to verify changes at the HTTP surface.
---

# Verifying analysis_service

The runtime surface is the FastAPI app from `analysis_service.api.create_app()`.
Production auth needs `ANALYSIS_AUTH_PROVIDER=oidc` plus `ANALYSIS_OIDC_ISSUER`,
`ANALYSIS_OIDC_AUDIENCE`, and `ANALYSIS_OIDC_JWKS_URL`; `create_app()` fails closed
without them.

## Launch offline

`OidcSettings` refuses a JWKS URL that is not `https://`, so a local JWKS
server on `http://` cannot stand in for an IdP. Pass a verifier to
`create_app()` in its place. A runner in place of a **Deployment** also needs
each setting the Deployment would give. Take them from `tests/test_api.py`, as
`make_client` does:

```python
import os
import sys

os.environ |= {"ANALYSIS_OFFLINE": "1", "ANALYSIS_JOB_STORE": "memory"}
sys.path.insert(0, os.getcwd())  # run from the repository root

import uvicorn
from analysis_service import StubPipelineRunner
from analysis_service.api import create_app
from tests import test_api
from tests.factories import SEEDING_BUDGET

app = create_app(
    runner=StubPipelineRunner(),
    frameworks=("stride",),
    limits=test_api.TEST_LIMITS,
    job_deadline_seconds=test_api.TEST_DEADLINE_SECONDS,
    max_active_jobs=test_api.TEST_MAX_ACTIVE_JOBS,
    budget=SEEDING_BUDGET,
    verifier=test_api.FakeVerifier(),
)
uvicorn.run(app, port=8470)
```

- Send `test_api.auth("alice-token")` as the header. `bob-token` and
  `carol-token` are the other owners in `test_api.TOKENS`.
- Set the env vars **before** you import `analysis_service.api`.
- `ANALYSIS_JOB_STORE` has no default. For the `sql` store on SQLite, merge
  `tests.sql_stores.sql_env(<new directory>)` into the environment in place of
  `memory`.
- uvicorn is a `web` dependency-group member, not a wheel dependency. The
  group is in `[tool.uv] default-groups`, so `uv sync` installs it.

`create_app()` with no runner uses the real ADK graph: the **Deployment**
builds an `analysis_service.pipeline.AdkPipelineRunner` for each framework
selection (`analysis_service.deployment.Deployment.runner`). A submitted job
then calls the vendor that each model tier names, and it fails without a tier
config and credentials. To run the real graph on canned model output, build a
pipeline with a `resolve_model` that returns a `BaseLlm` stand-in, as
`tests/test_pipeline.py` does.

## Flows worth driving

- `GET /healthz` unauthenticated → 200.
- `POST /v1/jobs` (Bearer token, `{"sources": [{"kind": "description", "label": ..., "text": ...}],
  "frameworks": [{"name": "stride"}]}`) → 201 + Location; with the stub runner
  the job completes in the background almost instantly.
- `GET /v1/jobs/{id}` → completed + per-node progress; never contains the report.
- `GET /v1/jobs/{id}/report` → full report JSON; `/events` → SSE stream that
  replays and closes on terminal jobs (`curl -N`, `Last-Event-ID` resumes).
- Ownership: a second subject's token must get 404 (not 403) on all reads.
- Errors: no/expired/tampered token → 401 problem+json with
  `WWW-Authenticate: Bearer`; sources over `max_source_bytes` or `max_sources`
  (`test_api.TEST_LIMITS` in the offline launch: 512 bytes, 3 sources) → 413; garbage JSON → 422.

## Gotchas

- Every error body must be `application/problem+json` — check `-i` output.
- Don't `pkill -f <script name>` from a command whose own command line
  contains that name; it kills your shell (exit 144).
