---
id: 032
title: "Establish baselines and promote the gates"
label: wayfinder:task
status: closed
assignee:
blocked-by: [027, 028, 029, 031]
---

## Question

The measurement this whole eval effort was built to make. Run the full sweep
**~5×** to establish baseline metrics and, just as importantly, their
**variance** — the sweeps are what turn ticket 009's "mechanism decided now,
numbers derived later" into actual numbers.

It is blocked on all four of its predecessors deliberately:

- [Provision live infrastructure](027-provision-live-infrastructure.md) — nothing
  runs without Vertex.
- [Instrument and score critic yield](028-critic-yield-instrumentation.md) — so
  the sweeps measure the critic too, rather than needing a re-run.
- [Expand the golden corpus to 12](029-expand-golden-corpus.md) — baselines taken
  on six cases all move when the corpus reaches twelve, which wastes the sweeps.
- [Run the judge–human agreement bar](031-judge-calibration-bar.md) — an
  uncalibrated judge cannot produce a quotable recall figure, let alone a gate.

Then promote the gates:

- **Must-find recall becomes a hard per-case gate** (decision 18). Per-case, not
  aggregate: an aggregate hides a single case failing completely, which is the
  exact regression shape that matters most.
- **Tier 3 ratchets get noise bands derived from the observed spread**, not
  guessed. Five runs is the sample; if the spread is wider than the band anyone
  would want to gate on, that is itself the finding, and the honest response is a
  wider band rather than a tighter one that flakes.
- **Baselines are checked-in artifacts** (decision 16), so moving one is a
  reviewable diff with a human explaining why in the PR.

Tier 1 structural gates already gate day one and stay as they are.

**Critic yield arrives instrumented but un-thresholded**
([Instrument and score critic yield](028-critic-yield-instrumentation.md)): the
sweeps will print and record `ungrounded_killed` and `matched_killed` per case
and pooled, and whether either becomes a gate is this ticket's call, from the
observed spread. Read them as a pair. `matched_killed` — and especially
`must_find_killed` — is the one number here that can veto ticket 004's
generator-critic pattern rather than merely tune it, so if it is non-trivial,
that finding outranks the gate-promotion work in this ticket. Yield costs
almost nothing on top of a sweep: the pre-critic pass memoizes its judge
rulings for the post-critic pass, so it does not change the quota arithmetic
below.

Also settle what the sweeps make answerable and nothing else can: the
**near-vs-far exemplar recall delta** — tracked, deliberately **non-gating**,
since a large delta is a finding to act on, not a build to break. Recording that
number resolves the map's exemplar-domain-bias fog line, which has been open
since ticket 019 authored all 18 exemplars on one payments system.

Watch cost and quota rather than dollars: output on the eight-way `pro` fan-out
is the whole bill (Pro $10/1M out, confirmed July 2026), and `concurrency:
evals-live` allows one live run at a time — five sweeps over twelve cases is a
serialized wait, not an expense worth optimizing.

## Closed — out of scope (2026-07-23)

Not resolved on the route — **closed out of scope**. This ticket needs a live
Vertex call (sweeps, calibration runs, or re-bootstrapping against the real
model) to produce its numbers. The user ruled GCP/Vertex provisioning and all
live eval measurement out of scope — the app assumes a correctly configured
environment — which closed [Provision Vertex access](027-provision-live-infrastructure.md)
and, with it, the only path to those numbers here. The offline machinery ships;
the numbers, the >=90% judge bar, and the Tier-2/3 gate promotions are a future
eval effort, not a ticket in this map. See the map's Out of scope section.
