# CLAUDE.md

Project instructions for the STRIDE threat-modeling service.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `mstarks01/work-agent`, driven through the `gh` CLI;
wayfinder maps use native sub-issues and issue dependencies. Completed local-markdown
maps under `.wayfinder/` are archived history, not live. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` glossary at the repo root, ADRs in `docs/adr/`.
See `docs/agents/domain.md`.
