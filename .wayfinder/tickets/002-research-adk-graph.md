---
id: 002
title: "Research: ADK graph features and per-agent Vertex model config"
label: wayfinder:research
status: open
assignee: claude (research subagent)
blocked-by: []
---

## Question

What is the current state (latest release, July 2026) of Google Agent Development Kit (ADK) for Python for building graph-based multi-agent workflows? Specifically: the graph/workflow API surface (workflow agents, graph-based orchestration primitives, conditional edges, parallel fan-out/fan-in); how each agent node selects a different Vertex model (e.g., Gemini Flash vs Pro per node); state/artifact passing between nodes and how to scope context per node to minimize tokens; async execution + streaming progress events; and the supported pattern for serving from a self-owned container (api_server/FastAPI mode) on Cloud Run. Cite primary sources (official docs, release notes, repo).

## Context pointer

Findings land on branch `research/adk-graph`.
