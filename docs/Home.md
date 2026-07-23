# STRIDE Threat-Modeling Service

An agentic STRIDE threat-modeling engine: semi-structured text describing a
system goes in, a structured JSON threat report comes out. The analysis runs as
a Google ADK multi-agent graph (an extraction pass, six parallel per-category
STRIDE analysts, and a grounding critic) over per-agent Vertex models.

There are two ways to call it:

- **In process** — embed [[Integration-Guide|`StrideEngine`]] directly and get a
  report back from a function call. This is the path for swapping the engine in
  behind an existing application's analysis interface.
- **Over HTTP** — the async [[HTTP-API|`/v1` job API]], with Ping-authenticated
  endpoints, for a decoupled front end.

Both surfaces drive the same pipeline and return the same
[[Report-Schema|`StrideReport`]].

## Pages

- [[Integration-Guide]] — embed the engine in process; the primary entry point.
- [[Report-Schema]] — the shape of the result, and the three outcomes.
- [[Configuration]] — config files and environment variables.
- [[HTTP-API]] — the `/v1` async job contract.
- [[Architecture]] — how the graph, models, and seams fit together.

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

`StrideEngine.from_config()` needs a configured Vertex environment to reach the
models (see [[Configuration]]); provisioning that environment is out of scope
for this repo.

## Status

The analysis code is complete and offline-tested. It has not yet been run
against live Vertex — the first real end-to-end run happens once the Vertex
environment is stood up. Persistent job/session backends are left as seams (see
[[Architecture]]); the in-memory defaults are enough to get a report in process.
