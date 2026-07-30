# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues on **`mstarks01/work-agent`**. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.

Both `sub_issues` and `dependencies` are `gh api` calls, not `gh issue` subcommands:

```bash
BLOCKER_ID=$(gh api repos/mstarks01/work-agent/issues/<blocker> --jq .id)   # database id, not #number
CHILD_ID=$(gh api repos/mstarks01/work-agent/issues/<child> --jq .id)
gh api --method POST repos/mstarks01/work-agent/issues/<map>/sub_issues -F sub_issue_id=$CHILD_ID
gh api --method POST repos/mstarks01/work-agent/issues/<child>/dependencies/blocked_by -F issue_id=$BLOCKER_ID
```

### The live map

_(none — no wayfinding effort is currently in flight. `/wayfinder` invoked with a loose idea
should chart a new map; there is nothing to resume.)_

### Completed efforts

Completed on GitHub Issues (canonical):

- [#24 — Map: answer "how do I use this?" — a first-run path for the integrator](https://github.com/mstarks01/work-agent/issues/24)
  — 10 tickets (8 resolved, 2 out of scope), charted 2026-07-29, complete 2026-07-30. A
  **planning** map: it decided the first-run path for an integrator embedding `StrideEngine`
  (docs plus two utilities — an unbloated in-process web app and a runnable `examples/`) and
  stopped at the spec. The map itself is a decision record; **the spec was implemented
  afterwards** as ordinary follow-up work needing no map, on `build/first-run-path`. Read the
  code first and the tickets' resolution comments for the reasoning behind it. The route:
  `uv sync` → model auth
  → lite web app → **Load example** → Analyze → embed in process, from a new `docs/First-Run.md`.
  The web app is two pages (a form page streaming `on_node` over SSE, then
  `docs/example-report.html` unedited with the run's JSON injected), clone-only in a top-level
  `webapp/`, never in the wheel, with uvicorn in a defaulted-on `web` dependency-group.
  `examples/` is the single source of truth for every code block in the prose. `docs/Home.md` is
  deleted, `README.md` is the sole index carrying no Python, and `docs/Configuration.md` sheds 176
  lines to HTTP-API.md and Architecture.md. **Two decisions here were later reversed by the
  maintainer and are not the current state.** #29 had the web app reuse `docs/example-report.html`
  at request time, and #34 had that file keep a real embedded sample, regenerated by dogfooding
  route steps 3–4 and guarded by an offline pytest check. Both are gone: the mock was deleted and
  the viewer moved to `webapp/report_view.html` as the app's own template, so `docs/` holds no
  sample report and no application code reads a documentation file. The guard, its issue (#36) and
  its PR (#37) were all closed unmerged. The lesson worth carrying: promoting a docs mock to a
  runtime dependency is what created the regeneration problem, not the mock itself.
  Evals are contributor-side
  and off the route: First-Run never mentions them, README's eval-side block is cut, and
  `evals/TUNING.md` gains an audience banner. A credential-free fixture runner and a stdin→stdout
  CLI were both charted in and ruled out of scope.

- [#3 — Map: make the model provider pluggable (multi-vendor)](https://github.com/mstarks01/work-agent/issues/3)
  — 12 tickets, complete 2026-07-28. A **planning** map: it settled the decisions
  (sole `LiteLlm` adapter, `base`/`strong` tiers, vendor-derived auth, served-build
  fingerprints, the certification bar, the four-file config cutover) and stopped
  short of implementing them. The implementation has since landed in PRs
  [#20](https://github.com/mstarks01/work-agent/pull/20) and
  [#21](https://github.com/mstarks01/work-agent/pull/21), so the decisions now
  live in code as well as in the tickets' resolution comments — read the code
  first and the comments for the reasoning behind it.

**Research and prototype branches are archived as tags, not branches.** Both maps' tickets
cite `research/*` findings files (`docs/research/*.md`, never merged to `main`) and
`prototype/*` code by branch name and commit SHA. Those nine branches were deleted on
2026-07-30; every tip is preserved as an annotated `archive/<branch>` tag, so the SHA
citations still resolve. Read one without touching the worktree:
`git show archive/research/litellm-sole-adapter:docs/research/litellm-sole-adapter.md`.
A future map's research subagents should expect the same treatment: the branch is throwaway,
the tag is the record.

Archived under `.wayfinder/`, from before this repo moved to GitHub issues:

- `.wayfinder/map.md` + `.wayfinder/tickets/` — the original service design map, 39
  tickets, complete 2026-07-23.
- `.wayfinder/model-tuning/` — per-tier ADK model tuning, 9 tickets, complete
  2026-07-25.

The two archived efforts use the local-markdown convention (`assignee:` frontmatter
as the claim, a `blocked-by:` list for dependencies) rather than the sub-issue and
dependency operations above; don't take them as a model for how to chart a new one.

Reopening any of the three would be a **fresh map, not a resumption** — including
#3, whose closed tickets are the *record* of decisions taken, not a backlog. Work
that merely implements #3's decisions needs no map at all.

Ordinary follow-up work — fixing drifted docs, implementing a settled decision,
repairing a bug — is not a wayfinding effort. Chart a map only when the route to
the destination is genuinely unclear and the effort is too big for one session.
