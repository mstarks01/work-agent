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
`analysis_service.capabilities.CAPABILITIES` holds 118 of them, such as
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
expressions. The rationale is in this repository's words. 104 rows are
`always`, and 241 read at least one capability.

**A conjunction reads facts about the whole application.** `all(oauth-client,
authorization-code-flow)` holds when some component is an OAuth client and
some component uses the code flow, even if they are two components. So the
error goes one way: a requirement can stay applicable when it applies to no
component, and a requirement is never ruled out by a wrong pairing.

**The evaluator uses three-valued logic.** An unknown term is never false. So
a requirement is `not-applicable` only when a capability it needs is stated
absent, and the decision names that fact. An `unknown` decision names the
capabilities that would settle it.

**A row reads the subject of the requirement, never its control.** A password
rule applies because users sign in with a password. A rule that asks for a
second factor applies because the application authenticates, and not because
a second factor exists.

**The System Model carries the capabilities an answer states.** Its
`capabilities` list holds one entry for each statement: the capability,
`present` or `absent`, and the `source_excerpt` and `source_label` that say so.
The validity gate checks the quote as it checks an element's. Two statements
that disagree leave the capability `unknown`. The list is on the model, and not
in the assertion catalog, because the assertion pass is off by default and a
default job must still read its capabilities.

**Extraction states no capability.** The extraction and repair nodes fill
`EmittedSystemModel`, which has no `capabilities` field, and `extract.md` asks
for none. Asking extraction for capabilities cost element identity: against
no rule, five extraction sweeps each side measured aligned recall 3.5 sd lower
and endpoint recall 1.6 sd lower, while the attribute figures did not move. In
75 extractions the sources stated only three distinct correct absences, so the
answers were already where nearly every capability came from (#1291, comment of
2026-09-29).

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

**The report states each decision.** A framework block carries an
`applicability` list: one entry for each selected unit, with its state, the
facts that decided it and their first quotes, or the capabilities still
missing, and a reason. `ruled_out` reads the same entries, and the block's own
check refuses a unit that is `not-applicable` in one of `applicability` and
`scope` and not in the other. A block whose precondition refused the model
carries no entries, because its scope answers for every unit.

**The rule has its own benchmark.** `run.py rule-applicability` scores the
rule against labelled fixtures in `evals/applicability/`, one for each test
class #1291 lists, and simulates the early questions from a fixture's truth.
It reports false exclusions, false inclusions, unknown accuracy and how many
questions settle 50%, 75%, 90% and 100% of what the answers can settle. It
reads no claim, so it is apart from the conformance and disposition scores.

**A subject that every web application has is `always`.** TLS, third-party
components and error handling have one honest answer, so a question about them
tells nothing. A subject that a real application can lack is a capability,
even where a description rarely states it.

## Consequences

A job rules a requirement out only when an answer states the absence of its
subject. So every conditional requirement stays `unknown` until an early
question asks for the capability, even where a source states the absence. A
job with questions off keeps them `unknown`, and its lanes rule on them.

**A lane reads the rule, not its own presence test.** The scope line gives each
lane three lists from the rule: the units code ruled out, the units that apply,
and each open unit under the capability question that would settle it. A lane
may rule an open unit out on the fact that question asks about, and the fan-in
refuses an exclusion of a unit that applies. The lane skills name the model
fields that answer a question, and they state no presence test of their own.

The page asks every part in the same round as its parent, because a resumed
job does not pause again. So a "yes" to OAuth asks the role questions at once,
on the same page.

`reviewed_by` names who accepted a row or a capability, and is `None` until
the maintainer reads it. The maintainer reviewed every capability, every
conditional row and the `always` row V14.1.1. The other 103 `always` rows are
not reviewed.

The presence tests in `frameworks/asvs/rules.py` stay as leads for a lane. They
decide no applicability.

