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
```

Ticket 023 fits its loader to this layout, not the reverse — content before
code, the same order tickets 019/020 used.

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

## Running the checks

```sh
python evals/verify_corpus.py            # check every case and the fixtures
python evals/verify_corpus.py --write-sha  # restamp source_sha256 after editing source.md
python evals/judge_calibration/build_pairs.py  # regenerate pairs.json from the labels
```

Credential-free and deterministic, so it runs on every PR (ticket 009 decision
17). Ticket 023 ports these checks into the offline test job.

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
