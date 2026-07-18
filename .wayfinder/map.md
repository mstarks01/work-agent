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
- [Domain model: canonical system representation](tickets/003-domain-model-system-representation.md) — classic five DFD element types, typed-slug IDs, flat trust zones with derived boundary crossings, fixed security-relevant attributes with explicit `unknown` + assumptions list, controlled asset-tag enum, mechanical validity gate with one repair pass. Glossary in `CONTEXT.md`.

## Not yet specified

- Observability: wiring the custom logging library, tracing/OTel across graph nodes.
- Error handling in the graph: partial results, retries, timeouts, poison inputs.
- CI/CD pipeline and Cloud Run deployment details (Ping middleware specifics follow org patterns).
- Cost/quota controls per model tier.
- Prompt authoring for each agent (after topology lands).
- Implementation build-out tickets — graduate once topology, report schema, and contracts close. (System Model schema graduated to [Implement System Model schema and validator](tickets/012-implement-system-model-schema.md).)

## Out of scope

- Per-analysis human review flow (pending_review states, reviewer queue/roles) — deliver reports as-is, labeled AI-generated; a later effort if wanted.
- Vertex AI Agent Engine migration — rejected for this effort (see Decisions).
- Front-end implementation — this effort owns the service contract only.
