---
id: 034
title: "Migrate Gemini 2.5 → 3.x before the 2026-10-16 retirement"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

**Reframed 2026-07-23** as a code/config migration. The eval gate is gone: the
user ruled GCP/Vertex provisioning and all live eval measurement out of scope
(the app assumes a correctly configured environment), so
[Establish baselines and promote the gates](032-establish-baselines.md) is closed
out of scope and no longer blocks this. Ticket 007 decision 4's "upgrades are
eval-gated" cannot be honoured without a suite that runs, so for this effort the
migration is a straight pinned-string bump verified against the assumed-configured
environment by whoever runs it — not a metric-gated move. The judge migration
(`evals/config/judge.toml`) is also out of scope now that the eval judge is; this
ticket owns only the **production** tier config, `config/model_tiers.toml`.

**Hard external deadline: Gemini 2.5 retires on Vertex 2026-10-16.** Every
production LLM node is pinned to `gemini-2.5-pro` / `gemini-2.5-flash`
(`config/model_tiers.toml`, version 2 after
[Verify the pinned Vertex model strings resolve](026-verify-pinned-model-strings.md)).
This is dated work, and the date does not move.

The one real gate left is **external**, not a ticket: as of 2026-07-21 the 3.x
models (3 Flash, 3.1 Pro) were **preview-stage**, and the restated pin rule from
ticket 026 admits only the most specific *stable GA* identifier — rejecting
`-preview`, `-exp`, `-latest`. So today there is nothing rule-compliant to
migrate **to**. Re-check GA status first; if 3.x has gone GA, the rule resolves
itself and this is a normal tier bump plus a prompt-compat read. If the deadline
approaches with no GA option, the decision this ticket owns is which rule bends
and how it is recorded — a decision for the map, not a silent config edit.
