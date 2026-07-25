# Iteratively testing and improving model performance

How to turn "the model feels off" into a measured, reviewable change. The
harness (`README.md`) is the instrument; this is the loop you drive it in. The
blessing workflow (`BLESSING.md`) is upstream of this one — it produces the
corpus this loop measures against.

The whole method is one rule: **change one thing, measure it against the noise
band, and promote only what beats the band.** Everything below is that rule
applied to the levers you actually have.

## The live boundary — read this first

A scoring sweep calls Vertex, and **provisioning Vertex is out of scope for this
repo** (wayfinder tickets 027 / 032 / 033). What ships here is the *instrument
and the offline machinery*: the corpus, the scorer, the judge seam, the
metrics, the eval gate, and the single-sourced promotion path. The steps that
spend judge/analyst calls (`run --mode analysis`, `calibrate`, a sampling sweep)
need ADC for Vertex (IAM, never API keys — decision 17) and are a **future eval
effort**, not something you can run in this tree today.

So this document describes the loop end-to-end, but flags each step as either
**offline** (runnable now, credential-free) or **live** (needs Vertex). Treat
the live steps as the design of record for when credentials exist; the shipped
default is `temperature = 0`, unchanged, until a sweep says otherwise.

## What the instrument measures

Every `run` writes one JSON artifact holding every judge ruling, every bucket
decision, and the aggregate metrics. Before you change anything, know what each
number is — the full list and its caveats live in `README.md` ("Metrics, and
what they are not"). The load-bearing ones for a tuning decision:

- **must-find recall** — per case, never aggregated. An aggregate hides one case
  failing completely, which is the regression that matters.
- **near/far exemplar delta** — the gap between near- and far-domain recall.
  All 18 analyst exemplars anchor on one payments domain, so anchoring shows up
  only as this delta, not as an absolute score. Case `01` is the near control
  and is not optional.
- **critic yield** — always read as a *pair*: `killed-ungrounded` (the critic
  earning its cost) against `killed-real` / `must_find_killed` (the critic
  destroying real findings). A kill count alone says nothing.
- **ungrounded rate** — the one gating bucket among unmatched threats.

Every metric is **judge-relative**: valid for tracking movement and comparing
configurations, never quotable as an absolute or against another tool's
published figures.

## The loop

### 1. Establish a baseline — and its variance (live)

You cannot tell a real improvement from noise without knowing how much a metric
moves when *nothing* changes. Run the full sweep **~5×** on the fixed
configuration and record both the mean and the **spread** of each metric (ticket
032):

```sh
for i in $(seq 1 5); do
  python -m evals.harness.run run --mode analysis --out baseline-$i.json
done
```

The spread is the product, not a byproduct. A change that moves a metric by less
than the run-to-run spread has moved nothing. If the spread is wider than any
band you would want to gate on, that is itself the finding — widen the band, do
not tighten it into something that flakes.

Check the judge first: `run` scores are only meaningful once the judge clears
the **≥90% agreement bar** (`calibrate`). A failing bar means the judge prompt
needs work, not a lowered bar — fix it before you trust any recall number.

### 2. Change exactly one lever

Pick one of the levers below and change only it. Two changes at once and you
cannot attribute the movement.

### 3. Re-run and compare against the band

Re-run the sweep with the change and compare each metric to the **baseline
band**, not to a single baseline run. Read the numbers the way the harness
prints them:

- must-find recall **per case** — did any single case regress?
- the near/far delta **per arm** — a change can lift aggregate recall while
  widening the anchoring gap, which is a worse model dressed as a better one.
- critic yield as a pair — did `killed-real` / `must_find_killed` move? That
  number can *veto* a change outright, not merely tune it.

A change that beats the band on must-find recall without widening the near/far
delta or raising `killed-real` is a real improvement. Anything else is noise or
a trade you have to argue for in the promoting PR.

## The levers

### Sampling (the pinned decoding params)

The mechanism ships; the *values* are what a sweep settles (ticket 033).
`config/sampling.toml` is read by production and the eval suite from the one file
(decision 15) — there is no eval-only copy, because grading a configuration you
do not ship is how a suite goes green while production drifts. The canonical
sampling sweep is three arms on the same corpus, **decided by the far-domain
cases** (near cases sit closest to the exemplar and flatter anchoring):

1. **temperature 0** — the shipped default, and the pin under test;
2. **the production model default** — temperature unset;
3. **k=3 union-merged** (Self-MoA) — `candidate_count = 3`, currently *reserved*
   (the loader rejects `≠ 1` until the union/dedupe path exists), so this arm is
   a code change, not a config edit. It costs 3× the analyst fan-out, so it must
   beat the *band*, not just the mean.

Two ways to point a sweep at a different arm:

- **Deploy-style, via the recorded escape hatch** — set
  `STRIDE_SAMPLING_{TIER}_{PARAM}` (offered params only: `temperature`, `top_p`,
  `seed`, `thinking`) and run. The override flows into the run's provenance
  fingerprint, so a swept run reads as *uncertified* against the blessed
  manifest — which is the point (see `docs/Configuration.md`).
- **Scripted, via the harness seam** — `build_eval_pipeline(entry,
  sampling=<SamplingConfig>)` binds an arbitrary config for one sweep without
  touching the file. This is the path a multi-arm sweep script uses.

`thinking_budget` is the deliberately-absent other half of this question: unset
in the file because pinning an unmeasured value reads as a decision nobody made.
Whether it becomes a fourth arm or its own effort is a call for after the three
arms above have numbers.

### Prompts and exemplars

The analyst prompts and their exemplars (`prompts/`) are the biggest lever on
recall and on the near/far delta — the delta *exists* because the exemplars all
anchor on one domain. A prompt or exemplar change is measured exactly like a
sampling arm: baseline band, one change, re-run, read the per-case recall and
the delta. Because the exemplars cause the anchoring, a change here is the most
direct way to move the delta — for better or worse.

### The corpus

The reference sets are non-exhaustive by construction and **converge from real
output** (decision 11), so the corpus is itself a lever you improve over time.
Each scoring run surfaces `valid-unlisted` threats — grounded, plausible,
simply not in the reference set — in `unlisted_for_promotion`. Recurring ones
are reviewed and promoted into the reference set at the next blessing pass
(`BLESSING.md`, steps 4–6, for the affected case only). Promotion is a
reviewable diff with a human explaining why; it is never automatic. Expanding or
correcting the corpus moves every baseline, so re-establish the band afterwards.

### The judge

The judge (`evals/config/judge.toml`) is pinned *separately* from sampling
because a judge change re-scores all history. It is the measuring stick, not a
config under test — do not tune it to make a number look better. The only
sanctioned judge change is one that raises judge–human agreement, gated by
`calibrate` at ≥90%.

## Promoting a winner

Once an arm beats the band and you have decided to ship it, promotion is a
**single-sourced write** so the shipped config and the eval gate cannot drift
(ticket 08). `evals.harness.certify.promote(sampling, served_models,
resolve_tier)` — a function, not a CLI — does both halves from one winning
`SamplingConfig`:

1. re-pins the values in `config/sampling.toml` **in place**, preserving the
   comments (the "why-absent" record is the point of the file); and
2. derives the per-node generation-identity fingerprints and blesses them in
   `evals/blessed-fingerprints.toml`.

Because both come from the same config, a blessed fingerprint always describes
the params the file actually holds. Promoting a param the file leaves *unset*
raises — turning an UNVERIFIED param (`top_p`, `top_k`, penalties — ticket 04)
into a pinned one is a human decision that owes a rationale, not a silent sweep
write. Baselines and the manifest are checked-in artifacts, so a promotion is a
reviewable PR with the run artifacts behind it.

After promotion, a production run's fingerprints match the blessed set and
`run` reports **certified**; before it, and for any override-driven sweep, the
run is **uncertified** and its aggregates are never silently folded into a
trusted number. Hard-fail on uncertified is the off-by-default
`--require-certified` CI knob.

## What blocks, and what is only tracked

Gating is deliberately staged (ticket 009 decisions 16 / 18 / 19):

| Signal | Status | Rationale |
|---|---|---|
| **Tier 1 structural** (report parses, references resolve, severity matches the matrix, summary matches contents) | **blocks** day one | A malformed report is never a valid result. |
| **certification** (fingerprints blessed) | surfaced always; blocks only under `--require-certified` | Never silently trust a drifting generation identity. |
| **must-find recall** | tracked, **not** blocking yet → hard per-case gate after ~5 baselines | A gate that fires before anyone knows the normal range just trains people to bypass it. |
| **near/far delta, critic yield** | tracked, non-gating | Findings to act on, not builds to break — until the spread says where a band belongs. |

The order is the whole philosophy: **the mechanism is decided now, the numbers
are derived later** — by this loop, from measured runs, never guessed.
