---
id: 033
title: "Answer the sampling question with the instrument"
label: wayfinder:task
status: closed
assignee:
blocked-by: [032]
---

## Question

Decision 15: settle sampling **by measurement, not by argument**. The mechanism
already ships — `config/sampling.toml` is read by production and the eval suite
from one file and bound onto all nine LLM nodes, with no env override, because an
ops-tunable temperature is how a suite goes green while production drifts. What
stays unspecified is the **value**.

Temperature is pinned at `0` as a starting point, and that pin is exactly what is
under test: temperature 0 plausibly amplifies exemplar anchoring while foreclosing
the Self-MoA recall lever. Same corpus, three arms:

1. **temperature 0** — the shipped default;
2. **production model default**;
3. **k=3 union-merged** (Self-MoA, ticket 001 §4.4).

Let the **far-domain cases decide**. Near-domain cases sit closest to the
exemplar and will look good under anchoring; the far cases are where anchoring
costs recall, which is the effect this arm exists to detect. Read the near/far
delta per arm, not just aggregate recall.

Blocked on [Establish baselines and promote the gates](032-establish-baselines.md)
because without the measured run-to-run variance from those ~5 sweeps, a
difference between arms cannot be distinguished from noise — and k=3 in
particular costs 3× the analyst fan-out, so it needs to beat the band, not just
the mean.

**Thinking budget** is the other half of this and remains deliberately absent from
`config/sampling.toml` — pinning an unmeasured value would read as a decision
nobody made. Whether it becomes a fourth arm here or its own ticket is this
ticket's call to make once the three arms above have numbers.

Whatever wins, the outcome is a versioned bump to `config/sampling.toml` with the
run artifacts behind it.

## Closed — out of scope (2026-07-23)

Not resolved on the route — **closed out of scope**. This ticket needs a live
Vertex call (sweeps, calibration runs, or re-bootstrapping against the real
model) to produce its numbers. The user ruled GCP/Vertex provisioning and all
live eval measurement out of scope — the app assumes a correctly configured
environment — which closed [Provision Vertex access](027-provision-live-infrastructure.md)
and, with it, the only path to those numbers here. The offline machinery ships;
the numbers, the >=90% judge bar, and the Tier-2/3 gate promotions are a future
eval effort, not a ticket in this map. See the map's Out of scope section.
