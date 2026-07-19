---
id: 012
title: "Implement System Model schema and validator"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Implement the canonical System Model as Pydantic models plus the mechanical validator, exactly per the resolution of [Domain model: canonical system representation](003-domain-model-system-representation.md): five element types with the agreed attribute sets, typed-slug ID scheme, `unknown` values, top-level assumptions list, controlled asset-tag enum (config-extendable), derived boundary-crossing computation, and the validity-gate rules returning structured errors suitable for the repair pass. AFK task: pure code, no decisions left. Consult `python-style` and `owasp-security` skills.

## Resolution

Resolved 2026-07-18. Bootstrapped the Python package (`pyproject.toml`, src layout, uv-managed, Pydantic 2) and implemented the schema per ticket 003:

- `src/stride_service/system_model.py` — the five element types (`ExternalEntity`, `Process`, `DataStore`, `DataFlow`, `TrustBoundary`) with the agreed common + per-type attribute sets, `extra="forbid"` and length limits on all strings. Only the resolution's enums are `Literal`s (entity `kind`, `exposure` incl. `unknown`, boundary `kind`); other security-relevant attributes are free-form strings with the `UNKNOWN` sentinel. Typed-slug ID helpers (`normalize_name`, `make_element_id`, `make_flow_id`; prefixes `entity`/`process`/`store`/`flow`/`boundary`), top-level `assumptions` list, `SystemModel.boundary_crossings()` derives crossings mechanically and fails closed on dangling endpoints.
- `src/stride_service/validation.py` — the mechanical gate: `validate()` returns structured `ValidationIssue`s (codes: `schema`, `duplicate-id`, `id-mismatch`, `invalid-reference`, `no-trust-zones`, `illegal-asset-tag`) covering unique deterministic IDs, referential integrity of flow endpoints / `trust_zone` / assumptions, ≥1 zone, and the asset vocabulary (config-extendable via `extra_asset_tags`). `parse_and_validate()` converts Pydantic parse errors into the same issue shape for the repair pass; schema failure returns no model (fail closed).
- `tests/` — 29 pytest cases (factory model in `tests/factories.py`); all pass via `uv run --group dev pytest`.

Interpretation calls made (flag in review if wrong): flow-ID slugs come from endpoint IDs minus type prefix with label = normalized flow name; `trust_zone` holds the Trust Boundary's element ID; flow endpoints must be zoned elements (never boundaries/flows).
