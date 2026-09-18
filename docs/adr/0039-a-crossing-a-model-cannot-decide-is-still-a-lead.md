# 39. A crossing a model cannot decide is still a lead

- **Status**: proposed, priced on the corpus below
- **Date**: 2026-09-18
- **Effort**: [#1052](https://github.com/mstarks01/work-agent/issues/1052),
  which reopened [ADR 0038](0038-a-component-reaches-the-graph-only-in-a-zone.md)
  on two of the three numbers that ADR named.
- **Relates to**:
  [ADR 0032](0032-a-derived-crossing-names-an-inferred-zone.md), which settles
  how a zone nobody stated is recorded, and
  [ADR 0027](0027-vocabulary-raises-a-lead-and-rules-nothing-out.md), whose rule
  — a vocabulary raises a lead and rules nothing out — this decision applies to
  a derived fact rather than to a term.
- **Evidence**: `run.py bottleneck` over the thirteen signed corpus cases, and
  the simulation in "What it costs" below.

## Context

ADR 0038 requires a component to hold a zone, and where no source states one the
model infers it and records an `Assumption`. A maintainer sitting on 2026-09-18
signed reference facts for all thirteen corpus cases and declined to endorse
**55** of those placements, leaving each one standing only because the schema
demands it. The oracle's ceiling fell to 118 of 119 in the same sitting. Those
are two of the three numbers ADR 0038 named as reopening it.

**The inference is load-bearing rather than cosmetic.** Of 180 must-find
reference findings, **73 cite a boundary crossing with at least one inferred
endpoint, and 25 cite one where both endpoints were inferred.**

## The decision this rejects

The obvious reading of "let `trust_zone` admit an absence" is that a crossing
needs two known zones, and a flow with an unplaced endpoint derives none.
**Simulated over the corpus, that removes 29 of 43 crossings and takes the
structural lead away from all 73 of those must-finds.** It trades a finding
resting on a guess for a finding with nothing to raise it, which is worse: the
guess is at least visible and now, since #1056, stated in the lead.

## Decision

**`trust_zone` admits an absence, and a flow whose endpoints' zones the model
cannot compare raises an undecidable crossing rather than none.**

Four rules.

**1. A component may be placed nowhere.** `trust_zone` takes the unknown
sentinel, and `validate` stops reporting `invalid-reference` for it. A component
the sources place is placed; one they do not is unplaced, and neither the
extraction nor the resolver invents a zone to satisfy the schema.

**2. A crossing has three outcomes, not two.** Both zones known and different is
a crossing; both known and equal is not; **either unknown is undecidable**. A
`BoundaryCrossing` carries which it is, and `assumed_endpoints` keeps its
meaning for the placements a model still infers under ADR 0032.

**3. A rule keyed on a crossing fires on an undecidable one, and says so.**
This is ADR 0027's rule applied to a derived fact: an undecidable crossing
raises a lead and rules nothing out. The facts already carry
`source_zone_assumed` and `destination_zone_assumed` (#1056); an undecidable
crossing adds `crossing_decided: false`, so a lane agent weighs a lead it would
otherwise never see.

**4. A severity or a verdict may not rest on an undecidable crossing alone.**
A lead is not a finding. Where the crossing is the only structure behind a
claim, the claim is a question for the submitter rather than a ruling — which is
what the `needs-info` disposition already exists for.

## What it costs

Simulated by setting every placement the reference reads as unstated to absent,
over the thirteen signed cases:

| | |
|---|---|
| crossings derivable today | 43 |
| still decidable | **14** |
| undecidable | **29** |
| must-finds whose crossing becomes undecidable | **73** |

Under rule 2 alone those 73 lose their lead. Under rule 3 they keep it, and the
lead says the crossing is undecided.

The implementation cost is the one ADR 0038 priced: `trust_zone` is read by 32
sites across nine modules, and every rule that reasons about a crossing gains a
case. Rule 3 is what makes that spend worth making rather than merely honest.

## What would falsify this

- **A reader finding that an undecidable lead is noise.** 29 crossings become
  leads that say less than they do today. If a reviewer reads those findings and
  judges them unhelpful, rule 3 is wrong and rule 1 should not ship without it.
- **A corpus where placement is usually stated.** Here 29 of 43 crossings
  become undecidable and 73 of 180 must-finds cite one. A corpus whose sources
  place their components would leave few undecidable crossings, and rule 3 would
  then be machinery for a case that rarely arises.

## Consequences

- Extraction stops being asked to invent a placement, so the 55 unendorsed
  assumptions become absences the model states outright.
- `boundary_crossings()` stops raising on an endpoint without a zone, which it
  does today, and answers `undecidable` instead.
- Every **Framework Package** sees the third outcome at once, because a crossing
  is derived from the System Model and the System Model is the service's. A
  package whose rules read a crossing gains a case; one whose rules match terms
  in prose is untouched.
- The **Evidence Catalog** gains no entry. An undecidable crossing is a property
  of two elements' attributes, and those attributes already publish their own
  unknown entries.
