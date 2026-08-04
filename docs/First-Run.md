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

## 2. Choose a vendor and set its auth

**Nothing is selected by default.** [`config/model_tiers.toml`](../config/model_tiers.toml)
ships with both tiers empty, so this step is required rather than a default you
might override: a run that has not chosen stops at startup with an error naming
the three vendors and the two places a selection can be made.

That is deliberate. All three vendors are reached through one adapter and none
is privileged, so shipping one of them selected would make that claim true of
the mechanism while the values quietly said otherwise.

Pick a vendor, write the pair into the file, then set that vendor's
credentials — each vendor implies its own credential mode, so the two are never
configured separately.

```toml
# config/model_tiers.toml
[tiers.base]
vendor = "vertex"
model = "gemini-2.5-flash"

[tiers.strong]
vendor = "vertex"
model = "gemini-2.5-pro"
```

The two tiers select independently: `base` is the workhorse (extraction,
repair), `strong` is judgement (the six category agents, the critic, the re-ask). A
mixed pair — a cheap model from one vendor, judgement from another — is
ordinary rather than a special case.

Then the credentials for whichever vendor you named.

### Vertex

Vertex authenticates with Application Default Credentials, never an API key. For
a local first run, mint them against your own account:

```sh
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project your-gcp-project
```

Your account needs `roles/aiplatform.user` on that project. Then:

```sh
export STRIDE_VERTEX_PROJECT=your-gcp-project
export STRIDE_VERTEX_LOCATION=us-central1
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/gcloud/application_default_credentials.json"
```

That third export is required even though `gcloud` just wrote the file to its
own well-known path, and it is the step that surprises people: most Google
libraries discover ADC there by themselves, and this service deliberately does
not. It reads only *declared* credential material, because probing the
filesystem would make a build's outcome depend on the state of whichever laptop
it ran on. If `gcloud` printed a different path, export that one.

Do not create a service-account key for this. CI does not use one either — it
federates short-lived credentials from GitHub's OIDC token, a separate one-time
setup described in [WORKLOAD_IDENTITY](../.github/WORKLOAD_IDENTITY.md).

### Anthropic or OpenAI

Both authenticate with an API key, and only the vendor your tiers actually name
is read — a key for a vendor the config does not select never authenticates
anything.

For Anthropic, `vendor = "anthropic"` in both tier tables with models such as
`claude-sonnet-4-6` on `base` and `claude-opus-5` on `strong`:

```sh
export STRIDE_ANTHROPIC_API_KEY=sk-ant-...   # the full key, not a prefix
```

For OpenAI, `vendor = "openai"` with models such as `gpt-4.1-mini` on `base` and
`gpt-4.1` on `strong`:

```sh
export STRIDE_OPENAI_API_KEY=sk-...          # the full key, not a prefix
```

Model names must be pinned — no `-latest`, `-preview` or `-exp`. Claude takes
the dateless ID shown above, which is the canonical snapshot from generation
4.6 on; this service runs 4.6 and later only, so the older dated forms are
rejected. [Configuration](Configuration.md#models-and-vendors) gives the rule
per family.

### Selecting without editing the file

`STRIDE_MODEL_{BASE,STRONG}_VENDOR` and the matching `_MODEL` make the same
selection from the environment, which is how a deployed revision retunes without
an image rebuild and how CI states its own choice. They must move **together**:
setting `_VENDOR` alone is a startup error, since a mismatched pair passes every
other check and would die on the first node of a paid-for job.

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
and streams each node as it finishes — extraction, six parallel category agents,
then the grounding critic. When it completes you get the full report.

What to look at first:

- **Boundary crossings** — where data moves between trust zones. The
  highest-signal part of the model.
- **Confirmed vs needs-info threats.** The sample deliberately leaves a few facts
  unstated (is the S3 bucket encrypted? is the admin console authenticated?), and
  those come back as `needs-info` rather than as invented findings. That contrast
  is the behaviour to understand before you write your own sources.
- **The grounds under each threat** — the quote, unknown attribute or boundary
  crossing the finding was raised on. This is the fastest way to judge whether a
  finding is real, and the fastest way to see what your text left unsaid.
- **Provenance** — the served model build and sampling fingerprint for every LLM
  node, which is what makes a report reproducible.

Then replace the sample with your own system and analyze again.
[Integration-Guide](Integration-Guide.md) explains what makes a source
extract well; the short version is that anything you do not state becomes
`unknown`, so state the controls you actually have.

## 5. Embed the engine

The web app was the demonstration. This is the thing you ship:

<!-- docs-include: examples/embed.py#embed -->
```python
async def main(engine: StrideEngine) -> None:
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
