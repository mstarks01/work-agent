# 46. Every open fact is asked, and code writes the answer

- **Status**: accepted
- **Date**: 2026-09-27
- **Effort**: [#1225](https://github.com/mstarks01/work-agent/issues/1225),
  its first question type
- **Relates to**: [ADR 0043](0043-a-link-answer-is-closed-and-code-writes-it.md)
  and [ADR 0044](0044-a-resumed-job-starts-at-prepare.md)
- **Evidence**: `QA-2026-09-26-03-E6` in `evals/experiments/`

## Context

Most findings are `needs-info`: they rest on facts the sources never state,
and each verdict names those facts. #1225 proposed asking the submitter a
capped set of them. Measured with the corrected key (PR #1256), a cap of six
settles 53% of the conditional findings without the STRIDE lane closing and
24% with it. Asking every fact settles all of them, at about 15 questions a
report without the closing and 28 with it.

## Decision

**Every open fact is asked, and none is capped.** The questions are ordered
greedily: the next one is the fact the most still-open findings cite. Each
question states how many findings are settled once it and every question
before it is answered. The submitter answers from the top as far as they
choose. The findings of every framework count together, because one answer
settles a fact for every framework that cites it.

**A question is keyed by its open fact**, `UnknownRef.key`, and an answer sends
the key back. The resumed job analyses the checkpoint the question was asked
about (ADR 0044), so the key names the same fact when the answer arrives.

**Code writes every answer, and a model reads none.**

- An **attribute** answer is written onto the model the resumed job analyses,
  and the element's notes say the submitter gave it. A closed attribute takes
  one of its declared values; a zone takes one of the model's boundaries.
- An **assertion** answer replaces the open row with a stated row that keeps
  its subject, predicate and scope and quotes the answer's line of the answers
  Source. A term predicate takes one of its terms or `absent`.
- A **subject** answer has no structured home. It reaches the lanes as its line
  of the answers Source, and the answer settles nothing a rule reads.

An answer is checked against the checkpoint before the job is admitted, so a
wrong answer costs nothing.

**A fact answer needs no catalog.** A checkpoint may hold none, and a resumed
job's `prepare` then reads none (`SEEDED`). So a deployment with the assertion
pass off still takes attribute and subject answers.

## Consequences

The answer settles the fact even against the sources, following the
maintainer's decision of 2026-09-25 on #1225. A free-text subject answer is
the weakest kind: it changes what a lane reads, and no rule can check it.

Nothing here is measured against a live model. A paid pair on one case, with
and without answers, is what would show what the answers change in the report.
