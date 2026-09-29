# 51. A requirement applies by a rule over capabilities

- **Status**: accepted
- **Date**: 2026-09-29
- **Effort**: [#1291](https://github.com/mstarks01/work-agent/issues/1291)
- **Relates to**: [ADR 0027](0027-vocabulary-raises-a-lead-and-rules-nothing-out.md), whose
  rule that silence is not absence this keeps, and
  [ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md),
  whose pause the capability questions will use

## Context

ASVS 5.0.0 publishes 345 requirements and no applies-when field. The ASVS
package had presence tests that raise a lead for a lane, but no rule said
whether one requirement applies. So each lane agent decided applicability
again from prose, and two lanes could read one fact two ways. A report could
not say, for each requirement, "this applies", "this does not apply, for this
stated reason", or "the input does not say, and this is the fact we need".

## Decision

**A capability is a closed fact about the whole application.**
`analysis_service.capabilities.CAPABILITIES` holds 59 of them, such as
`browser-frontend`, `oauth-client` and `turn-server`. Each has a meaning, a
yes-or-no question and an optional parent. The table is framework-neutral: a
capability says what the application has, never what a framework asks of it.

**A capability is `present`, `absent` or `unknown`.** Silence is `unknown`. An
absent parent makes each child absent, and a present child makes its parent
present. Where a stated fact and its parent disagree, the stated fact stays as
it is.

**Each requirement has one applicability expression.**
`frameworks/asvs/applicability.json` holds a row for each of the 345
requirements: `always`, one capability, or `all` and `any` over smaller
expressions. The rationale is in this repository's words. 103 rows are
`always`, and 242 read at least one capability.

**The evaluator uses three-valued logic.** An unknown term is never false. So
a requirement is `not-applicable` only when a capability it needs is stated
absent, and the decision names that fact. An `unknown` decision names the
capabilities that would settle it.

**A row reads the subject of the requirement, never its control.** A password
rule applies because users sign in with a password. A rule that asks for a
second factor applies because the application authenticates, and not because
a second factor exists.

**The System Model carries the capabilities a source states.** Its
`capabilities` list holds one entry for each statement: the capability,
`present` or `absent`, and the `source_excerpt` and `source_label` that say so.
The validity gate checks the quote as it checks an element's. Extraction writes
`absent` only where a source says so, and writes no entry for a capability the
text does not mention. Two statements that disagree leave the capability
`unknown`. The list is on the model, and not in the assertion catalog, because
the assertion pass is off by default and a default job must still read its
capabilities.

**A stated absence rules a requirement out before its lane runs.** ASVS's
`ruled_out` hook evaluates each requirement of the lane's chapter. A
`not-applicable` becomes a scope entry whose reason names the absent capability
and its quote, and the lane agent is told not to rule on it.

**A paused job asks the unknown capabilities its rule needs.** Each
framework counts, for each unknown capability, how many of its units at the
job's options an answer could settle; an absent parent settles every unit that
reads a child, so a parent's count includes them. The capability questions come
first in the early list, a parent before its parts, and need no prior from
earlier runs. Each takes "yes", "no" or "I don't know". The pause page shows a
part only once its parent is "yes", and the answer check refuses a "yes" to a
part whose parent the same answers call "no".

**An answer writes the capability.** A "yes" or "no" replaces every statement
about that capability with one that quotes the answer's line of the answers
Source, so the resumed job's `ruled_out` reads it before any lane runs. "I
don't know" writes nothing. A capability the sources state takes no answer,
unless an earlier round answered it. An open fact that names a capability is
the fifth spelling of `UnknownRef`, and it stays off the critic's schema.

**A subject that every web application has is `always`.** TLS, third-party
components and error handling have one honest answer, so a question about them
tells nothing. A subject that a real application can lack is a capability,
even where a description rarely states it.

## Consequences

A job rules a requirement out only when a source states the absence of its
subject. Descriptions rarely state one, so most conditional requirements stay
`unknown` until an early question asks for the capability. A job with questions
off keeps them `unknown`, and its lanes rule on them.

The page asks every part in the same round as its parent, because a resumed
job does not pause again. So a "yes" to OAuth asks the role questions at once,
on the same page.

An agent drafted every row and every question. `reviewed_by` is `None` on each
until the maintainer reads it.

The presence tests in `frameworks/asvs/rules.py` stay as leads for a lane. They
decide no applicability.

The facts-first extraction route writes no capability, so its jobs leave every
capability `unknown`.
