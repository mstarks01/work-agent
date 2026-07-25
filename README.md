# stride-service

An agentic **STRIDE threat-modeling engine**: semi-structured text describing a
system goes in, a structured JSON threat report comes out. The analysis runs as
a [Google ADK](https://google.github.io/adk-docs/) multi-agent graph over
per-tier Vertex Gemini models.

```
description text ──▶ extract ──▶ [ 6 STRIDE analysts in parallel ] ──▶ critic ──▶ StrideReport
                     (flash)              (pro)                        (pro)        (JSON)
```

An extraction pass builds the canonical **System Model** (a DFD); six per-category
analysts draft threats against it in parallel; a grounding critic reviews the
merged union in one pass — confirming, deduping, calibrating severity, and
rejecting the ungrounded. See [`CONTEXT.md`](CONTEXT.md) for the domain glossary.

## Two ways to call it

- **In process** — embed `StrideEngine` and get a report back from a function
  call. The path for swapping the engine in behind an existing analysis
  interface.
- **Over HTTP** — the async, Ping-authenticated [`/v1` job API](docs/HTTP-API.md)
  for a decoupled front end.

Both surfaces drive the same pipeline and return the same
[`StrideReport`](docs/Report-Schema.md).

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

Reaching the models needs a configured Vertex environment (ADC + project/location);
provisioning that is deliberately **out of scope for this repo**. Offline tests
and the in-memory stub runner need none of it.

## Repository layout

| Path | What lives here |
|---|---|
| `src/stride_service/` | The shipped engine — the only thing in the wheel. Graph, agents, config loaders, report schema, HTTP API. |
| `config/` | Versioned, fail-closed config: `model_tiers.toml`, `sampling.toml`, `resilience.toml`. |
| `prompts/` | Agent prompts and per-category exemplars. |
| `skills/` | The per-category STRIDE skill Markdown baked into the image. |
| `docs/` | User-facing documentation (see below). |
| `evals/` | Golden-case corpus, scorer, and the eval harness. **Never ships** in the image. |
| `tests/` | Offline test suite (no credentials required). |

## Documentation

- **[docs/Home.md](docs/Home.md)** — start here; the docs index and overview.
- [Integration-Guide](docs/Integration-Guide.md) — embed the engine in process.
- [Report-Schema](docs/Report-Schema.md) — the result shape, provenance, and the three outcomes.
- [Configuration](docs/Configuration.md) — config files, environment variables, per-tier sampling, and the eval gate.
- [HTTP-API](docs/HTTP-API.md) — the `/v1` async job contract.
- [Architecture](docs/Architecture.md) — how the graph, models, and seams fit together.

Eval-side docs: [evals/README.md](evals/README.md) (the harness and its metrics),
[evals/BLESSING.md](evals/BLESSING.md) (authoring a golden case), and
[evals/TUNING.md](evals/TUNING.md) (iteratively testing and improving model
performance).

## Development

The project uses [uv](https://docs.astral.sh/uv/). Python ≥ 3.11.

```sh
uv sync                       # install deps into .venv
uv run pytest                 # the offline suite — no credentials needed
uv run ruff check .           # lint
python evals/verify_corpus.py # mechanical checks over the golden corpus
```

Everything under `tests/` and `evals/verify_corpus.py` is credential-free and
deterministic. The live eval commands (`python -m evals.harness.run ...`) need
Vertex access and are out of scope here — see [evals/TUNING.md](evals/TUNING.md).

## Status

The analysis code is complete and offline-tested; it has not yet been run
against live Vertex. The shipped decoding default is `temperature = 0`; tuning
the per-tier sampling values is a future eval sweep (see
[evals/TUNING.md](evals/TUNING.md)). Persistent job/session backends are left as
seams — the in-memory defaults are enough to get a report in process.
