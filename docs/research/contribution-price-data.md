# Price data for a pre-run cost estimate

Research for [#324](https://github.com/mstarks01/work-agent/issues/324), on the
map [#319](https://github.com/mstarks01/work-agent/issues/319). Question: a
pre-run estimate needs a price per token per model, and the repository carries
none. Where does the price data come from, and what can calibrate the estimate?

## Finding 1 — litellm already ships the price map

The pinned dependency (litellm 1.97.0) packages an offline price map with
3,212 entries. All six models the tier template names are present:

| model | input $/tok | output $/tok |
|---|---|---|
| claude-opus-5 | 5e-06 | 2.5e-05 |
| claude-sonnet-4-6 | 3e-06 | 1.5e-05 |
| gpt-4o | 2.5e-06 | 1e-05 |
| gpt-5.6 | 4e-06 | 2e-05 |
| gemini-2.5-pro | 1.25e-06 | 1e-05 |
| gemini-2.5-flash | 3e-07 | 2.5e-06 |

Each entry also carries `cache_read_input_token_cost` and
`cache_creation_input_token_cost`, so cached prompt tokens price correctly.
The map is package data: it works offline, and it updates only when the
dependency updates. Upstream is
`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`.
A packaged price ages between dependency bumps; whether that needs a drift
alarm is fog on the map.

## Finding 2 — the artifact already records everything an actual needs

A version-2 artifact carries `node_usage` per node (`prompt_tokens`,
`cached_prompt_tokens`, `completion_tokens`, `reasoning_tokens`) and
`provenance.node_runs` names the served model per node execution. Nothing new
must be captured. Priced against the litellm map, the recorded 2026-08-22
sweep (13 cases, STRIDE analysis mode, gpt-5.6 on both tiers) comes to:

- 1,514,049 prompt tokens, of which 264,161 were cache reads
- 290,874 completion tokens
- **$0.60 for the whole sweep**

So the estimate can be: the per-node token profile of merged baselines,
multiplied by the map. Every contributed baseline then improves the estimate
for the next contributor, which is the flywheel the map wants.

## Finding 3 — the suffix trap

The recorded sweep's served model is `gpt-5.6-luna`, and the price map does
not hold that key. The base form `gpt-5.6` matched after the suffix was
stripped. An estimate must state its fallback rule for suffixed served builds,
and a missing price must be a visible refusal — never a silent zero, which
would understate the figure the contributor approves.

## Finding 4 — the field does not do this

lm-evaluation-harness has no pre-run estimator; its docs suggest a hand
formula (samples × average tokens × price). HELM disclosed roughly $100,000 of
aggregate cost after the fact, not per run and not in advance. A pre-run
estimate calibrated from recorded actuals would be ahead of both.

Sources:
- [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness)
- [lm-eval API evaluation notes with the cost formula](https://github.com/NousResearch/hermes-agent/blob/main/skills/mlops/evaluation/lm-evaluation-harness/references/api-evaluation.md)
- [AI evals are becoming the new compute bottleneck (HELM cost figure)](https://huggingface.co/blog/evaleval/eval-costs-bottleneck)
