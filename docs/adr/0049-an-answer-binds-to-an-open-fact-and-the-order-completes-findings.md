# 49. An answer binds to an open fact, and the order completes findings

- **Status**: accepted
- **Date**: 2026-09-28
- **Effort**: [#1289](https://github.com/mstarks01/work-agent/issues/1289)
- **Relates to**: [ADR 0046](0046-every-open-fact-is-asked-and-code-writes-the-answer.md),
  whose order and count this changes, and
  [ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md),
  whose eligibility rule this keeps
- **Evidence**: `QA-2026-09-26-03-E16` and `-E17` in `evals/experiments/`

## Context

The questions audit on #1289 left four questions open. Each one needed a
decision from the maintainer. E17 measured each one offline before the
maintainer chose.

## Decision

**The prior still decides which fields an early question asks.** A field
that the prior does not name for an element's type is not asked early, even
where the model leaves it open. When E17 made those fields eligible, the early
list grew by about 18%, and the list covered one more finding in 337. A
framework whose claims depend on requirements can make a fact eligible through
its own dependencies (#1291), and not through this prior.

**An answer binds to an open fact, or to a fact that an earlier round
answered.** One rule, `questions.open_attribute`, decides when an attribute is
open: it is unverified, or it is a zone that the service inferred. The early
list, the report list and the answer check all read that rule, and all read
it of `questions.prepared_model`: the model with the catalog applied, as the
lanes read it. A paused checkpoint holds the model before `prepare` applies the
catalog, so without this a fact the catalog states still read as open at the
pause. An answer to an
attribute that the sources state is refused before admission. So a
submission cannot overwrite a stated fact. The report list does not ask about
a stated attribute, even where the critic names it. In the archive, 6% of the
attribute questions came from the critic section and named a stated attribute.

**`unknown` is the answer that says the submitter does not know.** Every fact
takes it. Code writes nothing for it: an attribute keeps its value, an open
assertion row stays open, and the fact covers no finding. The analysis reads
it as a line of the answers Source. An `unknown` answer to a fact that an
earlier round settled reopens the fact. A job waiting at its pause takes it,
so a submitter can take back an answer they guessed, and every later round is
read off the model without the old answer (#1289). A report's follow-up
refuses it.

**A rejected draft ranks the questions and is not counted.** Its facts still
rank the evidence section, which keeps ADR 0046's stable order. No answer
brings a rejected draft into the report, so `cited_by`, `covered_so_far` and
`findings` leave it out.

**The next question is the one that completes the most findings.** Where no
single question completes a finding, the next questions are the facts of the
finding with the fewest left. This replaces "the fact the most open findings
cite". At six questions, it covers 119 of 148 findings on the grounds, where
the earlier order covered 111. On the critic's facts it covers 81 where the
earlier order covered 58 to 61. The list stays without a cap.

## Consequences

The field `covered_so_far` keeps its meaning, but its values rise at each
depth. The early list keeps its prior order, because a paused job has no
findings to complete.

A client that sends an answer to a stated attribute now gets `400`. The pages
send only the keys they list, so they are not affected.

The bounded question plan with more than one round before the analysis is not
decided. No measurement yet shows what an early answer changes in the lanes'
findings. A paid run with signed answers at the pause is the measurement for
that.
