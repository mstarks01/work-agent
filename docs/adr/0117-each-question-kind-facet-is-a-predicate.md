# 117. Each question-kind facet is a predicate

- **Status**: accepted
- **Date**: 2026-11-12
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029)
- **Would supersede**: [#1347](https://github.com/mstarks01/work-agent/issues/1347),
  whose binding of a stated fact to its facet this makes general
- **Relates to**: [ADR 0047](0047-the-critic-asks-a-kind-of-question-about-an-element.md),
  whose question kinds this regroups;
  [ADR 0073](0073-a-finding-can-wait-on-some-facets-and-an-answer-can-carry-a-detail.md),
  whose facets this makes predicates;
  [ADR 0115](0115-an-early-field-question-ranks-by-the-rules-that-read-its-fact.md),
  whose relevance cannot read a question kind today;
  [ADR 0123](0123-a-control-writes-facts-and-names-the-units-the-critic-judges.md)
  and [ADR 0125](0125-a-control-fills-only-an-open-fact.md), whose Control
  writes a question kind's answer and lays it on a rung

## Context

The service holds two vocabularies for one kind of fact.

- **Predicates** (23) are what the assertion pass reads from the sources, such
  as `transport-encryption` or `record-attribution`. A row is a structured
  fact with a subject, a value, a basis and a support span, and the lanes and
  the rules read it from the catalog.
- **Question kinds** (15) are what only an owner answers, or a Control states
  for the owner. Eleven kinds are answered in facets, 30 facets in all, such
  as the rate limit of a process or whether it checks object ownership. Four
  kinds are answered yes or no. An answer reaches the lanes only as its line
  of the answers Source. No rule can read it, and no source can state it.

Only two pairs connect the vocabularies (QA-2026-09-26-03-E24):
`internet-exposure` to `reachability.internet`, and `record-attribution` to
`audit-evidence.records-actor`.

A Control (ADR 0123) lets an owner state a protection once for a scope, such
as a rate limit on every process in a zone. The job writes it as an answer,
and a fact a Control writes is not asked (ADR 0125). So for an owner with
Controls, the pause asks less. The Control does not change what the answer
is: it is still a line of the answers Source that no rule reads.

This costs three things:

- **The largest part of the pause cannot be settled from the sources.** On
  the web app example with both frameworks, question kinds take about 66 of
  82 choices. Capacity limits (27) and authorization checks (26) have no
  predicate, so a description that states them still gets asked. A Control
  removes the question only for an owner who wrote one.
- **The lanes use these answers** (QA-2026-10-31-02-E1: 34 of 51 drafts quoted
  an early kind answer), so the facts matter to the report. They are held only
  as text, and the early order (ADR 0115) cannot rank a question kind by the
  rules that read it.
- **STRIDE coverage is uneven.** Denial of service has no predicate.
  Repudiation and elevation of privilege each lack one for a control they
  turn on: record protection, and per-request or ownership checks.

## Decision

**One vocabulary: each facet, and each yes-no kind, is a Predicate.** The
registry gains one row for each of the 30 facets and the 4 yes-no kinds. A
facet's row holds its question, its subject type and the closed values `yes`,
`no` and `not applicable`. A yes-no kind's row holds `yes` and `no`, the
answers its page offers. The `statement` and `protects` that a Control reads
move onto the row. "I don't know" writes no row, as an unknown writes nothing
today. A **Question Kind** stays as the group of predicates that a page asks
together about one element: its template, its `covers` text and its box. A
kind that is answered in free text stays outside, because a predicate holds a
closed value; no kind is answered that way today. A free-text subject stays a
line of the answers Source.

**A row settles only a question at its own subject.** A facet about a
component is settled by a row about that component. A row about a flow does
not settle a question about the element for every input or output. That
mismatch is why four of E24's six pairs were unsound, and this rule makes it
a refusal in code, not a ruling for each pair. An existing predicate that
means the same thing at the same subject replaces its facet, not a second
row beside it.

**An answer is a row.** An owner's answer to a facet becomes an assertion row
with the basis `stated` and a span on its line of the answers Source, as an
answer to an open assertion row does today. The answers Source keeps the
line, so the lanes read the answer in both forms.

**A Control's answer is a row on its rung.** A fact a Control writes becomes a
row through the same writer, with a span on the Control's line of the answers
Source. The rung of ADR 0125 holds without change: a row the sources stated
stands, an own answer lies over a Control, and a Control lies over a carried
answer. `fact_origin` stays the one reader of a fact's rung, so a row adds no
origin field.

**A predicate says whether the assertion pass extracts it.** Each new row
starts with extraction off. The assertion pass asks only for the predicates
that have it on. So phase 1 changes no model input.

**Coverage is checked.** A table gives the controls that each STRIDE lane
turns on, and names the predicate for each one. A test holds every lane to
at least one predicate and every predicate to a lane that reads it. The
ASVS rules read the catalog as STRIDE's do, so the same test covers each
ASVS lane.

## Phases

1. **Owner-only predicates, no model input change.** Add the rows with
   extraction off. Store each owner and Control answer as a row. Let the rules
   read them, so the early order (ADR 0115) and the follow-up can rank a
   question kind by the rules that read its facts. Retire E24's two pairs into
   the general subject rule. Offline evidence only.
2. **Extraction, one predicate group at a time.** Turn extraction on for the
   group with the most choices first: capacity limits, then authorization
   checks. For each group, measure the assertion pass's accuracy against
   signed facts on descriptions that state those controls, and the lanes'
   must-find coverage. Each group is a paid run, priced first and authorised
   by its own grant.

## Consequences

- Phase 1 alone gives the rules and the early order a structured fact for
  each answer, and gives each STRIDE lane a predicate. It does not ask fewer
  questions.
- The ASVS rules name the predicates that inform them too, but the ASVS prior
  names no question kind. So the ASVS rows do not move the early order, and
  only the tests in `tests/test_rule_informed_by.py` read them. They move it when the ASVS prior names a
  kind.
- A description or a configuration document that states a covered control
  settles its question once extraction is on for that group, and the page
  shows the fact with its source. Until then, only a Control removes the
  question.
- The assertion pass's prompt and output grow with each group turned on. The
  pass cost about $0.024 for each job when measured (#926), and it grows.
- The registry's identity rules do not change: a new predicate adds IDs and
  changes none. The archive replay that `AGENTS.md` asks for runs anyway,
  because the catalog's shape changes.
- The gain of phase 2 depends on owners stating these controls up front in a
  source. Only the owner walkthroughs (#1286) can show how often they do.

## Considered options

- **Add predicates beside the facets.** This needs a binding for each pair and
  holds one fact in two records with two readers, which drift. Rejected.
- **Keep two vocabularies and widen #1347's binding.** E24 shows that the
  existing predicates reach about 6 of 82 choices on the example. The large
  topics have nothing to bind to. Rejected.
- **Defer question kinds to the follow-up.** Refuted by QA-2026-10-31-02-E1:
  the lanes use early kind answers.
- **Let Controls carry the gain alone.** A Control removes a question only for
  an owner who wrote one, and its answer stays a line that no rule reads.
  Rejected.
- **A new basis for an owner's answer.** An answer to an open assertion row
  is `stated` today, with a span on its line, and `fact_origin` already tells
  an answer from a Control or a carried answer. A second basis would be a
  second reader of the rung. Rejected.
