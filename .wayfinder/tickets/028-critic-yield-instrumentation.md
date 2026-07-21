---
id: 028
title: "Instrument and score critic yield"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
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

## Resolution

Resolved 2026-07-21. Shipped as `evals/harness/critic_yield.py`, wired through
`modes.py` and `run.py`, credential-free and non-gating. Suite 414 passed /
1 skipped; `verify_corpus.py` clean.

**1. The shape question: drafts score as drafts, with no promotion.** The
ticket left open whether the pre-critic side scores as `DraftThreat` or through
a promotion, and the answer is neither a promotion nor a parallel scorer —
**`score_case` was widened to take `DraftThreat`**, which is what `Threat`
already inherits from ([Implement prompt loading, DraftThreat, and prompt
lints](020-implement-prompt-loading.md) made it the base class precisely so
promotion would be additive). Promotion was rejected on a specific ground, not
on taste: the two fields a draft lacks are `verdict` and `confidence`, which
are *the critic's own outputs*, so synthesizing them in order to measure the
critic decides the answer by fiat. In particular the `needs-info` adjudication
bypass would have to be either granted or denied to every draft, and either
choice moves the ungrounded count on the side the instrument reads.

Only one line in the scorer actually depended on `Threat`, and it is now
`_is_needs_info`, which asks whether the threat carries a verdict at all. The
decision-9 bypass is therefore **inactive before the critic has ruled** — the
honest reading of a set nobody has ruled on — and unchanged after.

**2. Comparability is held by construction, not by discipline.** Both sides go
through the same `score_case` and the same `candidate_claim` (the `title`), so
the failure the ticket warned about — a different claim string on either side
making the two numbers incomparable — cannot be introduced without changing one
function that both calls share. `candidate_claim` moved onto the draft base
class for the same reason.

**3. A memoizing judge, which turned out to be correctness and not just cost.**
The critic returns exactly the drafts it was given ([`assemble_threats`
enforces it](021-adk-graph-assembly.md)), so the post-critic set is a *subset*
of the pre-critic one and the second scoring pass re-asks questions the first
already answered. `MemoJudge` (in `judge.py`) replays them. The cost saving is
real — scoring both sides costs what scoring the superset alone cost, which is
what makes this affordable on every sweep — but the load-bearing property is
that **a judge at non-zero temperature cannot answer the same question two
different ways across the two passes**, which would surface as critic yield
rather than as judge variance. It is scoped per case, because `adjudicate`
takes a system model that is not part of the key. A critic that *edits* a title
is correctly charged a fresh question: the memo keys on the claim pair, not the
threat ID.

**4. Yield is per-killed-draft, not a difference of two aggregate rates.**
Differencing rates would have been wrong the moment the two sides' bypasses
differ (see 1). Instead each killed draft is labelled with its **pre-critic
disposition** — the state the critic actually faced — from one mutually
exclusive set: `matched-must-find`, `matched-expected`, `lane-error`,
`ungrounded`, `valid-unlisted`, `noise`, plus a `needs-info` that only the
post-critic side can produce and an `unscored` that should be unreachable and
exists so a future scorer path that stops covering the produced set shows up as
a label rather than a `KeyError` mid-sweep. `CaseScore` gained `produced_ids`
so the killed set is a set difference rather than an inference from which
buckets happen to be populated.

The two headline numbers fall out named: **`ungrounded_killed`** (the critic
earning the most expensive node in the graph) and **`matched_killed`** (the
critic destroying findings that matched a reference — the number that can veto
ticket 004's pattern), with `must_find_killed` as its sharpest form. `run.py`
prints them **on one line together**, deliberately: a kill count read alone
says nothing about which of the two is happening. `kill_precision` is
deliberately *not* the complement of `matched_kill_rate` — a killed
`valid-unlisted` draft is neither win nor loss, because reference sets are
non-exhaustive by construction and the corpus feedback loop settles those.
Aggregates **pool counts rather than averaging per-case rates**, so a case that
drafted three threats cannot outweigh one that drafted forty.

**5. Surfacing cost one state key and no production change.** `run_analysis`
and `run_end_to_end` now return an `AnalysisRun` (report + `merged_drafts`)
read off `STATE_MERGED_DRAFTS`, revalidated as `DraftThreat` on the way out of
session state exactly as `assemble_report` does, and fail-closed if an analysis
arrives with no drafts. Nothing in `stride_service` moved.

**Not verified live, same constraint as 022/023/026:** no Vertex credentials
here, so every number this instrument produces is still unobserved — the
machinery is tested offline against scripted `BaseLlm` stand-ins and a scripted
judge (13 new tests). The first real numbers arrive with [Establish baselines
and promote the gates](032-establish-baselines.md), which is also where any
threshold is derived from observed spread. Nothing here gates.

Changed: `evals/harness/critic_yield.py` (new), `evals/harness/scorer.py`,
`evals/harness/judge.py`, `evals/harness/modes.py`, `evals/harness/run.py`,
`evals/README.md`, `tests/eval_factories.py`,
`tests/test_evals_critic_yield.py` (new), `tests/test_evals_modes.py`.
