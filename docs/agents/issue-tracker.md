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

[#158 — Map: one validated system representation, many security frameworks](https://github.com/mstarks01/work-agent/issues/158)
— charted 2026-08-12 from [#139](https://github.com/mstarks01/work-agent/issues/139), 11 tickets, 6 resolved.
A **planning** map: it settles the spec for a framework-neutral analysis layer, where one extraction and one
**Valid System Model** feed N in-repo framework packages, and it stops at the spec. STRIDE becomes the first
package rather than the architecture; ASVS proves the second.

The spine is settled. The coupling surface is verified ([#159](https://github.com/mstarks01/work-agent/issues/159)),
ASVS asks a system representation for almost nothing ([#160](https://github.com/mstarks01/work-agent/issues/160)),
the job selects its frameworks from a set the deployment carries
([#161](https://github.com/mstarks01/work-agent/issues/161)), the taxonomy holds and one extraction serves
every framework ([#162](https://github.com/mstarks01/work-agent/issues/162)), a thin `Claim` supertype carries
what the service constructs ([#163](https://github.com/mstarks01/work-agent/issues/163)), and a package is a
declaration object beside a text root, checked before the first model call
([#164](https://github.com/mstarks01/work-agent/issues/164)).

The frontier is [#165](https://github.com/mstarks01/work-agent/issues/165) (may a framework extend the
evidence catalog), [#166](https://github.com/mstarks01/work-agent/issues/166) (one critic, or one per
framework), [#167](https://github.com/mstarks01/work-agent/issues/167) (how a second framework is measured)
and [#169](https://github.com/mstarks01/work-agent/issues/169) (where the notes, cases and domain packs sit).
[#168](https://github.com/mstarks01/work-agent/issues/168), the report envelope, waits on
[#167](https://github.com/mstarks01/work-agent/issues/167) and closes the map.

Two constraints ride the whole effort. A framework package is **in-repo only** — no third-party code loads.
And the no-shim rule removes "decide later" from the report envelope, so
[#168](https://github.com/mstarks01/work-agent/issues/168) cuts over once or declares no change.

### Completed efforts

Completed on GitHub Issues (canonical):

- [#76 — Map: tie every finding back to the input text that justifies it](https://github.com/mstarks01/work-agent/issues/76)
  — 9 tickets, charted 2026-08-03 and completed 2026-08-04. A **planning** map: it settled the
  spec for **finding-level attribution** — every threat carrying a non-empty, machine-checkable
  record of what justifies it — and stopped at the spec. **Not yet implemented.** The
  implementation is a cutover whose plan is [#85](https://github.com/mstarks01/work-agent/issues/85);
  the live corpus sweep it needs is filed as
  [#87](https://github.com/mstarks01/work-agent/issues/87), open. Read #85 first — it carries the
  order of operations — then #77 for the vocabulary the whole repo inherits, then the other seven
  resolution comments.

  **#77 fixes vocabulary that reaches every file.** The record is `grounds: list[Ground]` with
  kinds `quote` / `unknown-attribute` / `derived-fact`; *finding-level attribution* is the concept
  name, `grounds` the field, kept clear of `attribute` and of the two live senses of
  "attribution". And **`Analyst` now names a human**: the six agents become **category agents** —
  `analyze/<category>` in `config/model_tiers.toml`, `analyze_<category>` as graph node and in
  report `nodes[].node`, `prompts/analyze.md`.

  The route in one pass. `Ground` is **one flat model** — `kind` plus every branch's fields,
  defaulted `""`, with a `_check_shape` validator requiring its own branch's fields and forbidding
  the others, reusing `Verdict`'s pattern (#79). The discriminated union was the more honest shape
  and lost to a measured fact: provider schema compilers are the unpredictable part of this system
  and this rides in six `strong`-tier requests. Branches: **quote** = `text` (1000) +
  `source_label`; **unknown-attribute** = `element_id` + `attribute`, a separate type from
  `UnknownRef`; **derived-fact** = `flow_id` alone, no free-text escape hatch. `schema_version` →
  **2.0**, one bump for the whole cutover, earned by #77's silently-breaking node rename.
  Quote verification is **substring-per-fragment under a five-step pinned ladder** run in
  `join_drafts` (#80): exact substring rejects **78.2%** of 206 corpus excerpts because the
  sources are hard-wrapped, whitespace collapse alone takes that to 1.0%, and the pinned ladder
  leaves **0 false rejections** plus one true one. Consequence is **marked per entry, closed per
  threat** — an unverifiable quote still renders, and the job fails closed only when *no* ground
  on a threat verifies. `source_excerpt` **survives with its job restated** (#81): the excerpt
  answers why the element exists, grounds why the threat was raised. Instruction is a **Procedure
  step** in `analyze.md`, once and always-on (#82), and **the branch follows the trigger rather
  than being chosen**, so a threat carrying no quote is *correct*. The critic **reviews** grounds,
  **cannot touch** them, and sees **no submitter text**; its only lever is `confidence` as a
  **downgrade-only** modifier, never `verdict` (#83). Grounds render **after** the analysis as a
  kind-coded rail under Affected elements, an unverified quote losing its quotation marks and
  naming the failure in visible text (#84). Render safety is settled without touching the schema
  (#78): untrusted text never reaches `innerHTML`, and the report page gains a **strict nonce
  CSP**. The corpus **does not change** — all 224 reference threats stay untouched, because
  `ReferenceThreat` deliberately does not carry what it does not grade (#85).

  Three findings outlived their tickets. **Resolving #78 found a live stored XSS on `main`** in
  the element table's attrs column, filed off-map as
  [#86](https://github.com/mstarks01/work-agent/issues/86) and fixed on
  `fix/escape-element-attrs`; #78's rule supersedes that fix at implementation time. **Resolving
  #81 found that a category agent never sees the submitter's source text**, which made the map's
  own settled decision 1 unimplementable — `analyze.md` gains `{input_text}` and
  `prepare_analysis` strips the three source fields from the model rendered to the agents *and*
  the critic. And **#85 found the eval side's `ungrounded` metric collides with the new field** —
  it means *hallucinated*, not *carrying no grounds* — renaming it `unsupported` across 96 sites.

  Corrections worth knowing: #80 corrected #79 on a mechanism — **there is no draft repair path**,
  so a bad draft raises out of `merge_drafts` and fails the job. #83 corrected #79's record —
  `related_unknowns` does have a referential check today, the `element_id` half. #85 corrected its
  own ticket twice: the exemplar guard is `tests/test_prompt_lints.py`, not
  `test_corpus_lints.py`, and reference grounding was assumed to be the bulk of the work when it
  is none of it.

  **PII residue** is the one thing ruled out of scope on closing, inherited from #49's fog: grounds
  put more submitted prose and more speaker names on screen, and only `source_speaker` is
  strippable. Provenance weighting by source kind and service-side retention of submitted text
  were ruled out while charting.

  Prototypes: `prototype/quote-verification` @ `6ed8d77` (#80's measurement, re-runnable) and
  `prototype/grounds-display` @ `94bac96` (#84's three variants, losers included). Both throwaway
  and both due the `archive/` tag treatment described at the end of this section.

- [#49 — Map: accept call transcripts as job input](https://github.com/mstarks01/work-agent/issues/49)
  — 10 tickets, charted and completed 2026-07-31. A **planning** map: it settled the spec for
  accepting analyst↔developer interview transcripts as job input and stopped there. **The spec has
  since been implemented** — written up as [#61](https://github.com/mstarks01/work-agent/issues/61)
  and merged 2026-08-01 in [#63](https://github.com/mstarks01/work-agent/pull/63), seven commits on
  `build/sources-cutover` following
  [#57, the cutover inventory](https://github.com/mstarks01/work-agent/issues/57): no green
  intermediate (the no-shim rule makes the contract atomic), config ahead of the contract, and one
  narrow ADR at `docs/adr/0001-sources-replace-description.md` creating the directory. **Read the
  code first**, then #57, then the other nine resolution comments for the reasoning behind it.

  Two things did not land with it. The **corpus regression run**
  ([#59](https://github.com/mstarks01/work-agent/issues/59)'s exit criterion) needs live provider
  credentials and has not been run against merged `main`; the `severity_rubric.md` edit reaches
  every analyst on every job, and 20 elements across 5 of the 12 cases carry `notes`. And the
  thirteenth corpus case is [#64](https://github.com/mstarks01/work-agent/issues/64), open and
  contributor-side — only its schema migration is in.

  The route in one pass: `description` is replaced by a uniform `sources` list of
  `{kind, label, text}` — `kind` a closed enum, `label` a required, unique, 200-char, model-visible
  citation key, order presentation-only ([#50](https://github.com/mstarks01/work-agent/issues/50)).
  Budget is **bytes, not tokens** (tokens would make the public contract deployment-dependent):
  100 KiB total and 10 sources, no per-source cap, on `config/resilience.toml` **v3**
  ([#52](https://github.com/mstarks01/work-agent/issues/52)). Extraction gains six conversational
  rules placed **once and always-on** in `extract.md`
  ([#53](https://github.com/mstarks01/work-agent/issues/53),
  [#55](https://github.com/mstarks01/work-agent/issues/55)): a hedge or admitted gap is `unknown`
  plus the speaker's words in `notes` and never an Assumption; facts come from assertions not
  questions, every speaker read alike; sources carry **equal weight**, so a disagreement is
  recorded rather than adjudicated, flattening to `unknown` where the field allows and to a
  schema-forced value plus an `assumptions` entry where it does not. Rendering is **one fenced
  block per source with no caller-controlled byte outside a fence**, the fence sized to its content
  ([#54](https://github.com/mstarks01/work-agent/issues/54)); `GraphExecutor.run` takes
  `Sequence[Source]` and renders internally, which is the seam the whole cutover turns on.
  Traceability gains `source_label` (gate-enforced against the job's labels — the first gate rule
  taking data from outside the model) and an ungated, redactable `source_speaker`, with
  `source_excerpt` held at 1000 chars
  ([#56](https://github.com/mstarks01/work-agent/issues/56)). Downstream, `notes` gets a
  **bounding** rule — context for the needs-info question, never evidence, never weight on a rating
  — in the shared severity rubric plus one sentence each on `analyst.md` and `critic.md`
  ([#59](https://github.com/mstarks01/work-agent/issues/59)). The eval corpus gains **one
  two-source transcript case** and every `case.json` gains a `sources` array
  ([#58](https://github.com/mstarks01/work-agent/issues/58)).

  Two findings outlived their tickets. **Measurement beat survey**
  ([#51](https://github.com/mstarks01/work-agent/issues/51)): four real Teams `.vtt` exports are
  34.8% spoken words and 65% machinery, a raw 60-minute export blows the cap while the same call
  cleaned does not, and attribution names the *participant* but never the **role** — which is why
  extraction reads every speaker alike and why `source_timestamp` was rejected (cleaning strips cue
  timings, so the field would be empty exactly on compliant input). And **the tree beat the
  charted list**: #57's verification found the ticket's own inventory wrong in five places and
  missed four modules, `execution.py` and `__init__.py` among them. Findings live on
  `archive/research/transcript-exports` @ `935fc57` and `archive/prototype/multi-source-render`
  @ `e4a17a6`.

  Two questions were left in fog deliberately, both needing real extractions rather than argument:
  what the validity gate should do with a **rambling call** that never settles into a system, and
  **PII residue** — participant names ride into `source_excerpt` by #53's rule and quoted claims
  into `notes` by #55's, and only `source_speaker` was made strippable. Five areas are **out of
  scope** and do not graduate: file-format parsing in the service, front-end acquisition UX,
  integrator-facing transcript-prep guidance, any rationale/claim carrier in the System Model, and
  a condensation pre-pass.

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
