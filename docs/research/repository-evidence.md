# How a GitHub repository can supply implementation evidence for ASVS (wayfinder #484)

**Question.** A job carries prose, so an ASVS requirement that only source code or a
configuration file can settle stops at a `needs-other-evidence` **Scope Entry**. Could a
connected repository supply that evidence, and how, without a repository becoming a third
prose **Source** and without source files becoming **Element**s of the **System Model**?

**This file records facts and a recommendation. It rules nothing.** The ruling belongs to #484
and to an ADR after it. [ADR 0004](../adr/0004-evidence-references.md),
[ADR 0010](../adr/0010-package-cannot-extend-the-evidence-catalog.md) and
[ADR 0013](../adr/0013-asvs-rules-applicability-and-never-a-pass.md) govern every proposal
below, and each proposal is argued against them.

**Method.** Three kinds of evidence, kept apart.

1. **The repository, at `8085c43`.** `src/analysis_service/sources.py`, `evidence.py`,
   `grounding.py`, `report.py`, `frameworks/asvs/record.py`, `frameworks/asvs/rules.py`, and
   the ADRs named above.
2. **The ASVS corpus, 96 reference records over 11 cases.** Counted with
   `evals.harness.reference.load_corpus` on 2026-09-08. #226 records that one case of the
   eleven has been read by a person, so the counts are strong evidence of what the corpus
   authors expected and weaker evidence of what ASVS needs.
3. **Primary sources, read on 2026-09-08.** The OASIS SARIF 2.1.0 specification, the GitHub
   REST reference, the GitHub Apps authentication guides, the CodeQL CLI licence, the Semgrep
   CLI reference and licences, the gitleaks and osv-scanner repositories, tree-sitter, and the
   ASVS 5.0.0 assessment chapter. Each is cited where it is used and listed at the end.

---

## TOP-LINE VERDICT

**Recommendation: Option B, with Option C's bounded snippets in a second phase. Build a second
closed catalog from a pinned commit, let a lane select from it, and add no positive state.**

Six findings support that.

1. **The gap is large and it is concentrated.** 61 of the 96 corpus records, 64 percent, expect
   `needs-code` or `needs-config`. Every one of them is a deferral today. On the archived
   first sweep the attribution instrument charged 14 of 49 losses to deferral, and the other
   27 to a verdict that asked for prose where prose cannot answer. A repository is the only
   input kind that reaches either.

2. **The evidence seam was built for this and needs no redesign.** `CARRIED_EVIDENCE_KINDS`
   is threaded as an argument everywhere it is read, and its own docstring says it becomes a
   job field the day the service accepts code. `partition_proposals` reads `carried` and
   nothing else. So a job that carries a repository changes one value, and no package changes.

3. **The ground model already has the right shape, twice.** A `quote` is a span the service
   verifies against bytes it holds. An **Evidence Reference** is an ID the agent copies and the
   service resolves. A repository ground is the second shape with a stronger property: the
   agent names a path and a line range, and the service copies the bytes. No ladder is needed,
   because the model never writes the text.

4. **Everything a scanner emits fits one interchange format, and the format carries the
   commit.** Semgrep, gitleaks, osv-scanner and GitHub code scanning all emit or return SARIF
   2.1.0. A SARIF run names its `revisionId`, so a result for a different commit is refused
   before it is read. GitHub's own upload limits give a bound for ingestion.

5. **Licences decide which tools may run, and one is excluded.** The CodeQL CLI licence forbids
   use on a codebase that is not open source without a GitHub Advanced Security licence. So
   this service must never invoke CodeQL. It may read SARIF that a repository's own code
   scanning already produced. Semgrep's engine is LGPL-2.1; its public rules carry the
   Semgrep Rules License v1.0, which needs a legal read before any rule ships in this
   repository. gitleaks is MIT and osv-scanner is Apache-2.0.

6. **No positive state is earned yet.** ADR 0013 rules that no field carries a pass. A repository
   shows what was committed, never what was deployed, and 33 of the 70 level 1 requirements
   read a control's configuration, which often lives outside the repository. The corpus has
   no record that expects a positive answer, so a positive state would ship with no way to
   measure its false-positive rate. Phase 3 revisits it after a measured sweep.

**What this rejects.** Option A, raw retrieval by lane agents, on cost, reproducibility and
prompt injection. Option D alone, because scanners cover a small part of the standard and
the corpus shows it. Lexical or semantic search over the repository as the retrieval
mechanism, for the reasons ADR 0008 gave. A `verified` state. Automatic enrichment of the
System Model. Any execution of repository code, which rules out builds and CodeQL databases.

---

## PART 1 — What the service holds today

