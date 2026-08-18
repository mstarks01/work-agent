# Evals

How we measure the analysis quality: a corpus of hand-blessed "golden" cases, a
scorer that compares the service's output against them, and an LLM judge that
decides when two threats describe the same thing.

Nothing in this directory ships in the production image — the corpus, the judge,
and the scorer are test-side only, and the package build takes just
`src/stride_service`. Two companion guides:

- **[BLESSING.md](BLESSING.md)** — how to author a new golden case.
- **[TUNING.md](TUNING.md)** — how to use these evals to improve the models.

## Layout

```
evals/
  README.md                     this file
  BLESSING.md                   how to author a golden case
  TUNING.md                     how to tune the models against the corpus
  verify_corpus.py              mechanical checks over everything below
  corpus/<NN>-<slug>/
    source.md                   the submitted text
    model.json                  the blessed System Model (passes the shipped validator)
    claims/<framework>.json     that framework's reference set, keyed to model.json's IDs
    corrections.md              notes on how the model was corrected, and why
    case.json                   metadata, provenance, and the declared sources
  judge_calibration/
    build_pairs.py              the hand-labelled judge fixtures (edit this)
    pairs.json                  generated from build_pairs.py (never hand-edit)
  config/judge.toml             the judge's own pinned model and settings
  prompts/                      the judge's prompts (NOT the shipped prompts/)
  harness/                      the scorer and the eval runner
```

## The harness

| Module | What it owns |
|---|---|
| `harness/reference.py` | The `ReferenceThreat` type and the fail-closed corpus loader. |
| `harness/structural.py` | The structural gates — the only checks that fail a run. |
| `harness/judge.py` | The pinned judge, its two calls, and the `Judge` seam for testing. |
| `harness/scorer.py` | The scoring pipeline: prefilter → judge → match → bucket → severity. |
| `harness/critic_yield.py` | What the critic added and removed, scored on both sides. |
| `harness/grounds.py` | What the category agents did with `grounds` — the branch mix, the padding number and the unverified-quote rate — plus the two failures the grounding path kills a case with. Judge-free. |
| `harness/coverage.py` | What each category agent was offered and how much of it its drafts cite, pooled over the sweep. Judge-free. |
| `harness/stability.py` | Run-to-run stability: which references two or more finished sweeps agree on. Judge-free, and reads artifacts rather than re-running. |
| `harness/calibration.py` | Judge-vs-human agreement over the labelled fixtures. |
| `harness/provenance.py` | What each node execution actually ran on — tier, requested route, served build, fingerprint — written into the artifact and read back by a promotion. |
| `harness/certify.py` | Promoting a winning configuration: rewrites `config/sampling.toml` and records its fingerprints as blessed. The certification check itself lives in the service (`stride_service.certification`), which this imports. |
| `harness/modes.py` | The three run modes over the shipped graph, and the extraction score: element agreement, the derived crossings, and the attributes a Candidate rule reads. Judge-free. |
| `harness/run.py` | The command-line entry point. |

Sampling parameters are **not** here: the harness reads `config/sampling.toml`
at the repo root, the exact same file production reads. Grading a configuration
you don't ship is how a test suite goes green while production quietly drifts.
The judge's own model is pinned separately in `evals/config/judge.toml` — it
names a `(vendor, model)` pair like any tier does, and has no environment
override. Changing anything in that file re-scores every past result, so treat
it as a re-baselining event rather than a dependency bump.

Every run also records which model builds the provider actually served, not just
which ones it asked for. Stable model identifiers name the current build rather
than a frozen one, so two runs that report different served versions have run on
different models — that's a model change, not a regression, and nothing else in
the output would reveal it.

Those served builds land in the artifact's `provenance` block, one entry per node
execution, which is what makes promotion a command rather than an archaeology
exercise: a fingerprint is `sha256(served build, tier sampling)`, and neither half
is recoverable from the configured tier strings. The block carries an
`artifact_version` beside it at the artifact's root; a promotion refuses any
version it does not know rather than interpreting it best-effort.

## Running

Offline — no credentials, safe on every change:

```sh
python evals/verify_corpus.py                 # check every case and the judge fixtures
python evals/verify_corpus.py --write-sha     # restamp source digests after editing a source
python evals/judge_calibration/build_pairs.py # regenerate pairs.json from the labels
pytest tests/test_evals_*.py tests/test_corpus_lints.py
```

