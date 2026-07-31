# stride-service

An agentic **STRIDE threat-modeling engine**: semi-structured text describing a
system goes in, a structured JSON threat report comes out. The analysis runs as
a [Google ADK](https://google.github.io/adk-docs/) multi-agent graph over
per-tier models from any supported vendor — Vertex, Anthropic or OpenAI, with no
privileged default.

```
description text ──▶ extract ──▶ [ 6 STRIDE analysts in parallel ] ──▶ critic ──▶ StrideReport
                     (base)              (strong)                     (strong)      (JSON)
```

An extraction pass builds the canonical **System Model** (a DFD); six per-category
analysts draft threats against it in parallel; a grounding critic reviews the
merged union in one pass — confirming, deduping, calibrating severity, and
rejecting the ungrounded. See [`CONTEXT.md`](CONTEXT.md) for the domain glossary.

## Two ways to call it

- **In process** — embed `StrideEngine` and get a report back from a function
  call. The path for swapping the engine in behind an existing analysis
  interface.
- **Over HTTP** — the async [`/v1` job API](docs/HTTP-API.md), authenticated with
  a bearer token from any OIDC identity provider, for a decoupled front end.

Both surfaces drive the same pipeline and return the same
[`StrideReport`](docs/Report-Schema.md).

**New here? [docs/First-Run.md](docs/First-Run.md)** takes you from a clone to a
real report to the engine embedded in your own code, in five steps.

**No vendor is selected out of the box.** `config/model_tiers.toml` ships with
both tiers empty, so choosing one is a required first step rather than an
override — "no privileged default" is a property of what ships, not just of the
mechanism. Reaching the models then needs credentials for whichever vendor each
tier names: Google Cloud application default credentials plus a project and
location for Vertex, or an API key for Anthropic or OpenAI. If either the
selection or its credentials are missing, startup stops with an error naming
what to set rather than running on some fallback nobody chose; see
[docs/Configuration.md](docs/Configuration.md). Offline tests and the in-memory
stub runner need none of it.

## Repository layout

| Path | What lives here |
|---|---|
| `src/stride_service/` | The shipped engine — the only thing in the wheel. Graph, agents, config loaders, report schema, HTTP API. |
| `config/` | Versioned config that stops startup rather than falling back: `model_tiers.toml`, `sampling.toml`, `resilience.toml`, `blessed-fingerprints.toml`. |
| `prompts/` | Agent prompts and per-category exemplars. |
| `skills/` | The per-category STRIDE skill Markdown baked into the image. |
| `docs/` | User-facing documentation (see below). |
| `examples/` | Runnable embedding examples and the shared sample description. The source of truth for every code block in the docs. |
| `webapp/` | The lite first-run web app. **Never ships** in the wheel; run from a clone. |
| `evals/` | Golden-case corpus, scorer, and the [eval harness](evals/README.md). **Never ships** in the image. |
| `tests/` | Offline test suite (no credentials required). |

The wheel carries the engine, but a *run* also needs `config/`, `prompts/` and
`skills/` repo-adjacent or pointed at by the `STRIDE_*_DIR` variables.

## Documentation

- **[First-Run](docs/First-Run.md)** — start here; clone to embedded engine in five steps.
- [Integration-Guide](docs/Integration-Guide.md) — embed the engine in process.
- [Web-App](docs/Web-App.md) — the lite local front end used on the first run.
- [Report-Schema](docs/Report-Schema.md) — the result shape, provenance, and the three outcomes.
- [Configuration](docs/Configuration.md) — config files, environment variables, and per-tier decoding.
- [HTTP-API](docs/HTTP-API.md) — the `/v1` async job contract, and its bearer auth.
- [Architecture](docs/Architecture.md) — the graph, the seams, and how a run is certified.


## Development

The project uses [uv](https://docs.astral.sh/uv/). Python ≥ 3.11.

```sh
uv sync                                    # install deps into .venv
uv run pytest                              # the offline suite — no credentials needed
uv run ruff check .                        # lint
uv run python evals/verify_corpus.py       # mechanical checks over the golden corpus
uv run python examples/sync_docs.py --check # the docs' code blocks match examples/
```

Everything under `tests/` and `evals/verify_corpus.py` is credential-free and
deterministic. The live eval commands (`python -m evals.harness.run ...`) need
configured provider credentials — see [evals/TUNING.md](evals/TUNING.md).

## Status

The analysis code is complete and covered by an offline test suite. Three things
are worth stating separately, because they are different kinds of claim:

- **The blessed-fingerprint list ships empty, permanently and by design.**
  `config/blessed-fingerprints.toml` is *deployment-local*: certification attests
  that a report came from the exact model builds and sampling parameters your
  deployment blessed, so the project can never ship a certified pair on your
  behalf. An empty list is the correct shipped state, not missing work.
- **Per-tier sampling is an open tuning loop.** The shipped decoding default is
  `temperature = 0`. Improving the per-tier values is a measured process against
  the golden corpus — see [evals/TUNING.md](evals/TUNING.md).
- **No sanctioned baseline sweep has been run yet.** The live eval lane exists to
  run it (weekly, Mondays 06:00 UTC). This is a not-yet rather than a cannot.

Persistent job and session backends are left as seams — the in-memory defaults
are enough to get a report in process.
