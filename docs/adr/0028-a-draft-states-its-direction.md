# 28. A draft states its direction

- **Status**: accepted
- **Date**: 2026-09-09
- **Effort**: [#713 — a typed applicability state on the proposal](https://github.com/mstarks01/work-agent/issues/713),
  with the three contract contradictions recorded on it from the audit on
  [#732](https://github.com/mstarks01/work-agent/issues/732)
- **Builds on**: [ADR 0027](0027-vocabulary-raises-a-lead-and-rules-nothing-out.md),
  which named a typed fact as the next candidate after the word tables went;
  [ADR 0013](0013-asvs-rules-applicability-and-never-a-pass.md), which this
  keeps whole

## Context

An ASVS lane drafts a ruling on each requirement in its chapter. Whether that
draft argues a gap, asks a question or rules the requirement out lives in its
prose until the **Critic** reads it. `needs_evidence` cannot carry it, because
its empty value means the agent ruled either way, and `absent_elements` means a
term matched no text field, which is absence from the description and not from
the system.

Per case 01 run, the lanes draft about 138 rulings and the critic rejects 86 to
94 of them, 93 of 94 for `reasoning`. Of the rejected drafts, 37 to 62 rest on
absent-element grounds alone. Zero kept claims rest on absent-element grounds
alone in five of six runs. The critic and its re-ask are a third of the run's
cost and its two slowest nodes. Code cannot filter those drafts today, because
nothing on the draft says which of the three things it is.

Three sentences in the prompt bundle disagree about the same fact, and each pair
turns on what silence means:

1. `frameworks/asvs/output.md` step 8 tells the lane: the requirement applies
   and the input does not show it satisfied, so write the claim plainly.
   `frameworks/asvs/critic.md` tells the critic: a control's absence read from
   silence is rejected for `reasoning`. The lane is told to write what the
   critic is told to reject.
2. `prompts/analyze.md` says the agent does not rule and a verdict is the
   critic's. `frameworks/asvs/output.md` says rule each requirement, and
   `RequirementProposal` says an empty `needs_evidence` means the agent ruled.
   One word carries two meanings in the bundle one agent reads.
3. `prompts/critic.md` says a missing flow is a fact about the text and not the
   deployed system, and confirms nothing on it. The `absent_elements` bullet in
   `prompts/analyze.md` and the file-handling exemplars rule a requirement
   inapplicable because no element names a file path. One reader calls the
   graph's silence weak; the other lets it settle a ruling.

## Decision

**A proposal that rules on a catalog unit carries a required, closed
`direction` field, and code reads it before the critic does.** Three values:

| `direction` | what the lane asserts | what it must rest on | who settles it |
|---|---|---|---|
| `gap` | the input states a fact that fails the requirement | a quote or a stated attribute ground; never `absent_elements` alone | the critic confirms or rejects |
| `question` | the input does not settle the requirement | `needs_evidence` names what would | code, as `needs-info`, on the #439 path where an unknown ground is cited; otherwise the critic |
| `excluded` | the requirement has no subject in this system | `absent_elements`, or a stated fact that rules it out | the critic, by an `evidence` rejection, which is the one rejection that rules on the unit |

Three rules follow, and each closes one contradiction above.

**Silence never confirms.** A `gap` resting on absent-element grounds alone is
a **Dropped Claim** at the fan-in, with the reason that a gap needs a stated
fact, and the critic never reads it. The lane contract's first case changes
from "the input does not show it satisfied" to "the input states a fact that
fails it". What the input does not show is a `question`.

**The lane directs, the critic rules.** `direction` is the lane's word and
`verdict` is the critic's, and the prompts use one word for each act. The
sentence in `prompts/analyze.md` stands. The ASVS contract's step 8 becomes
"state each requirement's direction", and the docstring on
`RequirementProposal` no longer says an empty `needs_evidence` means the agent
ruled.

**Silence may exclude, and the record says so.** An `excluded` draft grounded in
`absent_elements` is what ADR 0027 already allows: a judgement that the
description names no such thing. It stays, and the rejected claim the report
keeps carries the absent-element ground with the term that matched nothing, so
a reader sees that the ruling reads the description's silence and not the
deployed system's. The draft carries its direction to the critic, and the
critic's text says an `excluded` on a verified absent element is judged on
whether the term is the requirement's subject and rejected for `evidence`
where it is. The one measured run without that sentence rejected 96 of 99
exclusions for `reasoning`. `prompts/critic.md`'s
rule that a graph's shape confirms no *threat* is unchanged, because it is a
rule about `gap` and not about `excluded`.

**The #439 path keeps its answer.** A draft citing an unknown-attribute ground
is ruled `needs-info` in code whatever its `direction`, because an unresolved
premise blocks confirmation and blocks exclusion alike. A `gap` with an unknown
ground reads as a `question`; the field does not override the ground.

**Which package declares it is a property, not a name.** The field belongs to a
package whose claims name a catalog unit and can rule it out. A package whose
claims compose an identity from an action and a place drafts no exclusion and
asks its questions through unknown grounds, so its proposal does not carry the
field, exactly as it does not carry `needs_evidence`. The neutral base stays
neutral, and `tests/test_framework_neutrality.py` holds the pairing: a record
that overrides `ruled_out` or names a unit declares `direction`; one that does
neither does not.

## Consequences

**The critic reads fewer drafts, and the ones it reads argue from a stated
fact.** The 37 to 62 absent-only rejected drafts per case 01 run never reach
it. Its `reasoning` rejections fall to the drafts that misread a stated fact,
which is the judgement the prompt asks of it.

**Every ASVS exemplar changes.** The 17 lane exemplar files gain the field, in
our own words under the package's content licence, and
`tests/test_prompt_lints.py` parses every block as the proposal, so a block
without it fails. The corpus reference
dispositions map onto the three values without an edit: `gap-from-prose` is a
`gap`, every `needs-*` is a `question`, and `not-applicable` is `excluded`.

**The proposal schema version moves.** A required field with no default is the
mechanism, as `needs_evidence` already is: a structured-output model omits a
field that carries a default, and a prompt alone does not oblige it to answer.

**The three sentences are rewritten together**, against this table, so the
contract has one reader for what silence means. That rewrite is the pull
request that accepts this record.

**What it does not do.** It records no unverified support. A stated control
that nothing verifies still produces no claim, which is ADR 0013's rule that
this framework never passes anything. Whether such a statement deserves a
recorded state of its own is a separate decision, and the audit's finding 14
holds it.

## Alternatives considered

**A word filter on grounds.** Rejected on #659: `absent-element` is absence
from the description, so the filter would drop questions and exclusions with
the gaps.

**Two ordered questions to the lane** (#655). Superseded by this record: the
field carries the same answer in one call, and the schema makes it required
where a second question makes it optional.

**Four values, with an `asserted` state for stated support.** Deferred, per the
last consequence above, because it reopens ADR 0013.

## Measurement before acceptance

Five runs of case 01 each way, reading the disposition numbers, the false
not-applicable rate, the critic's rejection count and the cost. The offline
half is decidable first: every exemplar's `direction` against its reference's
disposition, through the exemplar delta instrument, with no run. The run
confirms rather than discovers, and it waits on spend approval.
