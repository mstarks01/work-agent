---
id: 011
title: "Obtain existing PromptLoader interface"
label: wayfinder:task
status: closed
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

The existing PromptLoader class (loads prompts from a directory as individual files) is not in this repo. Human task: provide its source or interface signature so the stub here matches it exactly and the skill loader can mirror it. Resolution records where it lives and the interface facts later tickets depend on.

## Resolution

Resolved 2026-07-20. **The external PromptLoader is not available** — the human doesn't have the source or signature to hand. No reconciliation will happen; the stub stands.

- **`stride_service.skills.SkillLoader` is canonical, not provisional.** Its shipped shape is the interface: `SkillLoader(root: Path | str)`; `names() -> list[str]`; `load(name) -> str`. Names are root-relative POSIX paths without the `.md` suffix (`stride/spoofing`, `shared/severity_rubric`, `domains/<pack>`). Lazy per-call read, no caching, no templating — raw Markdown out. Fail-closed: unknown names and names resolving outside the root both raise `SkillNotFoundError` (traversal is denied as absent, never distinguished); malformed section structure raises `SkillFormatError`.
- **Prompt loading follows the same interface.** [Author agent prompts and few-shot exemplars](013-agent-prompts-exemplars.md) is no longer waiting on an external contract: it may define a `PromptLoader` in `stride_service` mirroring `SkillLoader` (directory of files in, named text out), or reuse `SkillLoader` against a `prompts/` root. That choice is ticket 013's to make — the constraint dropped, not the decision.
- **Unblocks** ticket 013, whose `blocked-by` drops to `[006]` (also already closed), putting it on the frontier.
- No code change beyond removing the now-false "reconcile once ticket 011 lands" notes from `src/stride_service/skills.py`; the suite stays 183 green.
