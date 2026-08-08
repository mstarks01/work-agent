# 5. A verdict's shape is a re-askable problem, not a fatal one

- **Status**: accepted
- **Date**: 2026-08-08
- **Relates to**: [ADR 0004](0004-evidence-references.md), the same root cause
  answered a different way, and [ADR 0002](0002-finding-level-attribution.md),
  which named the constraint both inherit.

## Context

A `Verdict` carries three rules between its own fields: `needs-info` must name
the unknowns that caused it, anything else must not carry them, and anything but
`confirmed` must state a reason. None of the three is expressible in a JSON
schema a provider will reliably compile — the same constraint ADR 0002 recorded
for `Ground`.

They were enforced in a pydantic `@model_validator` on `Verdict`, and `Verdict`
rode inside `ThreatRulings`, the critic's `output_schema`. ADK validates a
node's output schema on the way into session state, so a verdict whose fields
disagreed with its status raised there, the node failed, and the run ended.

That is expensive in a way the other failures on this path are not. The critic
rules on every draft in the job in a single pass — the corpus mean is 18.7 — so
one badly-shaped verdict discards six lanes of drafting *and* the largest call
in the graph. And a bounded re-ask for malformed critic output already exists:
`review_issues` collects the problems, the router sends them to `recritic`, and
a second failure raises `CriticOutputError`. A verdict shape error could never
reach it, because the job was already dead one seam earlier.

Nothing justified the asymmetry. A dropped draft, an invented threat ID and a
duplicate ruling are all re-asked. A missing reason is not a graver fault than
any of those.

## Decision

**The critic emits `ProposedVerdict`; the report carries `Verdict`.**

`ProposedVerdict` holds the same three fields with no rule between them, so
nothing a critic writes there is a shape error and the node cannot fail on one.
Every per-field constraint is untouched — `status` is still a closed vocabulary,
`reason` still has a maximum length — because those a schema *can* carry.

`review_issues` gained `_verdict_shape_issues`, which asks the three rules and
returns them as problems like every other check at that seam. They implicate
their threat, so the re-ask is shown the draft: neither naming the unknown a
`needs-info` hangs on nor writing the reason a rejection owes its reader can be
done from an ID alone.

`Verdict` keeps its validator and subclasses `ProposedVerdict`.
`assemble_threats` promotes one to the other, after `review_issues` has passed
on exactly those rulings — so the validator now audits this service's own
construction rather than refereeing a model's, and a raise from it means the two
have drifted apart.

## Consequences

**One badly-shaped verdict costs a re-ask instead of a run.** A second one still
fails the job, and still should — but it arrives as `CriticOutputError` naming
the fault, not as a schema traceback out of a cancelled node.

**The report's invariant is unchanged.** Every `Verdict` in a report still
satisfies all three rules, by construction rather than by inheritance from what
a model happened to emit. `schema_version` does not move.

**`recritic` gained a fault class**, so its prompt gained a procedure step for
it and its token cap moved. That is the standing cost of this shape: the re-ask
must be able to name and fix everything `review_issues` can report, so the two
move together.

**What was considered and rejected: deriving `status` from the fields.** Reading
"the critic named unknowns" as "the critic meant `needs-info`" is mechanical, and
is exactly the inference ADR 0004 refuses — it repairs an inconsistent output by
guessing which half was intended. The critic is asked again instead.

**Also rejected: restricting `related_unknowns` to the evidence catalog.** It
would make the reference a selection, as ADR 0004 did for grounds, and it is
wrong here for a reason already recorded on `_unresolved_unknown_ref_issues`: a
`needs-info` is legitimate against a *stated but vague* value — "some
encryption" in `encryption_at_rest` — which is not in the catalog, because the
catalog holds only attributes literally equal to `unknown`. The check stays at
existence, where it was deliberately put.
