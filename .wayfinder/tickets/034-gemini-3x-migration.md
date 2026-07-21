---
id: 034
title: "Migrate Gemini 2.5 → 3.x before the 2026-10-16 retirement"
label: wayfinder:task
status: open
assignee:
blocked-by: [032]
---

## Question

**Hard external deadline: Gemini 2.5 retires on Vertex 2026-10-16.** Every LLM
node and the eval judge are pinned to `gemini-2.5-pro` / `gemini-2.5-flash`
(`config/model_tiers.toml` and `evals/config/judge.toml`, both at version 2 after
[Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md)).
This is not an open-ended sweep — it is dated work, and the date does not move.

The tension to resolve: as of 2026-07-21 the 3.x models (3 Flash, 3.1 Pro) were
**preview-stage**, and the restated pin rule from ticket 026 admits only the most
specific *stable GA* identifier — rejecting `-preview`, `-exp`, `-latest`. So
today there is nothing rule-compliant to migrate **to**. Re-check GA status
first; if 3.x has gone GA, the rule resolves itself and this is a normal tier
bump. If the deadline approaches with no GA option, the decision this ticket owns
is which rule bends and how it is recorded — that is a decision for the map, not
a silent config edit.

Blocked on [Establish baselines and promote the gates](032-establish-baselines.md)
because ticket 007 decision 4 requires upgrades to be **eval-gated**: no tier
string moves without a baseline to move it against. That predecessor is therefore
on the critical path to a fixed calendar date — if baselines slip, this slips into
the retirement window, and the escalation is to compress the sweeps, not to skip
the gate.

Migrate the tiers and the judge as **separate, separately-versioned** decisions:
a judge bump re-baselines the whole suite (the judge is the measuring
instrument), while a tier bump is the thing being measured. Moving both at once
makes a metric change uninterpretable.

Every `run`/`calibrate` artifact carries a `models` block with both config
versions and `judge_served`, so the check afterwards is mechanical: if a metric
moves across two runs with different served versions, the model moved, not the
prompt.