**A job carries Sources.** A **Source** is `{kind, label, text}` with `kind` in
`{description, transcript}`. The shipped bounds are 100 KiB across all sources and 10 sources,
in `config/resilience.toml`, and the bound is bytes rather than tokens by ADR 0001. The report
carries a `SourceRef` per source: kind, label and `sha256`, and `InputRef.source_sha256` over
the whole. That is the provenance precedent a repository input must match.

**Every ground is one of five kinds.** `quote`, `unknown-attribute`, `absent-attribute`,
`derived-fact` and `absent-element`. The three model-derived kinds come from the **Evidence
Catalog**, which `evidence_catalog()` enumerates from the **Valid System Model**. The agent
copies an ID; the service resolves it. A `quote` is the one kind the agent writes, and
`grounding.py` verifies it against the job's bytes with a pinned ladder.

**ADR 0010 sets three tests for any widening of the catalog.** A derivation must be a pure
function of the model, framework-neutral, and keyed by IDs the model already carries. ADR 0012
was the first widening argued against them.

**An ASVS proposal names the evidence it needs.** `needs_evidence` is one of `""`, `prose`,
`code`, `config` or `people`. `partition_proposals` keeps a proposal whose kind is in `carried`
and turns every other one into a `needs-other-evidence` Scope Entry naming the kind. The fan-in
strips the field before the payload (`evidence._ROUTED_AWAY`).

**The corpus expects these kinds.**

| Expected disposition | Records |
| --- | ---: |
| `needs-code` | 33 |
| `needs-config` | 28 |
| `needs-more-prose` | 16 |
| `gap-from-prose` | 14 |
| `needs-people` | 3 |
| `not-applicable` | 2 |

By chapter, the two repository kinds sit in authorization (8 code), encoding and sanitization
(7 code), web frontend security (7 config, 2 code), session management (5 code), secure
communication (5 config), cryptography (4 config) and authentication (4 config). Validation
and business logic expects prose only, and secure coding and architecture expects two of the
three `people` records.

**Retrieval is keyed by fired rule.** ADR 0008 chose a rule firing over a query built from
caller text, because caller text is attacker-influenced and a query built from it is a lever
into the prompt. ADR 0027 then ruled that a word list raises a lead and rules nothing out.
Both rulings bind repository retrieval.

**The #483 note already assigned this ticket its part.** It keeps the five element types,
treats a CycloneDX document as a future evidence input under #484 and never as the model, and
names #415 and #484 as the homes for human-process facts as evidence kinds.

---

## PART 2 — Answers to the ten questions

### 2.1 What a GitHub-backed job carries

**A repository is a second kind of input, beside Sources and never among them.** A Source is
text the extraction prompt renders. A repository is bytes that no prompt renders wholesale.
Putting one in the `sources` list would put it through `render_sources` and into the
extractor, which the ticket's non-goals forbid.

Proposed shape, one entry, on the job envelope:

```text
repository:
  host: github
  owner: <owner>
  name: <name>
  ref: <branch, tag or SHA the caller asked for>
```

**The service resolves `ref` to a commit before anything else runs.** `GET
/repos/{owner}/{repo}/commits/{ref}` accepts a SHA, a branch or a tag, and with the
`application/vnd.github.sha` media type returns the SHA alone [GH-COMMITS]. The report then
carries both the requested ref and the resolved SHA, so a reader can tell "main" from the
bytes "main" pointed at. A caller that supplies a SHA gets the same call, which confirms the
SHA exists.

**The tree SHA is recorded too.** The commit response carries `commit.tree.sha`. Two commits
with one tree hold identical bytes, and the inventory below is a function of the tree, so the
tree SHA is the key a cache and a reproducibility check read.

**Fetch by archive, not by file.** `GET /repos/{owner}/{repo}/tarball/{ref}` answers with a
302 to a temporary URL, which expires after five minutes for a private repository [GH-CONTENTS].
One request replaces one call per file. The contents endpoint serves a file of at most 1 MB
in full, up to 100 MB by raw media type, and lists at most 1,000 entries per directory
[GH-CONTENTS]. The recursive tree endpoint truncates at 100,000 entries or 7 MB [GH-TREES].
The archive route has none of those seams, and it costs one point against the 5,000 requests
per hour a user or an installation holds [GH-RATE].

**One repository per job first, with the index in every ID from the start.** Every catalog ID
below carries a repository ordinal, so a second repository is a list where there was a
one-element list, and no ID changes meaning. Designing multi-repository analysis now would
cost a cross-repository identity question with no case asking it.

