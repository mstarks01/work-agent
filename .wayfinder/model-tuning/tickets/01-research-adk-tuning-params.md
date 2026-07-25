---
id: 01
title: "Research: ADK 2.5 tuning params — the GenerateContentConfig surface, per-class applicability, and which ADK owns"
label: wayfinder:research
status: resolved
assignee: "claude (research subagent)"
blocked-by: []
---

## Question

For `google-adk==2.5.0` / `google-genai` **as installed in this repo's `.venv`**, and for Gemini 2.5 on Vertex, enumerate the `GenerateContentConfig` fields that constitute *model tuning* (decoding / generation control) and classify each. Candidate params: `temperature`, `top_p`, `top_k`, `max_output_tokens`, `candidate_count`, `seed`, `stop_sequences`, `presence_penalty`, `frequency_penalty`, `thinking_config` (thinking budget), plus anything else the installed type exposes.

For each param, answer:

1. **Exposed & passed through?** Does the installed `google.genai.types.GenerateContentConfig` expose it, and does ADK actually forward it per node — i.e. through the `generate_content_config` an `LlmAgent` carries and the `_generate_content_config` helper in `stride_service.graph` composes (which already merges resilience's `http_options`)? Note anything ADK strips or ignores.
2. **Per-class or shared?** Applicable to *both* `gemini-2.5-flash` and `gemini-2.5-pro`, or class-specific? Pin down `thinking_config` / thinking budget especially — its valid range and whether "off" is legal differ between flash and pro on 2.5.
3. **Safe to tune, or owned elsewhere?** Which are set by our own graph and therefore must **not** be user-tunable: `response_schema` / `response_mime_type` are derived from each node's `output_schema`; `http_options` / timeout belong to `config/resilience.toml`. Flag any param whose override would break decode-time schema enforcement (ticket 021).
4. **`candidate_count` vs Self-MoA.** Does exposing `candidate_count` collide with the deferred Self-MoA recall lever (ticket 009 / `config/sampling.toml` comments)? Recommend whether it should be reserved rather than offered as a plain tuning knob.
5. **`seed` for reproducibility.** Confirm whether `seed` is honored on Vertex Gemini 2.5 and what guarantee it gives — this is the user's "repeatable, defensible results" lever beyond `temperature = 0`.

**Deliverable:** a table `param → { exposed?, per-class?, safe-to-tune?, notes }` plus a short recommended v1 tunable set. Most of (1) is answerable by reading the installed package; (2)/(5) need Vertex Gemini 2.5 docs. Capture the findings with a context pointer here.

## Context pointer

Findings captured during research; the full write-up is summarized below.

## Resolution

Resolved 2026-07-24 by research subagent. Full findings captured during research;
summary follows.

**Recommended v1 tunable set:**

- **Offer** (decoding knobs, all classes): `temperature` (already shipped),
  `top_p` (already on `SamplingConfig`, unset), `seed` — documented
  **best-effort, not a reproducibility guarantee** (`gemini-2.5-pro` reported
  non-deterministic even with fixed seed + `temperature = 0`).
- **Offer per tier, class-guarded:** `thinking_config.thinking_budget` — legal
  ranges differ by class, so never one shared value.
- **Reserve:** `candidate_count` (Vertex 1–8; *is* the deferred Self-MoA
  mechanism — extra candidates are silently dropped without ticket 009's
  union/dedupe), plus `top_k` / `max_output_tokens` / `presence_penalty` /
  `frequency_penalty` (unmeasured, each fights structured-JSON extraction).
- **Forbid** (owned elsewhere / contract-breaking): `response_schema` (ADK
  **raises** if set — `llm_agent.py:1078-1087`), `response_mime_type` (forced to
  `application/json`, override silently discarded), `stop_sequences` (truncates
  schema JSON), `http_options` / timeout (owned by `config/resilience.toml`),
  `tools` / `system_instruction` (ADK rejects).

**Installed-source facts:** all candidate fields exist on
`GenerateContentConfig` (`types.py:6018-6242`); `top_k` is typed **float**; ADK
forwards the whole config verbatim to Vertex `generate_content`
(`google_llm.py:282-285`), stripping no decoding param; `set_output_schema`
sets `response_schema` + `response_mime_type` from each node's `output_schema`
(`basic.py:92-94`).

**Per-class landmine:** `thinking_budget` — flash **0–24,576** (0 = off legal);
pro **128–32,768** (**0 is a 400**, cannot be disabled). Ticket 02's schema must
model off/auto/N intent and resolve to a class-legal value.

**Two doc gaps** (flagged in the file, not guessed): exact Vertex 2.5
`presence_penalty` / `frequency_penalty` ranges (client-rendered doc tables did
not yield values); per-class thinking numbers and seed semantics are cited to
official Google (Firebase AI Logic + Vertex GenerationConfig REST reference).
