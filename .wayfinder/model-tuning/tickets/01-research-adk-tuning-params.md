---
id: 01
title: "Research: ADK 2.5 tuning params — the GenerateContentConfig surface, per-class applicability, and which ADK owns"
label: wayfinder:research
status: claimed
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

**Deliverable:** a table `param → { exposed?, per-class?, safe-to-tune?, notes }` plus a short recommended v1 tunable set. Most of (1) is answerable by reading the installed package; (2)/(5) need Vertex Gemini 2.5 docs. Capture findings on branch `research/model-tuning-params` with a context pointer here.

## Context pointer

Findings land on branch `research/model-tuning-params`.
