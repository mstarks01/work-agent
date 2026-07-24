# Map: Per-tier ADK model tuning parameters

Label: `wayfinder:map`
Effort dir: `.wayfinder/model-tuning/`. Tickets live in `.wayfinder/model-tuning/tickets/` as child issues, numbered from `01`. A ticket is claimed by setting its `assignee:` frontmatter; open + unassigned + unblocked = frontier. Blocking is the `blocked-by:` list; a ticket is unblocked when every id it lists is `resolved`/`closed`.

**Status: charting (2026-07-24).** Fresh effort, separate from the completed [design map](../map.md). Destination named through grilling; three decision tickets created, one on the frontier.

## Destination

Both model classes the workflow uses (`flash`, `pro`) carry their own ADK/GenAI `GenerateContentConfig` tuning params, loaded fail-closed from the pinned config and applied **per node** in the graph. The pinned file stays canonical — eval and production read the same values — every run **stamps the effective per-tier params** for provenance, tuning is done by an **eval sweep that promotes a winner into the file**, and env override survives only as a **recorded, eval-gated escape hatch**. Shipped end-to-end with offline tests and docs. The tuned *values* and any live sweep run are out of scope (no Vertex here); this effort ships the mechanism, `temperature = 0` stays the default.

## Notes

- Language: Python. Consult `python-style` when writing Python and `owasp-security` for security-relevant code.
- **Execution is carried into this map** (user chose build, not plan-only): design tickets graduate implementation tickets from the fog.
- **Settled policy this effort turns on** (grilled 2026-07-24): pinned file is the source of truth eval+prod share; every run records the *effective* per-tier params (a fingerprint) so a result is self-defending; tuning is an eval sweep that promotes the winner into the file; env override is a recorded, eval-gated escape hatch, not the tuning path. This generalizes the model-string precedent (`STRIDE_MODEL_*` is overridable but the harness records the served `model_version` and upgrades are eval-gated — see [Verify pinned model strings](../tickets/026-verify-pinned-model-strings.md)) to sampling.
- Builds directly on prior-map decisions: two tiers ([007](../tickets/007-model-tier-assignment.md)/[017](../tickets/017-implement-model-config.md)), pinned single-block sampling ([009](../tickets/009-eval-suite-design.md) decision 15 / [023](../tickets/023-implement-eval-harness.md)), the resilience env-override precedent ([038](../tickets/038-graph-failure-policy.md)/[039](../tickets/039-implement-failure-policy.md)), served-version provenance ([026](../tickets/026-verify-pinned-model-strings.md)).
- **Deterministic code, models for judgement**: config loading and per-node param application are mechanical and fail-closed — no model judgement involved.
- **Author content before loader code**: design the `sampling.toml` layout before wiring the loader/graph.
- Commits: no AI-attribution trailers. User prefers concise communication.

## Decisions so far

<!-- charting resolves nothing; populated as tickets close -->

## Not yet specified

- **Implementation build-out** — graduates once the schema ([Decide the per-tier tuning config schema and sampling.toml migration](tickets/02-config-schema-migration.md)) and provenance/override ([Decide provenance stamping and the env-override / eval-gate policy](tickets/03-provenance-and-override-policy.md)) tickets close. Covers: restructuring `SamplingConfig` from one global object to a per-tier resolver; making `graph._generate_content_config` per-node (resolve node → tier → params); the provenance-stamping code in the report/run artifacts; parametrizing the eval harness so a sweep can vary per-tier params (offline-testable against scripted stand-ins); and updating `docs/Configuration.md`. Can't be sharply specified until the schema and provenance shapes are decided, so it stays fog. The eval **sweep run itself** and the tuned numbers stay out of scope (live Vertex).
- Whether the eval judge's own model params (`evals/config/judge.toml`) adopt the same per-class tuning shape or stay a separate pinned config — depends on the schema decision; revisit when [the schema ticket](tickets/02-config-schema-migration.md) closes.

## Out of scope

- **Running a live tuning sweep and choosing the "best" per-tier values** — needs live Vertex, the same boundary the whole live-eval chain sits behind on the [design map](../map.md). This effort ships the mechanism and offline machinery only; defaults are unchanged (`temperature = 0`), and the numbers are a future eval effort.
- **Any move off Gemini 2.x** — already ruled out on the design map; per-class applicability is decided against `gemini-2.5-flash` / `gemini-2.5-pro` only.
