---
id: 02
title: "Decide the per-tier tuning config schema and sampling.toml migration"
label: wayfinder:grilling
status: open
assignee: ""
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
