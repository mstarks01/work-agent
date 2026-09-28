# 50. A compound question kind is answered in facets

- **Status**: accepted
- **Date**: 2026-09-29
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Relates to**: [ADR 0046](0046-every-open-fact-is-asked-and-code-writes-the-answer.md),
  whose rule that code writes every answer this keeps, and
  [ADR 0047](0047-the-critic-asks-a-kind-of-question-about-an-element.md),
  whose kinds this answers

## Context

Nine question kinds asked who, what or how, and each one asked several things
at once. `capacity-limits` asked about rate, size, concurrency, quotas and
queue bounds together. So a submitter typed a paragraph for each element, and
an answer that named a rate limit said nothing about the others. On a typical
corpus case the kinds put 66 text boxes on the pause page.

## Decision

**Each compound kind is answered in facets.** A facet is one short question
about the element. It takes `yes`, `no`, `not applicable` or `unknown`. Eleven
kinds have two to four facets: the nine that asked who, what or how, and the
two yes/no kinds that asked two things (`provenance`, `content-validation`).
The maintainer approved the wording on 2026-09-29. The critic, the ranking and
the grouping still name the kind. Only the answer changes shape.

**The service writes the answer's line.** A submission sends a `facets` map in
place of `value`. `FactAnswer` checks each facet ID and value, and writes the
value from them in the kind's order, one clause per facet. So the line the
lanes read has one writer, and a bad facet is refused before admission. A kind
with facets takes no free text, except `unknown`.

**An answer is known when one facet says more than `unknown`.** An answer whose
facets are all `unknown` is the submitter's "I don't know": it covers no
finding.

**The pause page shows a kind as a table**, with a column for each facet, a
row for each element, and a first row that sets a whole column. A column that
holds for every element, such as "secrets in a secret store", takes one choice.

## Consequences

The pause page on a typical corpus case holds about 200 short lists and no
text box for a question kind, where it held 66 text boxes. A list is faster to
answer than a sentence, and the first row sets a column at once.

`reachability`'s two facets state exposure rather than protection: "yes" to
"Can the internet reach it?" is the risk. They are written as facts because
the reversed wording reads badly.

A facet adds no finding identity. A finding still waits on the kind, so an
answer covers the kind's findings once one facet is known. Whether a facet
should cover only the findings it bears on is not decided; it needs the
critic to name facets, which it does not.