**Credentials.** A GitHub App installation token is the mechanism the platform recommends for
a service. It lasts one hour, and it is scoped at creation to named repositories and named
permissions [GH-INSTALL]; the JWT that mints it lives at most ten minutes and is signed RS256
[GH-JWT]. A fine-grained personal access token is the fallback for a single user: it is scoped
to named repositories, with `Contents: read`, `Metadata: read` and `Code scanning alerts:
read` as the three permissions this design reads [GH-PAT]. A classic token with the `repo`
scope reaches every repository the user can see and is refused. The token arrives with the
job or from the deployment, is used for the fetch, and is never stored, logged or written to
the report. Where the credential lives is a deployment fact and where its material comes from
is a discovery, which is the split ADR 0021 and the vendor rows already make.

**Provenance in the report.** A `RepositoryRef`, beside `SourceRef`:

```text
host, owner, name
requested_ref
commit_sha
tree_sha
fetched_at
archive_sha256
inventory: {files, bytes, skipped: {vendored, generated, binary, symlink, submodule, oversize}}
classifier_version
extractors: [{name, version, files_read, facts}]
scanners: [{tool, version, rule_source, results, sarif_sha256}]
```

A repeated analysis of one tree SHA with the same extractor versions must produce the same
catalog, and `inventory` plus the extractor rows are what a test compares.

### 2.2 How repository content becomes evidence

**Keep the four routing kinds. Add a role, not a fifth kind.** `needs_evidence`,
`CARRIED_EVIDENCE_KINDS`, `EVIDENCE_FOR_DISPOSITION` and 96 corpus records are all keyed by
`prose | code | config | people`. A fifth routing value re-keys the corpus and the scorer for
no gain, because a lane agent asks "what kind of thing settles this", and `dependency` or
`workflow` is a place to look rather than a different kind of answer.

Each catalog entry carries a **role**, and one table maps role to kind:

| Role | Kind | How classified |
| --- | --- | --- |
| `source` | `code` | language of type `programming` in the linguist tables |
| `test` | `code` | `source` under a test path pattern |
| `manifest` | `config` | a known dependency manifest or lockfile name |
| `workflow` | `config` | `.github/workflows/*.yml` |
| `container` | `config` | `Dockerfile`, compose files |
| `infra` | `config` | Kubernetes and Terraform by extension and top-level keys |
| `server` | `config` | `nginx.conf`, `.htaccess`, similar known names |
| `settings` | `config` | framework settings modules and `.env.example` style files |
| `docs` | `prose` | language of type `prose` or `markup`, or a documentation path |
| `data` | none | language of type `data` with no role above |
| `vendored`, `generated`, `binary` | none | excluded before classification |

The tables are linguist's: `languages.yml` maps extensions and filenames to a language and a
type in `{programming, data, markup, prose}`, `vendor.yml` and `documentation.yml` list the
paths, and `generated.rb` holds the generated-file heuristics [LINGUIST]. Borrowing the data
files costs nothing and buys the same answer GitHub gives. The role table is keyed, so a file
that matches no row is `data` with no role, and a missing row raises rather than reading as
`source`.

**Prose in the repository is catalogued, not rendered.** A `README.md` becomes an entry that
says the file exists, its size and its digest. Its text does not enter a prompt unless the
caller lists that path as a Source on purpose. A caller-chosen Source is untrusted too, but a
person chose it, and the audit under #659 asked that stated support be kept apart from
verified support. A document the repository holds is stated support at most.

### 2.3 The repository evidence object

**A second closed catalog, the Implementation Evidence Catalog, owned by the service.** ADR
0010's three tests restate over a snapshot instead of a model:

1. every entry is a **pure function of the tree SHA and the extractor versions**;
2. every entry is **framework-neutral**, so every lane of every framework receives it;
3. every ID is **built from the snapshot's own identifiers**: the repository ordinal, the
   path and, for a span, the line range.

Two entry shapes, and two new **Ground** kinds to carry them:

```text
repo-fact
  id:        fact:<r>:<extractor>:<path>:<key>
  repo, commit_sha, path, line_start, line_end
  extractor, extractor_version
  fact_type   (closed, per extractor: dependency, route, auth-call, crypto-call,
               log-call, exception-handler, query-build, container-user,
               workflow-step, tls-setting, cookie-flag, scanner-result ...)
  value       (a short, typed value: a name and a version; a method and a path)
  origin      deterministic | scanner
  tool, tool_version, rule_id        (scanner origin only)
  content_sha256                     (of the bytes the fact was read from)

repo-excerpt
  id:        excerpt:<r>:<path>#L<start>-L<end>
  repo, commit_sha, path, line_start, line_end
  text        (copied by the service from the archive, bounded)
  content_sha256
```

**The agent selects a fact by ID, exactly as it selects an Evidence Reference today.** For an
excerpt the agent proposes `{path, line_start, line_end}` and no text. The service reads the
lines from the archive it holds, bounds them, and builds the ground. A span the archive does
not hold is reported as an unresolved reference, in the shape `resolve_proposals` already
uses for a bad catalog ID. The grounding ladder is not used, because there is no model-written
text to verify. What the ladder cannot do for a quote it cannot do here either: an excerpt can
be present and irrelevant, and that stays the **Critic**'s question.

