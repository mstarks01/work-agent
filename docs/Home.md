# STRIDE Threat-Modeling Service

An agentic STRIDE threat-modeling engine: you hand it semi-structured text
describing a system, and it returns a structured JSON threat report. The
analysis runs as a Google ADK multi-agent graph — an extraction pass, six
parallel per-category STRIDE analysts, and a grounding critic — over models from
whichever vendor you configure.

There are two ways to call it:

- **In process** — embed [`StrideEngine`](Integration-Guide.md) directly and get a
  report back from a function call. This is the path for swapping the engine in
  behind an existing application's analysis interface.
- **Over HTTP** — the async [`/v1` job API](HTTP-API.md), with bearer-token
  authentication, for a decoupled front end.

Both surfaces drive the same pipeline and return the same
[`StrideReport`](Report-Schema.md).

## Pages

- [Integration-Guide](Integration-Guide.md) — embed the engine in process; the primary entry point.
- [Report-Schema](Report-Schema.md) — the shape of the result, and the three outcomes.
- [Configuration](Configuration.md) — config files, environment variables, models and credentials.
- [HTTP-API](HTTP-API.md) — the `/v1` async job contract.
- [Architecture](Architecture.md) — how the graph, models, and seams fit together.

Eval-side docs live under [`evals/`](../evals/README.md): how analysis quality is
measured, how to author a golden case, and how to tune the models.

## Quick start

```python
from stride_service import StrideEngine, PipelineCompleted

engine = StrideEngine.from_config()           # build once, reuse
outcome = await engine.analyze(
    "Customers sign in to a web app that reads and writes an orders database.",
    system_name="Orders",
)

if isinstance(outcome, PipelineCompleted):
    report = outcome.report                   # a StrideReport
    print(report.summary.threat_count)
```

`StrideEngine.from_config()` needs credentials for whichever vendor each model
tier selects — Google Cloud application default credentials for Vertex, an API
key for Anthropic or OpenAI. Startup stops with an error if they are missing,
rather than running on a fallback nobody chose. See
[Configuration](Configuration.md). Provisioning that environment is out of scope
for this repo.

## Status

The analysis code is complete and covered by an offline test suite. It has not
yet been run against a live model — the first real end-to-end run happens once a
vendor environment is stood up, and until then no run has produced a certified
baseline. Persistent job and session backends are left as seams (see
[Architecture](Architecture.md)); the in-memory defaults are enough to get a
report in process.
