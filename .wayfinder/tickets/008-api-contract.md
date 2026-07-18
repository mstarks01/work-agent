---
id: 008
title: "Front-end API contract (async jobs, Ping auth)"
label: wayfinder:grilling
status: open
assignee:
blocked-by: [005]
---

## Question

Define the REST contract the front-end calls on Cloud Run: submit endpoint (input payload shape), job handle + status/progress mechanism (polling vs SSE streaming of node-level progress), result retrieval, error surface, and where Ping JWT validation sits (middleware; follows existing org patterns).
