---
id: 017
title: "Implement model-tier config in stride_service"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Implement the model-tier configuration per the resolution of [Per-agent Vertex model tier assignment](007-model-tier-assignment.md): a versioned config file defining the two tiers (`flash`, `pro`) as pinned Vertex model version strings and the node→tier mapping (extract/repair → flash; six analysts + critic → pro); a loader exposing the resolved model string per node, with `STRIDE_MODEL_FLASH` / `STRIDE_MODEL_PRO` env vars overriding tier strings only (node→tier is file-only); validation that rejects `-latest`/alias strings and unknown node or tier names. AFK task: pure code, no decisions left. Plus tests (mapping resolution, env override precedence, alias rejection). Consult `python-style` and `owasp-security` skills.

## Resolution

Resolved 2026-07-19. Shipped as `stride_service.model_tiers` + `config/model_tiers.toml`.

- **Canonical LLM node names** (facts later graph/prompt tickets depend on): `extract`, `repair`, `analyst/<category>` for the six `StrideCategory` literals (e.g. `analyst/information-disclosure`), `critic` — exported as `LLM_NODES` / `ANALYST_NODES`. Deterministic FunctionNodes never appear in the config.
- **Config file** `config/model_tiers.toml` (versioned, `version = 1`): `[tiers]` maps `flash`/`pro` to pinned Vertex strings (seeded `gemini-2.5-flash-002` / `gemini-2.5-pro-002` — retune at deploy); `[nodes]` maps every LLM node to a tier per ticket 007.
- **Loader** `load_model_tiers(path, env=None)` → frozen Pydantic `ModelTierConfig` with `resolve_model(node) -> str`. `STRIDE_MODEL_FLASH`/`STRIDE_MODEL_PRO` override tier strings only (node→tier is file-only, no env path exists to change it); overrides are validated identically to file values; set-but-empty vars raise.
- **Fail-closed validation** (`ModelConfigError`): pinned-string rule = must not end `-latest` and must end in a numeric version suffix (`-\d{3,}`, e.g. `-002`) — rejects aliases and dated previews; unknown/missing tier names, unknown/missing node names, extra keys, and bad TOML all raise. No cross-tier fallback anywhere by design.
- 27 tests in `tests/test_model_tiers.py` (mapping resolution incl. the shipped repo config, env precedence, alias/unpinned rejection, completeness); suite 129 green. Smoke-verified: env override retunes the seven pro nodes without touching flash; `-latest` via env rejected with the env var named in the error.
