# The web app

A lite, local, single-purpose front end for the engine. It exists to answer
"show me what this does" on a first run — [First-Run](First-Run.md) step 3 — and
nothing else. It is not a product surface, and it is not how you deploy this.

```sh
uv run python webapp/main.py
```

Then <http://127.0.0.1:8000>.

It **embeds the engine in process**. It does not call the [`/v1` API](HTTP-API.md),
so it needs no bearer token and no OIDC provider. It runs real models against
whatever you type, and every analysis costs whatever your vendor charges.

## Running it from a clone only

The app lives in a top-level `webapp/` directory and **never ships in the wheel**.
There is no `pip install` that gets you this app, no console script, and no
`[web]` extra — deliberately. The engine resolves `skills/`, `prompts/` and
`config/*.toml` relative to the repo root, so a wheel-installed copy would need
six `STRIDE_*` environment variables set by hand before **Load example** could
work. A first-run surface whose headline gesture needs six variables is not a
first-run surface.

Its server, `uvicorn`, is in a `web` dependency group that `uv sync` installs by
default. Dependency groups never enter the built distribution, so if you embed
the engine you inherit none of this — your dependency surface stays `fastapi`,
`google-adk`, `litellm`, `pydantic` and `pyjwt[crypto]`.

## What it shows you

**The models it is about to use**, one line per tier:

```
base   → vertex / gemini-2.5-flash
strong → vertex / gemini-2.5-pro
```

Read-only. **No input to this app can influence which model runs** — not a form
field, not a query parameter, not a header. Model selection lives in
[`config/model_tiers.toml`](../config/model_tiers.toml) and the `STRIDE_MODEL_*`
overrides, and it stays there: the app has no authentication, so a model selector
would be unauthenticated control over what runs and what it costs. To change
models, edit the config and restart.

It does not show credential status — the page appearing at all proves the
credential check passed — and it does not show sampling parameters. The
**served** model build that actually answered each node is different information,
and the report itself carries it.

**Progress, per node.** A run takes around 40 seconds. The page streams each
graph node as it finishes (`extract`, the six `analyst/*` nodes, `critic`,
`assemble`) rather than showing you a blank tab. Node names appear exactly as the
graph emits them.

**The report**, rendered by [`example-report.html`](example-report.html) — the
same self-contained viewer the docs link as a sample, served unedited with your
run's JSON injected into it.

## When the configuration is wrong

If the engine cannot be built — bad tier config, missing credentials, an
unsupported sampling parameter — the app still starts, but serves a **diagnostic
page instead of the form**. There is no textarea and no Analyze button, so no
analysis can run on a model nobody chose.

The page names the vendor your config selects and lists **every** environment
variable that vendor needs, marking the ones that are unset. It reports presence
only and never prints a value. For Vertex that is:

```
STRIDE_VERTEX_PROJECT           NOT SET
STRIDE_VERTEX_LOCATION          NOT SET
GOOGLE_APPLICATION_CREDENTIALS  NOT SET
```

All of them at once, rather than one per restart — the underlying check raises on
the first variable it finds missing, which would otherwise mean three restarts to
discover three variables.

**Recovery is always: fix it, then restart.** There is no retry button. A process
cannot pick up an environment variable that changed after it started, so a retry
would appear to work after a config-file edit and silently do nothing in the
credential case, which is the common one at this point.

## Its limits, which are the point

- **Loopback only.** `127.0.0.1`, hard-bound, with no host flag and no override.
  The no-auth posture is only safe there: anything reachable is an
  unauthenticated proxy to your vendor bill. Remote authenticated access is what
  [`/v1`](HTTP-API.md) is for.
- **One run at a time.** A second submission while one is running is refused with
  a message rather than queued.
- **Nothing is persisted.** Runs are held in memory, capped, oldest evicted
  first, and lost on restart. This is a demo surface, not a job store — `/v1`
  already is one.
- **No history, no export, no accounts.** If you want the JSON, take it from the
  engine directly; [`examples/embed.py`](../examples/embed.py) is four lines from
  `report.model_dump_json()`.

## How the report page is rendered

[`docs/example-report.html`](example-report.html) is a complete, dependency-free
renderer for the [report schema](Report-Schema.md). The app reads it at request
time, replaces the contents of its `<script type="application/json" id="report">`
block with your run's JSON, and serves the result. **The file itself is never
edited and never imported** — it stays a documentation artifact that happens to
be a working viewer, and `Report-Schema.md` keeps linking it as one.

That block holds no checked-in report; it exists only as the substitution
anchor. So opening the file straight from disk renders nothing, which is why
neither this page nor [First-Run](First-Run.md) points you at it as something to
look at on its own — run the app instead.

A stored sample was considered and dropped. To be honest about the provenance
panel it renders, it would have to be a real analysis, regenerated against live
models every time the schema or the tier names moved. That is recurring upkeep,
requiring credentials, for a file whose only reader is this app.
