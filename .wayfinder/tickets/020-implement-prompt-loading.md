---
id: 020
title: "Implement prompt loading, DraftThreat, and prompt lints"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: [019]
---

## Question

Fit the code to the prompt layout authored by [Author agent prompt files and category exemplars](019-author-prompt-files.md), per the decisions in [Author agent prompts and few-shot exemplars](013-agent-prompts-exemplars.md).

1. **Loader rename.** Move the shipped loader to `stride_service/markdown_loader.py` as a neutral `MarkdownLoader` with `MarkdownNotFoundError`/`MarkdownFormatError`; keep `SkillLoader` and the existing error names as aliases so the closed tickets depending on them stay green. One implementation — no `PromptLoader` subclass. The 102+ existing tests are the safety net for the refactor.
2. **`DraftThreat`** in `stride_service.report`: the seven analyst-owned fields (`id`, `category`, `title`, `description`, `affected_element_ids`, `severity`, `mitigations`), reusing the existing category-letter validator. No `verdict`, no `confidence` — those are the critic's. Give the critic path a typed `list[DraftThreat]` → `list[Threat]` + `rejected_threats` shape.
3. **`stride_service/prompts.py`** with `compose_analyst_prompt(loader, category)` and the peers for extract/repair/critic, mirroring `compose_analyst_skills`' stable-first ordering so the cacheable prefix survives.
4. **Mechanical critic checks** in the join/assemble `FunctionNode` seam — element-ID resolution against the System Model, plus wiring the existing category-letter and verdict-shape validators — so the critic prompt never asks the LLM to verify them.
5. **`tests/test_prompt_lints.py`**, mirroring `test_skill_lints.py`: four bodies with the exact four headings in order and non-empty; `prompts/exemplars/` containing exactly the six `StrideCategory` filenames (derived from the enum, not hardcoded); ≥3 H2 sections per exemplar file, each with exactly one fenced `json` block; **every block parsing as `DraftThreat`**; per-file token caps as module constants alongside the skill caps; no stray files under `prompts/`.

Note `test_skill_lints.py:68` hardcodes the `("stride/", "shared/", "domains/")` prefixes — prompts live under a sibling `prompts/` root, so that lint needs no change, but the prompt equivalent must be written rather than inherited.

## Resolution

Resolved 2026-07-20. Code fitted to the shipped prompt layout; suite 183 → **268 passed, 1 skipped**.

- **One loader, two roots.** `stride_service/markdown_loader.py` holds the implementation — `MarkdownLoader`, `MarkdownNotFoundError`/`MarkdownFormatError`, plus `estimate_tokens`/`split_sections`/`extract_section`, all root-agnostic. `stride_service.skills` now imports and re-exports them (`SkillLoader = MarkdownLoader`, and both error aliases) and keeps only the skill-specific parts: headings, caps, digest, composition. No subclass — the alias is an assignment, so `except SkillNotFoundError` catches a prompt failure and vice versa. The 183 existing tests passed unchanged through the move, which is what made it safe.
- **`DraftThreat` is `Threat`'s base**, not a parallel model. The seven analyst-owned fields and the category-letter validator live on `DraftThreat`; `Threat` adds `confidence` and `verdict` and inherits the rule, so a draft and the threat it becomes are checked identically and promotion is `Threat(**draft.model_dump(), ...)`. Serialization key order shifts (`confidence`/`verdict` now trail `mitigations`) — nothing asserts key order, and the report round-trips.
- **`STRIDE_CATEGORIES` moved to `stride_service.report`**, next to the `StrideCategory` it derives from; `skills` re-exports it. `critic.py` needed canonical category order and importing `skills` for it would have been backwards.
- **`stride_service/prompts.py`** — `compose_analyst_prompt(loader, category)` (shared body, then that category's exemplar file) plus `compose_{critic,extract,repair}_prompt`. Stable-first as ticket 006: the one `analyst.md` body is byte-identical across all six analysts, so the cacheable prefix ends only where the exemplars begin. Placeholders pass through untouched for ADK state templating. Composed analyst prompt 2459-2539 tokens; with skill text, 4635-4874 — inside the 6-8K envelope.
- **`stride_service/critic.py`** is the mechanical half of the critic step, both seams fail-closed with every issue listed at once. `join_drafts` merges the six analysts in canonical STRIDE order and adds the two checks that need the whole set: element references resolving against the System Model, IDs unique across it (plus the draft-vs-key category match, free at the join). `assemble_threats` checks the critic returned exactly the drafted set — nothing invented, nothing dropped — re-checks references after the critic's edits, checks `needs-info` unknowns resolve, then splits into `threats` (sorted most-severe-first, ties on ID) and `rejected_threats`. Category letters and verdict shape come free from the models; the seam wires them by construction.
  - **Why raise rather than drop.** A hallucinated element ID or a vanished threat is a defect to surface, not to paper over by discarding entries — same disposition as the extraction gate's structured failure. Dropping would also hide exactly the failure the eval suite needs to see. Model output is untrusted input (OWASP LLM05): it is validated here, before anything reaches the report.
  - Parsing raw model JSON into `DraftThreat`/`Threat` stays with [ADK graph assembly](021-adk-graph-assembly.md) — these functions take typed lists, so the node owns the parse and this module owns the cross-set logic.
- **`tests/test_prompt_lints.py`** (55 cases) ports ticket 019's verification script: four bodies with the exact four headings in order and non-empty, no fence in any `## Output`, exemplar filenames derived from `StrideCategory`, ≥3 sections each holding exactly one fenced `json` block, every block parsing as `DraftThreat` with a matching category letter, every `affected_element_ids` entry resolving against the IDs `analyst.md`'s `## Input` defines, per-file caps from the `prompts` module constants, and `prompts/` containing exactly the ten expected names and no non-`.md` files. All green against the shipped content on first run — content and code agree without either being adjusted to the other. Plus `tests/test_prompts.py` (composition, alias identity, traversal denial) and `tests/test_critic.py` (both seams), and a `sample_draft` factory.
- The composed-analyst budget assertion (3500 tokens) is a new guard: it is the only test that fails when prompt and exemplar growth stay individually under cap but sum past the envelope.
