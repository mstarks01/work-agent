---
id: 039
title: "Implement the graph failure policy: resilience config, critic re-ask, digest logging"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
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

## Answer

Shipped all five pieces; offline suite **460 passed / 1 skipped**, corpus clean.

1. **`config/resilience.toml` + `stride_service.resilience`.** `version = 1`,
   `attempts = 3`, `timeout_ms = 300000`, optional backoff knobs left unset so
   the SDK's own jittered defaults and retryable status set apply. Fail-closed
   loader mirroring `sampling.py`, but **env-overridable** — `STRIDE_RETRY_ATTEMPTS`,
   `STRIDE_TIMEOUT_MS`, `STRIDE_RETRY_INITIAL_DELAY|_MAX_DELAY|_EXP_BASE|_JITTER`
   — with the same set-but-empty-is-a-mistake and validate-overrides-like-file
   rules `model_tiers.py` uses. `to_retry_options()` and `to_http_options()`
   are the two SDK objects.

2. **Retry binding — a resolver wrapper, not a change to `ModelTierConfig`.**
   The ticket's "`resolve_model` returns `Gemini(...)`" lands as
   `stride_service.pipeline.resilient_resolver(tiers, resilience)`, because
   `ModelTierConfig.resolve_model` must keep returning the pinned *string*
   (every `test_model_tiers` assertion, and the `str | BaseLlm` resolver
   contract, depend on it). `build_default_pipeline` binds through it;
   `_model_name` unwraps `Gemini.model`, so the report's `nodes` array records
   the bare string, verified.

3. **Timeout binding.** `_llm_node` composes sampling and resilience into one
   `GenerateContentConfig` via a new `_generate_content_config` helper that
   sets `http_options.timeout`; `sampling.toml`/`SamplingConfig` untouched, so
   ticket 023's pinned-sampling rule stands. `resilience` is an *optional*
   `build_pipeline` arg (default `None`) only so the offline stand-ins, whose
   fakes never read a deadline, still build the graph; production and both
   eval-mode defaults always pass one.

4. **The critic re-ask — `router -> {accept, revise: recritic}` and
   `recritic -> rereview -> {accept, revise: critic_failed}`.** Sub-questions
   resolved:
   - **Own prompt file.** `prompts/recritic.md`, four fixed headings, mirroring
     `repair.md`'s bounded-correction shape — keeps `critic.md`'s cacheable
     prefix intact rather than bloating every job's critic prompt with a
     conditional section. `recritic_instruction` reuses `compose_critic_skills`
     byte-for-byte (the re-ask may re-rule a dropped draft, so it needs the
     rubric) and shares that prefix with the critic across jobs.
   - **State keys.** Two new *rendered* keys under the ticket-010 invariant:
     `previous_review` (the failing ruling) and `critic_issues` (the problem
     list), both written once by `route_review` on the revise edge and read
     only by the re-ask prompt template.
   - **`assemble_threats` split.** New `critic.review_issues()` is the check
     half — one definition of "well-formed critic output" shared by the router
     (which routes on it), `assemble_threats` (which still raises on it,
     fail-closed belt-and-suspenders) and `fail_review`. `route_review` is one
     function on two nodes, exactly like `validate_extraction` on
     `validate`/`revalidate`; the mechanical check thus moved *out* of
     `assemble_report` into `route_review`.
   - A second failure lands on `critic_failed`, which **raises** (`failed`, not
     `rejected` — the ticket-008 contract is unchanged).
   - **`recritic` is a distinct canonical LLM node** (`LLM_NODES`,
     `model_tiers.toml` → `pro`), not a reuse of the critic's binding: the
     `resolve_model` contract keys on canonical node names, so offline
     per-node scripting (ticket 021's whole offline story, and the
     `len(graph LLM nodes) == len(LLM_NODES)` invariant in `test_sampling`)
     *requires* it to have its own name. It always shares the critic's tier by
     construction.

5. **Digest logging + judge wiring.** `AdkPipelineRunner.run` wraps the drive
   in a try/except that logs `job <id> failed; source_sha256=<hex>` on any
   graph exception — the digest computed once and reused for the report, the
   untrusted text never logged (asserted). `VertexJudge` gained a `resilience`
   arg; its `_default_client` builds `genai.Client(http_options=...)` carrying
   the same retry + timeout, wired at both live `run.py` sites via `_live_judge`.

Consequences recorded rather than decided (both already fog on the map): the
`nodes` array still cannot carry attempt counts — SDK retries happen below the
runner's event stream — so retry visibility stays an observability line item;
and `attempts = 3` / `timeout_ms = 300000` are unchallenged until
[Establish baselines and promote the gates](032-establish-baselines.md) sees a
real 429. No new tickets graduate: this was the build behind ticket 038's
decisions, and it is done.
