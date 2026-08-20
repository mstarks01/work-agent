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

- **Credentials for your configured vendors.** Producing a sweep calls live
  models, so you need whatever the tiers in `config/model_tiers.toml` select —
  Google Cloud application default credentials plus a project and location for
  Vertex, or an API key for Anthropic or OpenAI. See
  [Configuration](../docs/Configuration.md#provider-environment). Scoring
  itself is offline — the matcher is a rule and the standing of the unmatched
  comes from the vote ledger — and so are `verify_corpus.py`, `pytest` and
  `calibrate`.
- **Dependencies installed:** `uv sync`.
- **A clean corpus:** `python evals/verify_corpus.py` should be green.

## The workflow at a glance

```mermaid
flowchart TD
    subgraph setup["Once per tuning session"]
        direction TB
        s1["1. Trust the rule<br/>calibrate"]
        s2["2. Establish a baseline<br/>run ×5 — the metric averages<br/>AND their spread"]
        s1 -- "≥90% agreement,<br/>or fix the rule" --> s2
    end

    subgraph tune["Repeat per idea"]
        direction TB
        s3["3. Change one lever<br/>edit/env — sampling,<br/>a prompt, or the corpus"]
        s4["4. Re-run and compare<br/>run ×5"]
        s5["5. Promote the winner<br/>commit — sampling also<br/>updates the blessed list"]
        s3 --> s4
        s4 -- "beats the baseline<br/>spread, per case" --> s5
        s4 -. "it doesn't" .-> s3
    end

    s2 --> s3
    s5 -. "next idea" .-> s3

    classDef step fill:#e0f2fe,stroke:#0284c7,stroke-width:1.5px,color:#082f49
    classDef win fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#052e16
    class s1,s2,s3,s4 step
    class s5 win
    style setup fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 4,color:#0f172a
    style tune fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 4,color:#0f172a
```

Do them in order. Steps 1–2 are setup you do once per tuning session; 3–5 are the
loop you repeat per idea.

## Step 1 — Trust the rule

**This step gates STRIDE's numbers and nothing else.** A **Framework Package**
whose claims carry a catalog identifier is matched by string, with no rule
composing an identity — ASVS is one, so its numbers are unaffected by anything
in this step.

STRIDE's recall and precision are measured by the identity rule
(`evals/harness/identity.py`): endpoint subset plus one action verb. If the
rule disagrees with the recorded labels, every number downstream of it is
noise. Check it first — offline, no credentials:

```sh
python -m evals.harness.run calibrate --out agreement.json
```

This must report **≥90% agreement**. If it doesn't, the fix is the rule or the
verb vocabulary (`evals/harness/verbs.py`), not a lower bar — a lenient rule
inflates recall silently, which is the expensive way to be wrong. Don't tune
anything until this passes. The shipped rule reports 92.5% over the 200 pairs
it can read, and the pairs it refuses are counted beside the bar.

What passing means is narrower than it looks. The labels are agent-authored and
a person has read 30 of the 339, so this measures whether the rule reproduces
them and not whether they are right. See the top of [README.md](README.md).

A rule change is a re-keying event, not a dependency bump: bump the fingerprint
version, run `rekey`, and the whole vote ledger recomputes under the new rule
with no re-vote. The retired LLM judge could not offer that, which is half of
why it is gone; the other half is that a human vote answers the question the
judge was guessing at.

## Step 2 — Establish a baseline (and its spread)

You can't tell a real gain from luck without knowing how much the numbers move
when *nothing* changes. Run the full suite five times on the current config:

```sh
for i in 1 2 3 4 5; do
  python -m evals.harness.run run --mode analysis --out baseline-$i.json
done
```

Then measure how much the numbers move when nothing changed. The **spread** is
the point — it's your significance threshold for every later comparison — and
`stability` computes it from the artifacts, no credentials and no re-run:

```sh
python -m evals.harness.run stability baseline-*.json --out baseline-spread.json
```

Read two things off it. `worst_case_recall_spread` is the band any later
comparison has to clear. `sometimes_matched` — references found in some runs and
not others — is the same fact per reference, and it is the more useful one when
a case's recall happens to land on the same number twice by finding different
threats.

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
| **critic yield** (a pair) | kills more junk (`killed-rejected`) without killing real findings (`killed-real`) | A kill count alone tells you nothing — read both halves together. |

All of these are *relative to the rule and the ledger*. Use them to compare
configurations and track movement; never quote them as absolute scores or
against other tools.

### The half a person grades

Neither of the two halves above says whether a finding is *right* — both grade
the tool against records an agent wrote. The review loop is what adds a human
judgement, and it costs no credentials:

```sh
uv run python webapp/review.py --voter <your-name> --artifact baseline-1.json
```

Watch two numbers, and read them the way you read critic yield — as a pair:

| Metric | What a good change does | Trap |
| --- | --- | --- |
| **up-vote rate** (per case) | rises, or holds | It rises trivially if the tool produces fewer, safer findings. Read it against how many findings the run produced. |
| **substance down-vote rate** | falls | A style down-vote is not this number. `poorly-written` leaves the finding in the pool and moves the writing score, so a config that writes worse cannot flatter this one. |

A vote hangs on a finding's fingerprint, so tuning does not re-spend it: after
the first sitting, a configuration change puts only its *new* findings in the
queue. That is what makes this affordable per experiment rather than per
session.

### The mechanically matched half

ASVS's numbers rest on a catalog match rather than a composed identity.
Watch these, under `applicability` and `applicability_aggregate` in the
artifact:

| Metric | What a good change does | Trap |
| --- | --- | --- |
| **applicability recall and precision** | both go up, or one holds while the other rises | **Read them together or not at all.** An ASVS claim rules applicability and never a pass, so a lane that ruled *everything* applicable scores 100% recall. Recall alone is trivially winnable and worthless. |
| **`off_catalog`** | is zero, and stays zero | Not a tuning number. It counts claims naming a requirement the run's level does not carry — the package composing an identifier its own catalog cannot reach. Any value here is a bug to fix, not a lever to pull. |
| **applicability exemplar delta** | shrinks or holds | Same question as STRIDE's, over this package's own exemplars. `exemplar_proximity` sits on the `(case, framework)` pair, so a case near STRIDE's payments exemplar is near nothing of ASVS's. |
| **applicability yield** (a pair) | rejects more the corpus did not expect (`earned`) without rejecting what it did (`destroyed`) | The ASVS critic's only destructive move is `rejected` — the ruling that a requirement does not apply. `destroyed` is the veto number here, exactly as `matched_killed` is for STRIDE, and a rejection count alone reads as either. |

**What moves each cell, when recall is short:**

| Cell | Usual cause | Where to look |
| --- | --- | --- |
| `missed` | the lane agent did not raise a requirement the case expects | that chapter's `frameworks/asvs/lanes/<chapter>/skill.md` and its exemplars |
| `over_applied` | the agent ruled a requirement applicable the case did not expect | the chapter's `## Applicability` section — or the corpus, see below |
| `off_catalog` | the composed identifier is outside the run's level | a defect in the package, never in a prompt |

**And check the lead first.** `evals/harness/triggers.py` reports, per framework,
whether a deterministic rule fired in a claim's lane on an element it names.
ASVS sits at 32% against STRIDE's 80%, and eleven of its seventeen chapters draw
no lead on any reference claim at all
([#218](https://github.com/mstarks01/work-agent/issues/218)). A missed
requirement in one of those chapters is not a prompt problem — the agent was
never handed the lead. **This costs no provider call**, so read it before
spending a sweep.

## Step 3 — Change exactly one lever

Change one thing at a time — two at once and you can't tell which moved the
numbers. Your levers, roughly in order of leverage:

### Sampling (decoding parameters)

`config/sampling.toml` holds the per-tier decoding params. To *try* a value
without editing the file, use an environment override (see
[Configuration](../docs/Configuration.md#sampling-overrides)) — this is exactly
what a sweep does:

```sh
# Try a warmer temperature on the strong-tier category agents for one run:
STRIDE_SAMPLING_STRONG_TEMPERATURE=0.4 \
  python -m evals.harness.run run --mode analysis --out warm-strong.json
```

The canonical sampling experiment is three arms on the same corpus, **decided
by the far-domain cases**:

1. **`temperature = 0`** — the shipped default (greedy).
2. **the model's own default** — a warmer temperature (`STRIDE_SAMPLING_*_TEMPERATURE`).
3. **k-of-n sampling** — draw several candidates and union them (higher recall,
   but several times the cost, so it has to clearly earn it).

Run each arm five times, compare each to the baseline band, and read the near/far
delta *per arm*: temperature 0 may be flattering the near-domain cases while
costing recall on the far ones, which is the whole reason to test it.

### Prompts and exemplars

The category-agent prompt and its worked examples (`prompts/analyze.md` and
`prompts/exemplars/`) are the strongest
lever on recall — and the *cause* of the near/far gap, since all the examples
come from one domain (payments). Editing an exemplar or adding one from a
different domain is the most direct way to move that gap. Measure it exactly like
a sampling change.

### The corpus

The reference sets aren't meant to be exhaustive up front — they grow from real
output. Every run flags what it produced that the reference set does not carry,
one key per framework:

```sh
jq '.unlisted_for_promotion'      baseline-1.json   # STRIDE: grounded, plausible, unlisted
jq '.over_applied_for_promotion'  baseline-1.json   # ASVS: ruled applicable, not expected
```

The two are the same question, answered by different people's records.
STRIDE's lists the findings a reviewer voted into the pool — real, just not in
the reference set — so promotion consumes a human judgement already made.
ASVS's falls out of set arithmetic, and `off_catalog` has already taken out
the entry that is a package bug rather than a judgement.

Recurring ones are worth adding to a case's reference set (see
[`BLESSING.md`](BLESSING.md)). This makes the corpus a better yardstick over
time — but it also shifts every baseline, so re-run Step 2 after changing it.

## Step 4 — Re-run and compare

Re-run the suite five times with your change and compare to the baseline band:

- **Per-case must-find recall** — did any single case regress below its baseline
  spread? One case collapsing vetoes the change even if the average rises.
- **Stability** — run `stability` over the five new artifacts too. A change that
  lifts recall while widening the spread has bought an average with volatility,
  and the next sweep may not reproduce it.
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
and every run reads as uncertified. One command writes both, from the winning
artifact:

```sh
# Preview: shows every identity that would be certified, writes nothing.
python -m evals.harness.run promote candidate.json

# Apply, once the block above says what you expected.
python -m evals.harness.run promote candidate.json --yes
```

It needs no credentials. Everything it certifies was **observed during the
sweep** and written into the artifact's `provenance` block — which served build
answered for each tier, and the sampling that was resolved alongside it. You do
not supply the model strings, and there is no way to: promotion recomputes each
fingerprint from the recorded served build and sampling with the same function
the service certifies against, and refuses an artifact whose stored hashes don't
follow from the identities beside them.

The preview is the point of the two-step. It prints, per tier, the requested
route, the build that actually answered, every decoding parameter (with `unset`
shown as `unset`, never as a zero), and the full fingerprint that will be
blessed:

```text
STRONG
  requested: vertex_ai/gemini-2.5-pro
  served:    vertex_ai/gemini-2.5-pro-002
  nodes:     analyze_spoofing, analyze_tampering, critic

  temperature:         0.0
  top_p:               unset
  max_output_tokens:   64000
  ...

  fingerprint:
    792c8e41...
```

`--yes` rewrites `config/sampling.toml` in place (keeping its comments) and adds
the fingerprints to `config/blessed-fingerprints.toml`, keyed by tier. Commit
both. Blessing is additive — an existing blessed build stays blessed, and
promoting the same artifact twice adds nothing the second time.

**When a tier was answered by two builds.** A sweep is hours long and providers
rotate, so one tier can present two served builds in a single run. Promotion
refuses rather than picking:

```sh
python -m evals.harness.run promote candidate.json \
  --served strong=vertex_ai/gemini-2.5-pro-002 --yes
```

`--served` **selects among what was observed** — a build the sweep never saw is
rejected, so the flag can narrow what gets blessed and can never introduce it.
Run it again naming the other build if both should be certified; the manifest
accumulates.

Two refusals worth knowing about, both deliberate:

> `promote` refuses to pin a parameter the file deliberately leaves unset (like
> `top_p`). Those are unset because there's no measured value to pin — turning
> one on is a real decision that belongs in a reviewed edit with a reason, not a
> silent sweep write.

> It also refuses an artifact measured under a different `sampling.toml` schema
> version, or one carrying an `artifact_version` it doesn't know. Both are hard
> cutovers: re-run the sweep rather than re-pinning values across a schema
> change, which would bless a fingerprint describing parameters no run carried.

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

A run reports **incomplete** if a tier its graph declares presented no
fingerprint at all. That is an assertion rather than a measurement — every tier
has a node that always runs — so it should never be seen; if it is, the sweep
did not exercise what it claims to have measured, and its scores are recorded
as untrusted whether or not `--require-certified` is set.

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
| **must-find recall, near/far delta, critic yield, coverage** | No — printed and recorded | These are findings to act on, not build breakers, until enough baselines exist to know what "normal" is. |
| **applicability recall, precision and `off_catalog`** | No — printed and recorded | Same reason, and they cost no provider call. |
| **Token usage and latency** | No — printed and recorded | Cost and wall-clock per node. What they inform is a budget decision, not a correctness one. |
| **Stability** | No — and it is not part of a run at all | It needs two finished sweeps, so it is its own command over their artifacts. |

The shipped default remains `temperature = 0`. Tuning the per-tier values to
something better is exactly the loop above — run it once you have live
credentials and the baselines to measure against.
