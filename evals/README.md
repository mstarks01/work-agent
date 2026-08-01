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
    threats.json                the reference threat set, keyed to model.json's IDs
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
| `harness/calibration.py` | Judge-vs-human agreement over the labelled fixtures. |
| `harness/certify.py` | Promoting a winning configuration: rewrites `config/sampling.toml` and records its fingerprints as blessed. The certification check itself lives in the service (`stride_service.certification`), which this imports. |
| `harness/modes.py` | The three run modes over the shipped graph. |
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
tool's published figures.

- **must-find recall** — did the tool find the threats a case marks as
  essential? Reported **per case**, never averaged: an average hides one case
  failing completely, which is the failure that matters most.
- **lane accuracy** — was each threat filed under the right STRIDE category? A
  misfiled threat counts as a category error, not a near-miss.
- **element accuracy** — did the threat cite the right element? Scored, but never
  used to reject a match — citing the process where the reference cited the flow
  at its endpoint still counts as the same threat.
- **ungrounded rate** — of the threats the tool produced that aren't in the
  reference set, how many assert facts the model doesn't support? This is the one
  "extra threat" bucket that counts against the tool; a plausible, grounded extra
  does not.
- **critic yield** — always read as a **pair**: how many junk threats the critic
  removed (good) *against* how many real threats it removed (bad). A kill count
  on its own tells you nothing about which of those two is happening.
- **near/far exemplar delta** — see below.

## The corpus

Twelve cases, each sized so a human can enumerate its threats exhaustively
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
