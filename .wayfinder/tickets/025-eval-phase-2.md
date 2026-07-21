---
id: 025
title: "Eval phase 2: baselines, ratchets, critic yield, corpus expansion"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
blocked-by: [023, 024, 026]
---

## Question

Complete the eval suite designed in [Golden-case eval suite design](009-eval-suite-design.md). Phase 1 (tickets 022–024) deliberately shipped the spine; everything here has its design already fixed and was deferred only because the binding cost is serialized SME time (decision 19).

**Establish baselines and promote the gates.** Run the full sweep ~5× to measure baseline and variance, then promote must-find recall to a hard **per-case** gate (aggregate hides a single case failing completely) and derive Tier 3 noise bands from the observed spread rather than guessing them. Baselines are checked-in artifacts, so moving one is a reviewable diff with a human explaining why (decision 16).

**Answer the sampling question with the instrument, not by argument** (decision 15). Same corpus at temperature 0 vs. production default vs. k=3 union-merged (ticket 001 §4.4 Self-MoA); let the far-domain cases decide. Temperature 0 plausibly amplifies exemplar anchoring, and this is the measurement that says whether it does.

**Critic yield** (decision 10). Instrument the graph to expose the pre-critic merged union so the same run scores both sides of the critic: ungrounded threats killed, and real threats killed with them. This is the direct empirical test of ticket 004's generator–critic bet against its comparators (Semgrep ~20% kill at 92–96% agreement; ~86% raw FP unfiltered).

**Reporting refinements** over the same matched pairs: element accuracy, the `noise` / `valid-unlisted` split, and the **near- vs far-exemplar recall delta** — tracked and deliberately **non-gating**, since a large delta is a finding to act on, not a build to break. That number is what finally resolves the map's exemplar-domain-bias fog line.

**Corpus expansion to 12** (decision 6), following ticket 022's blessing workflow: the remaining internal systems, two more cookbook conversions, and the two adversarial/degenerate cases — a sparse input that should yield heavy `unknown` + `needs-info`, and one that should fail the validity gate and exercise `repair` → `reject`. Those two grade behaviors nothing else in the corpus reaches.

**Re-bootstrap the phase-1 corpus against the real `extract` node.** Ticket 022 was authored with no Vertex credentials available, so each phase-1 candidate model came from an agent stand-in running `prompts/extract.md` rather than the pinned Flash node (`"bootstrap": "agent-stand-in"` in every `case.json`). The blessed models are unaffected — they are blessed against the source text — but the `corrections.md` diffs are currently signal about *the prompt*, not about that model's own blind spots, which is what decision 2 asked them to be. Re-run `extract` over each `source.md`, re-record the diff, and flip the provenance field. Cheap, and it is what makes the accumulated "what extraction habitually gets wrong" record trustworthy enough to weight the extraction eval by.

**Corpus feedback loop** (decision 11): surface recurring `valid-unlisted` threats in the run artifact for SME review and promote them into the reference sets at the next blessing pass. Non-exhaustive ground truth converges from real output, never from someone trying to be exhaustive up front. (The artifact side of this shipped with [Implement the eval harness, ReferenceThreat, and scorer](023-implement-eval-harness.md) — `unlisted_for_promotion` — so what remains here is the human pass over it.)

**Run the judge–human agreement bar for the first time** (decision 13). Ticket 023 implemented the check, its fixtures and its gate, but the same missing-credentials constraint that hit ticket 022 means it has **never been executed against the real judge**: no number exists yet. `python -m evals.harness.run calibrate` must clear ≥90% before any recall figure from this suite is quoted, even internally. Failing it is a judge-prompt authoring task, not a reason to lower the bar. Until it runs, every metric the harness prints is judge-relative to a judge whose calibration is unmeasured — a strictly weaker claim than "judge-relative".

**Run the first live check of the model strings, and plan the 2.5 → 3.x move against a deadline.** [Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md) corrected `gemini-2.5-{pro,flash}-002` (a Gemini 1.5-era convention that resolves to nothing) to the stable identifiers `gemini-2.5-pro`/`gemini-2.5-flash` on documentary evidence only — nothing here has spoken to Vertex. The first-run checklist lives in `.github/WORKLOAD_IDENTITY.md`; run it before anything else in this ticket, since a 404 makes every sweep below impossible. Then: **Gemini 2.5 retires on Vertex 2026-10-16.** The 3.x models were preview-stage as of 2026-07-21 and so fail the restated pin rule, but the baseline sweeps this ticket establishes are exactly what ticket 007 decision 4 requires before a tier string moves — so the generation migration is this ticket's work, on a hard external deadline, not an open-ended sweep. Every artifact now carries `models.judge_served`; if a metric moves across two runs with different served versions, the model moved, not the prompt.

