# Map: Agentic STRIDE Threat-Modeling Workflow

Label: `wayfinder:map`
Tickets live in `.wayfinder/tickets/` as child issues. A ticket is claimed by setting its `assignee:` frontmatter; open+unassigned+unblocked = frontier.

## Destination

A production-quality STRIDE threat-modeling service in this repo: Python, Google ADK graph-based multi-agent workflow, self-owned container on Cloud Run behind Ping-authenticated endpoints. Front-end submits semi-structured text one-shot, receives a job handle, polls/streams until a structured JSON report (per-element threats + severity + mitigations, traceable to input elements) is ready. Shipping includes a golden-case eval suite in CI.

## Notes

- Language: Python. Always consult the `python-style` skill when writing Python and the `owasp-security` skill for security-relevant code.
- **Execution is carried into this map** (destination is a full production build): once design tickets close, implementation tickets graduate from the fog.
- Per-agent Vertex model selection is a hard requirement (e.g., Flash for extraction, stronger models for analysis). Token/context optimization is a standing preference.
- Prompts load via an existing PromptLoader class (directory of files; not yet in this repo — see [Obtain existing PromptLoader interface](tickets/011-prompt-loader-interface.md)). Skills likely load the same way; stub for now.
- Commits: no AI attribution trailers. User prefers concise communication.

## Decisions so far

- Deployment boundary — Cloud Run (own container; Agent Engine rejected: IAM-only endpoint auth can't front Ping, logging constrained to stdlib/Cloud Logging paths, Agent Identity pre-GA).
- Input shape — hybrid: semi-structured front-end text normalized by a cheap extraction agent into a canonical system model.
- Interaction model — one-shot with async delivery (job handle, poll/stream progress; no mid-run user input).
- Output — structured JSON: per-element STRIDE threats with severity + mitigations, traceable to input flows/endpoints.
- Quality bar — golden-case eval suite in CI; cases bootstrapped from OWASP Threat Model Cookbook + internal systems, SME-blessed once.
- [Research: multi-agent quality patterns for threat modeling](tickets/001-research-quality-patterns.md) — generator–critic wins; skip debate and mixed MoA; STRIDE-per-element fan-out + one strong-tier critic pass, ~2-4x token budget. Findings on `research/quality-patterns`.
- [Research: ADK graph features and per-agent Vertex model config](tickets/002-research-adk-graph.md) — ADK 2.5.0 Workflow Runtime: directed graph with conditional edges/JoinNode fan-in, per-node model strings, `include_contents='none'` + state templating for context scoping, SSE streaming, `get_fast_api_app()` on Cloud Run. Findings on `research/adk-graph`.
- [Domain model: canonical system representation](tickets/003-domain-model-system-representation.md) — classic five DFD element types, typed-slug IDs, flat trust zones with derived boundary crossings, fixed security-relevant attributes with explicit `unknown` + assumptions list, controlled asset-tag enum, mechanical validity gate with one repair pass. Glossary in `CONTEXT.md`.
- [Decide graph topology and quality pattern](tickets/004-graph-topology-quality-pattern.md) — six parallel STRIDE-category analysts + one grounded critic pass (verdicts, dedupe, severity calibration); no debate/voting; static ADK Workflow: extract → validate/repair → prepare → 6 analysts → join → critic → router → assemble, with deterministic FunctionNode bookends and a reserved REVISE route.
- [Implement System Model schema and validator](tickets/012-implement-system-model-schema.md) — shipped as `stride_service` package (Pydantic 2, src layout, uv): five element types + typed-slug ID helpers, derived `boundary_crossings()`, mechanical gate returning structured `ValidationIssue`s, `parse_and_validate()` for the repair pass; 29 tests green.
- [STRIDE report schema and severity model](tickets/005-report-schema.md) — likelihood×impact with matrix-derived band (DREAD rejected); per-threat critic-calibrated confidence; verdicts with unknown-refs; `rejected_threats` audit array; self-contained payload embedding the System Model; job/nodes/summary metadata. Prototype on `prototype/report-schema`.
- [Skills-as-SME design and injection](tickets/006-skills-sme-design.md) — two-axis library: six per-analyst category skills + shared severity rubric, mechanical domain-pack hook (empty in v1); five fixed sections per skill, exemplars stay in prompts; repo Markdown baked into image via PromptLoader-mirroring SkillLoader; full text in system instruction with CI token caps; critic gets rubric + mechanically assembled boundary digest.
- [Per-agent Vertex model tier assignment](tickets/007-model-tier-assignment.md) — two named tiers, pinned version strings (no aliases): flash → extract/repair, pro → six analysts + critic; versioned config file with env-var override on tier strings only; no cross-tier auto-degrade, upgrades eval-gated.
- [Front-end API contract (async jobs, Ping auth)](tickets/008-api-contract.md) — custom `/v1` job API wrapping the ADK Runner (stock ADK routes non-prod only): POST /v1/jobs → poll GET /v1/jobs/{id} + SSE /events → GET /v1/jobs/{id}/report; lifecycle queued→running→completed|failed|rejected with ValidationIssues on rejection; RFC 9457 errors; Ping JWT dependency, owner-only reads (404); storage/TTL deferred.

## Not yet specified

- Observability: wiring the custom logging library, tracing/OTel across graph nodes.
- Error handling in the graph: partial results, retries, timeouts, poison inputs.
- CI/CD pipeline and Cloud Run deployment details (Ping middleware specifics follow org patterns).
- Job store backend and retention TTL (report JSON at rest; contract reserves 404-after-expiry — see [Front-end API contract](tickets/008-api-contract.md)).
- Cost/quota controls per model tier.
- Technology-domain skill packs: authoring + the pack-selection rule table in `prepare` — graduate when evals show domain-specific recall gaps.
- Implementation build-out tickets — graduate once topology, report schema, and contracts close. (System Model schema graduated and shipped via [Implement System Model schema and validator](tickets/012-implement-system-model-schema.md); report schema graduated as [Implement report schema in stride_service](tickets/014-implement-report-schema.md); skills design graduated as [Author v1 STRIDE category skills and severity rubric](tickets/015-author-skill-files.md) and [Implement SkillLoader, boundary-digest assembly, and CI skill lints](tickets/016-implement-skill-loader.md); model tiering graduated as [Implement model-tier config in stride_service](tickets/017-implement-model-config.md); API contract graduated as [Implement job API in stride_service](tickets/018-implement-job-api.md).)

## Out of scope

- Per-analysis human review flow (pending_review states, reviewer queue/roles) — deliver reports as-is, labeled AI-generated; a later effort if wanted.
- Vertex AI Agent Engine migration — rejected for this effort (see Decisions).
- Front-end implementation — this effort owns the service contract only.
