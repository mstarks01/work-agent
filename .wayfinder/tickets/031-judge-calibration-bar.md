---
id: 031
title: "Run the judge–human agreement bar for the first time"
label: wayfinder:task
status: closed
assignee:
blocked-by: [027]
---

## Question

Decision 13 of [Golden-case eval suite design](009-eval-suite-design.md) set a
**≥90% judge–human agreement bar**.
[Implement the eval harness, ReferenceThreat, and scorer](023-implement-eval-harness.md)
implemented the check, its 129 hand-labelled fixtures (76/53, weighted to hard
negatives), and its gate — but the same missing-credentials constraint means it
has **never been executed against the real judge**. The mechanism exists; the
number does not.

Run `python -m evals.harness.run calibrate` against the judge pinned in
`evals/config/judge.toml`, and record the number.

This ticket blocks [Establish baselines and promote the gates](032-establish-baselines.md)
for a reason that is easy to skip past under deadline pressure: until it clears,
every metric the harness prints is judge-relative to a judge whose calibration is
unmeasured — a strictly weaker claim than "judge-relative", and not a number that
may be quoted anywhere, **even internally**. Baselines derived from an
uncalibrated judge would then be ratcheted against, which launders the unmeasured
judge into a gate.

**If it fails the bar, that is a judge-prompt authoring task, not a reason to
lower the bar.** The prompts live in `evals/prompts/`, lint-fenced out of the
shipped tree, and iterating them is in scope here. What is out of scope is moving
90% to fit the result.

Record `models.judge_served` from the run artifact alongside the agreement
figure: the bar was cleared by whatever version actually answered, and
`gemini-2.5-pro` is a documented alias for the current recommended build (see
[Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md)),
so the served version is the only durable record of what was measured.

## Closed — out of scope (2026-07-23)

Not resolved on the route — **closed out of scope**. This ticket needs a live
Vertex call (sweeps, calibration runs, or re-bootstrapping against the real
model) to produce its numbers. The user ruled GCP/Vertex provisioning and all
live eval measurement out of scope — the app assumes a correctly configured
environment — which closed [Provision Vertex access](027-provision-live-infrastructure.md)
and, with it, the only path to those numbers here. The offline machinery ships;
the numbers, the >=90% judge bar, and the Tier-2/3 gate promotions are a future
eval effort, not a ticket in this map. See the map's Out of scope section.
