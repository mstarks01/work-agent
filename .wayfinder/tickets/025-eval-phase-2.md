---
id: 025
title: "Eval phase 2: baselines, ratchets, critic yield, corpus expansion"
label: wayfinder:task
status: open
assignee:
blocked-by: [023, 024]
---

## Question

Complete the eval suite designed in [Golden-case eval suite design](009-eval-suite-design.md). Phase 1 (tickets 022–024) deliberately shipped the spine; everything here has its design already fixed and was deferred only because the binding cost is serialized SME time (decision 19).

**Establish baselines and promote the gates.** Run the full sweep ~5× to measure baseline and variance, then promote must-find recall to a hard **per-case** gate (aggregate hides a single case failing completely) and derive Tier 3 noise bands from the observed spread rather than guessing them. Baselines are checked-in artifacts, so moving one is a reviewable diff with a human explaining why (decision 16).

**Answer the sampling question with the instrument, not by argument** (decision 15). Same corpus at temperature 0 vs. production default vs. k=3 union-merged (ticket 001 §4.4 Self-MoA); let the far-domain cases decide. Temperature 0 plausibly amplifies exemplar anchoring, and this is the measurement that says whether it does.

**Critic yield** (decision 10). Instrument the graph to expose the pre-critic merged union so the same run scores both sides of the critic: ungrounded threats killed, and real threats killed with them. This is the direct empirical test of ticket 004's generator–critic bet against its comparators (Semgrep ~20% kill at 92–96% agreement; ~86% raw FP unfiltered).

**Reporting refinements** over the same matched pairs: element accuracy, the `noise` / `valid-unlisted` split, and the **near- vs far-exemplar recall delta** — tracked and deliberately **non-gating**, since a large delta is a finding to act on, not a build to break. That number is what finally resolves the map's exemplar-domain-bias fog line.

**Corpus expansion to 12** (decision 6), following ticket 022's blessing workflow: the remaining internal systems, two more cookbook conversions, and the two adversarial/degenerate cases — a sparse input that should yield heavy `unknown` + `needs-info`, and one that should fail the validity gate and exercise `repair` → `reject`. Those two grade behaviors nothing else in the corpus reaches.

**Re-bootstrap the phase-1 corpus against the real `extract` node.** Ticket 022 was authored with no Vertex credentials available, so each phase-1 candidate model came from an agent stand-in running `prompts/extract.md` rather than the pinned Flash node (`"bootstrap": "agent-stand-in"` in every `case.json`). The blessed models are unaffected — they are blessed against the source text — but the `corrections.md` diffs are currently signal about *the prompt*, not about that model's own blind spots, which is what decision 2 asked them to be. Re-run `extract` over each `source.md`, re-record the diff, and flip the provenance field. Cheap, and it is what makes the accumulated "what extraction habitually gets wrong" record trustworthy enough to weight the extraction eval by.

**Corpus feedback loop** (decision 11): surface recurring `valid-unlisted` threats in the run artifact for SME review and promote them into the reference sets at the next blessing pass. Non-exhaustive ground truth converges from real output, never from someone trying to be exhaustive up front.
