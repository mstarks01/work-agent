# 36. A settled assertion is evidence a lane may cite

- **Status**: accepted
- **Date**: 2026-09-16
- **Effort**: step 5 of
  [#961 — the extraction audit](https://github.com/mstarks01/work-agent/issues/961),
  which is Phase 4 of
  [#926 — implement a source-backed assertion layer](https://github.com/mstarks01/work-agent/issues/926).
- **Amends**: [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md)
  by widening its first test. [ADR 0034](0034-an-assertion-is-a-scoped-fact-with-a-support-span.md)
  left the consumer migration open, and this settles the first consumer.
- **Evidence**: the nine archived assertion runs under
  `evals/emissions/20260914T201030Z-assert-model-benchmark/`, and
  [`docs/research/assertion-readers.md`](../research/assertion-readers.md).

## Context

ADR 0034 gave the service an assertion catalog beside the **System Model** and
said nothing reads it until Phase 4. The catalog holds the facts the graph has
no field for: ten of the sixteen registered predicates project into nothing,
and a second factor stated absent, a credential stated shared and a grant
stated wide are among them. Every one of those facts reached a **Lane Agent**
as prose inside a control attribute, and reached no rule, no **Evidence
Reference** and no figure.

The reader inventory found the seam. Eleven of the fifteen production readers
route through `control_state`, and the one place a lane agent takes a fact
from is the **Evidence Catalog**. So the first consumer is the catalog, and
the question this ADR answers is which rows it may offer.

ADR 0010 holds the catalog to three tests: a derivation is a pure function of
the **Valid System Model**, it is framework-neutral, and its ID is built from
IDs the model already carries. An assertion fails the first test as written.
It is a function of the job's *catalog*, which the model does not hold.

**What the recorded runs settle.** In nine of nine archived runs on case 01,
across three models, the MFA absence sits on the principal `shopper accounts`.
Never on an interaction. A principal binds to no element, so a **Candidate**
rule that reads the catalog would have no element to place a lead on and would
fire on nothing. The fact reaches an agent through the catalog table or not at
all.

## Decision

**A settled assertion of a predicate the graph has no field for is an Evidence
Reference. Its ID is the row's own computed identity, used verbatim. The
report embeds the catalog it was cited from.**

Four rules.

**1. One rule says which rows settle, and every consumer reads it.**
`assertions.settled` is that reader: a row settles when it holds a value
rather than `unknown`, its basis is support of some kind, its assessment is
one the projection admits, and no other row disagrees with it at its subject,
predicate and scope. `legacy` is never support. A conflict settles nothing on
either side until an adjudication is recorded. An `unsupported` or
`unresolved` row is set aside. `assertions.answer` is the typed query a
consumer asks about one subject under one predicate, and it reads `settled`
rather than the rows, so no consumer decides for itself which rows count.

**2. Where a fact is cited is a property of the row, and of what reached the
graph.** A row the graph already carries — its projection wrote the catalog's
value into the attribute and the model holds it — reaches every reader through
that field, so a second entry for it would be a second reader of one fact, and
the two could disagree. Every other settled row is offered through the
catalog. `assertions.offered` is that reader, and `assertions.projected_attribute`
says whether a row reaches a field at all.

*Amended by #926's implementation audit.* This rule first read "a property of
its predicate", and offered only `UNPROJECTED` predicates. A predicate names a
field and a field belongs to one element type, so a legal
`authentication-mechanism` row on a **Process**, or a `credential-presented`
row on a principal, reached neither a projection nor the catalog. Over the 103
archived assertion records against their blessed models, 43 such rows reached
no reader, and four more — two pairs of compatible values on one flow's
`authentication` — were lost the same way. A scoped control is too, whenever
one is stated.

**3. The reference is the identity.** ADR 0034 rule 4 made an assertion's ID
a pure function of its four identifying parts, recomputed by every reader.
The catalog uses that ID as the reference, so the row and the entry can never
spell one fact two ways, and a report checks a cited row by recomputing the
identities of the catalog it embeds.

**4. A scoped row settles its scope and nothing wider.** A second factor
required for administrators is offered with its scope in the gloss and sits
beside an unscoped absence rather than suppressing it. An answer is per
subject and per predicate by construction, so a control on one interaction
never suppresses an unknown on another, and a mechanism never implies a second
factor, a grant or a rotation.

**ADR 0010's first test now reads: a pure function of the job's validated
artifacts, the Valid System Model and the resolved assertion catalog.** The
second and third tests hold unchanged: every lane of every framework receives
the same rows, and the reference is an ID the report already embeds.

## What this decision does not settle

- **Candidate rules over the catalog.** The recorded runs put the facts this
  layer exists for on subjects that bind to no element. A lead needs a place,
  so no rule reads the catalog yet. Placing a principal's fact needs either a
  ruling that aligns a principal to an entity, or a prompt that asks for the
  interaction subject. Both are #961 step 6's, measured before they are paid
  for.
- **Unknown rows as evidence.** An `unknown` assertion is a question, and the
  critic's `needs-info` routing names an element and an attribute. A question
  about a principal has no such pair, so unknown rows stay out of the catalog
  until the critic's vocabulary reaches them.
- **The projecting predicates.** The graph stays authoritative for them, and
  ADR 0034's migration of the projection is a later slice.
- **The lane prompt.** `prompts/analyze.md` describes the catalog's rows by
  kind, and the rendered gloss carries what an assertion row states. The prompt
  is edited when a paid run measures what the wording buys, because an edit
  re-baselines every blessed fingerprint.

## Consequences

**A stated absence is a row a claim can rest on.** "No MFA for shopper
accounts" is an entry every lane of every framework selects from, with the
subject, the value, the basis and the scope in the gloss. A claim resting on it
carries a ground the report checks on load.

**An inference reads as one.** A row this service inferred is settled and
offered, and its gloss says `inferred`, so a reader tells the service's
conclusion from the source's statement.

**Nothing the catalog does not settle can ground a claim.** An unknown, a
conflict, an unsupported row and a legacy row are outside the table, and a
report that cites one is refused.

**A job that runs no assertion pass is the job it was.** The catalog offers
the model's own rows and nothing else, and the report's `assertions` is
`None`.
