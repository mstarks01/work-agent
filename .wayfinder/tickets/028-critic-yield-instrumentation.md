---
id: 028
title: "Instrument and score critic yield"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

Decision 10 of [Golden-case eval suite design](009-eval-suite-design.md): score
**both sides** of the critic from one run — ungrounded threats it killed, and
real threats it killed with them. This is the direct empirical test of ticket
004's generator-critic bet against its comparators (Semgrep ~20% kill at 92-96%
agreement; ~86% raw FP unfiltered). Without it, the critic is the most expensive
node in the graph and the one with no evidence behind it.

Credential-free, and the reason this is not blocked on
[Provision live infrastructure](027-provision-live-infrastructure.md): the whole
scorer already runs offline against the 129 recorded judge labels, and the graph
runs offline against scripted `BaseLlm` stand-ins. It should ship **before**
baselines are established, so the ~5 sweeps in
[Establish baselines and promote the gates](032-establish-baselines.md) measure
critic yield rather than needing a re-run to get it.

Most of the wiring already exists — this is a surfacing job, not new machinery:

- `stride_service.graph` already parks the pre-critic union at
  `STATE_MERGED_DRAFTS`, written by `merge_drafts`.
- `evals.harness.modes.run_graph` already returns the full final state, so
  `run_analysis` can reach that key without touching the production seam.
- `score_case` already does everything needed to score a threat set against a
  case's references.

So the open question is mostly **shape**: `merged_drafts` are `DraftThreat`s, not
`Threat`s, and `score_case` consumes `Threat` (`candidate_claim` reads `title`,
which `DraftThreat` carries as the base class shipped by
[Implement prompt loading, DraftThreat, and prompt lints](020-implement-prompt-loading.md)).
Decide whether the pre-critic side scores as `DraftThreat` directly or through a
promotion, and keep the judged task **identical** on both sides — a different
claim string on either side makes the two numbers incomparable and the yield
meaningless.

Report per case and in aggregate: drafts in, threats out, references matched
before vs. after, and the two numbers that matter — **ungrounded killed** (the
critic earning its cost) and **matched-reference killed** (the critic destroying
real findings). The second is the one that can veto the pattern.

Non-gating on arrival: this is an instrument, and its thresholds — if any — are
derived in [Establish baselines and promote the gates](032-establish-baselines.md)
from observed spread, not guessed here.

Note for whoever takes this: the other half of 025's "reporting refinements"
bullet is **already shipped** and needs no work — `element_accuracy`,
`element_jaccard`, the `ungrounded`/`valid-unlisted`/`noise` split,
`lane_errors`/`lane_accuracy`, and `exemplar_delta` are all live in
`evals/harness/scorer.py` and surfaced by `run.py`.
