# 39. A crossing a model cannot decide is still a lead

- **Status**: accepted. All four rules ship. Rules 1 to 3 are in the code and
  measured offline; rule 4 binds the lane and the critic, and it is written
  into `prompts/analyze.md` and `prompts/critic.md`. Every rule carries the
  maintainer's ruling of 2026-09-18 on the 26 findings in
  [the reading](../research/undecidable-crossings.md), and rule 4 is that
  ruling's own wording. What a lane agent *does* with an undecidable lead
  still needs the execution comparison named below
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

**4. An undecidable crossing establishes nothing.** It confers *eligibility for
analysis* and no more. It does not establish that a crossing occurred, that a
control is missing, or that an attack succeeds. A claim whose only support is an
undecidable crossing is a question for the submitter rather than a ruling, which
is what `needs-info` already exists for, and a critic that lets one stand as a
premise has accepted a fact nobody stated.

Rule 4 is the maintainer's wording rather than a paraphrase of it, because the
distinction it draws is the whole of what the reading in
`docs/research/undecidable-crossings.md` established. Of the 26 findings read
there, **eight survive the loss of the zone as they are written, fourteen need
an attack prerequisite made explicit, and four overclaim in their present
wording**. None of them needed an invented network zone to justify
investigating. Several needed stronger premises to justify their current attack
sentence, and that distinction is what the lead generator and the critic have to
preserve.

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

## What the change measures offline

Rules 1 to 3 ship, and the two free instruments move:

| | before | after |
|---|---:|---:|
| oracle: required rows a perfect reading keeps | 121 of 122 | **122 of 122** |
| oracle: placements the reading had to invent | 46 | **0** |
| `bottleneck`: rows the `unknown-placement` shape loses | 3 | **0** |
| archived emissions the gate refuses on replay | 0 | 0 |

The one row the path lost was case 12's: the sources name a supplier and never
place it, so ADR 0038 rule 2 dropped the supplier, the interaction to the portal
and the authentication fact on that interaction. That is the first of ADR 0038's
own revisit conditions, and it is now closed.

The 46 invented placements are the more important number. A perfect reading of
the corpus needed 46 guesses to satisfy the schema, and every one of them was
charged against the reference's own `unknown` as a wrong claim. It now needs
none.

**One reading this decision did not spell out.** Rule 3 says a rule keyed on a
crossing fires on an undecidable one. Four of the five crossing-keyed rules do.
The fifth, STRIDE's privilege-zone transition, does not: its premise is what the
two zones *are*, and an unplaced endpoint has no kind to read, so firing there
would assert the authority change rule 4 says an undecidable crossing cannot
establish. The rule skips it and says why.

## Where rule 4 is written down

Rule 4 has three readers and one rule. The code carries two of them:
`evidence_catalog` omits an undecidable crossing, so no agent can cite one and
no critic can let one stand; `crossing_facts` carries `crossing_decided`, so a
lead says which kind it is. The third reader is the instruction, and until now
it said nothing: both prompts are handed a crossings block carrying `decided`
and neither told its reader what the field means.

- **`prompts/analyze.md`, step 2.** An undecidable crossing is a reason to look
  at the flow and never a premise in the claim. The claim rests on what the
  model states — a control on the flow, the data it carries, the authority at
  an end — or it names the placement as the thing to answer first. The
  crossing cannot be cited; the unplaced endpoint's `trust_zone` can, as an
  unknown, which is what makes the claim conditional through the machinery that
  already exists.
- **`prompts/critic.md`, step 1.** A draft on such a flow is a fifth case. One
  whose argument rests on a stated fact passes on that ground; one that needs
  the boundary to have been crossed is **needs-info** where it names the
  placement as the open question, and **rejected** for `reasoning` where it
  asserts the crossing as a fact.

That split is the reading's own: of its 26 findings, none needed an invented
zone to justify investigating, and several needed a stronger premise to justify
their attack sentence. A critic that rejected every draft on an undecidable
crossing would lose the first group, which is the failure rule 3 exists to
prevent.

`tests/test_evidence.py` holds the offer the lane prompt points at — every
unplaced endpoint of an undecidable crossing publishes its own `unknown` row —
and `tests/test_graph.py` holds that the block both prompts read still carries
`decided`. Neither test can read the prose; what they pin is that the prose
points at something real.

## What this decision does not settle

**Benefit and cost need an execution comparison.** The reading establishes that
these flows deserve to stay eligible for analysis. It does not measure what a
lane agent does with an undecidable lead, how many claims a critic then has to
reject for resting on one, or what the change costs in tokens and latency across
32 sites. Those need a run, and the corpus that would ground it exists only
since the sitting of 2026-09-18.

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
