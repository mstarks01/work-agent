# 47. The critic asks a kind of question about an element

- **Status**: accepted; the table's wording is not yet reviewed
- **Date**: 2026-09-27
- **Effort**: [#1225](https://github.com/mstarks01/work-agent/issues/1225)
- **Relates to**: [ADR 0046](0046-every-open-fact-is-asked-and-code-writes-the-answer.md),
  whose critic section this makes consistent
- **Evidence**: `QA-2026-09-26-03-E7` and `-E8` in `evals/experiments/`

## Context

Where an open fact has no place in the model, the critic wrote it as a
free-text `subject`. Those questions were the least consistent part of a
report's questions: 77 of 79 on one sweep were distinct texts, and two critic
samples of the same drafts cited 16 and 25 of them. Their kinds were few and
stopped growing: 11 kinds on 13 cases, none new after the ninth, and a table
read off the first six cases covered 40 of the 46 questions after them.

## Decision

**A closed table of question kinds, each about one element.**
`QUESTION_KINDS` holds 15 kinds: the 11 read off the critic's questions, and
four more read off the facts the corpus's claims rest on (security
configuration, failure handling, update process, data exposure). Each kind is
one question with one slot for the element.

**The critic names such a fact as a kind and an element.** `UnknownRef`
gains `question`, and the provider schema lists the kinds as an enum, so a
constrained critic cannot invent one. The kind is part of the fact's key. The
review seam sends back a kind the table lacks, or an element the model lacks.
The critic prompt asks for a kind where one fits, and for a `subject` only
where none does.

**The fallback is kept and counted.** A `subject` is still legal, and a report
counts how many of these facts were typed and how many fell back
(`question_fallback`). The questions route returns the count, and `run.py
score` prints it. A low, steady share says the table is enough; the fallback
texts name the kinds it lacks.

**Nobody has reviewed the wording.** An agent drafted every row, and
`reviewed_by` stays `None` on each until the maintainer reads it.

## Consequences

A kind is less specific than the critic's own sentence, which often listed the
limits it meant. The critic's reason still carries its words; the question is
the kind.

The critic prompt changed, so its instruction digest moved, and nothing here
is measured against a live model. What a paid run should read: the fallback
rate on the tuned cases, then on the holdout cases, which no agent read while
the table was drafted. Every archived report reads 100%, because it predates
the table.

The table is the service's and neutral. ASVS's critic text still sends a
question about a document to `subject`, and no kind covers that, so ASVS keeps
more free text by design.
