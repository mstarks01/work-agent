# Integration Guide

`Engine` is the in-process entry point: hand it the text describing a
system and it returns a [`Report`](Report-Schema.md). It owns none of the
[HTTP contract's](HTTP-API.md) ceremony — no auth token, no job store, no polling
— so it is the right surface for swapping this pipeline in behind an
application's own analysis interface.

## Building an engine

Build once and reuse. `from_config()` composes the pipeline's cacheable prompt
prefix at construction, so a fresh engine per call pays that cost every time.

```python
from analysis_service import Engine

engine = Engine.from_config(
    ["stride"]
)  # bundled prompts, bundled config, pinned models
```

**`frameworks` is required and has no default.** It names which security
frameworks this engine analyses with, in the order its reports' blocks will
carry, and a name this build does not carry raises rather than being dropped —
a job that silently analysed fewer frameworks than it asked for is the failure
that rule exists to prevent. Pass a `FrameworkSelection` instead of a bare name
where a framework takes job-level options:

```python
from analysis_service import FrameworkSelection

engine = Engine.from_config([FrameworkSelection(name="stride")])
```

If [config](Configuration.md) is missing or invalid, `from_config()` raises
rather than running nodes on some default model or sampling nobody chose. It
reads paths and overrides from the environment; pass `env=` to override that
(mainly for tests):

```python
engine = Engine.from_config(
    ["stride"],
    env={
        "ANALYSIS_MODEL_STRONG_VENDOR": "anthropic",
        "ANALYSIS_MODEL_STRONG_MODEL": "claude-opus-5",
    },
)
```

The pair above is arbitrary: `vertex`, `anthropic` and `openai` all go here,
and this service prefers none of them.

`_MODEL` on its own retunes a tier whose vendor the file already names. Nothing
is selected by default, so where the file names none — as it ships — both halves
have to be passed, for both tiers.

One engine holds no cross-call state, so it is safe to share across concurrent
tasks.

### When you need the configuration too

`from_config()` resolves a `Deployment` — this installation's config files,
located by the `ANALYSIS_*` variables in [Configuration](Configuration.md) and read
once — and builds the engine from it. Resolve it yourself when you need both:

```python
from analysis_service import Deployment, Engine

deployment = Deployment.from_env()  # reads config; no credentials touched
engine = Engine.from_deployment(deployment, deployment.frameworks)

deployment.frameworks  # what config/frameworks.toml carries

deployment.tiers.tiers["strong"].model  # what the strong tier selected
```

The two stages fail differently on purpose. `from_env()` raises for a missing or
invalid config file. `from_deployment()` raises for a credential or provider
problem — by which point you still have the `Deployment`, so you can report
*which* vendor the config selected. That is exactly what the first-run app's
diagnostic page does.

Reuse one `Deployment` rather than resolving a second: it builds its runner and
its certification gate once, so everything sharing it is provably on the same
configuration.

## Running an analysis

`analyze` is async and drives one submission to a terminal state:

```python
async def analyze(
    sources: Sequence[Source],
    *,
    system_name: str | None = None,
    caller: str = "in-process",
    on_node: NodeCallback | None = None,
) -> PipelineOutcome
```

- `sources` — an ordered, non-empty sequence of untrusted `Source` values, each
  `{kind, label, text}`. Build them with `Source.description(text)` or
  `Source.transcript(text)`, both of which default the label, or name one
  yourself with `label=`. Bounded by this deployment's config — 100 KiB total
  across all sources and 10 sources as shipped — and counted in UTF-8 bytes
  rather than tokens. Every source enters the pipeline as data inside its own
  fenced block, never as an instruction.

  Order is presentation only: **sources carry equal weight**, so listing a
  document before a transcript does not make it authoritative. Where two
  sources disagree, extraction records the disagreement rather than choosing —
  the attribute lands as `unknown` with both claims quoted in the element's
  `notes`, or, where the schema cannot hold `unknown`, as a value plus an entry
  in the model's `assumptions`.
- `system_name` — optional label echoed into the report. Blank falls through to
  a default; over 200 characters (`MAX_SYSTEM_NAME_CHARS`) is a caller error.
- `caller` — isolates the underlying session; it is not an authorization
  identity. Pass a per-tenant value if you run multiple callers.
- `on_node` — optional async callback invoked with each node name as it
  completes, for progress or tracing.

## Writing the sources

A source is free-form — prose, bullets, a table, a rough dump or a transcribed
call all work, and it will be incomplete. The first pipeline stage transcribes
it into a canonical system model; the quality of the report tracks how much of
the following the text actually states.

The service takes **text only**: decode a `.vtt`, `.docx` or meeting-tool export
before submitting. Give each source a `label` you will recognise, because it is
what every quote in the report cites.

Include, as far as you know them:

- **Components** — the actors/external entities, the running processes, and the
  data stores.
- **Data flows** — who calls whom, in which direction (a webhook or callback the
  other side initiates is its own flow).
- **Trust zones** — network segments, auth boundaries, privilege levels. If the
  text implies none, the whole system is treated as one zone.
- **Security-relevant attributes** — for each component and flow:
  `authentication`, `encryption_in_transit`, `encryption_at_rest`, `exposure`
  (is it internet-facing?), `interface_kind` (does the process present a web
  interface?), and `data_classification`.
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
is exactly the signal the category agents act on.

## The three outcomes

`analyze` returns a `PipelineOutcome`, which is the job lifecycle's
`completed | rejected` split (see [Report-Schema](Report-Schema.md) for the full result shape);
an internal failure raises instead. The engine never returns a partial report.

This is [`examples/embed.py`](../examples/embed.py) — a runnable file, included
here rather than retyped, so the shape below is provably the shape that works:

<!-- docs-include: examples/embed.py#embed -->
```python
async def main(engine: Engine) -> None:
    """Analyze one system, handling every outcome the run can have."""
    # A job takes an ordered list of sources. One written description is the
    # simplest case; add Source.transcript(...) for a recorded call, and give
    # each a label you will recognise when you read it back in the report.
    sources = [
        Source.description(SAMPLE.read_text(encoding="utf-8"), label="Orders note"),
    ]

    try:
        outcome = await engine.analyze(sources, system_name="Orders")
    except EngineInputError as exc:
        # Raised before any model runs: no sources, too many, more bytes than
        # the deployment allows, or an over-long system_name. Your caller's
        # mistake, not the service's — surface it as a validation error.
        print(f"invalid submission: {exc}", file=sys.stderr)
        raise
    except Exception:
        # An internal failure: a model error that exhausted its retries, or a
        # fail-closed check tripping. Nothing partial comes back — the engine
        # never returns a best-effort report. Log it, surface a generic error.
        print("analysis failed", file=sys.stderr)
        raise

    if isinstance(outcome, PipelineRejected):
        # The sources could not be turned into a valid system model. This is
        # actionable by whoever wrote them: each issue names what to fix.
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
submission that breaks the input contract — no sources, more sources than the
deployment allows, more bytes than it allows, or an over-long `system_name`.
Treat it as a caller/validation error, not a pipeline failure. A malformed
*individual* source raises earlier still, as a `ValidationError` when you
construct the `Source`.

There is no per-source byte cap, only a total, so the over-budget message names
no single culprit — it carries a per-label breakdown so you can decide what to
trim.

## Synchronous callers

If you are not in an event loop, `analyze_sync` wraps `analyze`. It refuses to
run inside an already-running loop (where `asyncio.run` would fail anyway) —
`await analyze(...)` there instead, as above.

From [`examples/embed_sync.py`](../examples/embed_sync.py):

<!-- docs-include: examples/embed_sync.py#embed_sync -->
```python
def analyze_orders(engine: Engine) -> None:
    """The synchronous call, with the same three outcomes as the async one."""
    sources = [
        Source.description(SAMPLE.read_text(encoding="utf-8"), label="Orders note"),
    ]

    try:
        outcome = engine.analyze_sync(sources, system_name="Orders")
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
    for block in outcome.report.analyses:
        print(f"{block.framework}: {block.summary.claim_count} claims")
```
<!-- /docs-include -->

The `PipelineRunner` protocol is a real seam — see
[Architecture](Architecture.md#seams) for it and the store seam.
