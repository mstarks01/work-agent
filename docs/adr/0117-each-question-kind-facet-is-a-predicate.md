# 117. Each question-kind facet is a predicate

- **Status**: proposed
- **Date**: 2026-10-31
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029)
- **Would supersede**: [#1347](https://github.com/mstarks01/work-agent/issues/1347),
  whose binding of a stated fact to its facet this makes general
- **Relates to**: [ADR 0047](0047-the-critic-asks-a-kind-of-question-about-an-element.md),
  whose question kinds this regroups;
  [ADR 0073](0073-a-finding-can-wait-on-some-facets-and-an-answer-can-carry-a-detail.md),
  whose facets this makes predicates;
  [ADR 0115](0115-an-early-field-question-ranks-by-the-rules-that-read-its-fact.md),
  whose relevance cannot read a question kind today

## Context

The service holds two vocabularies for one kind of fact.

- **Predicates** (23) are what the assertion pass reads from the sources, such
  as `transport-encryption` or `record-attribution`. A row is a structured
  fact with a subject, a value, a basis and a support span, and the lanes and
  the rules read it from the catalog.
- **Question-kind facets** (15 kinds, about 37 facets) are what only an owner
  answers, such as the rate limit of a process or whether it checks object
  ownership. An answer reaches the lanes only as its line of the answers
  source. No rule can read it, and no source can state it.

Only two pairs connect the vocabularies (QA-2026-09-26-03-E24):
`internet-exposure` to `reachability.internet`, and `record-attribution` to
`audit-evidence.records-actor`.

This costs three things:

- **The largest part of the pause cannot be settled from evidence.** On the
  web app example with both frameworks, question kinds take about 66 of 82
  choices. Capacity limits (27) and authorization checks (26) have no
  predicate, so a description that states them still gets asked.
- **The lanes use these answers** (QA-2026-10-31-02-E1: 34 of 51 drafts quoted
  an early kind answer), so the facts matter to the report. They are held only
  as text.
- **STRIDE coverage is uneven.** Denial of service has no predicate.
  Repudiation and elevation of privilege each lack one for a control they
  turn on: record protection, and per-request or ownership checks.

## Decision

**One vocabulary: each question-kind facet is a Predicate.** The registry
gains one row for each facet. The row holds the facet's question, its subject
type, and the closed values `yes`, `no` and `not applicable`. "I don't know"
writes no row, as an unknown writes nothing today. A **Question Kind** stays
as the group of predicates that a page asks together about one element: its
template, its `covers` text and its box.

**A row settles only a question at its own subject.** A facet about a
component is settled by a row about that component. A row about a flow does
not settle a question about the element for every input or output. That
mismatch is why four of E24's six pairs were unsound, and this rule makes it
a refusal in code, not a ruling for each pair. An existing predicate that
means the same thing at the same subject replaces its facet, not a second
row beside it.

**An owner's answer is a row.** An answer to a facet becomes an assertion row
with the owner's basis, quoting its line of the answers source, as an answer
to an open assertion row does today. The answers source keeps the line, so
the lanes read the answer in both forms.

**A predicate says whether the assertion pass extracts it.** Each new row
starts with extraction off. The assertion pass asks only for the predicates
that have it on. So phase 1 changes no model input.

**Coverage is checked.** A table gives the controls that each STRIDE lane
turns on, and names the predicate for each one. A test holds every lane to
at least one predicate and every predicate to a lane that reads it. The
ASVS rules read the catalog as STRIDE's do, so the same test covers each
ASVS lane.

## Phases

1. **Owner-only predicates, no model input change.** Add the facet rows with
   extraction off. Store answers as rows. Let the rules read them, so the
   early order (ADR 0115) and the follow-up can rank a question kind by the
   rules that read its facts. Retire E24's two pairs into the general subject
   rule. Offline evidence only.
2. **Extraction, one predicate group at a time.** Turn extraction on for the
   group with the most choices first: capacity limits, then authorization
   checks. For each group, measure the assertion pass's accuracy against
   signed facts on descriptions that state those controls, and the lanes'
   must-find coverage. Each group is a paid run, priced first and authorised
   by its own grant.

## Consequences

- A description or a configuration document that states a covered control
  settles its question once extraction is on for that group, and the page
  shows the fact with its source.
- The assertion pass's prompt and output grow with each group turned on. The
  pass cost about $0.024 for each job when measured (#926), and it grows.
- The registry's identity rules do not change: a new predicate adds IDs and
  changes none. The archive replay that `AGENTS.md` asks for runs anyway,
  because the catalog's shape changes.
- The gain depends on owners stating these controls up front. Only the owner
  walkthroughs (#1286) can show how often they do.

## Considered options

- **Add predicates beside the facets.** This needs a binding for each pair and
  holds one fact in two records with two readers, which drift. Rejected.
- **Keep two vocabularies and widen #1347's binding.** E24 shows that the
  existing predicates reach about 6 of 82 choices on the example. The large
  topics have nothing to bind to. Rejected.
- **Defer question kinds to the follow-up.** Refuted by QA-2026-10-31-02-E1:
  the lanes use early kind answers.
