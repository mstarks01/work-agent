# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the STRIDE service glossary (System Model, Element, Model Tier, Verdict, …).
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

This repo is **single-context**: one `CONTEXT.md` at the root, one `docs/adr/`.

```
/
├── CONTEXT.md          ← the glossary; exists and is maintained
├── docs/adr/           ← not created yet; /domain-modeling adds it lazily
└── src/stride_service/
```

There is no `CONTEXT-MAP.md` and there are no per-context `src/<context>/docs/adr/`
directories — this is a single Python package, not a monorepo. If that ever changes,
re-run `/setup-matt-pocock-skills` to switch to the multi-context layout.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (LiteLlm as the sole model adapter) — but worth reopening because…_
