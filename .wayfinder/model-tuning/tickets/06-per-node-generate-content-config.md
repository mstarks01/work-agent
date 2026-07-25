---
id: 06
title: "Per-node graph._generate_content_config from resolve_sampling"
label: wayfinder:task
status: resolved
assignee: "github@michaelstarks.com"
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

## Answer

Wired per-node sampling; `graph.py`/`pipeline.py` back to green (all 520 tests
pass). The `resolve_model` sibling pattern carried straight over.

**Graph (`graph.py`):**
- `build_pipeline` now takes `resolve_sampling: SamplingResolver` in place of
  the old `sampling: SamplingConfig` — the injected sibling of `resolve_model`,
  same canonical node name in, that node's `TierSampling` out.
- `_llm_node` resolves *both* off the one `TIER_NODE_BY_GRAPH_NODE[name]` key:
  `model=resolve_model(tier_node)`, config from
  `resolve_sampling(tier_node)`. No graph-wide sampling constant remains, so
  flash nodes (`extract`/`repair`) and pro nodes (analysts/`critic`/`recritic`)
  each carry their own tier's `GenerateContentConfig`.
- `_generate_content_config(sampling: TierSampling, resilience)` folds the
  node's `TierSampling.to_generate_content_config()` and then the resilience
  `http_options` into one config — `http_options` stays owned by
  `resilience.toml` (never sourced from sampling; ticket 03 invariant held).
- Deterministic `FunctionNode`s remain model-less and sampling-less (unchanged);
  `recritic` resolves the same `pro` `TierSampling` as `critic` via the shared
  `model_tiers` node→tier map — no node left on a stale global.

**Production wiring (`pipeline.py`):** `build_default_pipeline` builds
`resolve_sampling=make_resolve_sampling(sampling, tiers.resolve_tier)`.
`load_sampling` now receives `env=env` (was implicitly `os.environ`), so the
`STRIDE_SAMPLING_*` overrides read the injected env consistently with the tier
and resilience loaders.

**Eval harness (`evals/harness/modes.py`):** `build_eval_pipeline` keeps its
`sampling=` surface and builds the resolver internally from its already-loaded
`tiers` — no eval-side call-site churn.

**Tests:** three new graph tests — each LLM node binds *its* tier's config
(flash ≠ pro when the file diverges, asserted on `temperature`/`seed`/
`thinking_budget`); per-node sampling composes with the resilience timeout
without dropping either; and no `http_options` when resilience is absent. The
`build_pipeline` call sites in `test_graph`/`test_pipeline` switched to
`resolve_sampling`.

**Follow-on:** [ticket 07 (provenance stamping)](07-provenance-stamping.md) was
blocked by 05 + 06 — now unblocked, the new frontier. No fog graduated (07–09
were already charted); nothing ruled out of scope.
