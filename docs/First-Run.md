# First run

Start here. Five steps from a clone to the engine embedded in your own
application, with a real report in the middle.

There is no credential-free stage. The first thing you run is a real analysis on
real models, because a threat report produced by a stub would tell you nothing
about whether this is worth embedding. Model auth is step 2 and it is the only
prerequisite.

## 1. Clone and install

```sh
git clone https://github.com/mstarks01/work-agent.git
cd work-agent
uv sync
```

[uv](https://docs.astral.sh/uv/) handles the virtualenv. Python ≥ 3.11.

Everything below runs from this clone. The wheel carries the engine, but a run
also needs `config/`, `prompts/` and `skills/` alongside it, so working from a
checkout is the supported path — see [Configuration](Configuration.md) for the
`STRIDE_*_DIR` overrides if you need to relocate them later.

## 2. Set model auth for one vendor

Each tier picks a vendor in [`config/model_tiers.toml`](../config/model_tiers.toml).
The shipped config selects **Vertex** for both tiers, so configure Vertex unless
you change it:

```sh
export STRIDE_VERTEX_PROJECT=your-gcp-project
export STRIDE_VERTEX_LOCATION=us-central1
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

For Anthropic or OpenAI instead, point the tiers at that vendor and set its key:

```sh
export STRIDE_ANTHROPIC_API_KEY=sk-ant-...
export STRIDE_OPENAI_API_KEY=sk-...
```

Nothing falls back. A missing variable stops startup with an error naming the
variable rather than quietly running on some default model — and the web app in
step 3 turns that error into a page listing every variable your vendor needs.
The full tables are in [Configuration](Configuration.md).

## 3. Start the web app

```sh
uv run python webapp/main.py
```

Then open <http://127.0.0.1:8000>. It embeds the engine in process and runs real
models; it is bound to loopback and has no authentication, which is only safe
because it is bound to loopback. See [Web-App](Web-App.md) for what it does and
what it deliberately does not do.

If you see a configuration page instead of a form, step 2 is not finished — the
page names exactly which variables are still unset.

## 4. Load the example and analyze

Click **Load example**, then **Analyze**. No typing.

That loads [`examples/orders.md`](../examples/orders.md), a small e-commerce
system described the way the extractor expects. The run takes roughly 40 seconds
and streams each node as it finishes — extraction, six parallel STRIDE analysts,
then the grounding critic. When it completes you get the full report.

What to look at first:

- **Boundary crossings** — where data moves between trust zones. The
  highest-signal part of the model.
- **Confirmed vs needs-info threats.** The sample deliberately leaves a few facts
  unstated (is the S3 bucket encrypted? is the admin console authenticated?), and
  those come back as `needs-info` rather than as invented findings. That contrast
  is the behaviour to understand before you write your own description.
- **Provenance** — the served model build and sampling fingerprint for every LLM
  node, which is what makes a report reproducible.

Then replace the sample with your own system and analyze again.
[Integration-Guide](Integration-Guide.md) explains what makes a description
extract well; the short version is that anything you do not state becomes
`unknown`, so state the controls you actually have.

## 5. Embed the engine

The web app was the demonstration. This is the thing you ship:

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

That is [`examples/embed.py`](../examples/embed.py), included here from the file
itself — run it with `uv run python examples/embed.py`. Build the engine once
with `StrideEngine.from_config()` and reuse it; construction composes a cacheable
shared prefix that a fresh engine per call would pay for every time.

Handle all three outcomes. `analyze` returns a report, returns a rejection
carrying validation issues, or raises — and a caller that checks only for the
report and falls through silently on a rejection is the bug this example exists
to prevent. If you cannot make the call site `async`, see
[`examples/embed_sync.py`](../examples/embed_sync.py).

## Where to go next

- [Integration-Guide](Integration-Guide.md) — writing good descriptions, the
  three outcomes in depth, input limits.
- [Report-Schema](Report-Schema.md) — every field of the report you just got
  back.
- [Configuration](Configuration.md) — changing models, vendors, and sampling.
- [Architecture](Architecture.md) — the graph, and the seams you can replace.
