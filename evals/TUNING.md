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

## The money: what `run` asks before it spends

`run` is the one command here that spends. Before the first request, it
prints what the sweep is expected to cost and waits for you to accept it.

There is **no ceiling**. You may accept any amount — it is your money, and
the gate exists so you know what you are accepting, not to cap it.

Every amount carries one of three labels, and the label says how good the
number is:

| Label | What it means |
| --- | --- |
| `recorded` | A merged Baseline ran this exact configuration and cost this. A real number. |
| `estimated` | Another Baseline's token counts, repriced for your models. A best guess. |
| `unpriced` | No number exists — a tier's model is absent from the price map, its vendor is a gateway with no one rate per model, or no merged Baseline exists to calibrate from. Never a zero. |

A sweep on a gateway route stays `unpriced` before the run and is not silent
after it. Such a provider states what it charged, the run records that figure
per node, and the artifact carries it under `node_charges` — so the sweep's
manifest reports what the account paid even where no rate could have predicted
it. Every other vendor reports token counts alone, where the arithmetic over
recorded rates is the answer and this block is empty.

**Accepting means typing the amount back.** An enter or a `y` never proceeds,
because a habit should not be able to spend money for you. Where no amount can
be stated, type `unknown` — that is a real acceptance of a cost nobody can
state.

For a script, pass the number you accept:

```bash
python -m evals.harness.run run --mode analysis --out sweep.json --accept-cost 5.00
```

The run refuses if the estimate is higher than that; raise the flag and mean
it. `--accept-cost unknown` accepts an unstatable cost the same way.

**The run holds you to what you accepted.** Between cases — never inside one —
it compares the spend so far to your accepted amount. At a terminal it shows
the new number and asks you to accept it again; under `--accept-cost` nobody
is there to ask, so the sweep stops. A stopped sweep keeps every report it
already paid for, and its artifact records the cases it never ran, so it
reads as the partial record it is.

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
        s3["3. Price the fix<br/>read the archived misses —<br/>a ceiling, in must-finds"]
        s4["4. Change one lever<br/>edit/env — sampling,<br/>a prompt, or the corpus"]
        s5["5. Re-run and compare<br/>run ×5"]
        s6["6. Promote the winner<br/>commit — sampling also<br/>updates the blessed list"]
        s3 -- "ceiling clears<br/>the spread" --> s4
        s3 -. "it doesn't:<br/>batch it, or drop it" .-> s3
        s4 --> s5
        s5 -- "beats the baseline<br/>spread, per case" --> s6
        s5 -. "it doesn't" .-> s3
    end

    s2 --> s3
    s6 -. "next idea" .-> s3

    classDef step fill:#e0f2fe,stroke:#0284c7,stroke-width:1.5px,color:#082f49
    classDef win fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#052e16
    class s1,s2,s3,s4,s5 step
    class s6 win
    style setup fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 4,color:#0f172a
    style tune fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 4,color:#0f172a
