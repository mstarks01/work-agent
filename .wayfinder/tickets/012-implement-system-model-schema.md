---
id: 012
title: "Implement System Model schema and validator"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

Implement the canonical System Model as Pydantic models plus the mechanical validator, exactly per the resolution of [Domain model: canonical system representation](003-domain-model-system-representation.md): five element types with the agreed attribute sets, typed-slug ID scheme, `unknown` values, top-level assumptions list, controlled asset-tag enum (config-extendable), derived boundary-crossing computation, and the validity-gate rules returning structured errors suitable for the repair pass. AFK task: pure code, no decisions left. Consult `python-style` and `owasp-security` skills.
