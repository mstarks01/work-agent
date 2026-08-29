# examples/

Runnable code for embedding `Engine` in your own application, plus the
sample system description the web app and the docs both use.

Everything here runs from a clone with provider credentials configured — see
[docs/First-Run.md](../docs/First-Run.md) steps 1 and 2. Nothing in this
directory ships in the wheel.

| File | What it is |
| --- | --- |
| [`orders.md`](orders.md) | The sample system description. The web app's **Load example** button loads this exact file, and the description block in [Integration-Guide](../docs/Integration-Guide.md) is generated from it. |
| [`embed.py`](embed.py) | Route step 5: embedding the engine, handling all three outcomes. The shape to copy. |
| [`embed_sync.py`](embed_sync.py) | The same thing from synchronous code, via `analyze_sync`. |
| [`sync_docs.py`](sync_docs.py) | Tooling, not an example — regenerates the docs' code blocks from the files above. |

Run either example against the sample:

```bash
uv run python examples/embed.py
uv run python examples/embed_sync.py
```

Both call live models and cost real money — roughly one analysis each. If you
have not set up credentials yet, [First-Run](../docs/First-Run.md) step 2 is the
place to start; a missing variable fails closed before any model runs.

## Why `embed.py` handles all three outcomes

`analyze` has three: it returns a report, it returns a rejection carrying
validation issues, or it raises. A caller that tests only for the report and
falls through silently on a rejection is a real bug, and it is the one this
directory exists to stop being copied out of the docs. Both examples branch on
all three, and the offline test suite walks every branch.

## How these stay honest

Examples rot faster than prose, because nothing reads them. Three checks stop
that here, and they answer different questions:

- **The docs show this code, byte for byte.** Every code block in the prose is
  generated from a named region of a file in this directory by `sync_docs.py`,
  and CI re-runs it in `--check` mode on every pull request. A block edited in
  the Markdown instead of here fails the build. This is why the docs carry no
  hand-written engine code at all.
- **The examples' own logic runs, on every pull request.** Each file exposes
  `async def main(engine)` rather than building its own engine, so the offline
  suite calls it with a stub runner and drives all three outcomes — no
  credentials, no models, no cost. That is what catches an example that imports
  cleanly but mishandles a rejection.
- **The models answer only when someone asks.** Neither check above runs a
  model, so neither can tell you the sample still extracts well or that the
  category agents still return threats for it. A live run of `embed.py` over
  `orders.md` does, and it is a step in
  [`evals-live.yml`](../.github/workflows/evals-live.yml) — which now runs on
  pull requests to the agentic surface and on manual dispatch, and on nothing
  else.

  **Nothing here is guaranteed on a timer any more.** That workflow's
  pull-request trigger is path-filtered to the agentic surface — prompts,
  skills, config, and the graph — and `examples/**` is *not* in the list. So a
  pull request that changes only `orders.md` fires no live run. Until the Monday
  06:00 UTC sweep was removed, that gap closed within a week; it no longer
  closes on its own, and a sample that has quietly become hard to extract will
  sit undetected until someone dispatches a run by hand.

  The narrow gap was accepted deliberately — widening the filter would spend
  live model budget on every documentation edit, and the offline lane already
  covers everything decidable without a model. The *open-ended* gap is not a
  decision about examples at all: the schedule was removed because Workload
  Identity Federation is unconfigured, so the weekly run failed every week
  rather than sweeping anything. Restoring federation and the `schedule:` block
  restores this guarantee.