A reader who asks *which repository bytes supported this ruling* gets the commit SHA, the path,
the line range and the digest of those lines, on the ground itself.

### 2.4 How much is deterministic before a model reads anything

**All discovery, and all narrowing.** The model's two jobs are unchanged: a lane agent selects
evidence and argues from it, and the critic rules. Five layers feed the catalog, in order, and
each is a pure function of bytes.

| Layer | Reads | Emits | Mechanism |
| --- | --- | --- | --- |
| L0 inventory | the archive | one entry per admitted file, with role | linguist tables; size, symlink, submodule and binary exclusions |
| L1 manifests | manifests and lockfiles | `dependency` facts with name and version | per-ecosystem parsers; osv-scanner on lockfiles for advisories |
| L2 configuration | container, infra, workflow, server, settings files | typed facts per file: a `USER` instruction, a `runAsNonRoot`, a TLS minimum, a cookie flag, a checkout step | one parser per format, each with a closed fact-type list |
| L3 syntax | `source` files | `route`, `auth-call`, `crypto-call`, `log-call`, `exception-handler`, `query-build` facts with symbol and line | tree-sitter queries per grammar |
| L4 scanners | the archive, or a SARIF file | `scanner-result` facts | Semgrep CE with local rules, gitleaks, osv-scanner; GitHub code scanning SARIF |

tree-sitter is a parser generator and an incremental parsing library that builds a concrete
syntax tree and gives useful results in the presence of syntax errors; it has one grammar per
language and an official Python binding, `py-tree-sitter` [TREE-SITTER]. Those two properties
are what L3 needs: a file that does not compile still yields its routes, and a new language is
a grammar and a query file rather than a parser.

**What is settled deterministically and what is not.** An L2 fact answers a parameter question
inside one file: a Dockerfile has no `USER` instruction. That absence is a fact about that
file. "No rate limiter exists in this repository" is not a fact any extractor can emit,
because no extractor parses every place one could live. So an extractor emits an absence only
inside an artifact it parsed completely, and reports the rest as *not found in what was
retrieved*. That is ADR 0027 applied to a repository: a lookup raises a lead and rules nothing
out.

**Nothing executes.** No build, no dependency install, no test run. A repository is untrusted
input, and ADR 0021's rule that no credential-bearing job runs unreviewed code reaches a
repository a caller submitted. This is also why L3 uses a parser and not a compiler, and why
CodeQL's compiled-language mode is out of reach on licence and on execution grounds alike.

### 2.5 How existing scanners take part

**SARIF 2.1.0 is the interchange, and one reader maps it into the catalog.** A `result` carries
`ruleId`, `level`, `message`, `locations` and `partialFingerprints`; a `physicalLocation`
carries `artifactLocation.uri` and a `region` with `startLine` and `endLine`; a run's
`versionControlProvenance` carries `repositoryUri` and `revisionId`; `tool.driver` carries
`name`, `version` and `rules` with `id` and `shortDescription` [SARIF §3.27, §3.29, §3.23,
§3.19]. Every field the catalog needs is there under a name the specification owns.

**A result is a lead, and the mapping enforces that.** A `scanner-result` fact carries the
tool, its version, the rule ID, the level and the location. It carries no message text and no
snippet. The lane agent that cites it argues from the catalog text of the ASVS requirement,
from the fact, and from an excerpt the service copies by location. A Semgrep rule about SQL
concatenation is not V1.2.4; it is a place to look for V1.2.4.

**Which tools, and on what terms.**

| Tool | Licence | Runs here | Output | Notes |
| --- | --- | --- | --- | --- |
| Semgrep CE | LGPL-2.1 engine [SEMGREP-LIC]; rules under Semgrep Rules License v1.0 [SEMGREP-RULES-LIC] | yes, with local rule files only | `--sarif` [SEMGREP-CLI] | `--metrics` defaults to `auto`, which reports usage when rules come from the registry; pin `--metrics=off` and ship no registry ruleset until the rules licence is read. Defaults: 5 s per rule per file, 1,000,000 bytes per target. |
| gitleaks | MIT [GITLEAKS-LIC] | yes, first | `sarif` [GITLEAKS] | Offline. Runs before any byte reaches a prompt; see 2.10. The project is feature-complete and takes security patches only. |
| osv-scanner | Apache-2.0 [OSV-LIC] | yes | SARIF 2.1.0 [OSV] | Reads lockfiles and SBOMs. Queries OSV.dev by default and has an offline database mode; the offline mode is the one a reproducible sweep uses. |
| CodeQL CLI | GitHub CodeQL terms [CODEQL-LIC] | **no** | | The licence forbids use "in connection with any codebase that is not an Open Source Codebase" without a GitHub Advanced Security licence. |
| GitHub code scanning | platform | read only | SARIF via `GET /repos/{o}/{r}/code-scanning/analyses/{id}` with `Accept: application/sarif+json` [GH-CODESCAN] | Filter the analyses list by `commit_sha`; needs `Code scanning alerts: read`. GitHub keys a result across uploads on `partialFingerprints.primaryLocationLineHash` [GH-SARIF]. |
| GitHub SBOM export | platform | **no** | SPDX JSON | The endpoint takes no ref and describes the default branch; the synchronous form closes on 2026-11-13 [GH-SBOM]. Evidence that is not pinned to the job's commit is not evidence for the job. |

