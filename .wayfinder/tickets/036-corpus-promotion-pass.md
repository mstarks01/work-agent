---
id: 036
title: "Run the corpus promotion pass"
label: wayfinder:task
status: closed
assignee:
blocked-by: [032]
---

## Question

HITL. Decision 11's other half, split out of
[Expand the golden corpus to 12](029-expand-golden-corpus.md) when the expansion
finished and this did not — holding 029 open behind a parked infrastructure
ticket would have hidden that the twelve cases are done.

Reference sets are non-exhaustive by construction and converge from real output,
never from someone trying to be exhaustive up front. The artifact side already
ships: `unlisted_for_promotion` comes out of the scorer
([ticket 023](023-implement-eval-harness.md)), listing threats that were
grounded and plausible and simply not in the reference set. What remains is the
human pass — read the recurring `valid-unlisted` threats, decide which are real,
and promote those into the reference sets.

This is blocked for a concrete reason rather than a scheduling one: **there is no
run artifact to read**. Nothing in this repository has spoken to Vertex, so no
scoring sweep has produced a single unlisted threat.
[Establish baselines and promote the gates](032-establish-baselines.md) is the
ticket that first runs the corpus at scale, so its artifacts are this ticket's
input.

Promotion is a repeat of `BLESSING.md` steps 4-6 for the affected case only: a
reviewable diff with a human explaining why, never automatic. Two things to hold
onto when it runs:

- **Promoting changes the denominator.** Every promoted threat raises the recall
  bar for every future run, so a baseline taken before promotion is not
  comparable to one taken after. Sequence the promotion against the gate move in
  032, do not interleave them.
- **`build_pairs.py` indexes references by position.** Inserting a threat into
  the middle of a `threats.json` silently re-points every calibration pair after
  it; `verify_corpus.py` catches the reference-claim mismatch, but the cheap
  habit is to append.

Resolved when the first promotion round has been read, decided and merged, with
the answer recording which threats were promoted, which recurring unlisted
threats were rejected and why, and what the promotion did to the corpus totals.

## Closed — out of scope (2026-07-23)

Not resolved on the route — **closed out of scope**. This ticket needs a live
Vertex call (sweeps, calibration runs, or re-bootstrapping against the real
model) to produce its numbers. The user ruled GCP/Vertex provisioning and all
live eval measurement out of scope — the app assumes a correctly configured
environment — which closed [Provision Vertex access](027-provision-live-infrastructure.md)
and, with it, the only path to those numbers here. The offline machinery ships;
the numbers, the >=90% judge bar, and the Tier-2/3 gate promotions are a future
eval effort, not a ticket in this map. See the map's Out of scope section.
