# Integration Guide

`StrideEngine` is the in-process entry point: hand it the text describing a
system and it returns a [`StrideReport`](Report-Schema.md). It owns none of the
[HTTP contract's](HTTP-API.md) ceremony — no auth token, no job store, no polling
— so it is the right surface for swapping this pipeline in behind an
application's own analysis interface.

## Building an engine

Build once and reuse. `from_config()` composes the pipeline's cacheable prompt
prefix at construction, so a fresh engine per call pays that cost every time.

```python
from stride_service import StrideEngine

engine = StrideEngine.from_config()   # repo prompts, repo config, pinned models
```

`from_config()` fails closed on missing or invalid [config](Configuration.md)
rather than running nodes on a default model or sampling. It reads paths and
overrides from the environment; pass `env=` to override (mainly for tests):

```python
engine = StrideEngine.from_config(env={"STRIDE_MODEL_STRONG_MODEL": "gemini-2.5-pro"})
```

One engine holds no cross-call state, so it is safe to share across concurrent
tasks.

## Running an analysis

`analyze` is async and drives one submission to a terminal state:

```python
async def analyze(
    description: str,
    *,
    system_name: str | None = None,
    caller: str = "in-process",
    on_node: NodeCallback | None = None,
) -> PipelineOutcome
```

- `description` — the untrusted system description. Bounded to 100 KiB of UTF-8
  (`MAX_DESCRIPTION_BYTES`); it enters the pipeline as data, never as an
  instruction.
- `system_name` — optional label echoed into the report. Blank falls through to
  a default; over 200 characters (`MAX_SYSTEM_NAME_CHARS`) is a caller error.
- `caller` — isolates the underlying session; it is not an authorization
  identity. Pass a per-tenant value if you run multiple callers.
- `on_node` — optional async callback invoked with each node name as it
  completes, for progress or tracing.

## Writing the description

The `description` is free-form — prose, bullets, a table, or a rough dump all
work, and it will be incomplete. The first pipeline stage transcribes it into a
canonical system model; the quality of the report tracks how much of the
following the text actually states.

Include, as far as you know them:

- **Components** — the actors/external entities, the running processes, and the
  data stores.
- **Data flows** — who calls whom, in which direction (a webhook or callback the
  other side initiates is its own flow).
- **Trust zones** — network segments, auth boundaries, privilege levels. If the
  text implies none, the whole system is treated as one zone.
- **Security-relevant attributes** — for each component and flow:
  `authentication`, `encryption_in_transit`, `encryption_at_rest`, `exposure`
  (is it internet-facing?), and `data_classification`.
- **Sensitive assets** in play — credentials, PII, financial, health, secrets,
  business-critical or availability-critical data.

Two behaviours worth knowing:

- **Anything you do not state becomes `unknown`.** The extractor transcribes; it
  does not invent facts. An `unknown` control is treated as unverified and tends
  to surface as a `needs-info` [threat](Report-Schema.md) rather than a confirmed
  one. Stating "the API requires OAuth" and omitting it produce materially
  different reports — so state the controls you have.
- **Keep the described system under 150 elements** (`MAX_ELEMENTS`; see
  [Configuration](Configuration.md)). A larger system comes back as a
  `too-many-elements` rejection — split it and submit the parts separately.
  Analysis quality is best in the 8–20 element range.

A short but well-formed description:

```text
Customers use a React web app over HTTPS to place orders. The web app calls an
internal Orders API (OAuth2 bearer tokens) inside our VPC, which reads and
writes an encrypted Postgres database holding customer PII and payment details.
A nightly job in the same VPC exports order summaries to an S3 bucket. Admins
reach the Orders API from a separate management network.
```

That names the actors, the flows, two trust zones (public edge, VPC; plus the
management network), the transport and auth on the exposed path, and the
sensitive data — enough for a grounded model. What it leaves unsaid (is the S3
bucket encrypted? is the admin path authenticated?) becomes `unknown`, which is
exactly the signal the analysts act on.

## The three outcomes

`analyze` returns a `PipelineOutcome`, which is the job lifecycle's
`completed | rejected` split (see [Report-Schema](Report-Schema.md) for the full result shape);
an internal failure raises instead. The engine never returns a partial report.

```python
from stride_service import PipelineCompleted, PipelineRejected

try:
    outcome = await engine.analyze(text, system_name="Orders")
except Exception:
    # Internal failure (model error exhausted retries, a fail-closed check
    # tripped). Nothing partial is returned. Log and surface a generic error.
    raise

if isinstance(outcome, PipelineCompleted):
    report = outcome.report                 # StrideReport
else:
    assert isinstance(outcome, PipelineRejected)
    for issue in outcome.issues:            # list[ValidationIssue]
        print(issue.code, issue.message)
```

| Outcome | Meaning | You get |
| --- | --- | --- |
| `PipelineCompleted` | The system was modelled and analysed | `.report` |
| `PipelineRejected` | The input could not be turned into a valid model | `.issues` |
| exception raised | Something failed internally | nothing — fail closed |

A rejection is about the *input* (for example a system too large to analyse —
see the `too-many-elements` code in [Configuration](Configuration.md)) and is actionable by the
caller. An exception is the service's fault and carries no issues.

`EngineInputError` (a `ValueError`) is raised *before* any model runs, for a
submission that breaks the input contract — empty description, oversized
description, over-long `system_name`. Treat it as a caller/validation error, not
a pipeline failure.

## Synchronous callers

If you are not in an event loop, `analyze_sync` wraps `analyze`:

```python
outcome = engine.analyze_sync(text, system_name="Orders")
```

It refuses to run inside an already-running loop (where `asyncio.run` would fail
anyway) — `await analyze(...)` there instead.

## Injecting your own runner

`from_config()` is the production wiring. For tests or a custom backend,
construct the engine with any object satisfying the `PipelineRunner` protocol:

```python
from stride_service import StrideEngine, StubPipelineRunner

engine = StrideEngine(StubPipelineRunner())   # no models; returns an empty report
```

See [Architecture](Architecture.md) for the runner and store seams.
