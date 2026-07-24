---
id: 06
title: "Per-node graph._generate_content_config from resolve_sampling"
label: wayfinder:task
status: open
assignee: ""
blocked-by: [05]
---

## Task

Wire the tier-keyed sampling into the graph so **each node** is configured with
its tier's decoding params, replacing the single global `SamplingConfig`
currently applied uniformly.

- Inject `resolve_sampling` (from [ticket 05](05-tier-keyed-sampling-config.md))
  alongside the existing `resolve_model`, and make
  `graph._generate_content_config` compose **per node**: it already merges
  resilience's `http_options` (ticket 01 research, `basic.py`), so fold the
  node's `TierSampling.to_generate_content_config()` into that same per-node
  composition rather than a graph-wide constant.
- Preserve the existing invariants: `http_options` stays owned by
  `config/resilience.toml` (never from sampling); deterministic FunctionNodes
  carry no model and no sampling.
- `recritic` runs on the same tier as `critic` (per `model_tiers.py`), so it
  resolves the same `TierSampling` — verify no node is left on a stale global.

**Tests:** offline — assert each LLM node receives the `GenerateContentConfig`
its tier resolves to (flash nodes ≠ pro nodes when the file differs), and that
`http_options`/resilience composition is unchanged. Consult `python-style`.
