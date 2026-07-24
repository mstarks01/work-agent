---
id: 02
title: "Decide the per-tier tuning config schema and sampling.toml migration"
label: wayfinder:grilling
status: resolved
assignee: "github@michaelstarks.com"
blocked-by: [01]
---

## Question

Given the safe-to-tune param set from [the research ticket](01-research-adk-tuning-params.md), design the configuration layout. Per-tier blocks (`flash`, `pro`) are already chosen; this ticket makes the shape concrete and authors the file layout **before** the loader is wired (per the effort's "author content before loader code" note). Decisions to lock:

1. **TOML structure.** How the per-tier params are expressed — e.g. `[tiers.flash]` / `[tiers.pro]` param tables in `config/sampling.toml`. Whether tuning is keyed by **tier** (flash/pro) or by **node** (the nine LLM nodes), given `model_tiers.toml` already maps node → tier. Recommendation expected: key by tier, since the user framed it as "both classes of models."
2. **`SamplingConfig` restructure.** From today's single global object applied to all nodes → a per-tier resolver (`sampling_for(tier)` or `for_node(node)`), preserving `extra="forbid"`, `frozen=True`, and fail-closed loading. No node may silently run on unlisted params.
3. **Version bump & migration.** `sampling.toml` goes `version = 1 → 2`. Decide how the flat v1 (`temperature`, optional `top_p`) migrates — the natural rule is both tiers inherit the old global values so v1 semantics are preserved exactly, and `temperature = 0` stays the default for both.
4. **Absent-param rule.** When a tier omits a param: fall through to the model's own default (today's rule for unlisted keys), or to a shared `[base]` block both tiers inherit? Pick one; the model-default rule is simplest and matches the current "pinning an unmeasured value claims a decision nobody made" stance.
5. **Judge scope.** Whether the eval judge (`evals/config/judge.toml`, its own model + sampling) adopts the same per-class shape now or stays a separate pinned config. Default lean: out of this effort unless research shows the judge shares the tension.

**Constraint carried in:** the file stays the canonical source of truth eval and production both read (decision 15). This ticket decides the *layout*; the loader/graph wiring graduates as implementation on close.

## Research input (from ticket 01, 2026-07-24)

- **The v1 param set is decided upstream.** Schema needs to carry the *offer*
  set — `temperature`, `top_p`, `seed`, and per-tier `thinking_budget` — and
  leave room for the *reserved* ones without shipping them; the *forbidden*
  params never appear in the file at all.
- **`thinking_budget` cannot be one shared number.** Flash accepts 0–24,576
  (0 = disabled), pro accepts 128–32,768 (0 is a **400**). The per-tier block
  must express intent (off / auto / N) and the loader must resolve to a
  class-legal value — the single strongest reason to key by **tier** and the
  hard reason the flat v1 shape cannot stretch.
- **`top_k` is typed `float`** on the installed `GenerateContentConfig` — set
  the validator type accordingly if `top_k` is ever admitted.

## Answer (resolved 2026-07-24, grilled with github@michaelstarks.com)

The scope widened mid-grill: the user asked for the **full decoding surface,
every param present with its default value and a one-line comment**, not the
minimal offer-set. That reverses decision 4's "absent = model default" toward
an explicit, self-documenting file. Decisions locked:

1. **Key by tier.** `[tiers.flash]` / `[tiers.pro]`; the node→tier map stays
   owned by `model_tiers.toml`, reused, never duplicated.
2. **Full decoding surface, each param commented.** Fields:
   `temperature`, `top_p`, `top_k`, `seed`, `max_output_tokens`,
   `candidate_count`, `presence_penalty`, `frequency_penalty`, `thinking`.
   The contract-breaking set (`response_schema` — ADK *raises*;
   `response_mime_type`; `stop_sequences`; `http_options` — owned by
   `resilience.toml`; `tools`; `system_instruction`) is **not** in the file,
   named instead in a "deliberately absent — why" comment block.
3. **Values.** `temperature = 0.0` (deliberate pin; comment notes model
   default is 1.0). `thinking` is a per-tier **mixed scalar**
   `Literal["off","auto"] | int`. `candidate_count = 1`, **reserved** — the
   loader **fail-closes if ≠ 1** (it is the deferred Self-MoA lever, ticket
   009; >1 silently drops candidates). Every other param is pinned to its
   **authoritative, cited per-class default** — which ticket 01 did *not*
   capture, so those numbers are a fresh research need (ticket 04). Pinning a
   guessed number is the "claims a decision nobody made" anti-pattern, so the
   file is not authored until 04 lands the cited values.
4. **`thinking` resolution (mechanical, in the loader — deterministic code).**
   `"off"` → `thinking_budget = 0` (flash only; **pro raises**, floor 128);
   `"auto"` → `-1` (dynamic); int `N` → class-range-checked (flash 0–24,576,
   pro 128–32,768) or raise. Absent is no longer used — every param is
   explicit now.
5. **Absent-param rule (decision 4) reversed.** No `[base]` block; no
   fall-through. Every param is present and pinned, so the file *is* the
   record of what every node runs on.
6. **Migration: hard cutover.** No backward compatibility (user: "No backward
   compatibility is needed"). `sampling.toml` is rewritten to `version = 2`;
   the loader accepts **only** `version == 2` and fail-closes otherwise. No v1
   shim, no runtime translation.
7. **`SamplingConfig` restructure.** From one global object to tier-keyed:
   `{version, tiers: dict[TierName, TierSampling]}`, both tiers required
   (mirrors `ModelTierConfig._check_complete`), `extra="forbid"` + `frozen`
   preserved throughout. The graph gets an injected
   **`resolve_sampling: node → TierSampling`** callable, the exact sibling of
   `resolve_model`; the node→tier walk lives once at the wiring site (reusing
   `ModelTierConfig.nodes`), so the graph stays purely node-centric.
   `_generate_content_config` takes the resolved `TierSampling` + resilience
   per node.
8. **Judge scope: out of this effort.** `evals/config/judge.toml` stays a
   separate pinned config — single-tier (`pro` only, so per-class shape buys
   nothing) and deliberately decoupled from `sampling.toml` (fixed measuring
   stick vs. config-under-test). Any judge-decoding change is a re-baselining
   event under ticket 009 decision 13, not part of this mechanism.

**Consequence flagged and accepted:** pinning defaults as explicit numbers
changes what the API receives vs. today's unset — identical behavior now, but
frozen against future model-default drift, which is the point for a
self-defending config.

**Graduates on close (with ticket 03):** the implementation build-out — the
`SamplingConfig` restructure, per-node `resolve_sampling`, the `sampling.toml`
rewrite, and `docs/Configuration.md` — now **also blocked on ticket 04**
(the per-class default values it must bake in).