```

Do them in order. Steps 1–2 are setup you do once per tuning session; 3–6 are the
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

**Read the error directions first; they are the measurement.** The shipped
rule has 13 false splits of 169 equivalent candidate pairs, 2 false merges of
92 candidate negatives and 2 false merges of 317 distinct reference pairs. A
split hands a reviewer one unmatched finding. A merge destroys a finding and
inflates recall, and nobody sees it happen — which is the expensive way to be
wrong, and why a lenient rule is never the fix. Watch the candidate column
first: it is the population a live run resembles.

The command then reports **≥90% agreement**. That bar is the admission gate for
a *candidate* rule, which has no pinned counts of its own yet; the shipped
rule's split and merge counts are pinned exactly in
`tests/test_evals_identity.py` and bind harder. If the bar fails, the fix is
the rule or the verb vocabulary (`evals/harness/verbs.py`), not a lower bar.
Don't tune anything until this passes. The pairs the rule refuses, and the
pairs a label sets aside as `unclear`, `unsupported`, or `invalid-claim`, are
counted beside the bar rather than inside it.

What passing means is narrower than it looks. The 288 calibration fixtures began
agent-authored, and review 02 read a part of the set as it stood on 2026-09-02:
every decision-boundary fixture, and a random sample of the rest.
`evals/calibration_labels/REVIEW-02.md` carries that sitting's own counts, which
are a record of what it read rather than a figure about the set today. This
still measures whether the rule reproduces recorded dispositions, not whether
either is externally right. See the top of [README.md](README.md).

A rule change is a re-keying event, not a dependency bump: bump the fingerprint
version, run `rekey`, and the whole vote ledger recomputes under the new rule
with no re-vote. The retired LLM judge could not offer that, which is half of
why it is gone; the other half is that a human vote answers the question the
judge was guessing at.

## The fan-out is what a provider quota sees

**One job fires one `strong`-tier request per lane of every framework it names,
all together at the barrier.** That is
`analysis_service.frameworks.widest_fan_out()` — 23 today — and at roughly 14K
input per lane it is a **~322K token burst from a single job**.

Against a 200,000 tokens-per-minute quota that job cannot complete, and no
ceiling in `config/resilience.toml` helps: `max_active_jobs` bounds *jobs*, and
this is one job. The failure is a provider `RateLimitError` after the configured
`attempts` are spent, mid-sweep, with no artifact written.

Two levers, in order of preference:

1. **Raise the quota.** The only route that sweeps every framework at once.
2. **`--framework stride`** — narrow the selection. Six lanes is a ~72K burst
   and fits under 200K. It is a pure selection: it names no option and changes
   no reference set, so a narrowed sweep measures the same cases the same way
   and simply measures fewer frameworks per case. It prints the cases it skips.

Check the arithmetic against your own quota before a long sweep rather than
after it. A rate-limited run spends real money and produces nothing.

## Step 2 — Establish a baseline (and its spread)

You can't tell a real gain from luck without knowing how much the numbers move
when *nothing* changes. **This is not a formality.** Five `gpt-4o` extraction
runs on an unchanged config, 2026-08-23, gave failed-case counts of **2, 2, 5,
5, 10** — a five-fold spread from chance alone. Three findings reported off
single runs that week had to be withdrawn once that was known.

Run the full suite five times on the current config:

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

### The band, measured, at each scale

The spread is a property of the instrument you read it on, and the corpus total
is the widest one available. Measured on the STRIDE corpus, 2026-09-13:

| instrument | band |
| --- | --- |
| one case, five runs | sd 0.89 must-finds on each strong case, 1.52 on the weak one |
| the corpus total, derived from those | sd about 4.0 must-finds of 129 |
| two corpus sweeps of one configuration | 6 must-finds apart, which is 1.1 sd |

The two sweeps ran on one corpus digest, one route and one model, and moved
−7 matched and −6 must-find across nine of thirteen cases. The commits between
them touch the extraction scorer, the loss instrument and `extract.md`, none of
which analysis mode reads.

**The weak case is the noisy one.** Case 03 read 5, 2, 6, 4, 5 of 9 — 17% of
its own total, against 9% for the two strong cases. The cases with the most
headroom are the hardest to measure, which is the opposite of convenient.

What that costs to see, at two standard deviations:

| effect, in must-finds | sweeps each side | approximate cost |
| --- | ---: | ---: |
| 3 | ~14 | ~$78 |
| 5 | ~5 | ~$28 |
| 8 | ~2 | ~$11 |
| 10 or more | 1 | ~$6 |

So **an effect has to exceed about 11 must-finds to show in a single
before-and-after pair of corpus sweeps.** Every prompt-edit ceiling priced in
the week to 2026-09-13 — 3, 4, 5, 9 and 14 — is at or under that line. That is
the reasoning behind step 3's rule, and #879 is where the measurement lives.

### Do not buy the band. Read it off the runs you have.

Five sweeps of one configuration is the obvious way to get the number above,
and it costs about $28 for a deviation on four degrees of freedom — whose own
95% interval runs from about 0.6 to 2.9 times the truth. `stability` reads it
off the fates instead, for nothing:

```sh
python -m evals.harness.run stability before.json after.json   --calibrate case09-r*.json --calibrate case03-r*.json
```

Each must-find is matched or missed in each run, so a reference matched in `m`
of `k` runs contributes `m(k-m)/(k(k-1))` to the total's variance. Summing over
the references gives the band from **two** sweeps, because the sum runs over
the references rather than over the runs.

That sum assumes the references move independently and they do not: a run that
goes badly goes badly across several at once, so the sum is a **floor**.
`--calibrate` takes repeat sets — three or more runs of one configuration,
which the single-case spreads of step 3 already produce — and measures the gap
rather than assuming it. The floor and the observed deviation are two readings
of one question, tested against each other.

Read on the two 13-case sweeps of 2026-09-13 with the three single-case repeat
sets as calibration: 14 of 86 readable must-finds moved, floor sd 2.65,
inflation 1.63 on 12 degrees of freedom, **sd 3.37**. Every repeat run that
lands from now on sharpens it and costs nothing extra.

An artifact older than the current schema is refused by the loader, and the
repeat sets on disk are usually older than the instrument reading them. Lift a
copy first:

```sh
python -m evals.harness.run migrate evals/runs/<set>/*.json --out /tmp/lifted
```

It writes copies, never in place, and never inside `evals/baselines`. A file it
cannot help — a sweep from before execution identities were recorded — is named
and skipped rather than stopping the rest.

### Two standards for a name

Extraction is graded on element IDs, which are derived from names, so a model
that identifies a component correctly and names it its own way scores as though
it found nothing. Those are two different questions and the score now asks both:

- **Naming-policy conformity** — `endpoint_recall`. Did the extraction keep the
  source's wording, which is what `extract.md` rule 3 asks for?
- **Semantic fidelity** — `sourced_recall`. Does the name identify the thing the
  source describes at all?

The gap between them is `naming_departures`, and it reads the way the gap
between strict and endpoint recall reads: wide means an extraction that found
the architecture and named it its own way, narrow at a low number means one that
found different things.

Nothing infers a rename. A case's `aliases` in `case.json` lists the other names
a **reader** ruled supported, each with the excerpt from the case's own source
that supports it — `verify_corpus` refuses one whose excerpt the text does not
carry, one that crosses its element's type, and one that derives another
element's ID. A case nobody has ruled on reads exactly the strict number.

**One alignment, read by every figure.** `evals/harness/alignment.py` pairs
each blessed element with one produced element and records the evidence: an
exact ID, a reader's alias, a flow under its own label between aligned
endpoints, or a flow the discriminators (`operations` and whether a protocol is
stated) single out between them. Two flows the rules cannot tell apart are
listed as an ambiguity and paired with nothing. `aligned_recall`,
`aligned_interaction_recall`, `actor_recall`, `initiator_recall`, the attribute
comparison and `extraction-losses` all read that one object, so a ruling on a
name reaches every figure: before it, the reader's alias on case 09's
spreadsheet moved `sourced_recall` and left `initiator_recall` at 0.0 (#961).
The strict figures stay beside the aligned ones.

The score says little about an extra element, on purpose. `extra_status` reads
`equivalent` where the alignment paired it and `unreviewed` everywhere else — an
unreviewed extra may be a component the corpus omits, one the model invented, or
a name nobody has ruled on, and no figure can tell those apart. Two diagnostics
are named for exactly what they read: `same_type_unmatched_candidates` lists the
extras whose type has an unaligned blessed element, which one missing store and
twenty unrelated extra stores fills with twenty candidates, and
`name_tokens_absent_from_source` lists the extras with a name word the source
never uses. Neither is an invention count and neither is a rename count.

Read over the six archived runs of 2026-09-13, on the ten elements a reader has
ruled: 9.2 absent a run by exact ID, of which **7.3 were found under the ruled
name** and 1.8 were genuinely absent.

**Price your own fix, not the corpus.** `--row <case>:<reference>` narrows the
band to the references a fix targets, which is the reading step 3 asks for. The
nine rows #867 targets read sd 1.27, so its ceiling of 9 needs **one run each
side** of the six cases that hold them — against 13 runs each side to see an
effect of 3 on the corpus total.

ASVS has its own band and nobody has measured it. It has 17 lanes and its own
scorer, so nothing here carries across — but the instrument reads its record
too, so its first pair of sweeps will report one.

### Which numbers matter

The run prints and records several metrics ([`README.md`](README.md) has the full
list). When tuning, watch these three:

| Metric | What a good change does | Trap |
| --- | --- | --- |
| **must-find coverage** (per case) | goes up, or holds, on **every** case | An aggregate average hides one case collapsing. Always read per case. It is coverage of a reference, not a measure of whether the argument is right (#890). |
| **near/far exemplar delta** | shrinks or holds | A change can lift average coverage while widening this gap — a worse model that looks better. The far-domain cases are the honest test. |
| **critic yield** (a pair) | kills more junk (`killed-rejected`) without killing real findings (`killed-real`) | A kill count alone tells you nothing — read both halves together. |

All of these are *relative to the rule and the ledger*. Use them to compare
configurations and track movement; never quote them as absolute scores or
against other tools.

### The half a person grades

Neither of the two halves above says whether a finding is *right* — both grade
the tool against records an agent wrote. The review loop is what adds a human
judgement, and it costs no credentials:

```sh
uv run python webapp/review.py --voter <your-name> \
  --artifact baseline-1.json --artifact baseline-2.json  # ...one per run
```

Name every run of the arm. The queue then asks first about the findings those
runs disagree on, which is where one answer settles the most.

[VOTING.md](VOTING.md) is the whole procedure — what each answer moves, and
what the four standings do to a number. When tuning, watch two of them, and read
them the way you read critic yield — as a pair:

| Metric | What a good change does | Trap |
| --- | --- | --- |
| **`rejected_rate`** (per case) | falls, or holds | A style down-vote is not this number. `poorly-written` leaves the finding in the pool, so a config that writes worse cannot flatter this one. |
| **`pooled`** against **`unvoted`** | `pooled` rises while `unvoted` falls | `rejected_rate` reads 0.0 over a cold ledger. A low number beside a large `unvoted` count means nobody has looked, not that the tool is right. |
| **`writing_aggregate.objection_rate`** | falls, or holds | Where a style objection lands, and the only number it moves. It is a rate over the findings people answered, so read `answered` beside it. |

The votes reach the numbers with no provider call: `score` re-reads the ledger
over a finished sweep's saved reports.

```sh
python -m evals.harness.run score baseline-1.json
```

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
| **applicability recall and precision** | both go up, or one holds while the other rises | **Read them together or not at all.** An ASVS claim rules applicability and never a pass, so a lane that ruled *everything* applicable scores 100% recall. Recall alone is trivially winnable and worthless. Precision reads `n/a` until a case declares `reference_set: exhaustive`, because the complement of a sample is not a set of negatives — so today recall has no partner and no ASVS run is tunable on this row. |
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

## Step 3 — Price the fix before you spend

A sweep costs about $6 and sees nothing under the spread. So before a lever
moves, read what the fix can recover from the sweeps already on disk, and say
it as a number: must-finds of 129.

**Read the archived misses.** Every scored sweep carries two blocks that charge
each miss to what lost it. `losses` is STRIDE's: the verb (the lane cited the
place and wrote another action), `merged`, `misfiled` (the finding sits in
another lane), the critic, the place (a rule led there and nothing was
drafted), or `unled`. A `place` or `unled` row also says whether a surviving
draft sits one element over. `attribution` is ASVS's, by stage.

Both split a critic charge in two. A row carrying a non-empty `re_ask` names a
claim the *first* critic pass got wrong, so the ruling that lost it came out of
the bounded re-ask rather than out of the pass that reasoned about it. Price
those against the first pass and `recritic`, not against the critic's
reasoning; the `by_re_ask_kind` fold says which problem the first pass had.
`run.py score` writes both over any archived sweep, and the merged Baselines
under `evals/baselines/` are the sweeps to read first. A Baseline's files are
digest-sealed, so score it to a copy: the command refuses to write inside
`evals/baselines/`.

```bash
python -m evals.harness.run score evals/baselines/<baseline>/<sweep>.json \
  --out /tmp/<sweep>-rescored.json
```

The class a fix addresses is its ceiling, and the ceiling is a price rather
than a bound: the number of must-finds the fix is expected to recover, read off
the rows in its class. An exemplar edit is priced on the verb losses in its
lane; a candidate rule on the `unled` losses. Neither class is causal. A better
example can lead a lane to a place it never cited, a rule can recover a
different action at a place already led, and either can regress outside its
class. So price on the class, and read a gain or a loss outside it as a signal
about the mechanism, never as noise. The loss instrument charges each miss to
the first surviving claim at the reference's place, so a `verb` or `merged`
row records one observation and hides no other cause; a run can move rows
between classes. On 2026-09-09 one exemplar edit went to a paid sweep before
this was read; its ceiling was five must-finds, inside the band.

**Price an extraction or assertion change on the archived emissions.** An
extraction sweep keeps what `extract` emitted and an assertion sweep keeps
what `assert` proposed, and `evals/emissions/` holds every paid one. `replay`
re-parses each emission under the code and the corpus that stand and gives
every blessed element one fate: found, renamed under a reader's alias or a
flow's own label, respelled, mistyped, misattached, lost with its endpoint,
omitted outright, or unresolved because a candidate sits beside it that nobody
has ruled on. The fates are split per element type, so a change aimed at the
actors reads its ceiling off the `entity` column and nothing else. A signed
reference row on an assertion case takes a fate the same way.

```bash
python -m evals.harness.run replay evals/emissions/*.json --out /tmp/replay.json
```

An `omitted` count is the ceiling of a prompt change that asks the model to
read more. An `unresolved` count is not: it waits on a ruling, and a run
cannot move it. Read the two apart. And read the replay as a ceiling rather
than a prediction: it applies today's code to yesterday's emission, and a
behaviour the prompt under test would have changed is invisible to it.

**Price a scorer change on the frontier, with no run at all.** A verb
equivalence in `evals/harness/verbs.py` is a decision with a price on three
axes and a gain on one, and every number is offline:

```bash
python -m evals.harness.run price-verbs \
  --equivalent forge=inject=plant --equivalent read=recover-credential \
  --together --artifact evals/baselines/<baseline>/<sweep>.json
```

It prints the shipped rule's row and one row per candidate: labelled matches
the rule would split, labelled non-matches it would merge, reference pairs the
corpus records as two findings that it would call one, and the sweep re-scored
under it. Every new merge is named, because a count of wrong merges is a number
nobody can act on. A candidate that prices well is still a decision: a reference
merge is a pair the corpus says are two findings, and the corpus is the standard.

**Then apply the rule.** A fix whose ceiling sits inside the spread from step 2
gets no run of its own. Batch it with the next fixes until the batch clears the
band, because a Baseline is per commit and every merge makes the next run an
estimate rather than a recorded number. Spend on the narrowest instrument that
can see the change: one case first, five runs of that case next, and the corpus
only for a batch.

**Run cases at once where waiting is the cost.** A sweep runs one case at a
time by default. `--cases-in-flight N` runs N together, bounded by the
deployment's own `max_active_jobs`, and the artifact is byte for byte what a
sequential sweep writes. It buys the most in the extraction mode, which has one
node and so waits for its whole wall clock: 13 cases take 6.7 minutes and a
five-run spread takes 33.

Read what it costs before you turn it on. The spend hold runs **between
batches**, so at N the run may pass the amount you accepted by a batch rather
than by a case. Under `--accept-cost unknown` the hold never fires at all, at
any N. And N cases in flight reserve N times the maximum-output cost against a
gateway's in-flight budget, which is what refused a $2.41 sweep at a $1.24
balance on 2026-09-12.

**Do not price a targeted fix against the corpus total.** The total's band is
about 4 must-finds of 129, and no ceiling written so far clears it. A fix that
targets named rows is measured on those rows: the case-09 spread on 2026-09-12
read 9, 7, 9, 9, 8 of 10 against a Baseline's 6, and the per-reference fates
named exactly which rows moved. That cost $0.76 and answered more than the
$2.41 sweep beside it did. What is decidable offline — a verb pair against the frontier,
a corpus label, a scorer rule — is decided offline first, and the run confirms
rather than discovers.

## Step 4 — Change exactly one lever

Change one thing at a time — two at once and you can't tell which moved the
numbers. Your levers, roughly in order of leverage:

### Sampling (decoding parameters)

`config/sampling.toml` holds the per-tier decoding params. To *try* a value
without editing the file, use an environment override (see
[Configuration](../docs/Configuration.md#sampling-overrides)) — this is exactly
what a sweep does:

```sh
# Try a stated temperature on the strong-tier category agents for one run:
ANALYSIS_SAMPLING_STRONG_TEMPERATURE=0.4 \
  python -m evals.harness.run run --mode analysis --out warm-strong.json
```

The canonical sampling experiment is three arms on the same corpus, **decided
by the far-domain cases**:

1. **the model's own default** — the shipped state, which sets no temperature.
2. **`temperature = 0`** — greedy (`ANALYSIS_SAMPLING_*_TEMPERATURE=0`).
3. **k-of-n sampling** — draw several candidates and union them (higher recall,
   but several times the cost, so it has to clearly earn it).

Run each arm five times, compare each to the baseline band, and read the near/far
delta *per arm*: temperature 0 may be flatter on the near-domain cases while
costing recall on the far ones, which is the whole reason to test it.

**Arm 2 costs you models, and that is part of what it has to earn.** A tier that
states a temperature cannot run Claude 4.7 or later, which rejects the parameter,
and takes only `1` on an OpenAI reasoning family. Arm 1 runs anywhere. If arm 2
wins, `promote` will refuse it — the file leaves `temperature` unset with a
rationale, and pinning a param the file deliberately leaves unset is a human
decision that owes a replacement rationale, not a silent sweep write.

### Prompts and exemplars

The category-agent prompt and its worked examples (`prompts/analyze.md` and
`prompts/exemplars/`) are the strongest
lever on recall — and the *cause* of the near/far gap, since all the examples
come from one domain (payments). Editing an exemplar or adding one from a
different domain is the most direct way to move that gap. Measure it exactly like
a sampling change.

### What extraction actually does, and what a prompt edit reaches

Ten `gpt-4o` extraction runs and three `luna` ones over the 13-case corpus,
2026-08-23. Read this before writing a prompt rule, because two of the three
written that week did nothing.

**Models rename everything.** Both models find the same architecture and label
it differently: the corpus says `entity:shopper`, both write `entity:shoppers`;
the corpus says a flow is `settlement-webhook`, luna writes `payment-webhook`
and gpt-4o writes `post-webhook` for the same two endpoints. Roughly a third of
what reads as "missed one element, invented another" is one element under two
names, charged on both sides. #293 folds the flow label out of the score for
this reason and folds nothing else, because nothing else has a structural key.

**Models drop the elements that only ever act.** An element that initiates a
flow and never receives one is kept about 42% of the time, against 60% for
everything else — lower in every run of both sets. The telling part is that the
extraction writes `flow source 'process:store-server'`, the exact ID the corpus
uses, and never declares the store server. It is not a naming problem and not a
misunderstanding. A source describes an initiator by what it does — "every store
server asks the deploy controller once a minute" — so it reads as behaviour and
never reaches an inventory of structure. `initiator_recall` is the reading that
isolates it. It reads the graph's pure source nodes, so an actor that also
receives a callback sits outside its denominator; `actor_recall` is the plain
count over external entities, and both credit an element found under a reader's
alias, through the alignment.

**Models follow a contradiction rather than resolving it.** `extract.md` said
"write `unknown` where the text is silent" a dozen times, then gave `assets` a
closed vocabulary that rejects `unknown` and said nothing about the exception.
gpt-4o wrote `unknown`, which fails the whole model. It was following the
instruction into the one place it does not apply. **Check a new rule against
every closed vocabulary before shipping it** —
`test_every_extraction_failure_mode_is_declared` is that check.

**A prompt edit may simply not land, and you cannot tell which kind you have
written.** Closing the `unknown` contradiction worked: that failure appeared
once in 13 case-runs before and never in 65 after. Two edits asking for more
complete inventories did not, at either step — neither where the model notices a
dangling endpoint nor where the omission happens. Both were explicit and gave
examples. Neither moved the number at all.

So budget for a null result. An edit that removes a contradiction is a different
kind of change from one that asks for more thoroughness, and only the first has
worked here.

**Reading is strong; judgement is weak.** Same models, same documents:
`store.encryption_at_rest` agrees 100%, `boundary.kind` around 90%,
`process.assets` **4-15%**. The split is whether the answer sits in the text or
needs a judgement call — and `extract.md` gives the asset vocabulary without
ever saying what a tag denotes. Expect any attribute that asks "what matters
here" to score like `assets` until something scopes it.

**Price does not buy extraction quality.** `gpt-4o` costs about five times
`gpt-5.6-luna` and scored the same on element recall, worse on attributes, and
produced 8 structurally invalid models where luna produced none. One task on one
corpus, so do not generalise it — but do not assume the pricier model extracts
better either.

**Models emit structurally broken references.** One luna lane agent produced a
flow ID with its own label glued on twice. No instruction anticipates that; the
fail-closed join is what catches it.

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

## Step 5 — Re-run and compare

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

## Step 6 — Promote the winner

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

  temperature:         unset
  top_p:               unset
  max_output_tokens:   64000
  ...

  fingerprint:
    792c8e41...
```

Gemini is the example because one had to be. It is the one profiled family
whose served build differs from the requested route, which is what this preview
exists to show. On Claude, both lines hold the same string.

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
`ANALYSIS_SAMPLING_*` override, since an override changes the fingerprint — the
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
`ANALYSIS_REQUIRE_CERTIFIED` and it withholds the report rather than failing the
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

The shipped file still states no temperature, so each tier decodes at its
model's own default. Tuning the per-tier values to something better is exactly
the loop above — run it once you have live credentials and the baselines to
measure against.

## Choosing a model to sweep with

A sweep costs money and a cheap model costs less of it. What a cheap model can
answer is a narrower question than it first appears, and the two sweeps recorded
below are what the distinction rests on.

**Use the tier you would ship for any number about quality.** Recall, precision,
groundedness, critic yield and anything derived from them are facts about the
model that produced them. They do not transfer down the capability range, and a
number taken on a cheap model describes a deployment nobody runs.

**A cheap model is the right instrument for a different class of question**:
does the harness work end to end, can this shape occur, does a gate hold, what
does a sweep cost. Every one of those is a question about machinery rather than
judgement, and the machinery is the same whoever is behind it.

### What two vendors showed

`claude-opus-4-6` on 2026-08-14 (12 cases) and `gpt-5.6-luna` on 2026-08-23 (13
cases), both `analysis` mode, STRIDE only. **The two differ by vendor,
capability, corpus size and eight days of prompt edits at once**, so read them as
two observations rather than a controlled comparison.

The mechanical layer did not move:

| | opus | luna |
| --- | ---: | ---: |
| mis-shape at `merge_drafts` | 0 | 0 |
| structural failures | 0 | 0 |
| unverified-quote rate | 2.0% | 3.4% |

Judgement moved a great deal:

| | opus | luna |
| --- | ---: | ---: |
| grounds per threat | 3.34 | 2.73 |
| **quoteless threats** | **13%** | **44%** |

**The branch mix is the capability tell.** Luna's 914 grounds were
`unknown-attribute` 408, `quote` 264, `derived-fact` 216, `absent-attribute` 26.
Naming an unknown attribute costs no reading; finding the submitter's own words
and quoting them does. A weak model takes the cheap branch, and `quoteless_rate`
is where that shows up first — before recall does, and without a reference set.
Watch it when you change tier.

**A weak critic destroys signal.** On luna the critic killed 12% of drafts,
caught 0% of the ones the reference set marks rejected, and destroyed 10% of
real findings. The critic seat is the last place to economise.

### The stress-test argument, and its limit

For a *can this fail* question a cheap model is a stronger instrument than a
capable one: if the model most likely to emit a malformed draft emits none, the
residual is not being hit. That is why the zero mis-shape rate above is worth
more from luna than it would be from opus.

**The argument weakens wherever the weak model avoids the risky path.** Luna
quoted on 56% of its threats against opus's 87%, so it exercised the quote
ladder less per threat, and its 3.4% rests on 9 failures out of 264. A zero from
a model that never took the branch is not evidence about the branch.

### Two things that are not model comparisons

- **The fired half of coverage is deterministic.** `116/143 rule evaluations
  fired` against a previous `104/144` is a fact about the corpus and the rules,
  not about the model. Only the *cited* half moves with the model.
- **`extraction` mode grades a different tier.** `analysis` seeds the blessed
  model at `prepare`, so a poor extraction does not reach the lane agents. Luna
  extracted badly — 63% attribute agreement, 0.19-0.50 recall — and the analysis
  numbers above are unaffected by it.

### Cheap per token is not cheap per answer

Luna billed reasoning tokens at 39% of completion on the analysis sweep and 33%
on extraction, and its mean lane latency was 21-43 seconds — no faster than a
frontier model. The saving is real and the wall-clock is not, so a cheap tier
buys budget rather than time.
