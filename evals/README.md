# Evals

The golden-case corpus and its fixtures, designed in wayfinder ticket 009 and
authored in ticket 022. The harness that consumes them is ticket 023.

Nothing in this tree ships in the production image: the corpus, the judge prompt
and the scorer are eval-side by design (ticket 009 decision 14), and the package
build only takes `src/stride_service`.

## Layout

```
evals/
  README.md                     this file
  BLESSING.md                   the SME workflow that produces a case
  verify_corpus.py              mechanical checks over everything below
  corpus/<NN>-<slug>/
    source.md                   the submitted text
    model.json                  blessed SystemModel (passes the shipped validator)
    threats.json                reference threat set, against model.json's IDs
    corrections.md              bootstrap → blessed diff and its signal
    case.json                   metadata, provenance, source_sha256
  judge_calibration/
    build_pairs.py              the hand labels (edit this)
    pairs.json                  generated fixtures (never hand-edit)
  config/judge.toml             the separately-pinned judge (ticket 009 dec. 12)
  prompts/                      the judge's prompts — NOT the shipped prompts/
  harness/                      the scorer and the eval modes (ticket 023)
```

The harness was fitted to this layout, not the reverse — content before code,
the same order tickets 019/020 used.

## The harness

| Module | What it owns |
|---|---|
| `harness/reference.py` | `ReferenceThreat` and the fail-closed corpus loader |
| `harness/structural.py` | Tier 1 gates — the only ones that block in phase 1 |
| `harness/judge.py` | the pinned judge, its two calls, and the `Judge` seam |
| `harness/scorer.py` | lane prefilter → judge → assignment → buckets → severity |
| `harness/calibration.py` | judge-vs-human agreement over `judge_calibration/` |
| `harness/modes.py` | the three eval modes over the shipped graph |
| `harness/run.py` | the CLI |

Sampling is **not** here: `config/sampling.toml` at the repo root is read by
production and the eval suite alike (ticket 009 decision 15), because grading a
configuration you do not ship is how a suite goes green while production
drifts. The judge's own model and temperature are pinned separately in
`evals/config/judge.toml`, since a judge upgrade re-scores history.

Every artifact carries a `models` block: the tier and judge strings the run
asked for, their config versions, and `judge_served` — the model versions
Vertex reported actually serving the judge calls. Ticket 026: Gemini 2.5+
stable identifiers name the current build rather than a frozen one, so *what
answered* is what makes two runs comparable. Two different `judge_served`
values across runs mean the build moved; that is a model change, not a
regression, and nothing else in the artifact would show it.

## Running

```sh
python evals/verify_corpus.py                 # check every case and the fixtures
python evals/verify_corpus.py --write-sha     # restamp source_sha256
python evals/judge_calibration/build_pairs.py # regenerate pairs.json

pytest tests/test_evals_*.py tests/test_corpus_lints.py   # offline, no credentials

# live: needs ADC for Vertex (IAM, never API keys)
python -m evals.harness.run run --mode analysis --out artifact.json
python -m evals.harness.run run --mode extraction --case 01-payments-checkout
python -m evals.harness.run calibrate --out agreement.json
```

The loop these commands drive — establishing a baseline band, changing one
lever, comparing against the band, and promoting a winner — is
[`TUNING.md`](TUNING.md).

`run` exits non-zero only on **Tier 1 structural** failures. Must-find recall,
lane and element accuracy, the ungrounded rate, the severity confusion and the
near/far exemplar delta and critic yield are all computed, printed and written
to the artifact — and none of them block, until ticket 032 has the ~5 baseline
sweeps that say what normal looks like. `calibrate` exits non-zero below the 90% agreement bar;
failing it means the judge prompt needs work, not a lowered bar.

## Metrics, and what they are not

- **must-find recall** — per case, not aggregate: an aggregate hides one case
  failing completely, which is the failure that matters.
- **lane accuracy** — observable only because unmatched threats get a
  cross-lane pass. A misfiled threat is recorded as a lane error and the
  reference stays a miss; ticket 013 rejects misfiled threats rather than
  recategorizing them.
- **element accuracy** — scored, never a prefilter. A threat that cites the
  process where the SME cited the flow at its endpoint still matches.
- **ungrounded rate** — the only gating bucket among unmatched threats.
  `valid-unlisted` is explicitly *not* a failure, and recurring entries are
  surfaced in the artifact for promotion into the reference set.
- **`needs-info`** — never a false positive. Its own bucket, never adjudicated.
- **critic yield** — always read as a *pair*. `killed-ungrounded` is the critic
  earning the most expensive node in the graph; `killed-real` is the same critic
  destroying findings that matched a reference, and it is the number that can
  veto ticket 004's generator-critic pattern outright. A kill count on its own
  says neither. Both come from scoring the pre-critic draft union and the
  report through the *same* scorer on the same claim string (the title), so the
  two sides are comparable by construction; the second pass replays memoized
  judge rulings and costs almost nothing. Comparators: Semgrep's assistant
  kills ~20% at 92–96% agreement with human triage, unfiltered LLM enumeration
  runs ~86% raw false positives.

## Phase-1 corpus

Six cases (ticket 009 decision 19), each 8–20 elements so its reference set is
exhaustively enumerable by a human.

| Case | Domain | Exemplar proximity | Provenance |
|---|---|---|---|
| `01-payments-checkout` | payments | **near** | internal (synthetic) |
| `02-iot-fleet-telemetry` | IoT fleet | far | internal (synthetic) |
| `03-batch-data-pipeline` | batch data | far | internal (synthetic) |
| `04-ml-inference-service` | ML serving | far | internal (synthetic) |
| `05-cookbook-queue-webapp` | web app + queue | far | OWASP Threat Model Cookbook (CC-BY 4.0) |
| `06-cookbook-online-game` | online game | far | OWASP Threat Model Cookbook (CC-BY 4.0) |

Case 01 is the **control** and is not optional. All 18 analyst exemplars in
`prompts/` are anchored on one fictional payment service, and few-shot anchoring
on a single domain can bias recall toward its threat shapes. That bias is not
measurable as an absolute score — only as the **delta** between near- and
far-exemplar recall. Without case 01 there is nothing to subtract from.

The delta is a tracked, deliberately **non-gating** number: a large delta is a
finding to act on, not a build to break.

The corpus checks are credential-free and deterministic, so they run on every
PR (ticket 009 decision 17); `tests/test_corpus_lints.py` runs
`verify_corpus.py`'s checks in the test job, and the script stays runnable by
hand for corpus authors.

## Caveats to carry forward

- **Bootstrap provenance.** Phase-1 candidate models were produced by an agent
  stand-in running `prompts/extract.md`, not by the pinned `extract` node, so
  the `corrections.md` diffs are signal about the prompt rather than about that
  model's blind spots. See BLESSING.md, "Bootstrapping without credentials".
- **Judge-relative metrics.** Once scoring exists, recall and precision numbers
  are relative to the judge and its prompt. Valid for tracking movement and
  comparing configurations; not absolutes, and not comparable to published
  figures from other tools.
- **Deferred to phase 2** (ticket 025): the two adversarial cases (sparse input
  yielding `needs-info`, and a validity-gate failure exercising `repair` →
  `reject`) and the remaining six cases. The binding cost is serialized SME
  time, not code.
