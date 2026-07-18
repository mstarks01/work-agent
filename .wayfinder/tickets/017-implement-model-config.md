---
id: 017
title: "Implement model-tier config in stride_service"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

Implement the model-tier configuration per the resolution of [Per-agent Vertex model tier assignment](007-model-tier-assignment.md): a versioned config file defining the two tiers (`flash`, `pro`) as pinned Vertex model version strings and the node→tier mapping (extract/repair → flash; six analysts + critic → pro); a loader exposing the resolved model string per node, with `STRIDE_MODEL_FLASH` / `STRIDE_MODEL_PRO` env vars overriding tier strings only (node→tier is file-only); validation that rejects `-latest`/alias strings and unknown node or tier names. AFK task: pure code, no decisions left. Plus tests (mapping resolution, env override precedence, alias rejection). Consult `python-style` and `owasp-security` skills.
