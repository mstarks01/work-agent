---
id: 039
title: "Implement the graph failure policy: resilience config, critic re-ask, digest logging"
label: wayfinder:task
status: open
assignee:
blocked-by: [038]
---

## Question

Graduated on 2026-07-21 from
[Decide the graph's failure policy](038-graph-failure-policy.md), which settled
every disposition. This ticket is the build, and it is credential-free — the
whole graph already runs offline against scripted `BaseLlm` stand-ins, and a
stand-in that raises `APIError(429)` or `httpx.TimeoutException` tests the retry
path without a Vertex project.

Five pieces, in the order they depend on each other:

1. **`config/resilience.toml` + `stride_service.resilience`** — `attempts = 3`,
   `timeout_ms = 300000`, backoff knobs. Versioned like the other configs,
   fail-closed loader, **env-overridable** (unlike sampling: these change how
   hard we try, never which answer we get). Decide the env var names alongside
   `STRIDE_MODEL_FLASH`/`STRIDE_MODEL_PRO`.
2. **Retry binding** — `ModelTierConfig.resolve_model` returns
   `Gemini(model=..., retry_options=HttpRetryOptions(...))` rather than a bare
   string. Verify the report's `nodes` array is unchanged: `_model_name()`
   already unwraps `BaseLlm` via `.model`, and ticket 026's `models` block must
   still record the pinned string, not a repr.
3. **Timeout binding** — `_llm_node` composes the sampling config and the
   resilience config into one `GenerateContentConfig`, setting
   `http_options.timeout`. `config/sampling.toml` and `SamplingConfig` are
   **not** touched; the pinned-sampling rule stays as ticket 023 left it.
   Confirmed safe: ADK merges its tracking headers and api version into a
   caller-supplied `http_options` (`google_llm.py:227-235`).
4. **The critic re-ask** — the topology change, and the one with real design in
   it. `critic -> router -> {accept: assemble, revise: recritic} -> assemble`,
   mirroring `validate -> repair -> revalidate` so the one-pass budget is
   structural rather than counted. **The mechanical check moves out of
   `assemble_report` into `route_review`**, which needs the check to have
   something to route on and must park the issue list where the re-ask prompt
   reads it. Open sub-questions this ticket owns: whether `recritic` gets its
   own prompt file or `critic.md` grows a conditional section (ticket 019's
   four-heading rule and the prompt lints apply either way); what state keys the
   issue list and the previous ruling ride on, under ticket 010's dual-key
   invariant; and whether `assemble_threats` keeps raising or is split into a
   check half and a build half so the router and the node share one definition.
   A second failure is `failed`, not `rejected` — the ticket-008 contract does
   not change.
5. **Digest logging + judge wiring** — log `source_sha256` on a failed job so a
   poison input is identifiable across jobs without storing the text, and give
   `VertexJudge` (`evals/config/judge.toml`) the same resilience config, or a
   scheduled sweep still dies on one 429 after hours of work.

Resolved when the offline suite covers the retry path, the timeout path, the
re-ask path (clean, repaired, and second-failure), and the digest log line.
The numbers themselves are unverified against live Vertex, same constraint as
022/023/026/028/037 — no real 429 has ever been observed here, so
[Establish baselines and promote the gates](032-establish-baselines.md) is the
first thing able to challenge `attempts = 3` and `timeout_ms = 300000`.