**A SARIF file whose `revisionId` is not the job's commit is refused, and one with no
`revisionId` is refused too.** GitHub's upload caps are the bound for ingestion: 10 MB
gzipped, 20 runs per file, 25,000 results per run [GH-SARIF].

### 2.6 How evidence reaches a lane

**Keyed by fired rule and by lane, from tables, and never by a query.** ADR 0008's argument
holds unchanged: a query built from caller text is nondeterministic across index builds,
untestable offline, and a lever on which repository-authored text selects what the agent
reads. A repository is a larger body of caller text than a transcript, so the lever is longer.

Two tables select, both owned by the ASVS package as its retrieval keys:

- **lane → fact types**: the authentication lane retrieves `auth-call`, `dependency` facts
  for known identity libraries, `settings` facts under a closed key list, and
  `scanner-result` facts whose rule ID is in the lane's list; the secure communication lane
  retrieves `tls-setting`, `container` and `infra` facts, and so on for 17 lanes;
- **candidate rule → fact types**, where a fired rule narrows further: `tech:database` adds
  `query-build` facts.

Both are checked against the lane roster the way `tests/test_framework_neutrality.py` checks
every framework table: a lane with no row fails the test, and a fact type no lane reads fails
it too.

**Budget per lane, spent where the work happens.** Each lane gets a byte cap on rendered
excerpts, set in `config/resilience.toml` beside the source bounds. Facts are cheap and ride
whole; excerpts are ranked by fact type priority, then by path, and the cap cuts the tail. A
lane that retrieved nothing reports *no repository evidence was retrieved for this lane* as a
per-lane **Coverage** row, so an empty retrieval is visible and is not read as absence.

**Options compared.**

| Approach | Deterministic | Offline testable | Injection surface | Verdict |
| --- | --- | --- | --- | --- |
| lane and rule tables over typed facts | yes | yes | facts only | **adopt** |
| chapter-to-path heuristics | yes | yes | path names | adopt as one fact type, `path-match`, and never as the only key |
| symbol and syntax index | yes | yes | symbol names | adopt, as L3 |
| scanner-result routing | yes, per tool version | yes, from a pinned SARIF | rule IDs | adopt, as L4 |
| lexical search over the tree | yes | yes | every byte of the repository | reject as a key; ADR 0027 |
| semantic search | no | no | every byte, plus the index | reject; ADR 0008 |

### 2.7 How this meets the existing evidence semantics

**`CARRIED_EVIDENCE_KINDS` becomes a job property.** `("prose",)` for a job with Sources
alone; `("prose", "code", "config")` for a job with a repository. `partition_proposals` reads
`carried` and needs no change. `satisfies` in the eval scorer reads `carried` too, which is
why it was written as a function rather than a table, and the corpus dispositions stay valid:
a `needs-code` record on a repository job is satisfied by a `needs-info` claim on the code
path, exactly as `needs-more-prose` is satisfied on the prose path today.

**Carrying code does not settle a requirement.** Three cases follow, and each must stay
distinguishable in the report:

| The lane found | The claim | Verdict path |
| --- | --- | --- |
| an excerpt or fact that shows the gap | grounds cite it | `confirmed`, as today |
| an excerpt or fact that speaks to the requirement and does not show a gap | grounds cite it; the claim says what the evidence shows and what it cannot | `needs-info`, with the reason naming what is still missing: a deployed setting, a runtime check, a person |
| nothing that speaks to the requirement | no repository ground | `needs-info`, with a reason from code: *repository evidence retrieved for this lane did not speak to it* |

The third row is the one the ticket asks for: missing evidence stays apart from evidence of a
deficiency. It is ruled in code, by the rule `settled_by_grounds` already uses for an
unknown attribute. A `needs_evidence=code` proposal on a job that carries code and cites no
repository ground is settled `needs-info` before the critic reads it, with a reason that says
retrieval found nothing. The audit's F07 asks that this settlement still send the draft's
applicability and reasoning to the critic; this design should follow whatever #659 decides
for F07, because it is one rule and must have one reader.

