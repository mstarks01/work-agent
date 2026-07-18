---
id: 002
title: "Research: ADK graph features and per-agent Vertex model config"
label: wayfinder:research
status: closed
assignee: claude (research subagent)
blocked-by: []
---

## Question

What is the current state (latest release, July 2026) of Google Agent Development Kit (ADK) for Python for building graph-based multi-agent workflows? Specifically: the graph/workflow API surface (workflow agents, graph-based orchestration primitives, conditional edges, parallel fan-out/fan-in); how each agent node selects a different Vertex model (e.g., Gemini Flash vs Pro per node); state/artifact passing between nodes and how to scope context per node to minimize tokens; async execution + streaming progress events; and the supported pattern for serving from a self-owned container (api_server/FastAPI mode) on Cloud Run. Cite primary sources (official docs, release notes, repo).

## Context pointer

Findings land on branch `research/adk-graph`.

## Resolution

Resolved 2026-07-18 by research subagent. Full findings: `docs/research/adk-graph.md` on branch `research/adk-graph` (commit 228df6c). Cited to adk.dev docs, google/adk-python releases, PyPI, Google Developers Blog.

- Latest: google-adk 2.5.0 (2026-07-16); ADK Python 2.0 (GA 2026-05-19) introduced the Workflow Runtime — a directed-graph engine superseding `SequentialAgent`/`ParallelAgent`/`LoopAgent`.
- Graph API: `Workflow(name=..., edges=[...])`; nodes are `LlmAgent`, `FunctionNode`, or `JoinNode`. Conditional edges via router returning `Event(route=...)`; fan-out = multiple edges from one source; fan-in = `JoinNode` (waits for all upstreams — a failed upstream stalls it); loops are back-edges.
- Per-node models: each `LlmAgent` takes its own `model` string + `GenerateContentConfig` (Flash for analysts, Pro for critic). Default model is `gemini-3-flash-preview` since v2.2.0. Vertex config now `GOOGLE_GENAI_USE_ENTERPRISE=True` (was `GOOGLE_GENAI_USE_VERTEXAI` in 1.x — verify before relying).
- Context scoping: `output_key` → session state; `temp:` prefix for invocation-only state; `{key}`/`{artifact.name}` instruction templating; `include_contents='none'` strips history so a node sees only instruction + injected keys.
- Async/streaming: `Runner.run_async` yields `Event` stream; `RunConfig(streaming_mode=StreamingMode.SSE)`; nested node events cascade for progress; `/run_sse` HTTP surface. BIDI/live not supported in graph workflows.
- Cloud Run self-owned container: `get_fast_api_app()` from `google.adk.cli.fast_api` in own FastAPI `main.py` + Dockerfile; async `session_service_uri` required; v2.5.0 added state-based resumption for task-mode workflow nodes.
