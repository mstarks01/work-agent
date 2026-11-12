# 128. An unplaced source entering a privilege zone is a lead

- **Status**: accepted
- **Date**: 2026-11-06
- **Amends**: [ADR 0039](0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md),
  whose rule 3 covers STRIDE's privilege-zone transition for a flow into a
  placed zone

## Context

The STRIDE rule `elevation-of-privilege-privilege-zone-crossing` skipped every
undecided crossing. An extracted model places an endpoint only where a source
states it, so a flow from an unplaced source into a privilege or tenant zone
raised no lead. In five extraction sweeps (QA-2026-11-06-02-E2), two must-finds
lost their lead in every sweep for this reason: case 12 reference 13 and case 13
reference 16. In both cases the signed model places the source only as a
recorded assumption.

## Decision

**A flow into a placed privilege or tenant zone raises the lead, also where no
source places the other endpoint.** The rule reads the kind of the zone it
names, and the destination's kind is stated. The candidate carries
`crossing_decided: false`, so rule 4 of ADR 0039 still binds: the lead confers
eligibility for analysis, and a claim that needs the crossing names the
unplaced endpoint's `trust_zone` as an unknown.

**A flow out of a tenant zone raises the lead only on a decided crossing.** An
unplaced destination may sit in the same zone, so nothing is shown to leave it.

## Consequences

- An extracted model gives the two leads above in every sweep. Their effect on
  matched must-finds is unmeasured.
- Where the unplaced source sits inside the destination zone, the lead is
  false. The lane and the critic then read an undecided crossing, which
  establishes nothing.