`people` stays deferred. A repository holds no interview.

### 2.8 Whether a positive state is earned

**Not in phase 1, and phase 3 is the earliest a measured case could argue for one.**

ADR 0013 rules that an ASVS claim never reports a pass and that no field carries one. Three
facts say the repository does not change that ruling yet.

1. **A repository shows what was committed, never what runs.** 33 of the 70 level 1
   requirements read a control's configuration parameter (`asvs-l1-subjects.csv`), and a
   password policy, a TLS minimum or a cookie flag is often set in a deployed environment,
   a secrets manager or a platform console that no repository holds. A `USER` line in a
   Dockerfile is evidence the image was built to drop root; it is not evidence that the
   deployed container did.
2. **The standard says so.** Automated tools "will be unable entirely to verify many of the
   more complicated ASVS requirements or those that relate to business logic and access
   control", and verification may need "documentation, source code, configuration, and the
   people involved" [ASVS-0x04]. Source code is one of four.
3. **No corpus record expects a positive answer**, so a positive state would ship with a
   false-support rate nobody can measure. #659's F11 asked for an assessment axis with a
   *stated support, unverified* value, and #226 has read one case of eleven.

**What phase 3 should measure instead of adding a Verdict.** A Scope Entry state,
`supported-by-implementation`, for a unit a lane examined, found implementation evidence
that speaks to it, and raised no gap about. It carries the grounds that showed the control
and the commit they came from. It is not a Verdict, it sits beside `not-raised` rather than
replacing it, and it says *seen at this commit* and nothing more. That answers F11's axis
without a pass, and a corpus disposition `supported-in-code` or `supported-in-config` would
make it scoreable. It waits for a repository-backed corpus that a person has read.

**Which requirements a repository can examine at all, at level 1.** From the 70 L1 subjects:
33 control-config and 14 code-practice, 47, have a subject a repository can hold; 6
component, 5 identity, 3 trust-layer, 3 data-class and 2 flow are model subjects the System
Model already carries better; 4 are documents. Examine is the right verb. Settle is not.

### 2.9 Whether repository evidence enriches the System Model

**Not automatically, and not in phase 1 or 2.** The #483 note keeps the five element types
and names a CycloneDX document as an evidence input under this ticket, never as the model.
The same holds for a `dependency` fact: `FastAPI -> psycopg -> PostgreSQL` is implementation
evidence about a Data Flow the prose already named, and it belongs in the catalog with its
path and commit.

Of the four options the ticket lists, this recommends 1 and 4 together: the catalog holds the
fact, and a framework lane reasons from both the model and the catalog. Option 2, an explicit
enrichment phase, is #483's stage 3 (`component_type` from CycloneDX) and should be argued
there, as a **Proposal** a person accepts and never as an overwrite. Option 3, a sidecar
graph, is a third representation with no reader.

**Why not overwrite.** The model carries the submitter's own words with provenance to a
Source. A repository fact that contradicts them is a finding, and a good one: the prose says
`API -> PostgreSQL over TLS` and the settings file says `sslmode=disable`. Overwriting the
model would delete the contradiction the service exists to surface.

### 2.10 The trust and security model

Repository bytes are untrusted input (OWASP LLM01). Every protection below is a bound or a
rule that runs before a model reads anything.

| Threat | Protection |
| --- | --- |
| prompt injection in comments, strings, README, tests, generated files | no file is rendered whole; an excerpt is copied by the service by location, bounded, and rendered inside a fence sized to its own content by the rule `render_sources` already applies; `docs` and `generated` roles are never excerpted |
| secrets in the tree reaching a prompt | gitleaks runs on the archive first; every hit is redacted in the bytes the excerpt copier reads, and becomes a `scanner-result` fact for the configuration lane; the token the fetch used is never in the archive |
| pathological repositories | caps on archive bytes, file count, per-file bytes and per-lane excerpt bytes in `config/resilience.toml`; extractor timeouts per file, with Semgrep's 5 s per rule per file and 1 MB per target as the reference defaults; the tar is streamed with a running total so a decompression bomb stops at the cap |
| tar path traversal | entries with `..`, absolute paths or a leading `/` are refused, and the archive is never written to disk as a tree |
| vendored, generated, minified, binary | excluded at L0 by the linguist tables, by a NUL-byte sniff and by size; each exclusion is counted in `inventory.skipped` |
| symlinks and submodules | mode `120000` and type `commit` entries are listed and never followed [GH-TREES] |
| a SARIF file the caller or a scanner authored | `revisionId` must equal the commit; `message`, `snippet` and rule `help` are never rendered; the tool name must be in a table of tools this service knows; result and run counts are capped at GitHub's own limits |
| private repository permissions | an installation token scoped to the one repository with read permissions only, one hour of life, never persisted, never in a log or a report; the caller's concurrency ceiling applies to the job that carries it |
| execution | none; parsers and scanners read bytes, and no build, install or test runs |
| exhaustion of the GitHub API | one archive request per job and one commit request; code scanning reads are bounded by the analyses page size; a 403 or a rate-limit answer fails the job closed with the reason |

