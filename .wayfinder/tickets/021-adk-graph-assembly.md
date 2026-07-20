---
id: 021
title: "Assemble the ADK graph wiring nodes to prompts, skills, and models"
label: wayfinder:task
status: open
assignee:
blocked-by: [019, 020]
---

## Question

Build the ADK Workflow graph decided in [Decide graph topology and quality pattern](004-graph-topology-quality-pattern.md), wiring the pieces every prior implementation ticket shipped: `extract → validate/repair → prepare → 6 analysts → join → critic → router → assemble`.

Each LLM node binds its model via `resolve_model(node)` (`stride_service.model_tiers`, canonical names in `LLM_NODES`), its skills via `compose_analyst_skills`/`compose_critic_skills`, and its prompt via the ticket-020 composition functions. Deterministic FunctionNode bookends and the reserved REVISE route per ticket 004; `include_contents='none'` + state templating per ticket 002, including the category placeholder the single shared `analyst.md` depends on.

Replaces the `PipelineRunner` stub behind the seam that [Implement job API in stride_service](018-implement-job-api.md) left for it.

**Confirm at wiring time:** whether ADK 2.5.0 supports `output_schema` on `LlmAgent` workflow nodes. Ticket 013 assumed it does; the research notes (`docs/research/adk-graph.md` on `research/adk-graph`) confirm only *input*-schema validation. If unavailable, fall back to parsing `DraftThreat` from the raw emission through the repair path — lints and exemplars are unaffected either way.
