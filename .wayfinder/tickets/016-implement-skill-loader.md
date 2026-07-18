---
id: 016
title: "Implement SkillLoader, boundary-digest assembly, and CI skill lints"
label: wayfinder:task
status: open
assignee:
blocked-by: [006]
---

## Question

Implement in `stride_service` per [Skills-as-SME design and injection](006-skills-sme-design.md): a `SkillLoader` mirroring PromptLoader's directory-of-files interface (stub the obvious signature now; reconcile when [Obtain existing PromptLoader interface](011-prompt-loader-interface.md) resolves — do not block on it), mechanical assembly of the critic's category-boundary digest from section 1 of the six category files, composition of a node's skill text (category → shared rubric → selected domain packs), and CI lint tests over `skills/**/*.md`: token caps (category ≤ 3K, rubric ≤ 1K, pack ≤ 2K) and exact fixed section headings. Include tests for digest extraction against malformed/missing headings.
