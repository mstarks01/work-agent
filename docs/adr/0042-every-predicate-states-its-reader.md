# 42. Every predicate states its reader

- **Status**: accepted
- **Date**: 2026-09-26
- **Effort**: [#1242](https://github.com/mstarks01/work-agent/issues/1242)
- **Relates to**:
  [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md), which keeps
  the predicate registry the service's, and
  [ADR 0011](0011-package-text-follows-its-retrieval-key.md), which added the
  ninth package member by the same reasoning as this tenth one
- **Evidence**: the signed corpus facts and sweep B, in the issue

## Context

The assertion registry holds 21 predicates. Seven copy into a System Model
field, and every graph reader reads them there. The other 14 have no graph
field. With the assertion pass on, the **Evidence Catalog** offers each settled
row of those 14 to the lanes, and a lane may cite it. Only one **Candidate**
rule read a row, so for 13 predicates nothing turned a stated fact into a lead.

Nothing in the tree said which reader each predicate had. A predicate added to
the registry reached no rule, and no test failed.

## Decision

**Each Framework Package states, for every predicate with no graph field, the
candidate rule that reads it or why no rule does.** The member is
`predicate_readers`, a table keyed by predicate. A value is a rule ID the
package declares, or a `NoRule` with a reason. The reason is a property of the
framework or of the predicate, never a name, so it answers for a package or a
predicate added later.

The package gate refuses a table that omits a predicate, names a projected or
unregistered one, names a rule the package does not declare, or gives an empty
reason. A new predicate therefore stops every package from starting until each
one answers for it.

**A rule fires only on a stated value.** An `unknown` row is a question, and
the lane already reads it as one. A rule that reads a credential places its
lead on the flows that present the credential: through `credential-presented`
on the flow, or on a principal whose `represented-by` row names the flow's
source.

**A rule reads structure, never prose.** A predicate whose value is free text
gets no rule. So does one whose object the graph often cannot name, such as the
resource of an `authorization-grant`.

## Consequences

STRIDE gains five rules: a stated absence of origin, signature or destination
verification on a flow, a credential that stays valid, and a credential more
than one principal holds. ASVS gains none, because its rules select
requirements by what the graph shows is present, and a stated fact bears on how
a lane rules a requirement.

With `ANALYSIS_ASSERTIONS` off, no catalog exists and no new rule fires, so a
default install runs as before. The corpus carries no catalog, so the five
rules fire on no blessed case, and `tests/test_predicate_readers.py` fires each
one directly.