**What a reader should not believe.** Redaction and exclusion lower the surface; they do not
close it. An excerpt is still attacker-authored text inside the prompt, and the critic's
rules against instruction-following text apply to it as they apply to a transcript.

---

## PART 3 — Comparison matrix

Scored against the ticket's criteria. `+` helps, `-` costs, `0` neutral.

| Criterion | A raw retrieval | B catalog | C catalog + snippets | D scanners only | E combined |
| --- | --- | --- | --- | --- | --- |
| ASVS coverage gained | + | 0 | + | - | + |
| grounding and correctness | - | + | + | + | + |
| false-positive risk | - | + | 0 | + | 0 |
| absence vs evidence of absence | - | + | + | 0 | + |
| deterministic and reproducible | - | + | + | + | + |
| provenance | - | + | + | + | + |
| prompt-injection resistance | - | + | 0 | + | 0 |
| token cost | - | + | 0 | + | 0 |
| model-call count | - | + | + | + | + |
| language portability | + | - | 0 | - | 0 |
| private-repository security | - | + | + | + | + |
| implementation complexity | + | 0 | - | 0 | - |
| tool dependencies | + | + | 0 | - | - |
| fits the package contract | 0 | + | + | + | + |
| fits `needs_evidence` | 0 | + | + | + | + |
| System Model impact | 0 | + | + | + | + |
| eval and testability | - | + | + | + | 0 |

B wins on every axis but coverage and portability. C recovers coverage at the price of one
injection surface, the excerpt, which 2.10 bounds. E is C plus D, and D's marginal value is
real but small. So the staged path is B, then C, then D's ingestion, which is E arrived at by
measurement rather than assumed.

---

## PART 4 — Eval strategy, with #471

**Fixtures are pinned bytes, never a live fetch.** A repository-backed corpus case carries
`repository: {owner, name, commit_sha, tree_sha}` and a committed archive under
`evals/corpus/<case>/repo.tar.gz` with its digest in `case.json`. The offline suite reads the
archive and never GitHub. Every fixture repository must carry a licence the corpus can
reproduce, recorded in `provenance` as `docs/agents/licensing.md` requires for a converted
case; a synthetic repository written for the case is the safer default.

**Paired cases.** Two commits of one fixture where one line flips the ruling: a `USER`
instruction added, `sslmode` changed, a decorator removed. The pair is the regression test
for the third row of 2.7, because the *before* commit must read as a gap and the *after* as
`needs-info`, and never as a pass.

**Instruments**, each keyed into `evals/harness/instruments.py`:

| Instrument | Question |
| --- | --- |
| `retrieval` | of the catalog entries the reference names, how many reached the lane; and how many entries the lane got that the reference does not name |
| `association` | does the claim's repository ground name the path the reference names |
| disposition, extended | `false_confirmed` gains the repository-backed case: a gap ruled from code where the reference says code cannot show it |
| `attribution` | gains a `retrieval` stage between generation and critic, read off the lane's empty-retrieval Coverage row |
| `supported`, phase 3 only | false-support rate, when and only when a Scope Entry state exists to measure |

**The reference records grow one field.** `evidence_path`, the path a person judged to hold
the answer, so `association` has a ground truth. A record with no path is unjudged for that
instrument and excluded, as an unjudged disposition is today.

**Every case needs a human read before its numbers mean anything.** #226 holds one read of
eleven. A repository-backed case doubles what a reader must open.

---

## PART 5 — Staged proof of concept

**Do not start before the #659 baseline sweep exists.** The gain this ticket promises is a
fall in deferral and in false prose requests, and both are unmeasured on the current code.
Without the baseline, the first repository sweep has nothing to compare to.

**Phase 1.** One GitHub repository per job. Resolve the ref to a commit and a tree. Fetch the
archive with the bounds of 2.10. L0 inventory with the linguist tables. gitleaks, then L1
manifest parsing and L2 parsers for Dockerfile, GitHub workflows and one settings format. The
catalog with its two entry shapes and the two ground kinds. `carried` as a job property.
`RepositoryRef` in the report. Retrieval tables for four lanes, chosen by the corpus:
authorization, encoding and sanitization, web frontend security and secure communication. The
empty-retrieval rule of 2.7. No new Verdict, no new Scope Entry state, no scanner beyond
gitleaks. One repository-backed corpus case, read by a person, and one pre-flight sweep.

