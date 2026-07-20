---
id: 021
title: "Assemble the ADK graph wiring nodes to prompts, skills, and models"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: [019, 020]
---

## Question

Build the ADK Workflow graph decided in [Decide graph topology and quality pattern](004-graph-topology-quality-pattern.md), wiring the pieces every prior implementation ticket shipped: `extract → validate/repair → prepare → 6 analysts → join → critic → router → assemble`.

Each LLM node binds its model via `resolve_model(node)` (`stride_service.model_tiers`, canonical names in `LLM_NODES`), its skills via `compose_analyst_skills`/`compose_critic_skills`, and its prompt via the ticket-020 composition functions. Deterministic FunctionNode bookends and the reserved REVISE route per ticket 004; `include_contents='none'` + state templating per ticket 002, including the category placeholder the single shared `analyst.md` depends on.

Replaces the `PipelineRunner` stub behind the seam that [Implement job API in stride_service](018-implement-job-api.md) left for it.

**Confirm at wiring time:** whether ADK 2.5.0 supports `output_schema` on `LlmAgent` workflow nodes. Ticket 013 assumed it does; the research notes (`docs/research/adk-graph.md` on `research/adk-graph`) confirm only *input*-schema validation. If unavailable, fall back to parsing `DraftThreat` from the raw emission through the repair path — lints and exemplars are unaffected either way.

## Resolution

Resolved 2026-07-20. `google-adk==2.5.0` added as a dependency; graph in `stride_service/graph.py`, runner in `stride_service/pipeline.py`; suite 268 → **302 passed, 1 skipped**.

- **`output_schema` is supported — ticket 013's assumption holds.** Verified against the installed 2.5.0, not the docs: `workflow/_llm_agent_wrapper.py::process_llm_agent_output` validates a node's emission against `agent.output_schema` and stores the result under `output_key`, and `flows/llm_flows/basic.py` sets it as the model-side `response_schema` for a tool-less node (which every node here is). All three shapes convert cleanly through `google.genai`: `SystemModel`, `list[DraftThreat]`, `list[Threat]`. The raw-emission fallback is not needed and was not built.
  - **What that costs.** A schema-violating emission raises inside ADK and fails the job rather than routing to `repair`. Acceptable, and arguably correct: with `response_schema` enforced at decode time, what reaches our gate is exactly the *semantic* invalidity the repair pass was designed for (duplicate IDs, dangling endpoints, zone membership, ID-slug mismatch), while a truly malformed emission is a model failure a repair prompt does not fix. The `schema` issue code stays reachable through `parse_and_validate` for anything that slips past.
- **Topology as decided, plus three structural nodes.** `extract → validate ⇄ repair → prepare → 6 analysts → join → merge → critic → router → assemble`.
  - `revalidate` is a second `FunctionNode` over the *same* validate function rather than a back-edge to `validate`. The repair budget is exactly one, so the graph is acyclic and cannot spend a second pass — "one repair then reject" is visible in the topology instead of enforced by a counter.
  - `reject` is the terminal node the second failure lands on; it parks the validator's issues for the runner to return as `PipelineRejected`. Nothing is analyzed on a model that never passed the gate.
  - `merge` runs `join_drafts` behind ADK's `JoinNode`, which is a pure barrier with no user code of its own. A plain `FunctionNode` with six incoming edges would fire six times; only `JoinNode` waits for all predecessors.
- **Model binding.** `build_pipeline(resolve_model=...)` takes the resolver, so production passes `ModelTierConfig.resolve_model` and tests pass a fake returning a `BaseLlm` stand-in — the whole graph runs offline without a Vertex endpoint. Graph node names must be Python identifiers, so `analyst/information-disclosure` (canonical, tier config) maps to `analyst_information_disclosure` (graph) through the single `TIER_NODE_BY_GRAPH_NODE` dict; a test asserts its values are exactly `LLM_NODES`.
- **`{category}` is filled at build time, not by ADK.** The six analysts run in parallel against one session state, which cannot hold six values for one key. The six job-varying placeholders stay for ADK templating and are carried by dedicated *rendered* state keys, since ADK substitutes `str(value)` — a raw dict would reach the model as a Python repr. A lint asserts no other `{identifier}` survives in any composed instruction, which would otherwise be a `KeyError` at the first call.
- **Deterministic bookends, fail-closed.** `validate`/`prepare`/`merge`/`router`/`assemble`/`reject` are plain functions wrapped in `FunctionNode`s and tested directly. `prepare` derives crossings so no analyst can read a crossing that contradicts its model; `merge` and `assemble` raise `DraftJoinError`/`CriticOutputError` on a hallucinated element or a dropped threat, which aborts the workflow and fails the job loudly. Verified end to end: a scripted analyst citing `process:invented` fails the run rather than producing a report.
- **The seam is filled.** `AdkPipelineRunner` implements the ticket-018 `PipelineRunner` protocol, and `create_app()` now defaults to `default_pipeline_runner()` (repo Markdown, repo tier config, pinned models, failing closed on a bad config) instead of `StubPipelineRunner` — which stays for offline HTTP-surface work. App boot with the real graph measured at ~30 ms; `/healthz` 200, auth unaffected. The `verify` skill notes the new default.
- **Report metadata is the runner's.** The graph stops at an `Analysis` (system model, crossings, threats, rejected, summary); job identity, the input digest, and `nodes` timings are stamped by the runner, which is the only party that knows them. `duration_ms` is measured from the moment a node's last predecessor finished — the point the graph could have started it — to the event carrying its own output, read off the built graph's edges.
- **Not built, deliberately.** Ticket 004's "applicability matrix as a mechanical pre-filter on each analyst's element view" has no machine-readable source: `## Applicability` is prose in each skill file, and `analyst.md`'s procedure already directs the analyst to filter by it. A code-side matrix would duplicate that prose and could silently contradict it. All six analysts get the same rendered model; if evals show recall or token pressure that a pre-filter would fix, the matrix becomes a data file the skills are generated from, not a second copy.
