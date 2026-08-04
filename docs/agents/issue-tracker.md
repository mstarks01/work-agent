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

[#76 — Map: tie every finding back to the input text that justifies it](https://github.com/mstarks01/work-agent/issues/76)
— 9 tickets, charted 2026-08-03. A **planning** map: it settles the spec for finding-level
attribution — every threat carrying a non-empty record of what justifies it, a quote from the
submitter's own words, a named unknown attribute, or a named derived fact — and stops at the spec.
[#77](https://github.com/mstarks01/work-agent/issues/77) resolved 2026-08-03 and **fixes vocabulary
the whole repo inherits**, so read it before touching this map or the code it names. The record is
`grounds: list[Ground]` with kinds `quote` / `unknown-attribute` / `derived-fact`; *finding-level
attribution* stays the concept name while `grounds` is the field, keeping it clear of `attribute`
and of the two live senses of "attribution" (per-node regression attribution in the eval harness,
speaker attribution at `source_speaker`). And **`Analyst` now names a human**: the six agents become
**category agents**, keyed on what they do — `analyze/<category>` in `config/model_tiers.toml`,
`analyze_<category>` as graph node and in report `nodes[].node`, `prompts/analyze.md`. That rename
is a fail-closed config cutover carrying no shim, and it executes on the implementation branch
folded into the *same* cutover as the schema change, so the config version bumps once.

[#78](https://github.com/mstarks01/work-agent/issues/78) resolved 2026-08-03 and settles **render
safety** without touching the schema, so it constrains implementation rather than gating anyone:
untrusted text never reaches `innerHTML` — values go to the DOM as `textContent`, `esc()` shrinks
toward vestigial, and escaping quotes stops being a question because attributes are set by property
assignment. `render_report`'s `<`→`\u003c` escape stays; it guards the script-block boundary, a
different problem. The report page gains a **strict nonce CSP** with no `'unsafe-inline'`, replacing
`webapp/main.py:47-49`'s rationale, which was stale twice over — the viewer is the app's own
template now, and `render_report` already rewrites it on every request.
`Source._single_line_label` broadens to **reject** C0/C1, bidi, zero-width and BOM in a
`source_label`, one validator covering `/v1` and the in-process engine, fail-closed with no shim and
nothing versioned; quotes stay exempt so verbatim matchability survives. Integrators get a
**blanket** untrust rule in `docs/Report-Schema.md` — every string is untrusted — never a per-field
table, which would have omitted `grounds` and is how this ticket came to exist.

Resolving it found a **live stored XSS on `main`**: the element table's attrs column interpolates
`technology`, `data_classification`, `protocol`, `authentication` and `assets` into `innerHTML`
without `esc()` (`report_view.html:246-249`). Filed off-map as
[#86](https://github.com/mstarks01/work-agent/issues/86) — ordinary work, no `wayfinder:` label —
and fixed minimally on `fix/escape-element-attrs`. #78's rule supersedes that fix at implementation
time.

[#79](https://github.com/mstarks01/work-agent/issues/79) resolved 2026-08-03 and **opened the narrow
neck** — five tickets unblocked at once. `grounds: list[Ground]` is an eighth analyst-owned field on
`DraftThreat`, `min_length=1` and uncapped (matching `affected_element_ids`; `DraftThreat` caps no
list). `Ground` is **one flat model** — `kind` plus every branch's fields, defaulted `""` — with a
`_check_shape` validator that requires its own branch's fields and **forbids** the others. That is
`Verdict`'s pattern (`report.py:135-160`), reused so the repo has one answer to "tagged variant in a
provider-facing schema". The discriminated union was judged the more honest and more Pythonic shape
and **lost to a measured fact**: provider schema compilers are the unpredictable part of this system
(`config/sampling.toml:62-85` — Anthropic rejects `SystemModel`'s grammar as too large, and
`constrain_output = false` is documented as *not* a working fallback), and this schema rides in six
`strong`-tier `DraftThreats` requests. The accepted cost is that a mis-shaped entry is repaired
rather than prevented.

Branches: **quote** = `text` (1000, deliberately the same number as `source_excerpt`) +
`source_label`; **unknown-attribute** = `element_id` + `attribute` spelled exactly like `UnknownRef`
but a **separate type**, with `UnknownRef`'s critic-only ownership untouched; **derived-fact** =
`flow_id` alone, a reference never a copy, with no free-text field — free text is checkable by no
gate and would become the escape hatch for the findings whose justification matters most. Grounds
gets a **new threat-level referential check** beside `_citation_issues`: `parse_and_validate`
validates the system model only, so there was no threat gate to extend, and the critic's
`related_unknowns` stays unchecked until #83 rules on it. `schema_version` → **2.0**, one bump for
the whole cutover — earned by #77's node rename, which fails *silently* for a consumer keying on
`analyst_spoofing` — plus the versioning policy `docs/Report-Schema.md` never stated: additive is
minor, changing the meaning or spelling of an existing value is major.

[#80](https://github.com/mstarks01/work-agent/issues/80) resolved 2026-08-04 and answers **yes,
mechanically and cheaply** — but the headline is a fact about the *input*, not the model. Exact
substring rejects **78.2%** of the corpus's 206 element excerpts because the sources are
**hard-wrapped**, so a two-word quote straddles a newline nobody typed; collapsing whitespace runs
alone takes that to 1.0%, and every other rung in the ladder recovered nothing. The pinned policy
is NFKC + typographic folds, case, inline markdown markers, whitespace collapse, then `…`-separated
fragments matched in order — leaving **0 false rejections** in 206 plus one true one, a quote that
excised a span unmarked and stitched together a sentence the source never contains. Punctuation
-blindness is refused (buys nothing) and so is a similarity threshold (the fabricated quote scores
0.963, above any threshold a human would pick by intuition). The check runs in `join_drafts` beside
#79's set-membership check, while the source text is still held, and inherits `_citation_issues`'
no-sources escape. **Consequence: marked per entry, closed per threat** — an unverifiable quote
still renders, flagged on a **service-owned** list on `Analysis` rather than a field on
analyst-owned `Ground`, and the job fails closed only when *no* ground on a threat verifies.
Failing closed on any bad quote lost to the Rule of Three: 0/206 licenses ≤1.46% per quote, which
at 18.7 threats per job is a 24% chance of killing a job over a cosmetic mismatch, on evidence that
is 12 synthetic single-source cases.

Resolving it **corrected #79 on a mechanism, not a decision**: there is *no draft repair path*.
`repair` is extraction-only and `recritic` is critic-only, so a bad draft raises out of
`merge_drafts` and fails the job — #80 had to supply a consequence rather than inherit one. And it
exposed a real evidence gap: the corpus carries **zero transcript sources**, so the across-turns
and speaker-label cases rest on a constructed probe, now fog.

[#81](https://github.com/mstarks01/work-agent/issues/81) resolved 2026-08-04: `source_excerpt`
**survives with its job restated** — it answers *why this element exists*, `grounds` answer *why
this threat was raised*. Field, gate rule and 13 corpus cases unchanged, so **no cutover and
nothing versions**. It is kept for three things grounds cannot do: a threat-less element has no
other provenance, the verbatim-span requirement is pressure against invented elements, and
`_citation_issues` exists only because excerpts do. `CONTEXT.md`'s **Source Excerpt** entry loses
the threat→element→words chain to `grounds` and loses its "extraction evals" claim — **nothing
scores excerpts**; `score_extraction` is element-ID set arithmetic plus `crossings_match`
(`modes.py:212-227`), and the real consumers are `verify_corpus.py:157-183`'s lint and a human
blesser at `BLESSING.md:131`.

**Read this before touching the analyze path.** Resolving #81 found that a category agent
**never sees the submitter's source text** — `analyst.md` templates against `{system_model}` and
`{boundary_crossings}` only, so the excerpts inside the rendered model were the only submitter words
reaching it, which made the map's settled decision 1 (*authored, not derived*) unimplementable. Two
changes follow, both now constraints on #82 rather than open questions: `analyst.md` **gains
`{input_text}`** — no new plumbing, `execution.py:146-151` already writes the key and refuses a
caller override, and `run_analysis` passes `case.sources` even when injecting the blessed model at
`prepare`, so all three eval modes stay green — and `prepare_analysis` **strips `source_excerpt`,
`source_label` and `source_speaker`** from the model it renders to the analysts *and the critic*,
while `STATE_VALID_MODEL` keeps them for the report. That removes the nearest-excerpt shortcut #82
asks about instead of wording against it; `notes` is untouched, and no label is stranded because
each rides inside its own fence (`sources.py:21`). The accepted cost is that the bytes an analyst
saw are no longer the bytes the report carries. A finding's quote disagreeing with its element's
excerpt is **legitimate and ungoverned** — no gate, no prompt rule, not even a same-source
requirement, since a transcript remark can justify a threat against an element extracted from a
design doc.

Frontier now: [#82](https://github.com/mstarks01/work-agent/issues/82),
[#83](https://github.com/mstarks01/work-agent/issues/83) and
[#84](https://github.com/mstarks01/work-agent/issues/84), all unblocked and unclaimed — still a
parallel step, so expect concurrent sessions. All three had constraints added to their bodies by
#81; #82's stated constraint that "analysts already see every excerpt" was **struck as false**, and
its `graph.py:285` reference was stale (`graph.py:409`). #81 deliberately left the critic seeing
**no submitter words at all** and handed #83 the call on whether `critic.md` gains `{input_text}`.
[#85](https://github.com/mstarks01/work-agent/issues/85) waits on #82 alone now. #79 left three
questions explicitly to siblings: verbatim quote verification was #80's, prompt instruction is
#82's, and whether `related_unknowns` finally gains a check is #83's. #82 was retitled to "How a
**category agent** is instructed…" — its old title carried the vocabulary #77 retired. #80 hands
#81 a working matcher and a measured rate for the *same field* it rules on, and hands #82 the one
fact both its true rejections share: an unmarked elision, which `extract.md` rule 5 already
forbids.

The measurement lives on `prototype/quote-verification` @ `6ed8d77`
(`prototypes/quote_verification_prototype.py`), pushed — throwaway code, but re-runnable, and the
branch is the citation until the map completes and it becomes an `archive/` tag.

[#49](https://github.com/mstarks01/work-agent/issues/49) completed 2026-07-31 and has
moved to Completed efforts below; its spec was implemented and merged 2026-08-01.

### Completed efforts

Completed on GitHub Issues (canonical):

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
