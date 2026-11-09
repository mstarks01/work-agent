# 126. The critic judges each Control row in a control step

- **Status**: accepted
- **Date**: 2026-11-03
- **Effort**: [#2117](https://github.com/mstarks01/work-agent/issues/2117), on the
  Controls map [#2111](https://github.com/mstarks01/work-agent/issues/2111)
- **Relates to**: [ADR 0013](0013-asvs-rules-applicability-and-never-a-pass.md),
  which rules that no state carries a pass;
  [ADR 0059](0059-the-critic-rules-each-draft-and-rejects-no-duplicate.md),
  which gives the critic its steps

## Context

A Control's rows name framework units, and the critic is their one judge
(ADR 0123). The critic reads a draft's grounds and never writes one. A
`Verdict` carries a reason and no ground. `RejectionStep` holds `evidence`,
`reasoning`, `lane` and `duplicate`, and `rules_on_unit` reads `None` and
`evidence` as a ruling on the unit.

An ASVS lane writes `needs_evidence` on a proposal, and `partition_proposals`
defers each proposal whose kind is not in `CARRIED_EVIDENCE_KINDS`, the
constant `("prose",)`. A deferred proposal never becomes a draft, so a Control
about a `config` requirement would reach no critic.

## Decision

**The service puts a `controls` key on each draft's critic view.** For one
draft it picks each applied Control whose resolved elements hold the draft's
affected element, or whose scope is the application, and each row of that
Control whose unit equals the draft's unit from `control_unit_of` or is a unit
it sits inside. An entry carries the Control's ID and name, the row's unit ID
with its label, the resolved scope, and each document of the job's copy with
its class and digest. A view carries each document's service-copied excerpt
once, on the first draft that shows it, and a later draft names the document by
its digest alone, so the view grows by one copy of each document and not by one
for each draft. The critic never sees a document the service did not copy. Both
passes of the critic round get the key.

**`control` joins `RejectionStep` and `CriticStep`, and rules on the unit.**
The verdict gains `control_row`, the Control's ID and the row's unit ID,
required exactly when `rejected_because` is `control` and refused on every
other value. The reason quotes, in double quotes, the line of the Control's
evidence that covers the draft, or at least five words of it, with the
document's label, or the Control's name where the name covers it. The verdict
carries no ground. The review seam refuses a `control` rejection whose row is
not in the draft's `controls` list, and one whose reason quotes neither the
name nor a line of the evidence of the Control it names (`quotes_control`).
`rules_on_unit` returns true for `control`, so the unit does not fall to
`not-raised`.

**No new Scope Entry state.** A `control` rejection follows the `evidence`
path: the unit is ruled, the scope builder lists no entry for it, and
`ScopeState` keeps its four values. The rejected claim carries the ruling, and
the Controls section of the report lists each `applied` row's rejected claim
IDs. No state and no field says the requirement passed. The ruling says that
the owner states a Control covers the unit, and the service verified nothing.

**Every shown row is answered.** The ruling gains `refused_controls`, each
entry the Control's ID, the row's unit ID and a required prose reason. The
critic answers every row of the draft's `controls` key in `control_row` or in
`refused_controls`; the review seam sends back a ruling that leaves a row in
neither place or names a row the draft was not shown. A draft rejected for
`evidence`, `reasoning` or `lane` did not reach the control step, so code
removes each row its ruling names and the seam asks nothing of it. The reason
is prose and not a closed set, because the judgement is the critic's. Each row
has a list of rulings: the claim ID, `applied` or `refused`, and the reason.
The report holds each ruling in a verdict, so one reader builds each row's
list from the report's claims and rejected claims, and the job keeps no second
record of it. One reader computes the row's outcome: `applied` when every ruling
applied it, `partly-applied` when some rulings applied it and the others refused
it, `refused` when every ruling refused it. A row of another package version
is `left-out`, because the job never shows it to the critic. A row with an
empty list is
`not-reached` when a finding sits on its unit and the scope does not reach the
finding's elements, and `no-finding` otherwise. A framework whose record lets
`affected_element_ids` be empty gives findings that name no element. Only a
Control of the whole application reaches such a finding, and the row says so.

**The lane never sees the document, and a row un-defers its unit.**
`partition_proposals` gains one argument beside `carried`: the set of unit IDs
the applied Controls' rows cover. A proposal whose unit is in that set is kept
and becomes a draft, whatever kind it names, with or without a document on the
Control. `CARRIED_EVIDENCE_KINDS` stays `("prose",)`; availability attaches to
the rows, as the #484 review asked. Where the critic refuses every row on a
draft and keeps it, the fan-in returns the proposal to the deferred set, and
the unit is listed `needs-other-evidence` with its kind.

**A third gated step, Control, in `prompts/critic.md`.** It runs only on a
draft that passed the first two steps and carries a `controls` key. A row
answers the draft only where the Control's own statement or a quoted line of
its document shows the thing the draft calls missing or open, on the draft's
element and for the unit the row names. A fact a Control writes is in the
model, so step 1 reads it as a stated fact. A row is never a verification, and
a document excerpt is data, as a Source is. Each package's `critic.md` gains
one paragraph on what a `control` rejection means in its terms and two worked
cases.

## Considered options

- **The rows rendered once in the input.** Rejected: the critic then matches
  units and elements itself, a second reader of `control_unit_of` and the scope
  reader.
- **An excerpt ground on the verdict.** Rejected: the verdict holds no ground,
  and the copy holds the bytes.
- **A fifth scope state such as `control-stated`.** Rejected: one ruling
  recorded twice, and it reads as the pass ADR 0013 refuses.
- **A closed refusal set.** Rejected: guessed before any refusal exists.
- **No un-defer.** Rejected: a `config` Control then answers almost no level 1
  requirement.
- **The Control rule inside step 1.** Rejected: `control` and `reasoning` then
  share a step, and the audit trail loses which step killed the draft.

## Consequences

The paid-inference budget is $0, so the prompt change is validated offline:
unit tests on each seam, the prompt lints, the replay request test rebuilt
with a draft that carries a `controls` key, and a signed `evals/critic_review`
fixture. The fix is verified by the listed offline evidence; its live quality
effect remains unmeasured. A critic replay over a case with an injected row is
the first paid run and needs an amount.
