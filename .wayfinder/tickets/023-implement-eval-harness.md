---
id: 023
title: "Implement the eval harness, ReferenceThreat, and scorer"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: [022]
---

## Question

Build the phase-1 eval spine decided by [Golden-case eval suite design](009-eval-suite-design.md), fitted to the corpus layout ticket 022 authored.

**`ReferenceThreat`** — an eval-side Pydantic model, deliberately **not** `DraftThreat` and not in the shipped service package (decision 3). Fields per ticket 022. Import `StrideCategory`, `Rating`, and severity derivation from `report.py` so the vocabularies cannot drift. Lint: every `affected_element_ids` entry resolves in its case's blessed model, mirroring ticket 020's exemplar guard.

**Corpus loader** — reads cases from the ticket-022 layout, fail-closed in the shape `MarkdownLoader` established.

**Three eval modes over one corpus** (decision 1): extraction (text vs blessed model), analysis (blessed model injected at `prepare`, vs reference threats), end-to-end (text → report).

**Scorer**, in the order the standing principle demands — mechanical first, judgement only where nothing else will do:

1. mechanical lane prefilter — candidates must share a STRIDE category (decision 7a)
2. per-pair LLM judge on `claim` equivalence within lane; binary match + one-line rationale; **randomized pair order** (decisions 7b, 12)
3. mechanical one-to-one bipartite assignment (decision 7c)
4. unmatched threats adjudicated into `ungrounded` / `valid-unlisted` / `noise` (decision 9)
5. mechanical severity-calibration confusion over matched pairs via `derive_severity_level` — no judge (decision 8)

Reported metrics: must-find recall, expected-tier recall, lane accuracy, element accuracy, ungrounded rate, `needs-info` bucket. Element agreement is **scored, never used as a prefilter** (decision 8).

**Judge prompt lives in the eval tree, not `prompts/`** (decision 14) — it satisfies none of ticket 020's lints and must not ship in the production image. Judge model string is **pinned and versioned separately from the tiers** (decision 12).

**Pinned sampling config** (decision 15): temperature becomes a versioned config value in the `config/` tree alongside `model_tiers.toml`, **shared by eval and production**, defaulting to `0` — a knob, not a hardcoded constant, so the Self-MoA path stays open.

**Gating**: Tier 1 structural only — report parses as `StrideReport`, refs resolve, IDs unique with correct category letters, severity bands match `derive_severity_level`, summary counts consistent. Must-find recall computes and reports but does **not** block (decisions 16, 19).

**Offline-testable by construction**: the scorer must be unit-testable against ticket 022's ~100 labelled pairs with zero Vertex calls, since that is what the credential-free PR job runs (decision 17).

Verify the ≥90% judge–human agreement bar against those fixtures (decision 13). Failing it means the judge prompt needs work, not ship-anyway.

## Resolution

Resolved 2026-07-21. Shipped as `evals/harness/` (7 modules, nothing in the wheel — the build still takes only `src/stride_service`), plus one production module the design demanded be shared. Suite 395 green, 81 new tests, every one of them credential-free.

**`ReferenceThreat`** is `evals/harness/reference.py`, an eval-side Pydantic model with the six ticket-022 fields and no others; `StrideCategory`, `Rating` and `derive_severity_level` are imported from `report.py`, so `ReferenceSeverity.level` is the shipped matrix rather than a second copy of it. The dangling-reference lint runs at **load** time, not as a separate check: `load_case` refuses a case whose reference cites an element the blessed model lacks, which is stronger than linting it, because a silently dropped reference lowers the recall denominator and nobody notices a metric that improved.

**The scorer** (`scorer.py`) runs the five steps in the decided order, and two of them turned out to carry decisions the ticket only implied:

- **Lane accuracy needed a mechanism, not just a metric.** In-lane matching cannot observe a misfiled threat *by construction* — that is the point of the prefilter. So unmatched-on-both-sides threats get a bounded **cross-lane pass**, and a cross-lane match is recorded as a `LaneError`: the reference **stays missed** (ticket 013 rejects misfiled threats rather than recategorizing them) and the threat is **not** also adjudicated, so one mistake is counted once.
- **Assignment ties are broken toward `must-find`.** Maximum bipartite matching (Kuhn's, no new dependency) is not unique when several references could consume the same threat. The matching is maximum either way, so the tie-break costs nothing and settles it in favour of the references the Tier 2 gate will depend on.

**The judged half is a seam.** `Judge` is a protocol with two calls — claim equivalence and bucket adjudication — so the entire scorer is exercised offline against a stand-in replaying the SME's 129 recorded labels. That is the credential-free PR job, and it is why `candidate_claim(threat)` is the produced threat's **`title`**: ticket 019 defines a title as one scannable line naming the attacker action and its target, which is the register the reference `claim`s and the calibration fixtures are written in. Grading the 4000-char `description` would grade prose no one asked the model to reproduce, and — decisively — would make the offline judged task a *different* task from the live one, so the agreement number would not transfer.

**Judge pinning** lives in `evals/config/judge.toml` (`version`, `model`, `temperature`, `order_seed`), validated through ticket 007's own `validate_model_string`, with **no env-var override**: changing the judge is a re-baselining event, not a deploy knob. Prompts are `evals/prompts/judge_{claim_equivalence,adjudication}.md`, and a lint asserts nothing named `judge*` ever appears in the shipped `prompts/` tree.

**Sampling is the one piece that had to ship in production** (decision 15): `config/sampling.toml` + `stride_service.sampling`, bound onto **all nine** LLM nodes via `generate_content_config`. No env override on purpose — an ops-tunable temperature is exactly how the suite goes green while production drifts. `top_p` and thinking budget stay unset: pinning an unmeasured value would look like a decision nobody made.

**Two structural additions to `graph.py`, both to avoid a second copy of the topology.** `build_pipeline` gained an `entry` parameter: `prepare` is decision 1's analysis mode (blessed model seeded at `valid_model`, extraction half absent from the graph entirely), and `extract-only` runs `extract` and stops, since spending six analysts and a critic to score an extraction is six kinds of noise on one number. A duplicated eval-side graph would have drifted from the shipped one within a ticket or two.

**Gating is Tier 1 only, and it is deliberately not delegated.** `structural.py` re-asserts every property `StrideReport` already enforces, because a gate that would silently weaken if someone relaxed the model is not a gate, and because a raw payload has to be gradeable *before* it is known to parse. A failing payload reports the validator's own messages, not an error count — the artifact is what a human reads to fix the run.

**One thing did not happen, and it is the same constraint ticket 022 hit.** No Vertex credentials exist here, so the **≥90% judge–human agreement bar has never been run** (decision 13). The check, its fixtures, its two error directions and its non-zero exit are all implemented and unit-tested against replay judges; what is missing is the one live execution that produces a number. Until it runs, the harness's metrics are judge-relative to a judge whose calibration is **unmeasured** — weaker than "judge-relative", and not to be quoted even internally. Recorded as a line item on [Eval phase 2](025-eval-phase-2.md).

Unblocks [Wire the eval suite into GitHub Actions CI](024-ci-wiring-evals.md), whose offline job is `pytest tests/test_evals_*.py tests/test_corpus_lints.py` and whose live job is `python -m evals.harness.run`.
