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

**None.** [#319](https://github.com/mstarks01/work-agent/issues/319) completed 2026-08-26 and has
moved to Completed efforts below. Chart a new one only against the bar at the end of this file.

### Completed efforts

Completed on GitHub Issues (canonical):

- [#319 — Map: contributions arrive as pull requests — votes, sittings and baselines](https://github.com/mstarks01/work-agent/issues/319)
  — 12 tickets, charted and completed 2026-08-26. A **planning** map: it settled the spec for the
  contribution path — how an outsider submits review votes, case review sittings and model baseline
  sweeps, each as a pull request — and stopped at the spec. The implementation is ordinary follow-up
  work, and no issue carries it yet. The map's Decisions-so-far index holds a one-line gist per
  ticket; each line links the ticket that holds the detail.

  The route in one pass. **A vote binds to the GitHub account that submits it**
  ([#320](https://github.com/mstarks01/work-agent/issues/320)): the voter name is the login,
  standing lives in the checked-in roster `evals/review/voters.toml`, and CI checks author = voter.
  **The ledger splits one file per voter**
  ([#322](https://github.com/mstarks01/work-agent/issues/322)), so two vote PRs merge without
  conflict. **A Baseline lives at `evals/baselines/<derived-name>/`**
  ([#321](https://github.com/mstarks01/work-agent/issues/321)), named by five identity parts that
  CI recomputes; the case set must be the full corpus. **CI proves an artifact agrees with itself
  and with the repository, never that a model ran**
  ([#323](https://github.com/mstarks01/work-agent/issues/323)): five PR checks plus a
  usage-completeness lint, and a provenance label stands in for replication. **litellm's offline
  price map prices a sweep** ([#324](https://github.com/mstarks01/work-agent/issues/324), research):
  a recorded sweep costs $0.60, and a suffixed served build misses the map and needs a stated
  fallback. **Promotion is a roster flip**
  ([#326](https://github.com/mstarks01/work-agent/issues/326)), informed by a pairwise agreement
  report; `score` always writes a maintainer-only block and an all-standings block. **A Case
  Sitting is free and offline** ([#327](https://github.com/mstarks01/work-agent/issues/327)): the
  committed `REVIEW.md` carries everything, and a drifted digest re-opens the debt fail-closed.
  **One `submit` subcommand opens every PR**
  ([#325](https://github.com/mstarks01/work-agent/issues/325)), with a per-kind allowlist and a
  local run of CI's checks. **The comparison table is generated**
  ([#330](https://github.com/mstarks01/work-agent/issues/330)), and a stale copy fails CI. **The
  price map gets no drift alarm** ([#331](https://github.com/mstarks01/work-agent/issues/331)) —
  one disclosure line when a unit price changed since the calibrating Baseline ran. **The document
  surface is four files** ([#335](https://github.com/mstarks01/work-agent/issues/335)): a short
  root `CONTRIBUTING.md` routes by resource; `evals/VOTING.md`, `evals/BLESSING.md` and a new
  `evals/BASELINES.md` own the procedures, and prose never restates what `submit --dry-run` prints.
  **There is no spend ceiling** ([#334](https://github.com/mstarks01/work-agent/issues/334)): the
  gate is informed, affirmative consent — every amount carries one of three labels (`recorded` /
  `estimated` / `unpriced`), acceptance is typing the amount back, a script states
  `--accept-cost <usd|unknown>`, and a run that outspends the accepted figure re-prompts
  interactively or stops.

  Corrections worth knowing: #327 named the act — a **Case Sitting**, defined in `CONTEXT.md` by
  PR [#333](https://github.com/mstarks01/work-agent/pull/333), distinct from the Review Sitting
  that produces votes. #334 amended #325 twice: `--yes` and `--allow-unpriced` die on the estimate
  gate, replaced by `--accept-cost`. #335 ruled the place for the ceiling text before #334 ruled
  its content.

  Research findings sit at `archive/research/contribution-price-data` @ `890bc93`, per the tag
  convention below. **Out of scope and not graduating**: calibration pair labels and new golden
  cases (both are ordinary PRs), any merge gate that reads contributed data, baselines over
  private off-corpus cases, and a hosted submission service.

  **One decision here was reversed by the maintainer and is not the current state.** The map
  ruled that "the web app never gains a network write", beside ruling out a hosted service. On
  2026-08-27 `webapp/sitting.py` gained a button that opens the pull request through the
  operator's own authenticated `gh`. The hosted half of that line still stands — nothing is
  hosted, no credential is held, and the app still binds to loopback — but the app does now
  reach the network on a click. The endpoint carries five controls in exchange (a checked
  `Host`, `frame-ancestors 'none'`, `Sec-Fetch-Site`, a per-process page token, and no
  request-controlled arguments), and it is off when `gh` holds no login or when `--no-submit`
  is passed. Read the module docstring for what each control stops — in particular why the
  frame refusal is what makes the header check and the token mean anything, rather than a
  third opinion beside them. The CLI path is unchanged and remains the only way to submit a
  vote or a baseline.

- [#158 — Map: one validated system representation, many security frameworks](https://github.com/mstarks01/work-agent/issues/158)
  — 11 tickets, charted 2026-08-12 from [#139](https://github.com/mstarks01/work-agent/issues/139) and
  completed 2026-08-13. A **planning** map: it settled the spec for a framework-neutral analysis layer —
  one extraction and one **Valid System Model** feeding N in-repo framework packages — and stopped at the
  spec. The cutover plan is
  [#172](https://github.com/mstarks01/work-agent/issues/172), filed rather than left as a line in this map;
  it landed on 2026-08-14, and the ASVS package
  [#176](https://github.com/mstarks01/work-agent/issues/176) landed the same day.
  Read #172 first for the order of operations, then the eleven resolution comments in map order.
  [#139](https://github.com/mstarks01/work-agent/issues/139), the source idea, closed on 2026-08-18 with
  fourteen of its sixteen acceptance criteria met by merged code. Two issues carry the rest:
  [#200](https://github.com/mstarks01/work-agent/issues/200) — the eval sweep grades STRIDE only, so 63 ASVS
  references sit unread — and [#201](https://github.com/mstarks01/work-agent/issues/201) — semantic claim
  identity.

  **STRIDE becomes the first package rather than the architecture.** The coupling is narrower than the map
  charted and sits in six files ([#159](https://github.com/mstarks01/work-agent/issues/159)); four things the
  ticket listed carry no STRIDE at all, and `analysis.py` was already the neutral query layer.
  **ASVS asks a system representation for almost nothing**
  ([#160](https://github.com/mstarks01/work-agent/issues/160)): 67% of L1 is a property of code rather than a
  position in a graph, 5.0 deleted its architecture chapter, all 16 applicability predicates are presence
  tests, and this service can rule a requirement applicable or unknown but **never passed**. A late correction
  added a **tier-0 precondition** — ASVS scopes itself to web applications and never tests that, and two of
  this repo's 12 corpus cases fail it.

  The route in one pass. **The job selects its frameworks from a set the deployment carries**
  ([#161](https://github.com/mstarks01/work-agent/issues/161)): `frameworks` is required and non-empty, the
  contract carries **no default**, a fifth config file `config/frameworks.toml` holds the carried set, and an
  unknown name rejects on the input ladder. **The taxonomy holds**
  ([#162](https://github.com/mstarks01/work-agent/issues/162)) — five element types, controls stay string
  attributes, identities do not become nodes — so **one extraction pass serves every framework**, measured on
  188 of 251 control attributes already `unknown`. A thin **`Claim` supertype** carries the seven fields the
  service constructs and nothing a framework judges
  ([#163](https://github.com/mstarks01/work-agent/issues/163)), with `(framework, version)` one required pair.
  **A package is a declaration object beside a text root**, registered in a table like `VENDORS` and checked by
  a ten-check gate inside `Deployment.from_env`
  ([#164](https://github.com/mstarks01/work-agent/issues/164)); the ID rule becomes data, which deletes
  `^[STRIDE]-\d{2}$` outright. **The evidence catalog stays the service's and a package selects from it**
  ([#165](https://github.com/mstarks01/work-agent/issues/165)) — no package may add an entry, a derivation or a
  **Ground** kind, and grounds justify applicability, never undeterminedness. **Each package carries its own
  critic**, blind to every other framework ([#166](https://github.com/mstarks01/work-agent/issues/166)), which
  closes the cross-framework node #161 parked; a shared critic saves about 1,100 uncached tokens per extra
  framework per job and nothing else. **One corpus splits by framework inside each case**
  ([#167](https://github.com/mstarks01/work-agent/issues/167)): the grading contract is per framework, STRIDE's
  claim set is open and ASVS's is closed, and a CI merge bar replaces a load-time gate because a deployment
  cannot read `evals/`. **The report becomes one envelope named `Report` at `schema_version` 3.0**
  ([#168](https://github.com/mstarks01/work-agent/issues/168)): nine top-level fields stay, eight move into a
  per-framework block, `analyses` is an ordered list rather than a map, and ten checks split three on the
  envelope and seven written once over the neutral base. And **the retrieval key decides where knowledge
  lives** ([#169](https://github.com/mstarks01/work-agent/issues/169)): a **Reference Note** and a **Worked
  Case** move into the package because their key is a package rule, a **Domain Pack** stays one shared root
  because its key reads the neutral model, and a shared pack may never name a lane, a category, a requirement
  ID or a verdict state.

  Three findings outlived their tickets. **#165 found a live catalog bug** — `evidence_catalog` tests exact
  equality with `unknown` while `control_state` reads a leading token, so a control the input says is *not*
  there has no **Evidence Reference** — filed off-map as
  [#171](https://github.com/mstarks01/work-agent/issues/171); 18 of 300 corpus candidates fire on one.
  **#169 found a test that will pass while covering nothing**:
  `test_no_document_is_reachable_from_the_evidence_seam` globs `src/stride_service/*.py` flat, so a package's
  tables under `frameworks/<name>/` escape it. It is correct today and is not filed; #169 rules the fix.
  And **#166 corrected #163** — the service constructs a **Verdict** for every framework's claim, so the
  report's record is `RuledClaim(Claim)`, not `verdict` on `Claim` itself.

  Corrections worth knowing: **#160 corrected itself after closing** with the tier-0 web-application
  precondition, which became a run-time gate in #164 rather than an input-ladder check. **#162 corrected its
  own price** — the Evidence Reference scheme survives untouched — and found the repo carries **two definitions
  of "a control"**. **#168 corrected #163** on where `verdict` sits.

  **Certified but unmeasured** is the gap this map names rather than papers over: one `strong` fingerprint
  covers both frameworks, so a run of a framework nobody measured can be certified today. Nothing on this
  route was measured while the map ran, because **no live eval sweep had run in this repository by
  2026-08-13**. Every cost figure the eleven tickets carry is a count from the tree or a token estimate,
  never an observation of a run.

  **That last sentence expired the next day, and this entry is not where to check it.** The first live
  sweep landed 2026-08-14 and others followed; `evals/README.md` states which sweeps exist, what they
  measured and what they do not establish, and it is the only place that number is maintained. Read it
  before repeating anything here about what has been measured — the paragraph above is a record of what
  this map faced, not a claim about today.

  **Out of scope and not graduating**: the content of the ASVS requirements, external plugin loading, semantic
  claim identity (#139 folded it in and it deserved its own issue, now
  [#201](https://github.com/mstarks01/work-agent/issues/201)), graph databases and RDF, and the ADK
  workflow engine. Two things stay unspecified: which deterministic rules a second package carries, and the
  cost and concurrency of one job running two frameworks — the lane budget #164 declined to set.

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
