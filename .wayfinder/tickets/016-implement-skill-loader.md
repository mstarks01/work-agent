---
id: 016
title: "Implement SkillLoader, boundary-digest assembly, and CI skill lints"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: [006]
---

## Question

Implement in `stride_service` per [Skills-as-SME design and injection](006-skills-sme-design.md): a `SkillLoader` mirroring PromptLoader's directory-of-files interface (stub the obvious signature now; reconcile when [Obtain existing PromptLoader interface](011-prompt-loader-interface.md) resolves — do not block on it), mechanical assembly of the critic's category-boundary digest from section 1 of the six category files, composition of a node's skill text (category → shared rubric → selected domain packs), and CI lint tests over `skills/**/*.md`: token caps (category ≤ 3K, rubric ≤ 1K, pack ≤ 2K) and exact fixed section headings. Include tests for digest extraction against malformed/missing headings.

## Resolution

Resolved 2026-07-19. Shipped as `stride_service.skills` (43 new tests; suite 102 green, 1 expected skip for the empty v1 pack set):

- **`SkillLoader`** — directory of Markdown files in, named items out; names are root-relative POSIX paths without `.md` (`stride/spoofing`, `shared/severity_rubric`, `domains/<pack>`). Stub signature per the ticket; reconcile with [Obtain existing PromptLoader interface](011-prompt-loader-interface.md) when it resolves. Fail-closed: unknown names and path-traversal names raise `SkillNotFoundError`; malformed section structure raises `SkillFormatError` — no silent degradation.
- **Digest** — `category_boundary_digest()` assembles the critic's lane digest from the `## Scope` section of the six category files, canonical STRIDE order, verbatim. Headings are matched exactly (taken raw after `## `), so drift fails loudly.
- **Composition** — `compose_analyst_skills(loader, category, domain_packs=())` in stable-first order category → rubric → packs (cacheable prefix per ticket 006); `compose_critic_skills()` = rubric + digest only.
- **CI lints** (`tests/test_skill_lints.py`, over the real `skills/**/*.md`): filenames == `StrideCategory` literals; exact five H2 headings in order, nonempty; token caps via `estimate_tokens()` (words × 4/3, the ticket-015 convention) — category ≤ 3K, rubric ≤ 1K, pack ≤ 2K; no stray files outside `stride|shared|domains`; digest budget 2K (shipped digest ≈ 1.6K — ticket 006's ~1–1.5K estimate was slightly under, headroom noted in the lint comment).
- Caps/headings exported as constants (`SKILL_SECTION_HEADINGS`, `*_TOKEN_CAP`) so prompts and future tooling reference one source of truth.

Facts later tickets depend on: worst-case analyst skill text today ≈ 2.2K tokens (no packs), critic ≈ 2.3K — well inside the ~6–8K instruction budget; ticket 010 can treat both as stable cacheable prefixes.
