# 73. A finding can wait on some facets, and an answer can carry a detail

- **Status**: accepted
- **Date**: 2026-10-08
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F6)
- **Relates to**: [ADR 0050](0050-a-compound-question-kind-is-answered-in-facets.md),
  whose facets this lets a finding name, and
  [ADR 0047](0047-the-critic-asks-a-kind-of-question-about-an-element.md),
  whose question kinds the critic names

## Context

A finding that waits on a question kind referenced the whole kind. An answer
covered it only once every facet had a known answer. The audit of 7 October
2026 gave the case: a finding that needs only who acted stayed uncovered while
the log's protection was unknown, although the owner had answered who acts.
That is conservative, but it asks more of the owner than the finding needs,
and it understates what the answers resolve.

A closed answer also had no place for an exception. "Yes" to a capability
says the whole application has it, where the owner may know "yes, except the
public status endpoint".

## Decision

**A reference to a question kind can name the facets it needs**
(maintainer's decision of 2026-10-08: build offline now). `UnknownRef.facets`
lists them, from a closed set that the provider schema states. The critic's
prompt lists each kind's parts by ID and says to name them where an argument
rests on some parts only. A reference that names none of its kind's facets,
or only facets its kind does not have, waits on the whole question, so an
unknown facet name never loosens a finding.

**One rule reads what a finding needs.** `fact_answers.needed_facets` gives
the facets of one reference, `needs_of` merges the references of one finding
(a whole-question reference makes the whole question needed), and `covers`
says whether an answer gives every needed facet a known answer. The follow-up's
coverage (`questions.follow_up_needs`), a report's conditions
(`report_conditions.conditions` through `fact_status`) and the report page's
running count all read them. Each fact question carries `needs`: the findings
that wait on it only in part, with the facets each needs.

**An answer can carry a detail.** `FactAnswer.detail` is one line, at most
300 characters, in the submitter's own words. It follows the answer on its line
of the answers Source, so the analysis reads it as the submitter's words and a
capability's quoted evidence holds it. No code reads a fact out of it, so a
detail never changes which questions are asked. A changed detail counts as new
information for the follow-up. A later facet answer that sends no detail keeps
the earlier one. Both pages offer the detail beside a closed answer and beside
each row of a facet table.

## Consequences

The live effect of the prompt and schema change on the critic is unmeasured.
The critic may name facets too narrowly or not at all; where it names none,
coverage is as it was before this ADR.

A finding covered by its facets leaves the follow-up's waiting list, while the
question can still be asked for another finding that needs the whole kind.

Source-to-facet bindings (#1347) stay deferred by their own decision. This ADR
does not equate an application capability, an element attribute and a
reviewer's question; it narrows only which parts of one question a finding
needs.

## Evidence

`tests/test_facet_dependencies.py` and `TestFacetNeedsAndDetails` in
`tests/test_webapp_questions.py`.
