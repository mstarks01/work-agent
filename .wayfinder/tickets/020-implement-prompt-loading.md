---
id: 020
title: "Implement prompt loading, DraftThreat, and prompt lints"
label: wayfinder:task
status: open
assignee:
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
