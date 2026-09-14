# 34. An assertion is a scoped statement with a support span, and an element attribute is its projection

- **Status**: accepted
- **Date**: 2026-09-14
- **Effort**: Phase 1 of
  [#926 — implement a source-backed assertion layer](https://github.com/mstarks01/work-agent/issues/926),
  which elaborates [#741](https://github.com/mstarks01/work-agent/issues/741) and
  answers the root-cause audit in
  [#925](https://github.com/mstarks01/work-agent/issues/925).
- **Evidence**:
  [`docs/research/assertion-readers.md`](../research/assertion-readers.md), the
  reader inventory and the corpus measurement this decision rests on.
- **Relates to**:
  [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md), whose three
  tests every widening of what an agent may read must pass;
  [ADR 0031](0031-a-claim-identity-carries-no-direction.md), whose computed,
  versioned identity this copies; and
  [ADR 0032](0032-a-derived-crossing-names-an-inferred-zone.md), whose
  derived-never-declared rule this copies.

## Context

The **System Model** holds one string per security-relevant attribute. That
string is the only record of what a **Source** stated about a control, and every
rule reads its leading token.

**The corpus already writes more than one fact into one string.** Across the 13
blessed models, extraction states 21 mechanism values. Twelve join two clauses.
**Ten state an absence, and the Evidence Catalog offers nothing for any of the
ten** — `control_state` reads them as `stated`, so no entry is published, no
candidate rule fires, and no figure moves. The facts inside them are of four
kinds the schema has no field for: a second control's absence, a credential's
sharing scope, a credential's lifecycle, and an authorization grant.

**The gap is measurable from both ends.** `tests/test_evals_modes.py::TestWhatNoFigureHereReaches`
pins three mutations that every extraction figure scores as perfect: reverse
`no MFA` into `MFA enforced for every shopper`, replace every excerpt with a real
quote about something else, or move one flow's controls onto another flow.
#927 closed the empty-citation hole and #928 published the comparison
denominator. Neither reaches these three, because each needs a judgement about
what a source *means*, and no reduction of a string to a state supplies one.

**The seam is narrower than it looks.** The reader inventory finds 15 production
readers of the migrated facts. Eleven route on `control_state`, and two more
route on the same leading-token read. One renders the value verbatim into the
prompt every lane agent and every critic reads.

## Decision

**Extraction emits an assertion catalog beside the graph. The graph stays
authoritative for topology. For a migrated predicate the catalog becomes
authoritative, and the element attribute becomes a projection code computes.**

This ADR freezes the contract. It ships no schema; `#926` Phase 2 does that.

### The objects

**Assertion** — one statement about one **Subject**, in one **Predicate**, at one
**Scope**. It carries a value, a basis, its support, and a support assessment.
An assertion says what a source states. It never says what the deployed system
does.

**Subject** — what an assertion is about. Six types. `component`, `interaction`
and `zone` bind to an **Element ID** the graph already carries. `principal`,
`credential` and `artifact` are the assertion layer's own, so a shopper account
class needs no invented Process to hold a fact about it. A subject carries a
stable ID, a display label and its aliases, and the label is never the ID.

**Scope** — an ordered tuple of typed qualifiers that says who or what the
assertion covers. Five kinds: `principal`, `operation`, `resource`,
`environment`, `condition`. An empty scope means the source stated the fact
without a qualifier. It never means "every principal".

**Support Span** — where a statement sits in a source. It carries the source
label, the source's content digest, the exact quote, and Unicode code-point
offsets into the retained original text. A discontiguous excerpt takes several
spans.

**Predicate registry** — a table that defines each predicate: its meaning, the
subject types it accepts, its value type, whether it requires a scope, its
multiplicity, and the graph field it projects into.

### Ten rules

**1. An unknown is an assertion, not a missing row.** Every predicate admits two
terms beside its own vocabulary: `absent` and `unknown`. An `unknown` carries a
reason — `silent`, `hedged`, `unmeasured` or `truncated` — and needs no support
span. So a bound that stops the work writes a row rather than dropping a fact,
and #926's coverage object needs no separate type. **Absence from the catalog is
never an absent control.** It is a predicate nobody asked about.

**2. An absence is a positive statement, and its basis says who made it.** A
value of `absent` with basis `stated` is the source saying *no*. The same value
with basis `inferred` is this service saying so. The two never collapse.

**3. A conflict is derived, never declared.** Two assertions that share a
subject, a predicate and a scope, and differ in value, *are* the conflict. One
reader computes the set from the catalog. Nothing stores a conflict record,
because a stored record and the rows behind it are two readers of one rule. No
source order and no arrival order settles a conflict: **Source** already makes
no authority claim, and this changes nothing about that.

**4. Identity is computed, versioned, and local to one artifact.** An
assertion's ID is a pure function of its subject, its predicate, its scope and
its canonical value. A reader recomputes it rather than trusting a stored hash,
exactly as a **Claim** identity recomputes, so improving the rule re-keys the
catalog by recomputation. Two assertions that agree on all four are one
assertion with two support spans, which is how two sources for one fact keep
both provenances. **The ID is not a cross-run alignment.** Two extraction runs
that word one mechanism differently produce two IDs, and matching them is an
explicit alignment step whose unresolved cases stay visible.

**5. Code locates a span. A model never states an offset.** Extraction proposes
a quote. `analysis_service.grounding` decides whether the quote is in the source,
and code records the offsets of the span it found. That keeps one reader for
"is this quote in this source", so a span and a **Ground** can never disagree
about the same quote. A quote that does not verify yields no span, and the
assertion is refused or routed to repair rather than given a fabricated one.

**6. A basis names what is required.** `stated` requires at least one support
span. `inferred` requires at least one premise assertion and an explanation.
`derived` requires a rule ID and its version. `legacy` requires neither and is
never support for anything: an attribute imported from an old report is
`legacy`, whatever quote its element carries.

**7. The extractor's own word is not an assessment.** Support assessment is a
separate field with four values — `unchecked`, `supported`, `unsupported`,
`unresolved` — and it defaults to `unchecked`. Only a reader that is measured
against independent labels may set it, and it records that reader's identity and
version. There is no model judge here, exactly as there is none in claim
identity.

**8. The registry is the service's, and a framework package cannot extend it.**
ADR 0010's three tests hold. A predicate is a fact about the submitted sources
rather than about any method, so every lane of every framework receives the same
catalog. A framework-specific predicate is a framework conclusion wearing a
predicate's clothes, and it is refused.

**9. A projection that cannot fit says `unknown`.** An element attribute is
built from the catalog. Where several scopes or an unresolved conflict cannot
fit one string, the projection writes `unknown` and the catalog keeps every
assertion. It never picks one. A projection never writes a value the catalog
does not hold.

**10. A registry version and a projection version ride in provenance.** Both go
into the artifact's version record, so a run says which registry ruled its
values. A changed source digest, a changed value or a changed scope invalidates
the affected support assessments.

### The first registry

Fourteen predicates. The first release covers authentication, authorization,
encryption and trust relationships, plus the credential and verification
distinctions the audit fixtures need.

| Predicate | Subject | Value | Projects into |
| --- | --- | --- | --- |
| `authentication-mechanism` | interaction, component | mechanism text | `DataFlow.authentication` |
| `credential-presented` | interaction, principal | credential reference or text | `DataFlow.authentication` |
| `mfa-requirement` | interaction, principal | `required` | none |
| `authorization-grant` | principal | operation set; scope requires a resource and an operation | none |
| `credential-sharing` | credential | `per-principal`, `shared` | none |
| `credential-rotation` | credential | `rotated`, `not-rotated` | none |
| `credential-expiry` | credential | `expires`, `does-not-expire` | none |
| `transport-encryption` | interaction | mechanism text | `DataFlow.encryption_in_transit` |
| `storage-encryption` | component | mechanism text | `DataStore.encryption_at_rest` |
| `signature-verification` | artifact, interaction | `verified` | none |
| `destination-verification` | interaction | `verified` | none |
| `network-membership` | component | zone reference | `ZonedElement.trust_zone` |
| `administrative-authority` | principal | component reference | none |
| `tenant-ownership` | component | principal reference | none |

Every predicate also admits `absent` and `unknown`, by rule 1. Nine project into
nothing, which is the measurement in one line: **the graph has no field for nine
of the fourteen facts the first release scopes.**

**Neither zone predicate projects, and `TrustBoundary.kind` is why.** The zone
kind answers who controls a zone and then what authority it holds, so the two
predicates decide it together. It also holds one value from a closed vocabulary
with no `unknown` in it, and rule 9 needs an `unknown` to write when a
projection cannot fit. So the zone kind stays the graph's own, and Phase 4 rules
on it with the projection in front of it.

### Registry version 2

The first live run of the assertion node, on `01-payments-checkout`, read two
facts out of one source that the table above has nowhere to put. *"The password
comes from an environment variable on the worker"* is where a credential is
kept, and *"it is the only thing we expose to the internet"* is a claim about
every other component as well as that one. Version 2 answers both.

- **`credential-custody`** — where a credential is kept. Subject `credential`,
  free text, no graph field.
- **`internet-exposure`** — whether a component can be reached from the
  internet. Subject `component`, the terms `internet-facing` and `internal`,
  projecting into `exposure`.
- **`exclusive`**, a field on an assertion rather than a predicate. It says the
  source states **no other subject** holds this predicate at this value, which
  is what lets a reader close the world for one predicate. It is a claim, so the
  gate refuses it on a row that is not `stated` or carries no span.

Sixteen predicates now, **ten of which project into no graph field**.

**A fact outside the registry stays source material.** Extraction does not
invent a predicate and does not force a fact into the nearest wrong one. That is
the same rule the asset vocabulary and the **Ground** kinds already follow.

### What this decision does not settle

- **The extraction prompt.** Phase 3 decides how a model is asked for subjects,
  scopes and spans, and whether one call emits topology and assertions together.
- **The consumer migration.** Phase 4 decides how the evidence catalog carries a
  positive assertion, and ADR 0010's three tests govern it.
- **The `assumptions` list.** An **Assumption** stays the record of a value this
  service inferred into a graph attribute. The catalog's `inferred` basis covers
  predicates the graph has no field for. When a predicate's projection becomes
  authoritative, its Assumption entries derive from the catalog rather than sit
  beside it. Until then neither writes the other.
- **The promotion thresholds.** Phase 6 predeclares them against a published
  baseline, before any tuning.

## Consequences

**Nine facts gain somewhere to live, and ten corpus values stop hiding an
absence.** A stated absence becomes a row a rule can read and an agent can cite,
instead of prose inside a string that reads as a present control.

**Every reader keeps working while the catalog grows.** `control_state` decides
for eleven of the fifteen readers, and rule 9 keeps feeding it. So Phase 2 and
Phase 3 land with no consumer change, and Phase 4 migrates readers one at a
time.

**An artifact grows, and so does the cost of a run.** A model that emits spans
and scopes emits more tokens than one that emits a string per attribute. Phase 6
measures output volume, cost and latency against the baseline, and rule 1's
`truncated` reason is what a bound writes rather than dropping a fact silently.

**A support span is a new place a model can be wrong, and the design refuses to
hide it.** Rule 5 gives a span no way to exist without a verified quote. Rule 7
gives an unverified statement no way to read as verified. So the failure this
layer adds is a visible `unresolved`, rather than an invisible upgrade of a
guess into a fact.

**This ADR may be amended by a numbered ADR after Phase 4 or Phase 6.** It
freezes a contract before the consumers read it, which is what #926 Phase 1
asks for, and measurement is allowed to overturn a rule here.

**What was considered and rejected: six new element attributes.** Add
`mfa_requirement`, `credential_sharing` and the rest as columns on the schema.
It fails for the reason the current fields fail. A column holds one value per
element, read by its leading token, supported by nothing, and scoped to nobody.
The corpus's case 10 value — `sign-in exists for some readers; the mechanism,
its strength and how sessions are handled are not stated` — needs a scope and
three unknowns, and six more columns give it neither.

**What was considered and rejected: a stored conflict record and a stored
coverage record.** #926's data contract names both. Each is derivable from the
assertion rows, and a stored copy is a second reader of one rule that its own
test would agree with. Rule 1 folds coverage into an `unknown` row with a
reason, and rule 3 derives the conflicts.
