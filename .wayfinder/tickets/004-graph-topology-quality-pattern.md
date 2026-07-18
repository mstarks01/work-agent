---
id: 004
title: "Decide graph topology and quality pattern"
label: wayfinder:grilling
status: closed
assignee: claude.ai@michaelstarks.com
blocked-by: [001, 002, 003]
---

## Question

Given the research findings (quality patterns, ADK graph capabilities) and the canonical system model: what is the node graph? Which quality pattern (reviewer/reviser, debate, consensus, hybrid) do we adopt, where do nodes land (extraction → per-element or per-STRIDE-category analysts → critic/merge → severity → mitigation → report assembly?), what runs in parallel, and what are the conditional edges? This is the user's headline question — "where do the nodes naturally land?"

## Resolution

Resolved 2026-07-18 via grilling, grounded in the resolutions of tickets 001 (quality patterns), 002 (ADK graph), and 003 (canonical model).

1. **Quality pattern** — decomposed fan-out + one grounded generator–critic stage. No multi-agent debate, no voting ensembles, no same-context self-critique (all evidence-rejected; see ticket 001). Optional small-k same-model sampling on high-risk elements deferred until evals show a recall gap. Budget ~2-4x a bare pass.
2. **Fan-out axis** — six STRIDE-category analysts (S, T, R, I, D, E) in parallel, not per-element: fixed width keeps the graph static/declarative, one category skill loads per analyst, and the small canonical model is cheap to send six times. Applicability matrix applied as a mechanical pre-filter on each analyst's element view.
3. **Critic** — one pass, no revise loop in v1: per-threat verdicts (confirm / reject+reason / needs-info), cross-category dedupe, severity calibration. Verdicts kept as audit trail. Output goes through a router with a reserved (unwired) REVISE route so a loop is a config change later, gated on eval evidence.
4. **Severity/mitigations** — analysts draft them (they hold element context + category skill rubrics); critic calibrates consistency. Scale itself is ticket 005's decision.
5. **Graph** (ADK ≥2.5 `Workflow`):
   `START → extract (LlmAgent, cheap tier) → validate (FunctionNode) —INVALID→ repair (LlmAgent, one pass) → validate —VALID→ prepare (FunctionNode: derive crossings, slice per-category views) → 6 analysts (LlmAgent, mid tier, parallel) → JoinNode (analysts wrapped to emit sentinel on failure — detail owned by error-handling fog) → critic (LlmAgent, strong tier) → router (ACCEPT → assemble; REVISE reserved) → assemble (FunctionNode: deterministic JSON report + id integrity + verdict audit trail)`.
   `prepare`/`assemble` deliberately non-LLM: analysts can't receive a malformed view, the report can't cite a nonexistent element. Progress streaming via ADK event cascade per node.
6. **Tier shape** — cheap edges, strong critic (Self-MoA placement logic). Exact model IDs are ticket 007's decision.