**Phase 2.** L3 syntax facts through tree-sitter for Python and JavaScript. Semgrep CE with
local rules and `--metrics=off`, after the rules licence is read. osv-scanner offline. SARIF
ingestion from GitHub code scanning. Retrieval tables for all 17 lanes. Paired cases and the
`retrieval` and `association` instruments. Five runs of one configuration.

**Phase 3.** More grammars. The `supported-by-implementation` Scope Entry state, argued in an
ADR against ADR 0013 and measured on paired cases. #483's stage 3 enrichment as accepted
Proposals.

**The recommendation of no change, and when it applies.** If the phase 1 sweep does not move
deferral or false prose requests on the four lanes it covers, stop at phase 1 and record the
measurement. The cost of phase 2 is a scanner dependency set, a licence question and a second
parser per language, and none of that is earned by a design that did not move a number.

---

## PART 6 — What would falsify this

**Finding 1 falls** if the human read of the corpus reclassifies the `needs-code` and
`needs-config` records as `needs-more-prose` at scale. Then the gap is a prompt gap, and
#226 is the ticket that would show it.

**Finding 3 falls** if a measured phase 1 shows lanes citing excerpts that are present and
irrelevant at a rate the critic does not catch. Then the excerpt shape needs the ladder's
cousin, a relevance check, and Option B without C is the fallback.

**Finding 5 falls** if the CodeQL terms change or the Semgrep rules licence permits bundling.
Recheck the licence files, not this note.

**Finding 6 falls** if a paired case shows a repository setting that no deployment can
override, at a rate that makes `seen at this commit` and `in force` the same fact for a class
of requirements. That is the argument phase 3 would need to make.

---

## Sources

- [ASVS-0x04] OWASP ASVS 5.0.0, `0x04-Assessment_and_Certification.md`, tag `v5.0.0`.
  <https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x04-Assessment_and_Certification.md>
- [SARIF] OASIS SARIF Version 2.1.0 Errata 01, sections 3.19, 3.23, 3.27, 3.29, 3.30.
  <https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/sarif-v2.1.0-errata01-os-complete.html>
- [GH-COMMITS] GitHub REST, Get a commit. <https://docs.github.com/en/rest/commits/commits>
- [GH-TREES] GitHub REST, Get a tree. <https://docs.github.com/en/rest/git/trees>
- [GH-CONTENTS] GitHub REST, Repository contents and archive downloads.
  <https://docs.github.com/en/rest/repos/contents>
- [GH-CODESCAN] GitHub REST, Code scanning. <https://docs.github.com/en/rest/code-scanning/code-scanning>
- [GH-SARIF] GitHub Docs, SARIF support for code scanning.
  <https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/sarif-support-for-code-scanning>
- [GH-SBOM] GitHub REST, Software bill of materials. <https://docs.github.com/en/rest/dependency-graph/sboms>
- [GH-RATE] GitHub REST, Rate limits. <https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api>
- [GH-PAT] GitHub Docs, Managing your personal access tokens.
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>
- [GH-INSTALL] GitHub Docs, Generating an installation access token for a GitHub App.
  <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app>
- [GH-JWT] GitHub Docs, Generating a JSON Web Token for a GitHub App.
  <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app>
- [LINGUIST] github-linguist, `docs/overrides.md` and the data files it names.
  <https://github.com/github-linguist/linguist/blob/main/docs/overrides.md>
- [TREE-SITTER] tree-sitter. <https://tree-sitter.github.io/tree-sitter/>
- [CODEQL-LIC] GitHub CodeQL CLI licence. <https://github.com/github/codeql-cli-binaries/blob/main/LICENSE.md>
- [CODEQL-LANG] CodeQL supported languages. <https://codeql.github.com/docs/codeql-overview/supported-languages-and-frameworks/>
- [SEMGREP-CLI] Semgrep CLI reference. <https://docs.semgrep.dev/cli-reference>
- [SEMGREP-LIC] Semgrep engine licence, LGPL-2.1. <https://github.com/semgrep/semgrep/blob/develop/LICENSE>
- [SEMGREP-RULES-LIC] Semgrep Rules License v1.0. <https://github.com/semgrep/semgrep-rules/blob/develop/LICENSE>
- [GITLEAKS] gitleaks README. <https://github.com/gitleaks/gitleaks/blob/master/README.md>
- [GITLEAKS-LIC] gitleaks licence, MIT. <https://github.com/gitleaks/gitleaks/blob/master/LICENSE>
- [OSV] osv-scanner output formats. <https://google.github.io/osv-scanner/output/>
- [OSV-LIC] osv-scanner licence, Apache-2.0. <https://github.com/google/osv-scanner/blob/main/LICENSE>
