---
id: 026
title: "Verify the pinned Vertex model strings resolve"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
blocked-by: [024]
---

## Question

Confirm — against a real Vertex project, not documentation — that every model string this repo pins actually resolves, and correct them if not.

Three files pin one:

- `config/model_tiers.toml` — `flash = "gemini-2.5-flash-002"`, `pro = "gemini-2.5-pro-002"` ([Per-agent Vertex model tier assignment](007-model-tier-assignment.md))
- `evals/config/judge.toml` — `model = "gemini-2.5-pro-002"` ([Golden-case eval suite design](009-eval-suite-design.md) decision 12)

**The `-002` suffix could not be corroborated while wiring CI** ([Wire the eval suite into GitHub Actions CI with WIF](024-ci-wiring-evals.md)). Gemini 2.5's published GA identifiers on Vertex are `gemini-2.5-pro` and `gemini-2.5-flash` with no numbered suffix; `-002` is the numbering convention of the retired 1.5 generation. If that holds, every LLM node in the graph and the eval judge are pinned to strings that return `404 Publisher Model not found`, and no live run has ever proved otherwise — tickets 022 and 023 both shipped without credentials, so nothing in this repo has yet spoken to Vertex.

The check is cheap once credentials exist: `gh workflow run "Evals (live Vertex)" -f mode=extraction` exercises the `flash` node, and `calibrate` exercises the judge.

**What needs deciding, not just fixing**, is what ticket 007's "pinned, never an alias" rule means for a generation that ships no numbered versions. `gemini-2.5-pro` is the *stable* identifier — it is not an auto-updating alias in the `-latest` sense that decision rejected, but neither does it carry the version digits the rule assumed would exist. Either the rule's pinned-suffix lint (`model_tiers.py` rejects `-latest`/aliases) is already correct and the strings are simply wrong, or the rule needs restating in terms of what Vertex actually offers. Settle that, then correct all three pins and the lint together.

Also worth resolving in the same pass: Gemini 2.5 is no longer the current generation, and the tier strings are eval-gated by design ([Per-agent Vertex model tier assignment](007-model-tier-assignment.md)) — so whether to correct-in-place or correct-and-then-sweep a newer generation is a question the baselines from [Eval phase 2](025-eval-phase-2.md) answer, not this ticket.

## Resolution

Resolved 2026-07-21. The defect is confirmed and corrected; the rule is restated; the live check is inherited rather than performed, because no Vertex credentials, `gcloud`, or git remote exist in this environment.

**1. The strings were wrong, and the corroboration is conclusive.** `-002` is the *Gemini 1.5* stable-build convention (`gemini-1.5-pro-002`). Google's naming changed with the 2.5 generation: stable Gemini 2.5+ builds carry **no numeric suffix**, and the published GA identifiers are `gemini-2.5-pro` and `gemini-2.5-flash`. `gemini-2.5-pro-002` and `gemini-2.5-flash-002` name nothing, so every LLM node in the graph and the eval judge were pinned to `404 Publisher Model not found` — the whole service and the whole eval suite, neither of which had ever been executed against Vertex. Corrected in `config/model_tiers.toml` (version 1 → 2) and `evals/config/judge.toml` (version 1 → 2). The judge bump re-baselines nothing, because no baseline exists: the ≥90% agreement check has still never run.

**2. Ticket 007's "pinned, never an alias" rule is restated, not merely re-applied** — the ticket's central question. The rule assumed version digits would always exist, and its lint enforced exactly that (`_PINNED_SUFFIX = -\d{3,}$`), which meant the lint rejected every string that actually resolves and accepted only strings that do not. Worse, the bare `gemini-2.5-pro` is documented as *"an alias for the current recommended stable build"* — so for 2.5+ there is **no non-alias GA identifier to pin to at all**. 2.5's only dated builds (`-preview-09-2025`) are preview-stage, which is a worse bargain than the alias: shorter life and a preview SLA in production.

So "pinned" now means **the most specific *stable GA* identifier Vertex publishes for a generation**:

- `validate_model_string` rejects `-latest` and any string containing `-preview` or `-exp`, plus empty/whitespace-padded values; numbered builds stay legal where a generation offers them (`gemini-1.5-pro-002` still passes), so the rule doesn't have to move again when Google's convention does.
- Ticket 007's decision 4 is untouched: no cross-tier auto-degrade, upgrades eval-gated, ops retunes via `STRIDE_MODEL_FLASH`/`STRIDE_MODEL_PRO` — those overrides now pass through the restated rule identically.

**3. Reproducibility moves from the string to the record.** The old rule bought eval reproducibility with version digits; a stable identifier that names "whichever build is current" cannot. Rather than pretend otherwise, the harness now records **what actually answered**: `VertexJudge` captures `response.model_version` from every judge call (a single funnel — `_ask`), and both `run` and `calibrate` artifacts carry a `models` block with the tier strings, both config versions, the judge string, and `judge_served`. Two runs whose numbers differ across different `judge_served` values differ because the model moved — a fact nothing else in the artifact would show, and the exact fact a phantom regression gets blamed on the prompt instead. Graph nodes were already covered: `NodeRun.model` records the configured string per node in every report.

**4. Generation: correct in place, sweep later — with a deadline.** **Gemini 2.5 retires on Vertex 2026-10-16** (moved out from June 2026; Google commits to ≥6 months' notice once a Gemini 3 GA date is locked). As of 2026-07-21 the 3.x models on Vertex — Gemini 3 Flash, Gemini 3.1 Pro — are **preview-stage**, so they fail the rule this ticket just restated. Pinning them would mean shipping preview models *and* re-baselining every eval number against a baseline that does not yet exist. So: 2.5 stable now, and the generation move becomes a dated line item on [Eval phase 2](025-eval-phase-2.md) — roughly three months of runway, and phase 2 needs its ~5 baseline sweeps before it can judge a candidate anyway, which is the order ticket 007 decision 4 already requires.

**5. What was *not* done, and who inherits it.** The ticket asked for verification "against a real Vertex project, not documentation," and that could not happen here — the same missing-credentials constraint that shaped tickets 022 and 023, compounded by there still being no git remote, so the CI workflows have never executed either. The correction above rests on documentary evidence, which is strong (the naming change is documented and the old convention is generation-specific) but is not a resolving call. The live check is recorded in two places rather than resolved away: a **first-run checklist** in `.github/WORKLOAD_IDENTITY.md` (extraction dispatch exercises `flash`; `-f calibrate=true` exercises the `pro` judge; then read `models.judge_served`), and a line item on [Eval phase 2](025-eval-phase-2.md). A 404 on that first run is a config bug, not a CI bug.

Changed: `src/stride_service/model_tiers.py`, `config/model_tiers.toml`, `evals/config/judge.toml`, `evals/harness/judge.py`, `evals/harness/run.py`, `evals/README.md`, `.github/WORKLOAD_IDENTITY.md`, and three test modules. Suite 399 passed / 1 skipped; `verify_corpus.py` clean.