Live — needs credentials for whichever vendor the tiers are configured to use
(see [Configuration](../docs/Configuration.md#provider-environment)):

```sh
python -m evals.harness.run run --mode analysis --out artifact.json
python -m evals.harness.run run --mode extraction --case 01-payments-checkout
python -m evals.harness.run calibrate --out agreement.json

# Several candidate judges over the same pairs, side by side. Reports agreement
# with the human labels per candidate AND agreement between candidates, which is
# what says whether a conclusion depends on the judge's vendor. See TUNING.md.
python -m evals.harness.run calibrate \
  --judge-config evals/config/judge.toml \
  --judge-config /tmp/judge-anthropic.toml \
  --out judge-comparison.json
```

A `run --out artifact.json` also writes `artifact.reports/<case>.report.json` —
one whole report for each case that finished. The artifact holds the aggregates
this harness computes; the report holds what the agents said, so a question the
metric set did not anticipate is answered offline instead of costing a second
sweep. `extraction` mode stops at the validity gate, produces no report, and
says so. Expect roughly 30–80 KB per report. These files are publishable: they
carry corpus source text, which is in this repository.

An `extraction` run prints element recall and precision per case, then the
attribute agreement split by attribute. Read the split first when the element
numbers look clean: an extraction that names every element and types none of
them correctly scores 1.00 on both, and a `kind` or an `exposure` the live
pipeline stopped producing appears only in that column. Every number in it is
an instrument. It carries no threshold and fails no run — a low agreement is a
question to take back to the source text.

`--judge-config` is offered on `calibrate` only. A scored `run` is always
measured by the judge in `evals/config/judge.toml`: pointing a sweep at some
other judge produces numbers that look like the tracked series and are not
comparable to it.

Offline again, once a sweep has been reviewed — `promote` needs no credentials,
because everything it certifies was observed during the run and written into the
artifact:

```sh
python -m evals.harness.run promote artifact.json          # preview, writes nothing
python -m evals.harness.run promote artifact.json --yes    # re-pin and bless
```

See [TUNING.md](TUNING.md#step-5--promote-the-winner).

Also credential-free, over two or more finished sweeps of the same corpus —
what one sweep cannot tell you is how much of its own number is sampling noise:

```sh
python -m evals.harness.run stability run-a.json run-b.json --out stability.json
```

A `run` fails (exits non-zero) **only** on a structural problem — a report that
doesn't parse, references that don't resolve, a severity that contradicts the
matrix, or a summary that disagrees with its own contents. Every quality metric
below is computed, printed, and written to the artifact, but does **not** fail
the run: a gate that fires before anyone knows the normal range just trains
people to bypass it. `calibrate` is the exception — it fails below the 90%
judge–human agreement bar, because a judge that disagrees with humans makes
every other number meaningless.

## What the metrics mean

Every number here is measured **by the judge** — use them to compare
configurations and track movement, never as absolute scores or against another
tool's published figures. The **grounds** measurements at the end are the
exception: they are counted mechanically and mean the same thing across judges.

- **must-find recall** — did the tool find the threats a case marks as
  essential? Reported **per case**, never averaged: an average hides one case
  failing completely, which is the failure that matters most.
- **lane accuracy** — was each threat filed under the right STRIDE category? A
  misfiled threat counts as a category error, not a near-miss.
- **element accuracy** — did the threat cite the right element? Scored, but never
  used to reject a match — citing the process where the reference cited the flow
  at its endpoint still counts as the same threat.
- **unsupported rate** — of the threats the tool produced that aren't in the
  reference set, how many assert facts the model doesn't support? This is the one
  "extra threat" bucket that counts against the tool; a plausible, grounded extra
  does not.
- **critic yield** — always read as a **pair**: how many junk threats the critic
  removed (good) *against* how many real threats it removed (bad). A kill count
  on its own tells you nothing about which of those two is happening.
- **near/far exemplar delta** — see below.

The **grounds** measurements are judge-free, and each one watches a prompt rule
that nothing enforces mechanically:

- **grounds per threat** — `analyze.md` asks for one ground per load-bearing
  fact with no padding, so this should stay low. Rising means the agents are
  filling the field rather than citing what triggered them.
- **quoteless rate** — the share of findings carrying no quote. A finding whose
  trigger was an unknown attribute or a boundary crossing is *correctly*
  quoteless, so read this **low with suspicion, not high**: a rate near zero is
  evidence the agents are manufacturing quotes to fill a required field.
- **unverified rate** — of the quotes the agents wrote, the share the shipped
  ladder (`stride_service.grounding`) could not find in the source they name.
  Denominated in quotes, never in grounds.
- **failed cases** — the two ways the grounding path kills a case, counted
  rather than allowed to abort the sweep: `mis-shape` (a `Ground` carrying a
  combination of fields no branch permits) and `fail-closed` (a threat on which
  no ground verified at all). Both remain structural failures, so a run that
  hits either still exits non-zero.

**Coverage** is judge-free too, and is a rate over the whole sweep rather than a
per-case number: it counts what deterministic code offered each lane —
candidates, elements, boundary crossings, unknown controls — against how much of
it the lane's drafts cite. Read it as *cited*, never as *considered*: an agent
that examined a lead and correctly rejected it cites nothing. The `rules fired`
column counts firings over evaluations — one rule against one case — and the
unambiguous reading of it is zero: a lane whose rules fire nowhere in the corpus
is reading a shape the corpus does not contain, or nothing at all.

**Token usage and latency** are folded per node across the whole sweep and
printed as two tables. They answer different questions about the same
executions: the dearest node is not the slowest one, and the deterministic
derivations cost no tokens while still costing the job its seconds.

**Stability** needs two sweeps, so it is its own command rather than a metric of
one run. It compares which *reference indices* each sweep matched — a corpus
coordinate that means the same thing in every run, where a produced threat's ID
and wording do not — and splits every reference into matched-in-every-run,
matched-in-some, and matched-in-none. The middle bucket is the band a one-sweep
recall number can move within while nothing has actually changed, which is what
any comparison of two other numbers has to clear before it means anything.

## The corpus

Thirteen cases, each sized so a human can enumerate its threats exhaustively
(roughly 8–20 elements). Case `01` is the **control** and is not optional: every
worked example in the shipped prompts is drawn from one payments system, and
that can bias the tool toward payments-shaped threats. You can't see that bias as
an absolute score — only as the **gap** between recall on the near-domain case
(`01`) and the far-domain cases (everything else). Without `01` there's nothing
to compare against. That gap is tracked and watched, but never fails a build.

| Case | Domain | Proximity | Source |
|---|---|---|---|
| `01-payments-checkout` | payments | **near** | synthetic |
| `02-iot-fleet-telemetry` | IoT fleet | far | synthetic |
| `03-batch-data-pipeline` | batch data | far | synthetic |
| `04-ml-inference-service` | ML serving | far | synthetic |
| `05-cookbook-queue-webapp` | web app + queue | far | OWASP Threat Model Cookbook |
| `06-cookbook-online-game` | online game | far | OWASP Threat Model Cookbook |
| `07-cicd-store-deploy` | CI/CD release | far | synthetic |
| `08-sso-identity-broker` | identity & access | far | synthetic |
| `09-cookbook-sokify-retail` | online retail | far | OWASP Threat Model Cookbook |
| `10-cookbook-generic-cms` | content management | far | OWASP Threat Model Cookbook |
| `11-sparse-shift-scheduling` | workforce scheduling | far | synthetic (sparse input) |
| `12-overclaiming-supplier-portal` | supplier management | far | synthetic (over-claiming input) |
| `13-dispatch-control-plane` | field service dispatch | far | synthetic |

The Cookbook cases come from the [OWASP Threat Model
Cookbook](https://github.com/OWASP/threat-model-cookbook) (CC-BY 4.0), converted
to the kind of prose a user would submit; each case records its exact source and
licence in `case.json`. Cases `11` and `12` are deliberately adversarial — one
with input too sparse to model confidently (which should yield `needs-info`
threats, not invented ones), one that over-claims security controls the text
doesn't justify.

The corpus checks in `verify_corpus.py` are deterministic and need no
credentials, so they run on every PR (via `tests/test_corpus_lints.py`) and stay
runnable by hand.

## Things to keep in mind

- **The metrics are judge-relative.** Recall and precision numbers move *with*
  the judge and its prompt. They're valid for tracking change and comparing
  configurations, not as absolutes and not comparable to other tools' figures.
- **Reference sets grow from real output.** They aren't meant to be exhaustive up
  front. Each run surfaces grounded, plausible threats it produced that aren't in
  the reference set; recurring ones get promoted into the reference set on the
  next blessing pass (see [BLESSING.md](BLESSING.md)). Promotion is always a
  reviewed change with a human explaining why.
