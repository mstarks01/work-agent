---
id: 019
title: "Author agent prompt files and category exemplars"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

Write the prompt content decided by [Author agent prompts and few-shot exemplars](013-agent-prompts-exemplars.md) — content first, composition code second (ticket 020 fits the code to this layout, not the reverse).

Deliverables under `prompts/`:

- `extract.md`, `repair.md`, `analyst.md`, `critic.md` — each with exactly the four H2 headings `Role`, `Input`, `Procedure`, `Output`, in order, all non-empty. No JSON in `## Output`: semantics in prose only (good `title` vs `description`, the per-category ID numbering rule, when `mitigations` may be empty).
- `prompts/exemplars/<category>.md` ×6, filenames matching the `StrideCategory` literals exactly (as `skills/stride/*.md` already do). Three exemplars each — canonical, second-order, unknown-conditional — one H2 per exemplar using its unique name, each section containing exactly one fenced `json` block that parses as `DraftThreat`. Wrong-perspective contrast goes in the prose body only, never as JSON.
- One shared miniature system model in `analyst.md`'s `## Input`: a handful of elements and flows spanning a trust boundary, whose element/flow IDs all eighteen exemplars cite.
- `analyst.md` leaves the category as an ADK state placeholder — no per-category copies.
- `extract.md` carries two inline exemplars (sparse input → heavy `unknown`; a legitimate inference recorded in `assumptions`).

Token caps to write against: `analyst.md` ≤2000, each exemplar file ≤1500, `critic.md` ≤1500, `extract.md` ≤1500, `repair.md` ≤800.

The critic prompt's `## Procedure` runs the five judgment steps in fixed order (evidence → lane → duplicate → severity → confidence, 1-3 gating), and must not ask the LLM to perform any mechanical check that ticket 020 puts in code.
