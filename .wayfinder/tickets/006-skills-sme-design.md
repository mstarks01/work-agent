---
id: 006
title: "Skills-as-SME design and injection"
label: wayfinder:grilling
status: closed
assignee: me
blocked-by: [004]
---

## Question

How do skills make agents subject-matter experts: what skills exist (per STRIDE category? per technology domain — web, cloud, mobile, CI/CD?), what's in one (methodology, threat catalogs, severity rubrics), how are they loaded (same directory-of-files pattern as the PromptLoader), and how are they injected into each node's context without blowing token budgets (full text vs selected-on-demand)?

## Resolution

Resolved 2026-07-18 via grilling, grounded in tickets 001 (quality patterns), 004 (topology), and the CONTEXT.md glossary.

1. **Taxonomy — two axes, staged.** Six STRIDE-category skills, statically bound one-per-analyst, plus a shared severity rubric loaded by analysts and critic. Technology-domain packs (web, cloud, mobile, CI/CD, …) are a second axis selected *mechanically* in the `prepare` FunctionNode from System Model facts (element attributes, asset tags) — never by an LLM. v1 ships the selection hook with an empty pack set; packs are authored only when evals show domain-specific recall gaps.
2. **Anatomy — five fixed sections per category skill:** (1) category definition & scope boundary (cuts cross-category dupes), (2) applicability notes per element type (explains *how* to analyze what the mechanical applicability matrix admits, never contradicts it), (3) attribute-triggered threat patterns keyed to the fixed attribute vocabulary — the recall driver, and what makes `unknown` actionable, (4) failure-mode guardrails for the two documented misses (second-order threats, wrong threat perspective), (5) mitigation guidance per pattern. Few-shot exemplars stay **out** of skills — they are prompt material, owned by ticket 013.
3. **Storage/loading.** Plain Markdown, one skill per file: `skills/stride/*.md` (six categories), `skills/shared/severity_rubric.md`, `skills/domains/` (empty in v1). Versioned in git, baked into the container image — a skill edit is a deploy, reproducible for evals via image digest. A `SkillLoader` class mirrors PromptLoader's interface (directory in, named items out); stubbed with the obvious signature until ticket 011 delivers the real interface, then reconciled.
4. **Injection.** Full skill text into the node's system instruction — no RAG or on-demand section selection (retrieval adds a silent-recall-loss failure mode; the corpus is small). Order stable-first: category skill → shared rubric → selected domain packs → task instructions. With `include_contents='none'` + state templating (per ticket 002), a node's instruction side is identical across jobs — the cacheable shared prefix ticket 010 will exploit. Token budget enforced by CI lint over `skills/**/*.md`: category ≤ 3K tokens, rubric ≤ 1K, pack ≤ 2K (worst-case analyst instruction ~6-8K).
5. **Critic loads:** the shared severity rubric + a category-boundary digest assembled mechanically by SkillLoader from section 1 of all six category files (~1-1.5K tokens) — same lane definitions the analysts used, single source of truth. No threat catalogs, mitigation sections, or domain packs: verdicts anchor to System Model facts, not generative material. Section headings are structurally fixed and CI-lint-enforced to make the extraction safe. Extract/repair nodes load no skills.

Graduated tickets 015 (author v1 skill files) and 016 (implement SkillLoader + CI lints).
