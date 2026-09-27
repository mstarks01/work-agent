# 43. A link answer is closed, and code writes it

- **Status**: accepted
- **Date**: 2026-09-27
- **Effort**: [#1225](https://github.com/mstarks01/work-agent/issues/1225),
  its second question type
- **Relates to**:
  [ADR 0013](0013-asvs-rules-applicability-and-never-a-pass.md), under which
  a stated fact never makes a requirement pass, and [ADR 0042](0042-every-predicate-states-its-reader.md) on the
  rules that read principal facts
- **Evidence**: `QA-2026-09-26-03-E2` and `-E3` in `evals/experiments/`

## Context

A fact about a principal — "no MFA for shopper accounts", "the calling teams
hold the API key" — reaches a graph element only through a stated
`represented-by` row, because an identification this service guessed would
move every fact about the principal onto the wrong element. The sources almost
never state that row. Over the archive, 443 of 444 settled principal rows
reached no element, so no candidate rule and no scope check could place them.

A match by name cannot fix it: 2 of the 16 principals that shared a word with
exactly one element would have landed on the wrong one.

## Decision

**The report asks, and the submitter answers.** For each principal with a
settled fact and no link, the report asks which component it is. The questions
are derived from the report's catalog, never stored.

**The answer is closed.** It names one entity, process or store by its element
ID, or `none`. It arrives as a structured `links` field on the next
submission, never as prose, so no model has to read it.

**Code composes the answers into a Source of kind `answers`**, and writes each
answer as a stated `represented-by` row that quotes its line of that Source.
The gate then checks the quote as it checks any other, and the report shows
the submitter's words. A caller may not submit an `answers` Source itself, so
the quoted text is always the text code wrote. `none` writes the link as
`absent`, which settles the question without placing the principal.

**The answer settles the link.** It replaces any `represented-by` row the
`assert` node wrote for that principal, following the maintainer's decision of
2026-09-25 on #1225 that an answer to the service's own question settles the
fact.

**An answer matches a principal by name.** Subject IDs change between runs, and
the name is what the question showed, so both sides are folded for case,
plural and possessive. An answer that matches nothing, or names a component the
new model lacks, writes nothing and is kept as an issue.

**A deployment with no assertion catalog refuses `links`**, because nothing
there would read them.

## Consequences

Every node sees the answers Source, because every node reads one rendering of
the job's sources. `prompts/extract.md` tells extraction to take nothing from
it. Priced on the archive, a report asks 2.2 link questions on average and at
most 5, and the answers would place 351 of the 443 stranded rows. Corpus recall
barely moves, because no case carries answers; the value is that stated facts
about principals reach the rules that already read them.
