# 19. A fault in one entry of one claim never costs the job

- **Status**: accepted
- **Date**: 2026-08-27
- **Amends**: [ADR 0017](0017-a-groundless-claim-costs-its-entry.md), whose
  `GroundlessClaim` becomes `DroppedClaim` and gains every remaining reason;
  [ADR 0009](0009-a-bad-reference-costs-its-entry.md) and
  [ADR 0002](0002-finding-level-attribution.md), whose fatal cases are gone.

## Context

ADR 0017 dropped and marked a claim that lost every ground. Four faults of the
same shape still failed the whole job: one agent's one entry, at a seam with no
re-ask, discarding every lane's work.

1. A claim naming an element the model does not contain in
   `affected_element_ids`. The prompt warned that one "kills the whole lane";
   it killed every lane.
2. A lane's emission failing the proposal schema on one item — a verb outside
   the closed set, a severity value the enum does not hold, neither a reference
   nor a quote. The node validates the whole batch, so one item failed the node.
3. A quote naming a source label the job does not carry.
4. Two drafts under one ID.

## Decision

**Each costs its entry.** One mark, `DroppedClaim`, records every claim the
service removed, with a `reason` in the agent's own words. A per-entry loss that
leaves the claim standing gets a per-entry mark: `unresolved_references` for an
element ID dropped from `affected_element_ids`, the structural twin of
`unresolved_mentions`.

**A batch validates by salvaging.** The node validates its emission against
`ProposalBatch` before anything else runs, so the batch itself validates each
item alone: the ones that pass fill `claims`, the ones that fail fill
`invalid`, and `invalid` is kept out of the JSON schema so the provider is still
asked for the strict shape. The fan-in turns `invalid` into `DroppedClaim`
marks, composing the ID from the package's rule where the key is readable.
`InvalidProposal` names no package's key field: it carries the item's scalar
fields, and the resolver reads whichever its package keys on.

**A source label naming nothing is an unfindable quote.** It is the agent's
field, so it is marked by the quote check with a reason that says which fault
it was, and the claim is dropped only if it stood on that quote alone.

**The first draft under an ID wins.** The lane order is the package's own
declared order, so the choice is deterministic.

**What still fails a job** is a fault no single entry can cause: a lane that
wrote nothing, a critic whose re-ask did not reconcile, and a catalogued ground
the service itself built wrongly.

## Consequences

**The eval sweep's `dropped_rate` now covers every reason**, and the harness
no longer has a failure kind per agent fault: `classify_failure` keeps `other`
for the faults that remain.

**The viewer lists dropped claims under the block** with the reason, and shows
a dropped element reference on the claim's card beside a dropped prose mention.

**The prompt's warnings are corrected**: an invented element ID and an uncited
claim are described as dropped, not fatal.

**Framework parity.** Every path here is the shared fan-in or the shared batch
model; no package carries a branch. A package that mints its own IDs and a
package that keys on a catalog both compose the dropped claim's ID from their
own `IdRule`.
