# 37. A flow identity carries its endpoint types

- **Status**: accepted
- **Date**: 2026-09-16
- **Effort**: finding 5 of
  [#961 — the extraction audit](https://github.com/mstarks01/work-agent/issues/961).
  The implementation is its own issue; this records the identity and the
  migration contract it must meet.
- **Relates to**: [ADR 0031](0031-a-claim-identity-carries-no-direction.md),
  whose claim identity composes from the element IDs this decision changes.
- **Evidence**: the audit's probe, pinned in `tests/test_validation.py`: an
  entity named `x` and a process named `x`, each writing the same label to
  one store, derive one flow ID.

## Context

A flow's ID is `flow:<source slug>-to-<destination slug>:<label slug>`. The
endpoint's type prefix is dropped on the way in, so two legal, distinct
elements of different types with one name derive one flow. The gate refuses
the collision today as a `duplicate-id`, before any reference is rewritten
through it, so the defect cannot reach a report silently. It can still stop a
correct model at the gate, and it is a latent defect in a rule, not a
measured loss: no corpus case holds two same-named elements of different
types. That justifies keeping the fix off the extraction experiment's path,
and nothing more. The defect is established; this decision says how it is
fixed.

Every flow ID is persisted and cited. The blessed models, the reference
claims, the vote ledger, the archived reports and the emissions all spell
flow IDs in the current shape, and a claim's identity composes from the
element IDs it touches. Changing the derivation is a schema change with a
migration.

## Decision

**A flow's identity encodes its complete typed endpoint IDs and its
discriminator, in a canonical form that decodes back to its parts. The
change is versioned, migrated from the original graph, and never rewrites a
historical artifact.**

Six rules.

**1. The endpoints keep their types.** The ID is built from the source's
and the destination's full element IDs, prefix included. An entity and a
process with one name derive two flows.

**2. The encoding is unambiguous, and a decoder proves it.** The three parts
are serialized under a delimiter that cannot occur in any part, and the
implementation ships the function that parses an ID back into its three
parts. An element ID is `<prefix>:<slug>` with the slug drawn from lowercase
letters, digits and hyphens, so a colon and a hyphen occur inside a part and
are not delimiters. `flow:entity:x>store:y:read` illustrates the intent, and
the implementation picks the delimiter against the ID pattern and holds the
round trip in a test. A string format is not assumed collision-free because
it reads well.

**3. The label is the discriminator for now, and that is a recorded
limitation.** Whether the model permits two independently addressable
interactions with identical endpoints and labels is decided separately.
Until it is, two flows with the same typed endpoints and the same label are
one identity, the gate refuses the duplicate, and nothing merges them
silently or invents a suffix. If that decision adds a stable interaction key
apart from the display label, it is a second identity version under rule 4.

**4. The identity is versioned, and readers dispatch by version.** The rule
that derives a flow ID carries a version beside the claim identity rule ADR
0031 keys by table. An artifact records the version its IDs were written
under. A reader of an old artifact decodes it under that version's rule and
its identity rules, which stay in the tree; recording a version without
keeping its decoder is not compatibility. A comparison across versions goes
through the migration mapping, never through string equality.

**5. Migration builds the mapping from the original graph.** An old flow ID
alone cannot recover the endpoint types it dropped. The migration reads the
graph the ID was derived from, computes the new ID from the typed endpoints
it finds there, records the old-to-new mapping, then migrates every
dependent reference and recomputes every claim identity from the mapped
elements. A graph in which two elements already collide under the old rule
is flagged, and the migration stops on it rather than guessing which flow a
reference meant.

**6. A vote carries forward only where the migration proves the change is
one-to-one and the claim's meaning and evidence are unchanged.** Such a vote
keeps its original provenance and gains the mapping it crossed. A split, a
merge, an ambiguous reference or a changed interaction semantics needs
review, and the row is marked for it rather than carried.

## Scope of the first implementation

Typed endpoint encoding with its decoder, the version, and the migration
with its mapping, flags and vote rules, each with a migration test over the
corpus and the archived artifacts. Label-independent interaction identity is
a separate decision and a separate issue. The gate's collision refusal stays
as it is, and its test is the acceptance probe: after the change the probe
derives two flows and no issue.

## Consequences

- Every blessed model, reference claim file and vote ledger row is rewritten
  once by the migration, under a recorded mapping. Historical artifacts keep
  their IDs and their version.
- The alignment reads element IDs and does not care which version derived
  them, but a replay of an old emission against a migrated corpus goes
  through the mapping, so the replay gains a version check.
- A Baseline sealed under the old version reads as its own version;
  comparing it to a new one is a migration, not a string match, and the
  identity seal names the version.
