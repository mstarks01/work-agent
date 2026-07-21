---
id: 022
title: "Author the phase-1 golden corpus and SME blessing workflow"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Author the phase-1 golden-case corpus decided by [Golden-case eval suite design](009-eval-suite-design.md) — content first, harness code second (ticket 023 fits the loader and scorer to this layout, not the reverse).

**Six cases**, each 8–20 elements so its reference set is exhaustively enumerable by a human (decision 5):

- one payment/checkout system — the **control** case, near-identical in shape to `analyst.md`'s shared exemplar
- three structurally far from it — an IoT fleet, a batch data pipeline, an ML inference service
- two OWASP Threat Model Cookbook conversions

Without the payments control there is nothing to subtract from and the exemplar-domain-bias delta is unmeasurable, so it is not optional (decision 6).

**Two artifacts per case** (decision 1):

- `model.json` — a blessed `SystemModel`, **bootstrapped from a real `extract` run and then SME-corrected**, not hand-authored (decision 2). Must pass the shipped validator. Keep the bootstrap→blessed diff per case as recorded signal about what `extract` habitually gets wrong.
- `threats.*` — a list of `ReferenceThreat`: `category`, `affected_element_ids` (all must resolve in that case's `model.json`), `claim` (one sentence, attacker-action phrasing), `tier` (`must-find` | `expected`), `severity` (`likelihood`/`impact` only — no band, it derives), `notes` (SME rationale, never scored).

Plus the source input text per case, and its `source_sha256`.

**Blessing workflow** — one-time, offline, before a case merges; nothing interactive and nothing at runtime (per-analysis human review stays out of scope). An SME signs off on both artifacts in one reading session, one PR, one approval. The SME corrects the bootstrapped model by working a checklist against the **source text**, never against the candidate model — that is the whole mitigation for the anchoring risk in decision 2. Document the workflow itself as part of this ticket so the phase-2 expansion and the corpus feedback loop (decision 11) can follow it.

**Judge calibration fixtures, authored in the same session** (decision 13): ~100 candidate pairs hand-labelled match/no-match. These are the ground truth for the ≥90% judge–human agreement bar, and they persist as the fixtures that let ticket 023's scorer be unit-tested offline with zero Vertex calls.

Deliberately **not** in phase 1: the two adversarial/degenerate cases (sparse-input `needs-info`, and validity-gate failure exercising `repair` → `reject`) and the remaining six cases. Deferred to ticket 025 (decision 19) because the binding cost is serialized SME time.

Internal-system cases carry a sanitization obligation this ticket owns: no real hostnames, credentials, or customer data in a fixture that lives in the repo.

## Answer

Resolved 2026-07-21. Corpus, blessing workflow and judge fixtures authored under `evals/`; `python evals/verify_corpus.py` is green on all six cases and the fixtures, and the service suite is unaffected (314 passed, 1 skipped).

**Layout** (ticket 023 fits its loader to this, not the reverse):

```
evals/README.md                       layout contract, corpus table, caveats
evals/BLESSING.md                     the SME workflow
evals/verify_corpus.py                mechanical checks, credential-free
evals/corpus/<NN>-<slug>/{source.md,model.json,threats.json,corrections.md,case.json}
evals/judge_calibration/{build_pairs.py,pairs.json}
```

Nothing here is packaged — the wheel takes `src/stride_service` only, so the corpus and fixtures cannot reach the production image (decision 14's rule, applied to the corpus as well as the judge prompt).

**The six cases**, all 8–20 elements, all passing the shipped validator, all deriving at least one boundary crossing, all carrying at least one reference threat in every STRIDE lane:

| Case | Elements | Refs | Proximity | Provenance |
|---|---|---|---|---|
| 01-payments-checkout | 14 | 21 | **near** (control) | internal, synthetic |
| 02-iot-fleet-telemetry | 19 | 18 | far | internal, synthetic |
| 03-batch-data-pipeline | 16 | 17 | far | internal, synthetic |
| 04-ml-inference-service | 16 | 18 | far | internal, synthetic |
| 05-cookbook-queue-webapp | 16 | 17 | far | OWASP cookbook, Threat Dragon "Demo Threat Model", CC-BY 4.0 |
| 06-cookbook-online-game | 20 | 18 | far | OWASP cookbook, "Battle Royale Game Flow Diagram" (pytm), CC-BY 4.0 |

The two cookbook conversions are real entries from `github.com/OWASP/threat-model-cookbook`, cited with their licence in `case.json`. Case 06's source diagram is 32 elements, over the band that makes ground truth exhaustively enumerable, so it converts a scoped subset (the player and moderation paths) and records what was dropped and why the removal changes no remaining element's attributes. Cookbook material is diagram-shaped and the service's input is text, so conversion means rendering the diagram as submitted prose. Case 05's original Threat Dragon annotations were used only as a check that the blessed set misses nothing a human modeller found — never copied in, since the reference set has to be written against our schema, tiers and severity model.

**Far-domain selection is deliberate about what "far" means.** Each far case carries at least one trust shape the payments exemplar has no instance of: physically accessible devices with one shared credential and a pull-based firmware path (02); file provenance rather than caller identity, and availability failures that are silent staleness rather than downtime (03); an artifact loaded as code, plus model-mediated cross-tenant disclosure (04); an asynchronous queue as the trust hand-off (05); the operator's own code running on hardware the operator does not control (06). If exemplar anchoring biases recall, those are the lanes where it should show.

**Bootstrap provenance — the one place this ticket departs from decision 2.** No Vertex credentials and no `gcloud` exist in this environment, so the real `extract` node could not be run. Confirmed with the user, the candidate models were produced by an agent stand-in running the shipped `prompts/extract.md`. The blessed models are unaffected — blessing is against the *source text*, which is the whole anchoring mitigation — but the `corrections.md` diffs are signal about the prompt rather than about the pinned Flash model's own blind spots. Every `case.json` records `"bootstrap": "agent-stand-in"`, the caveat is stated in both `README.md` and `BLESSING.md`, and re-running against the real node is now an explicit line item on [Eval phase 2](025-eval-phase-2.md).

**The corrections are already a usable error taxonomy.** 37 recorded corrections across six cases, with a `## Signal` section per case. The most repeated failure is **a stated qualifier stranded in `source_excerpt`** — "shared", "never rotated", "does not check" — that never reaches an attribute an analyst reads (cases 02, 03, 05); the most repeated *structural* failure is **elements dropped when they sit off the main narrative path** (02, 06). Also recorded: direction reversal on pull/poll flows (02, 05), invented controls the text never stated (01, 02, 03), one **invented absence** — a control asserted *missing* rather than unknown, which is worse, since analysts file confident findings on it (04) — transport values written into the `authentication` field (06), invented cardinality from a count (03), and type assignment following an element's name rather than its described behaviour (04, 05). This is what decision 2 wanted the diff kept for; it is the raw material for weighting the extraction eval, and it is why re-recording it against the real node is worth doing.

**Reference threats: 109 total.** `claim` is attacker-action phrasing throughout, since that is the judge's matching target and a claim phrased as a missing control gives it nothing to compare. Severity carries `likelihood`/`impact` only — the band derives. **61 of the 109 are `must-find` (56%, 9–11 per case)** — flagged here as the calibration most likely to move at the first blessing review. Each was tiered on decision 4's test, *missing it means the tool does not work*, judged per threat and not against a target rate; but 56% is high against a published off-the-shelf baseline recall under 0.30 (ticket 001 §2), and phase 2 promotes must-find recall to a hard per-case gate with a 100% target. If the baseline sweeps say the gate is unreachable, the honest fix is re-tiering specific threats with a reason — not lowering the target, and not quietly. Re-tiering is a blessing-pass action; steps 4 and 6 of `BLESSING.md` cover it. Same-element threats are deliberately kept distinct across lanes (read vs. modify on one flow), which is the distinction both analysts and judges most often collapse.

**Judge calibration: 129 hand-labelled pairs, 76 match / 53 no-match**, weighted toward hard negatives — same element, same lane, *different attacker action* — because a judge that matches too readily inflates recall silently, which is the expensive direction of error. The set deliberately includes pairs that differ only in which element they cite (labelled **match**, since matching is decided on the claim and element agreement is scored separately, decision 8) and candidates asserting facts the model does not support (labelled **no-match**; downstream those are `ungrounded`, the one gating bucket). The labels live in `build_pairs.py` with a rationale each; `pairs.json` is generated, and reference claims bind to the corpus **verbatim by index**, so rewording a reference cannot silently detach a fixture from what it was labelled against — `verify_corpus.py` fails when it does.

**`verify_corpus.py` is the lint spec for ticket 023**, the same relationship ticket 019's script had to ticket 020. It checks: case metadata completeness and `source_sha256` against `source.md`; model validity through the shipped `parse_and_validate`; the 8–20 element band; that at least one boundary crossing derives; that every `affected_element_ids` entry resolves (mirroring ticket 020's exemplar guard); legal categories, tiers and ratings; one-sentence attacker-action claims; every lane populated; at least one must-find per case; every severity pair derivable through the shipped `derive_severity_level`; and, for the fixtures, that reference claims match the corpus verbatim, labels are legal, and neither label falls below 30% of the set. Vocabularies are imported from `report.py` and `validation.py` rather than restated, so the corpus cannot drift from the shipped schema.

**Sanitization** (this ticket's obligation): the four internal cases are synthetic systems written for this corpus rather than sanitized copies of real ones — nothing real to leak by omission. `BLESSING.md` states the rule for future cases: sanitize before the text is written down, never afterwards.
