---
id: 026
title: "Verify the pinned Vertex model strings resolve"
label: wayfinder:task
status: open
assignee:
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
