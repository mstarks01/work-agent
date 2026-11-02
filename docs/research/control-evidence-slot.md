# What slot a Control's evidence fills beside Sources (wayfinder #2113)

**Question.** A **Control** may carry a document, such as a configuration file. The map
[#2111](https://github.com/mstarks01/work-agent/issues/2111) says that the document is
evidence beside **Sources** and never a Source, and that a later code scan
([#484](https://github.com/mstarks01/work-agent/issues/484)) fills the same slot. What is
that slot in the code today, and what does the `#484` plan need from it?

**This file records facts and pointers. It rules nothing.** The ruling belongs to the ADRs
of the map.

**Method.** The code is read at `origin/main` (`14d17a3c`). The line numbers below point
at that commit. The other sources are `docs/research/repository-evidence.md` (frozen at
`8085c43`), the two comments on #484, the resolution comment on
[#2112](https://github.com/mstarks01/work-agent/issues/2112), and the `Control` entry of
`GLOSSARY.md` on the branch `docs/wayfinder-live-map-2111`.

---

## 1. The input shape the #484 note names

**A repository is a second job input beside Sources, and never among them.** The note
says so at `docs/research/repository-evidence.md:135-138`. The reason is mechanical. A
Source is text that the extraction prompt renders. `render_sources` wraps each Source in
the register of its kind (`src/analysis_service/sources.py:642`), and the kind is a closed
set of four: `description`, `transcript`, `diagram`, `answers`
(`src/analysis_service/sources.py:53`). A repository is bytes that no prompt renders
whole. An entry in `sources` would go through `render_sources` and into the extractor.

The note proposes one entry on the job envelope, `repository`, with a host, an owner, a
name and a ref (`repository-evidence.md:139-146`). The report gets a `RepositoryRef`
beside `SourceRef`, with the commit SHA, the tree SHA, the archive SHA-256 and the
inventory (`repository-evidence.md:175-189`). The first comment on #484 keeps that shape:
one repository per job, with an ordinal in every ID, so a second repository is additive.

The review comment on #484 changes the names and keeps the slot. The core accepts a
`RepositoryLocator` and receives an `ArtifactSnapshot` from an injected provider adapter
(correction 1). The ground kinds become `artifact-fact` and `artifact-excerpt`, so that
"uploaded archives, local source trees, SBOMs, generated configuration, and future
deployment evidence" fit without a second report-schema redesign (correction 6).

**What a Control's document shares with that shape.** Four properties.

1. It never enters `sources`. The glossary entry says that a Control "may carry evidence,
   such as a configuration file, which is never a Source"
   (`GLOSSARY.md:99` on the map branch). The #2112 resolution gives the reason: the
   extraction must never read a configuration file as a system description (#2112, point
   6). This is the same reason the note gives at `repository-evidence.md:135-138`.
2. It is an uploaded artifact with a digest. An **Owner File** is an object plus a row
   that holds the SHA-256 and the size (`docs/adr/0118`, decision; `src/analysis_service/
   owner_files.py:46-58`). The review comment's `ArtifactSnapshot` names an uploaded
   archive as one producer. A Control's document is the same class of thing: bytes the
   service holds, with a digest it checks.
3. The job keeps its own copy. #2112 point 7 says that a job keeps a copy of each applied
   Control, as `docs/adr/0119` does for an Owner File Source. The note asks the same of a
   repository: the report carries the resolved SHA, so a reader can tell the ref from the
   bytes (`repository-evidence.md:152-154`).
4. It carries a class from the `needs_evidence` set. The note keeps the four routing
   kinds and adds a role per file, with one table from role to kind
   (`repository-evidence.md:205-224`). A Control's document carries the kind directly.

**What differs.** Today an Owner File can hold only a Source kind. The validator
`_uploaded_kind` refuses a kind outside `KIND_FORMATS`
(`src/analysis_service/owner_files.py:65-69`), and `KIND_FORMATS` is keyed by
`SourceKind` (`src/analysis_service/sources.py:96`). So the Files section stores Sources,
and a Control's document cannot be an Owner File row without a change to that rule.

## 2. The class set `config`, `code`, `people` and its one reader

**The set is declared once, on the ASVS proposal.** `needs_evidence` is
`Literal["", "prose", "code", "config", "people"]` at
`src/analysis_service/frameworks/asvs/record.py:478`. That line is the only place in
`src/` that spells `"people"`. The neutral layer declares no such set: `ScopeEntry.needs`
is a plain `str` of at most 100 characters (`src/analysis_service/claims.py:2404`), and
`CARRIED_EVIDENCE_KINDS` is a `tuple[str, ...]` with one entry, `("prose",)`
(`src/analysis_service/sources.py:246`).

**The readers of the field.**

| Reader | Where | What it does with the kind |
| --- | --- | --- |
| `partition_proposals` | `frameworks/asvs/record.py:334-357` | defers each proposal whose kind is not in `carried` |
| the fan-in | `src/analysis_service/fan_in.py:196-200` | passes the constant `CARRIED_EVIDENCE_KINDS` to it; the only call site |
| `scope_entries` | `claims.py:2737-2744` | writes `needs=kind` and `reason=deferral_reason(kind)` on a `needs-other-evidence` entry |
| `deferral_reason` | `claims.py:2334-2341` | "a lane raised it, and settling it needs {kind}" |
| `_ROUTED_AWAY` | `src/analysis_service/evidence.py:882` | strips the field before a draft is built |
| `DISPOSITION_FOR_EVIDENCE` | `evals/harness/reference.py:356-363` | maps each kind to a corpus disposition; `test_disposition_vocabulary` checks the keys against the Literal |
| `_routed` | `evals/harness/applicability.py:472-479` | accepts `needs-info` where the kind is carried, else a `deferred` entry with that `needs` |

The lane prompt teaches the set at `frameworks/asvs/output.md:32`. `prose` is "a fuller
description of *this* system". `code`, `config` and `people` apply "when no description
could, because the answer is in the implementation, in a deployed setting, or with a
person".

**Whether `prose` belongs to a Control document.** The facts say no, in three places.
The record says that `prose` "means more of the same input would" settle the
requirement, so the claim stands as a request the submitter can act on
(`frameworks/asvs/record.py:435-437`). The same input is a Source. The note catalogs a
repository's prose and never renders it: a `README.md` becomes an entry with a size and a
digest, and "its text does not enter a prompt unless the caller lists that path as a
Source on purpose" (`repository-evidence.md:234-238`). The map Note and #2112 point 6
list the document classes as `config`, `code`, `people`, with no `prose`. A document of
prose that describes the system is a Source, and the Owner File path already carries one
(`docs/adr/0119`).

The review comment on #484 adds one fact about `config`. The kind "conflates repository
configuration with deployed state", and an evidence record needs an authority dimension
such as `repository`, `deployment`, `runtime` or `organization` (correction 7). A Control
document of class `config` comes from the owner, and the owner can state which of those it
is. A repository file cannot.

## 3. The ground kind the critic cites, and who copies the excerpt

**Today's ground kinds.** `GroundKind` holds seven: `quote`, `unknown-attribute`,
`absent-attribute`, `derived-fact`, `absent-element`, `assertion`, `unknown-assertion`
(`src/analysis_service/claims.py:123-131`). A `quote` carries `text` and `source_label`,
and the model writes the text. The service then checks the text against the Source's
bytes with the ladder in `grounding.py`, and repairs or marks it
(`src/analysis_service/fan_in.py:679-775`).

**No model ever writes a Ground.** The docstring says so at `claims.py:541-553`. The one
writer is `resolve_proposals` (`src/analysis_service/evidence.py:885`). It copies a
catalog entry the agent names, or builds a `quote` or an `absent-element` ground from the
words the agent wrote (`evidence.py:838-860`).

**The kind the note proposes for an artifact.** Two new kinds, `repo-fact` and
`repo-excerpt` (`repository-evidence.md:253-270`). For an excerpt, "the agent proposes
`{path, line_start, line_end}` and no text. The service reads the lines from the archive
it holds, bounds them, and builds the ground" (`repository-evidence.md:272-276`). The
`text` field is "copied by the service from the archive, bounded"
(`repository-evidence.md:268`). The ladder is not used, because there is no model-written
text to verify. The ground carries the commit, the path, the lines and their digest
(`repository-evidence.md:280-281`). The excerpt renders inside a fence sized to its own
content, by the rule `render_sources` already applies (`repository-evidence.md:471`). The
review comment renames the kinds to `artifact-fact` and `artifact-excerpt` (correction 6)
and states the rule: "The model should cite locations, not create them" (correction 5).

So the answer to "who copies the excerpt" is the service, by location. The model names
a path and a line range. This is a stronger property than a `quote` has, and the note
says so at `repository-evidence.md:48-53`.

**What the critic cites today.** The critic reads a draft's `grounds` and never writes
one (`prompts/critic.md:31`). Where a Source sentence defeats an argument, the critic
quotes the sentence with its label in the free-text `reason`. The service glosses an
`assertion` ground for the critic through `ground_gloss`
(`src/analysis_service/critic.py:927-941`), so the critic rules against the row's words
and not against its identity. A `Verdict` carries a `reason` and `related_unknowns`, and
no `grounds` field.

**What #2112 asks the critic to cite.** The critic rejects a draft with a new rejection
step, `control`, which cites the row (#2112, point 4). `RejectionStep` today is
`evidence`, `reasoning`, `lane`, `duplicate` (`claims.py:108`), and `CriticStep` is the
first three (`claims.py:114`). A row is not a ground, and the ruling shape holds no
ground. If the row rests on a document excerpt, the excerpt reaches the critic only as
rendered evidence, and the ground kind that carries it is the `artifact-excerpt` kind of
#484, built by the service.

## 4. How a carried kind settles an ASVS lane today

**The mechanism, in order.**

1. The lane writes `direction: question` and `needs_evidence: <kind>` on a proposal
   (`frameworks/asvs/record.py:478-497`; `frameworks/asvs/output.md:17,32`).
2. The fan-in passes `CARRIED_EVIDENCE_KINDS`, the constant `("prose",)`, to
   `partition_proposals` (`fan_in.py:196-200`; `sources.py:246`).
3. A proposal whose kind is in `carried` is kept, and becomes a draft. A proposal whose
   kind is not in `carried` is deferred (`record.py:349-357`). A deferred proposal never
   becomes a draft and never reaches the critic (`claims.py:992-997`).
4. Each deferred unit becomes a Scope Entry in the state `needs-other-evidence`, with
   `needs=<kind>` and the reason "a lane raised it, and settling it needs <kind>"
   (`claims.py:2737-2744`; `claims.py:2334-2341`). The validator requires `needs` on
   exactly that state (`claims.py:2410`).
5. A kept draft goes to the critic. `settled_by_grounds` composes the `needs-info`
   reference half from the draft's open grounds and decides no verdict
   (`claims.py:1167-1192`). The critic rules `confirmed`, `needs-info` or `rejected`
   (`prompts/critic.md:50`). ADR 0013 holds: no state carries a pass
   (`docs/adr/0013`, decision).

So today, a `question` with `needs_evidence: config` ends as a Scope Entry on every job.
The field is stripped from the draft (`evidence.py:882`), so a report never says which
carried kind a `needs-info` claim asked for. The scorer reads `carried` for the same
reason (`applicability.py:447-460`).

**What the #484 note proposes.** `CARRIED_EVIDENCE_KINDS` becomes a job property:
`("prose",)` for a job with Sources alone, `("prose", "code", "config")` for a job with a
repository (`repository-evidence.md:385-390`). `partition_proposals` and the scorer need
no change. Carrying a kind does not settle a requirement. The note gives three outcomes
(`repository-evidence.md:392-408`): a fact that shows the gap gives `confirmed`; a fact
that speaks and shows no gap gives `needs-info` with the missing thing named; nothing
that speaks gives `needs-info` with a reason from code, "repository evidence retrieved
for this lane did not speak to it". The third rule runs in code before the critic reads
the draft. `people` stays deferred (`repository-evidence.md:409`).

The review comment objects to the job-wide property. "A repository's presence must not
make `CARRIED_EVIDENCE_KINDS` globally become `("prose", "code", "config")`", because
presence does not establish relevant content; availability is per requirement
(correction 2). An unsuccessful search is not a claim (correction 3). Its outcome table
says that evidence which appears to support the control gives no pass, only a
non-verdict coverage row.

**What a Control document of class `config` would do to a row that waits on
`needs_evidence: config`.** Two cases, by the one reader.

- If `carried` stays `("prose",)`, nothing changes. The proposal is deferred at
  `record.py:351`, and the Scope Entry says "settling it needs config". The document is
  invisible to the ASVS seam, whatever it holds. The row of the Control reaches the critic
  only on a draft, and a deferred proposal is never a draft.
- If the document adds `config` to the job's carried kinds, the proposal becomes a draft
  and reaches the critic. The critic can then confirm the gap, or rule `needs-info`, or
  reject it. The row of the Control reaches the critic on that draft, as #2112 point 4
  says. With no ground on the document, the note's third rule gives `needs-info` before
  the critic reads it. The scorer's `_routed` then accepts a `needs-info` claim for a
  `needs-config` record (`applicability.py:476-478`).

In both cases the lane still writes `config`, because the lane sees the catalog and the
Sources and not the document (`output.md:32`). A Control's neutral part is different: it
writes facts into the model before the lanes run, through `answered_model`
(`src/analysis_service/fact_writes.py:264`; #2112, point 2), so a row that a fact settles
never asks for `config` at all.

## 5. What #484's phase 1 would change if this map lays the slot first

**Phase 1 as the note states it** (`repository-evidence.md:558-566`): one GitHub
repository per job; resolve the ref to a commit and a tree; fetch the archive; the
inventory with the linguist tables; gitleaks; manifest and two configuration parsers;
"the catalog with its two entry shapes and the two ground kinds"; "`carried` as a job
property"; `RepositoryRef` in the report; retrieval tables for four lanes; the
empty-retrieval rule of 2.7. No new Verdict and no new Scope Entry state.

**What phase 1 would need to change.**

1. The job envelope entry. The note puts `repository` on the envelope. If the map lays a
   second input list beside `sources`, the repository becomes one producer into that
   list, as the review comment's `ArtifactSnapshot` already asks (correction 1).
2. The report reference. `RepositoryRef` (`repository-evidence.md:175-189`) would become
   the provenance of one producer, beside the provenance of a Control document (an
   owner's object with a SHA-256, `docs/adr/0118`).
3. The ground kind names and IDs. The note's `repo-excerpt` ID is
   `excerpt:<r>:<path>#L<start>-L<end>` with `<r>` a repository ordinal
   (`repository-evidence.md:265-266`). A Control document has no ordinal and no path
   in a tree. The review comment's `artifact-excerpt` is the kind both producers can share,
   and its ID rule would have to name an artifact and not a repository.
4. The class writer. The note classifies a repository file by a role table
   (`repository-evidence.md:211-224`). A Control document carries a class the owner
   states. The slot then has two writers of one field, and `docs/agents/one-rule-one-reader.md`
   asks that the two be tested against each other.
5. The carried-kind rule. The note makes `carried` job-wide; the review asks for it per
   requirement. If the map decides this for a Control document, phase 1 inherits the
   decision and does not decide it again.

**What phase 1 gets for free.**

- The slot on the envelope, the job's own copy and the report reference.
- `carried` threaded as an argument. `partition_proposals` needs no change
  (`record.py:334-357`; `sources.py:239-246`), and the scorer's `satisfies` reads
  `carried` already (`applicability.py:432-466`).
- The one ground kind for a copied excerpt, its branch in `Ground._REQUIRED`
  (`claims.py:655-663`), its resolver branch in `resolve_proposals`, and its render for
  the critic, in the shape `ground_gloss` already gives an assertion ground
  (`critic.py:927-941`).
- The fence rule for a rendered excerpt (`sources.py:642`; `repository-evidence.md:471`).
- The decision of whether a carried kind is job-wide or per requirement.

## 6. Open questions for the map

1. **Job-wide or per requirement.** Does a document of class `config` add `config` to
   the job's carried kinds for every lane (`repository-evidence.md:385-390`), or does
   availability attach to the rows the Control names (#484 review, correction 2)? Who
   states which requirements a document speaks to?
2. **The ground on the ruling.** The `control` rejection step cites a row (#2112, point
   4). A `Verdict` holds no ground. Does the step carry an excerpt ground, a row ID, or
   both? Does a `control` rejection rule on the unit? `rules_on_unit` reads only `None`
   and `evidence` as a ruling (`claims.py:1349`).
3. **Where the document lives.** An Owner File row holds a Source kind only
   (`owner_files.py:65-69`). Does a Control document get a second kind set on that table,
   or a table of its own? ADR 0118's key rule gives a file key two parts.
4. **Who classifies.** The owner states `config`, `code` or `people` on upload; #484
   classifies by a role table. Which writer wins where the two disagree, and which test
   compares them?
5. **The authority of a `config` document.** The review comment asks for `repository`,
   `deployment`, `runtime` or `organization` on an evidence record (correction 7). Does a
   Control document carry that field, and does a row read it?
6. **Whether the lane sees the document.** The lane writes `config` when no description
   could settle the requirement (`output.md:32`). If the lane never sees the document, a
   row waits on `config` on every job, and only the critic can read the row.
7. **The `people` class.** The note defers `people` on every job
   (`repository-evidence.md:409`). What does a document of class `people` hold, and what
   row reads it?
8. **Bounds and redaction.** The note runs gitleaks before any model reads a byte
   (`repository-evidence.md:464-471`). A configuration file an owner uploads commonly
   holds a credential. Does a Control document get the same redaction, and the same
   per-file cap?
9. **The eval word.** A `needs-config` record is satisfied by `needs-info` where `config`
   is carried (`applicability.py:476-478`), and the report cannot tell two carried kinds
   apart (`applicability.py:457-460`). Does a row a Control settles need a disposition of
   its own?
10. **No pass.** ADR 0013 and the #484 review's outcome table give no positive state. A
    row's outcome `applied` (#2112, point 4) rejects a draft. Which Scope Entry state, if
    any, does the rejected unit take?

---

## Sources

- `docs/research/repository-evidence.md`, frozen at `8085c43`; sections 2.1, 2.2, 2.3,
  2.7, 2.10 and Part 5.
- Issue #484, the first comment (the ten answers) and the review comment (the ten
  corrections and the outcome table).
- Issue #2112, the resolution comment, points 2, 4, 6 and 7.
- `GLOSSARY.md:99` on `docs/wayfinder-live-map-2111`, the `Control` entry.
- `src/analysis_service/claims.py`, `src/analysis_service/evidence.py`,
  `src/analysis_service/fan_in.py`, `src/analysis_service/sources.py`,
  `src/analysis_service/owner_files.py`, `src/analysis_service/fact_writes.py`,
  `src/analysis_service/critic.py`, `src/analysis_service/frameworks/asvs/record.py`,
  `frameworks/asvs/output.md`, `prompts/critic.md`, `evals/harness/reference.py`,
  `evals/harness/applicability.py`, all at `14d17a3c`.
- `docs/adr/0012`, `docs/adr/0013`, `docs/adr/0118`, `docs/adr/0119`.
