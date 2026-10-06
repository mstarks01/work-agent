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

[#1522](https://github.com/mstarks01/work-agent/issues/1522) — persistent storage for jobs,
reports and sources. PostgreSQL or SQLite holds job state and principals; an S3-API store or the
filesystem holds reports and sources. Read the map's Decisions so far before you take a ticket.

### Completed maps

A completed map lives as a closed GitHub issue. Its tickets' resolution comments hold the reasoning,
and the code holds the current state, so read the code first. Each map below settles the rules named
beside it.

- [#491](https://github.com/mstarks01/work-agent/issues/491) — Amazon Bedrock as a vendor row.
  - A deployment declares the credential mechanism, and the vendor's SDK may then discover the
    material. An `IAM` mode passes no credential material at all
    ([#493](https://github.com/mstarks01/work-agent/issues/493)).
  - `boto3` is an optional extra named `bedrock`, because litellm reaches a bare `import boto3` in
    both credential modes ([#498](https://github.com/mstarks01/work-agent/issues/498), ADR 0023).
  - A Bedrock model identifier matches one regex,
    `[<scope>.]anthropic.claude-<name>-<major>[-<minor>]` with an optional date and build tail. An
    ARN is refused, because it hides which model ran and carries an account id into a fingerprint
    ([#494](https://github.com/mstarks01/work-agent/issues/494)).
  - The Claude generation parse is vendor-blind: every Claude pattern composes from shared atoms
    ([#495](https://github.com/mstarks01/work-agent/issues/495)).
  - No region enters the fingerprint, on any vendor. A region field would be wrong exactly when a
    cross-region profile is in use ([#496](https://github.com/mstarks01/work-agent/issues/496)).
  - **The Bedrock conformance pair is `global.anthropic.claude-sonnet-4-6` and
    `global.anthropic.claude-opus-5-5`** ([#497](https://github.com/mstarks01/work-agent/issues/497),
    [#626](https://github.com/mstarks01/work-agent/issues/626)). The pair must profile both schema
    paths, and only Claude can: Nova and Llama take only the forced tool path (ADR 0058). The pinned
    litellm sends Sonnet 4.6 on the native path and Opus 5.5 on the tool path. The `global.` prefix
    names a cross-Region inference profile and no geography, so #496 holds.
  - **A floating marker is a whole word, never a fragment of one.** One table of `word -> message`
    is the single reader of the rule ([#605](https://github.com/mstarks01/work-agent/issues/605)).
  - A Google API key is a separate vendor row, `gemini`, not a second mode on `vertex`
    ([#600](https://github.com/mstarks01/work-agent/issues/600)).
- [#369](https://github.com/mstarks01/work-agent/issues/369) — the sitting app walks many cases in
  one session. A part-finished sitting is a **Draft Sitting** outside the repository, and one
  sitting pull request carries every finished case (ADR 0020). A case's recorded sets open only once
  the reader's own list for that case exists.
- [#319](https://github.com/mstarks01/work-agent/issues/319) — contributions arrive as pull
  requests. A vote binds to the GitHub account that submits it, and the ledger has one file per
  voter. CI proves an artifact agrees with itself and with the repository, never that a model ran.
  There is no spend ceiling: every amount carries a label (`recorded`, `estimated` or `unpriced`),
  and a person accepts it by typing it back.
- [#158](https://github.com/mstarks01/work-agent/issues/158) — one validated system
  representation, many security frameworks. One extraction serves every **Framework Package**. A
  package selects from the service's evidence catalog and never adds to it. Each package carries
  its own critic. The retrieval key decides where knowledge lives. The cutover plan is
  [#172](https://github.com/mstarks01/work-agent/issues/172).
- [#76](https://github.com/mstarks01/work-agent/issues/76) — every finding ties back to the input
  that justifies it. A claim carries a non-empty `grounds` list. The critic reviews grounds and
  cannot change them. Untrusted text never reaches `innerHTML`.
- [#49](https://github.com/mstarks01/work-agent/issues/49) — call transcripts as job input. A job
  carries a `sources` list, and its budget is in bytes, not tokens (ADR 0001). Every source carries
  equal weight, so extraction records a disagreement and does not settle it.
- [#24](https://github.com/mstarks01/work-agent/issues/24) — a first-run path for the integrator.
  `docs/First-Run.md` is the route. No application code reads a documentation file, and `docs/`
  holds no sample report.
- [#3](https://github.com/mstarks01/work-agent/issues/3) — a pluggable model provider. `LiteLlm` is
  the sole adapter, and the tiers, vendor-derived auth, served-build fingerprints and the
  certification bar follow from it.

**Research and prototype branches are archived as tags, not branches.** A map's research and
prototype work lives on a throwaway branch, and its tip is preserved as an annotated
`archive/<branch>` tag, so a SHA citation still resolves. Read one without touching the worktree:
`git show archive/research/litellm-sole-adapter:docs/research/litellm-sole-adapter.md`.

`.wayfinder/` holds two archived maps, the original service design map and per-tier model tuning.
They use the local-markdown convention (`assignee:` frontmatter as the claim, a `blocked-by:` list
for dependencies) rather than the sub-issue and dependency operations above; don't take them as a
model for how to chart a new one.

Reopening any completed map would be a **fresh map, not a resumption** — including
#3, whose closed tickets are the *record* of decisions taken, not a backlog. Work
that merely implements #3's decisions needs no map at all.

Ordinary follow-up work — fixing drifted docs, implementing a settled decision,
repairing a bug — is not a wayfinding effort. Chart a map only when the route to
the destination is genuinely unclear and the effort is too big for one session.
