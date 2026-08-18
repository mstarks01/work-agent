# CLAUDE.md

Project instructions for the STRIDE threat-modeling service.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `mstarks01/work-agent`, driven through the `gh` CLI;
wayfinder maps use native sub-issues and issue dependencies. Completed local-markdown
maps under `.wayfinder/` are archived history, not live. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name: `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`. The `wayfinder:*` and GitHub stock labels are
orthogonal to these. See `docs/agents/triage-labels.md`.

### Framework parity

Any fix, enhancement, eval or test for one **Framework Package** needs an explicit answer
in the PR body for **every other package in `PACKAGES`** — "nothing changes, because a
framework whose claims carry a catalog identifier needs no equivalence judgement" is a
fine answer; silence is not. State the reason as a property of the framework, never as its
name, so it answers for packages nobody has written yet. It runs every way, not outward
from STRIDE. See `docs/agents/framework-parity.md`.

### Domain docs

Single-context: one `CONTEXT.md` glossary at the repo root, ADRs in `docs/adr/`.
See `docs/agents/domain.md`.