## Answer

**025 was fog written as a ticket, and is resolved by decomposition rather than
by execution.** It was created as a placeholder when
[Golden-case eval suite design](009-eval-suite-design.md) closed and phase 1 was
cut to six cases; it accumulated nine workstreams, each of which is a session or
more. It is replaced by tickets 027-034, wired below. No measurement was
performed here — that was never available to this session.

**The blocker underneath is infrastructural, not analytical.** Verified on the
development box, 2026-07-21: `git remote -v` empty, `gcloud` not installed, no
ADC file, no `GOOGLE_*` or `STRIDE_MODEL_*` variables. Offline suite 399 passed /
1 skipped. So nothing in this repo has ever spoken to Vertex, no workflow in
`.github/` has ever executed, and *every* item in 025 that produces a number was
unreachable — the same constraint that shaped tickets 022, 023, 024 and 026, now
isolated into one ticket that can actually be handed to a human:
[Provision live infrastructure](027-provision-live-infrastructure.md).

**One bullet of 025 turned out to be already shipped and became no ticket at
all.** The "reporting refinements" item asked for element accuracy, the
`noise` / `valid-unlisted` split, and the near/far exemplar delta — all three are
live in `evals/harness/scorer.py` (`element_accuracy`, `element_jaccard`,
`_BUCKETS`, `lane_errors`/`lane_accuracy`, `exemplar_delta`) and surfaced by
`run.py`. What remains of that bullet is not code but a *run*, which is why the
near/far delta now sits inside
[Establish baselines and promote the gates](032-establish-baselines.md) as an
output rather than standing as work of its own.

**Critic yield is smaller than 025 implied, and is the one measurement-adjacent
item that is credential-free.** `stride_service.graph` already parks the
pre-critic union at `STATE_MERGED_DRAFTS`, and `modes.run_graph` already returns
the full final state, so the pre-critic side is reachable without touching the
production seam. The real open question is shape — `DraftThreat` vs `Threat` on
the scored side, keeping the judged task identical on both — which is what makes
it a ticket rather than a chore. It is deliberately **unblocked** and ordered
*before* the baseline sweeps, so those sweeps measure the critic instead of
needing a re-run.

The split, and why the edges fall where they do:

| Ticket | Blocked by |
|---|---|
| [027 Provision live infrastructure](027-provision-live-infrastructure.md) | — |
| [028 Instrument and score critic yield](028-critic-yield-instrumentation.md) | — |
| [029 Expand the golden corpus to 12](029-expand-golden-corpus.md) | — |
| [030 Re-bootstrap the phase-1 corpus](030-rebootstrap-corpus.md) | 027 |
| [031 Run the judge–human agreement bar](031-judge-calibration-bar.md) | 027 |
| [032 Establish baselines and promote the gates](032-establish-baselines.md) | 027, 028, 029, 031 |
| [033 Answer the sampling question](033-sampling-sweep.md) | 032 |
| [034 Migrate Gemini 2.5 → 3.x](034-gemini-3x-migration.md) | 032 |

Three edges are load-bearing and were the actual content of this decomposition:

- **031 blocks 032**, because baselines derived from an uncalibrated judge would
  then be ratcheted against — which launders an unmeasured judge into a gate.
  Decision 13 says no recall figure may be quoted before the ≥90% bar clears;
  making it a blocking edge is what stops that being skipped under deadline
  pressure.
- **029 blocks 032**, because baselines taken on six cases all move when the
  corpus reaches twelve, wasting the five sweeps that are the expensive part.
- **032 blocks 034**, because ticket 007 decision 4 requires tier moves to be
  eval-gated. That puts a serialized measurement chain on the critical path to a
  **fixed calendar date** — Gemini 2.5 retires on Vertex 2026-10-16 — so if
  baselines slip, the correct escalation is to compress the sweeps, not to skip
  the gate.

Note that 029 and 030 are split apart on purpose although both touch the corpus:
blessing is against the *source text* and needs no credentials (which is how
ticket 022 shipped six blessed cases with none), while re-bootstrapping candidate
provenance needs the real Flash node. Fusing them would have blocked all corpus
work behind infrastructure for no reason.

Frontier after this: **027** (HITL, hands the human a checklist), **028**
(credential-free code), **029** (HITL SME authoring). Nothing else is takeable.
