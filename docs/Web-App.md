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
`[web]` extra — deliberately. `webapp/main.py` is a standalone driver script
outside `src/stride_service`, the same as `examples/`, so no packaging
mechanism names it; that's independent of what the wheel bundles for the
*engine* itself (skills, prompts, config all ship with it — see
[Configuration](Configuration.md#config-paths-override-where-files-are-read-from)).
The app is clone-only because it's a demonstration script, not because the
engine it embeds would be missing anything if installed elsewhere.

Its server, `uvicorn`, is in a `web` dependency group that `uv sync` installs by
default. Dependency groups never enter the built distribution, so if you embed
the engine you inherit none of this — your dependency surface stays `fastapi`,
`google-adk`, `litellm`, `pydantic` and `pyjwt[crypto]`.

## What it shows you

**The models it is about to use**, one line per tier — whichever pair you
selected in step 2 of [First-Run](First-Run.md), since nothing is selected by
default:

```
base   → openai / gpt-4o
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

**Progress, per node.** A run takes around 40 seconds. The page streams **every**
graph node as it finishes — the model calls (`extract`, the six `analyze_*`
nodes, `critic`) and the deterministic ones between them (`validate`, `prepare`,
`merge`, `router`, `assemble`) alike — rather than showing you a blank tab. Node
names appear exactly as the graph emits them, which is why a category agent reads
`analyze_denial_of_service` rather than the `analyze/denial-of-service` that
[`config/model_tiers.toml`](../config/model_tiers.toml) keys on: graph node names
must be Python identifiers.

**The report** — threat cards, a severity summary, the extracted DFD, and the
served-build provenance for every LLM node. Each card carries the threat's
**grounds** under the analysis: the quotes, unknown attributes and boundary
crossings the agent raised it on, with a quote the service could not find in its
source marked as such rather than hidden.

## When the configuration is wrong

If the engine cannot be built — bad tier config, missing credentials, an
unsupported sampling parameter — the app still starts, but serves a **diagnostic
page instead of the form**. There is no textarea and no Analyze button, so no
analysis can run on a model nobody chose.

Where the config itself read cleanly — the credential and sampling cases — the
page names the vendor your config selects and lists **every** environment
variable that vendor needs, marking the ones that are unset. It reports presence
only and never prints a value. Vertex is shown because it needs the most
variables of the three vendors, which is what makes the next point visible:

```
STRIDE_VERTEX_PROJECT           NOT SET
STRIDE_VERTEX_LOCATION          NOT SET
GOOGLE_APPLICATION_CREDENTIALS  NOT SET
```

All of them at once, rather than one per restart — the underlying check raises on
the first variable it finds missing, which would otherwise mean three restarts to
discover three variables.

If the tier config is what failed, there is no selected vendor to report and the
page says so instead: fix the file named in the error first.

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

`webapp/report_view.html` is a self-contained renderer for the
[report schema](Report-Schema.md) — inline CSS and JS, no build step, no
dependencies. The app reads it, substitutes your run's JSON into its
`<script type="application/json" id="report">` block, and serves the result.

It holds no report of its own, so opening it from disk shows nothing. It is a
template belonging to this app, not a sample to look at.

## How the pages are protected

Two rules, held over all three pages rather than only the report page.

**Untrusted text reaches the DOM as text.** Every value the submitter can
influence renders as `textContent` or as a constructed node, never by assigning
a string of markup. That includes the form page, which is not obvious: a source
label and a validator message both travel back to it over SSE, and neither is
escaped for markup on the way. There is no escape helper on any page, which is
what makes forgetting one impossible rather than merely unlikely — the same
discipline had already failed once, silently, in the report's element table.

**Every page carries a strict nonce CSP,** `default-src 'none'` with a fresh
per-response nonce on each inline block and no `'unsafe-inline'` anywhere. Each
policy grants only what its own page does:

| Page | Grants beyond `default-src 'none'` |
| --- | --- |
| Report | `script-src`/`style-src` nonce. It loads nothing and calls nothing. |
| Form | the same, plus `connect-src 'self'` for `/example`, `/analyze`, `/events`. |
| Diagnostic | `style-src` nonce only — it runs no script, so it is granted none. |

`base-uri` and `form-action` are `'none'` everywhere; the form posts through
fetch, so a navigation away from it would be something going wrong. A page and
its policy are built together and served together, so serving one without the
other is not something the code can express.

Every response also carries `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer` — those are per response rather than per page,
which is why they are not part of the CSP.

None of this is what makes the app safe to run: **loopback binding is**. On
`127.0.0.1` the submitter is both attacker and victim. These are the controls
that keep that from being the only thing standing between the two.
