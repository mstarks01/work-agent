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

If [config](Configuration.md) is missing or invalid, `from_config()` raises
rather than running nodes on some default model or sampling nobody chose. It
reads paths and overrides from the environment; pass `env=` to override that
(mainly for tests):

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

A short but well-formed description — this is
[`examples/orders.md`](../examples/orders.md), the same file the web app's
**Load example** button loads:

<!-- docs-include: examples/orders.md -->
```text
Customers place orders through a React single-page web app, served to the public
internet over HTTPS with TLS 1.3. The web app calls an internal Orders API inside
our AWS VPC. Every call to that API carries an OAuth2 bearer token issued by our
identity provider, and the API rejects unauthenticated requests.

The Orders API reads and writes a Postgres database in a private subnet, holding
customer names, delivery addresses, phone numbers, and order history. The
database is encrypted at rest and reachable only from inside the VPC. Payment
authorisation is delegated to Stripe over TLS; card numbers never reach our
systems, and the Orders API stores only the charge identifier Stripe returns.

A nightly batch job running in the same VPC exports order summaries to an S3
bucket, which the analytics team queries the following morning.

Support administrators reach an admin console on the Orders API from a separate
corporate management network. Through it they refund orders, edit customer
delivery addresses, and look up order history on a customer's behalf.
```
<!-- /docs-include -->

That names the actors, the flows, three trust zones (public edge, VPC, the
management network), the transport and auth on the exposed path, and the
sensitive data — enough for a grounded model. What it leaves unsaid (is the S3
bucket encrypted? is the admin console authenticated?) becomes `unknown`, which
is exactly the signal the analysts act on.

## The three outcomes

`analyze` returns a `PipelineOutcome`, which is the job lifecycle's
`completed | rejected` split (see [Report-Schema](Report-Schema.md) for the full result shape);
an internal failure raises instead. The engine never returns a partial report.

This is [`examples/embed.py`](../examples/embed.py) — a runnable file, included
here rather than retyped, so the shape below is provably the shape that works:

<!-- docs-include: examples/embed.py#embed -->
```python
async def main(engine: StrideEngine) -> None:
    """Analyze one system description, handling every outcome it can have."""
    description = SAMPLE.read_text(encoding="utf-8")

    try:
        outcome = await engine.analyze(description, system_name="Orders")
    except EngineInputError as exc:
        # Raised before any model runs: empty description, oversized
        # description, over-long system_name. Your caller's mistake, not the
        # service's — surface it as a validation error.
        print(f"invalid submission: {exc}", file=sys.stderr)
        raise
    except Exception:
        # An internal failure: a model error that exhausted its retries, or a
        # fail-closed check tripping. Nothing partial comes back — the engine
        # never returns a best-effort report. Log it, surface a generic error.
        print("analysis failed", file=sys.stderr)
        raise

    if isinstance(outcome, PipelineRejected):
        # The description could not be turned into a valid system model. This
        # is actionable by whoever wrote it: each issue names what to fix.
        for issue in outcome.issues:
            print(f"rejected [{issue.code}] {issue.message}", file=sys.stderr)
        return

    assert isinstance(outcome, PipelineCompleted)
    summarise(outcome.report)
```
<!-- /docs-include -->

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

If you are not in an event loop, `analyze_sync` wraps `analyze`. It refuses to
run inside an already-running loop (where `asyncio.run` would fail anyway) —
`await analyze(...)` there instead, as above.

From [`examples/embed_sync.py`](../examples/embed_sync.py):

<!-- docs-include: examples/embed_sync.py#embed_sync -->
```python
def analyze_orders(engine: StrideEngine) -> None:
    """The synchronous call, with the same three outcomes as the async one."""
    description = SAMPLE.read_text(encoding="utf-8")

    try:
        outcome = engine.analyze_sync(description, system_name="Orders")
    except RuntimeError as exc:
        # analyze_sync was called from inside a running event loop. Await
        # engine.analyze(...) there instead — see examples/embed.py.
        print(f"wrong call for this context: {exc}", file=sys.stderr)
        raise
    except EngineInputError as exc:
        print(f"invalid submission: {exc}", file=sys.stderr)
        raise

    if isinstance(outcome, PipelineRejected):
        for issue in outcome.issues:
            print(f"rejected [{issue.code}] {issue.message}", file=sys.stderr)
        return

    assert isinstance(outcome, PipelineCompleted)
    print(f"{outcome.report.summary.threat_count} threats")
```
<!-- /docs-include -->

The `PipelineRunner` protocol is a real seam — see
[Architecture](Architecture.md#seams) for it and the store seam.
