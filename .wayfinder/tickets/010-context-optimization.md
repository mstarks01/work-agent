---
id: 010
title: "Context optimization strategy"
label: wayfinder:grilling
status: open
assignee:
blocked-by: [003, 004]
---

## Question

How is state sliced per node so each agent sees only what it needs: which slices of the canonical model each node type receives (e.g., an S-category analyst gets only auth-relevant elements?), what intermediate artifacts pass between nodes vs stay in session state, summarization/compaction points, and Vertex context-caching opportunities for shared prefixes (skills, system model).
