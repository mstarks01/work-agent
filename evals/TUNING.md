# Tuning the models

A practical guide to changing the model configuration, proving the change is a
real improvement, and shipping it. If you just want to understand the metrics,
read [`README.md`](README.md) first; if you want to *act* on them, you're in the
right place.

The method is one sentence: **change one thing, measure it against the run-to-run
noise, and keep only what clearly beats the noise.** Everything below is that
sentence with the commands filled in.

> For **contributors changing the shipped model config** in `config/`. It scores
> the engine against this repo's fixed golden corpus — not against your own
> system's text — and every scoring run calls live models. Embedding the engine
> instead? You want [docs/First-Run.md](../docs/First-Run.md).

## Before you start

- **Credentials for your configured vendors.** The scoring runs call live
  models, so you need whatever the tiers in `config/model_tiers.toml` select —
  Google Cloud application default credentials plus a project and location for
  Vertex, or an API key for Anthropic or OpenAI. The judge in
  `evals/config/judge.toml` has its own `(vendor, model)` pair and may need a
  different one. See
  [Configuration](../docs/Configuration.md#provider-environment). The offline
  steps (`verify_corpus.py`, `pytest`) need none of this.
- **Dependencies installed:** `uv sync`.
- **A clean corpus:** `python evals/verify_corpus.py` should be green.

## The workflow at a glance

```
1. Trust the judge         calibrate  ──▶ ≥90% agreement, or fix the judge prompt
2. Establish a baseline    run ×5     ──▶ the metric averages AND their spread
3. Change one lever        edit/env   ──▶ sampling, a prompt, or the corpus
4. Re-run and compare      run ×5     ──▶ beat the baseline spread, per case
5. Promote the winner      commit     ──▶ (sampling also updates the blessed list)
```

Do them in order. Steps 1–2 are setup you do once per tuning session; 3–5 are the
loop you repeat per idea.

## Step 1 — Trust the judge

Recall and precision are measured by an LLM judge. If the judge disagrees with
human labels, every number downstream is noise. Check it first:

```sh
python -m evals.harness.run calibrate --out agreement.json
```

This must report **≥90% agreement**. If it doesn't, the fix is the judge prompt
(`evals/prompts/`), not a lower bar — a lenient judge inflates recall silently,
which is the expensive way to be wrong. Don't tune anything until this passes.

## Step 2 — Establish a baseline (and its spread)

You can't tell a real gain from luck without knowing how much the numbers move
when *nothing* changes. Run the full suite five times on the current config:

```sh
for i in 1 2 3 4 5; do
  python -m evals.harness.run run --mode analysis --out baseline-$i.json
done
```

Then look at how each metric varies across the five runs. The **spread** is the
point — it's your significance threshold for every later comparison. A quick way
to eyeball must-find recall per case:

```sh
for f in baseline-*.json; do
  jq -r '.scores[] | "\(.case_id)\t\(.recall)"' "$f"
done | sort | column -t
```

If a metric's spread across the five runs is wider than any change you'd hope to
see, that metric simply isn't sensitive enough to gate on — note it and rely on
the others.

### Which numbers matter

The run prints and records several metrics ([`README.md`](README.md) has the full
list). When tuning, watch these three:

| Metric | What a good change does | Trap |
| --- | --- | --- |
| **must-find recall** (per case) | goes up, or holds, on **every** case | An aggregate average hides one case collapsing. Always read per case. |
| **near/far exemplar delta** | shrinks or holds | A change can lift average recall while widening this gap — a worse model that looks better. The far-domain cases are the honest test. |
| **critic yield** (a pair) | kills more junk (`killed-ungrounded`) without killing real findings (`killed-real`) | A kill count alone tells you nothing — read both halves together. |

All of these are *relative to the judge*. Use them to compare configurations and
track movement; never quote them as absolute scores or against other tools.

## Step 3 — Change exactly one lever

Change one thing at a time — two at once and you can't tell which moved the
numbers. Your levers, roughly in order of leverage:

### Sampling (decoding parameters)

`config/sampling.toml` holds the per-tier decoding params. To *try* a value
without editing the file, use an environment override (see
[Configuration](../docs/Configuration.md#sampling-overrides)) — this is exactly
what a sweep does:

```sh
# Try a warmer temperature on the strong-tier analysts for one run:
STRIDE_SAMPLING_STRONG_TEMPERATURE=0.4 \
  python -m evals.harness.run run --mode analysis --out warm-strong.json
```

The canonical sampling experiment is three arms on the same corpus, **judged by
the far-domain cases**:

1. **`temperature = 0`** — the shipped default (greedy).
2. **the model's own default** — a warmer temperature (`STRIDE_SAMPLING_*_TEMPERATURE`).
3. **k-of-n sampling** — draw several candidates and union them (higher recall,
   but several times the cost, so it has to clearly earn it).

Run each arm five times, compare each to the baseline band, and read the near/far
delta *per arm*: temperature 0 may be flattering the near-domain cases while
costing recall on the far ones, which is the whole reason to test it.

### Prompts and exemplars

The analyst prompts and their worked examples (`prompts/`) are the strongest
lever on recall — and the *cause* of the near/far gap, since all the examples
come from one domain (payments). Editing an exemplar or adding one from a
different domain is the most direct way to move that gap. Measure it exactly like
a sampling change.

### The corpus

The reference threat sets aren't meant to be exhaustive up front — they grow from
real output. Every run flags grounded, plausible threats it produced that aren't
in the reference set, under `unlisted_for_promotion` in the artifact:

```sh
jq '.unlisted_for_promotion' baseline-1.json
```

Recurring ones are worth adding to a case's reference set (see
[`BLESSING.md`](BLESSING.md)). This makes the corpus a better yardstick over
time — but it also shifts every baseline, so re-run Step 2 after changing it.

## Step 4 — Re-run and compare

Re-run the suite five times with your change and compare to the baseline band:

- **Per-case must-find recall** — did any single case regress below its baseline
  spread? One case collapsing vetoes the change even if the average rises.
- **Near/far delta** — did the gap widen? If so, you may have traded far-domain
  coverage for a better-looking average.
- **Critic yield** — did `killed-real` (real findings the critic threw out) go
  up? That's a regression hiding inside a higher kill rate.

A change that lifts or holds per-case recall, without widening the delta or
raising `killed-real`, is a keeper. Anything else is noise, or a trade you should
justify in the pull request.

## Step 5 — Promote the winner

**Prompt or corpus changes** ship like any code change: commit the edited files
with the run artifacts (or a summary) in the PR so a reviewer can see the gain.

**Sampling changes** need one extra step. Every report records the exact
configuration each node ran on as a **fingerprint** — a hash of the served model
build plus that tier's decoding parameters — and both the service and the eval
harness check those fingerprints against the ones this deployment has
**blessed**, in `config/blessed-fingerprints.toml`. Promoting a sampling winner
has to update *both* the config file and that blessed list, or the two disagree
and every run reads as uncertified. The `promote` helper writes both from one configuration so
they can't drift apart:

```python
from evals.harness.certify import promote
from stride_service.model_tiers import load_model_tiers
from stride_service.sampling import load_sampling

winner = load_sampling("config/sampling.toml")   # after editing it to the winning values
tiers = load_model_tiers("config/model_tiers.toml")

# served_models: the exact model string each node ran on in the winning sweep
# (read them from the run artifact's node results).
promote(winner, served_models, tiers.resolve_tier)
```

This rewrites `config/sampling.toml` in place (keeping its comments) and records
the winning fingerprints in `config/blessed-fingerprints.toml`, keyed by tier.
Commit both.

> Note: `promote` refuses to pin a parameter the file deliberately leaves unset
> (like `top_p`). Those are unset because there's no measured value to
> pin — turning one on is a real decision that belongs in a reviewed edit with a
> reason, not a silent sweep write.

### Certification

Once promoted, a production run's fingerprints match the blessed list and the
run reports **certified**. Until then — and for any run driven by a temporary
`STRIDE_SAMPLING_*` override, since an override changes the fingerprint — the
run is **uncertified**, and its scores are surfaced as untrusted rather than
folded quietly into a baseline. To make an uncertified run fail outright (in CI,
say):

```sh
python -m evals.harness.run run --mode analysis --require-certified
```

It is off by default: the blessed list ships empty, so on by default it would
fail every run before anyone had a baseline to compare against, and people would
just switch it off.

The service applies the same check to jobs it completes, using the same
`config/blessed-fingerprints.toml` — there, the equivalent switch is
`STRIDE_REQUIRE_CERTIFIED` and it withholds the report rather than failing the
job. See
[Architecture](../docs/Architecture.md#provenance-and-certification).

## What blocks a run, and what only informs it

Not every metric stops the world. The gating is deliberately staged:

| Signal | Blocks a run? | Why |
| --- | --- | --- |
| **Structural validity** (report parses, references resolve, severity matches the matrix, summary matches contents) | **Yes, always** | A malformed report is never a valid result. |
| **Certification** (every fingerprint blessed) | Only under `--require-certified` | Surfaced on every run, so a configuration that has drifted is never trusted silently. |
| **must-find recall, near/far delta, critic yield** | No — printed and recorded | These are findings to act on, not build breakers, until enough baselines exist to know what "normal" is. |

The shipped default remains `temperature = 0`. Tuning the per-tier values to
something better is exactly the loop above — run it once you have live
credentials and the baselines to measure against.
