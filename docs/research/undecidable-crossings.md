# The crossings ADR 0039 would make undecidable (#1052)

**Question.** [ADR 0039](../adr/0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md)
proposes that a component may be placed nowhere and that a flow whose endpoints'
zones the model cannot compare raises an *undecidable* crossing rather than
none. Its rule 3 — such a crossing still raises a lead — is the half that
decides whether the change is worth making, and no number settles it. A reader
has to say whether a weaker lead would still have reached the finding.

**This file is the reading material for that judgement. It rules nothing.**

`undecidable-crossings.csv` beside it holds one row per must-find finding citing
a flow that becomes undecidable, over the thirteen signed corpus cases: 29
flows, 76 rows, three of them flows no must-find cites at all.

**Method.** Every placement the signed reference reads as unknown is treated as
absent, and every crossing needing one of those endpoints is undecidable. The
flow's own control attributes ride on each row, because they are what decides
the question: a finding resting on `none; accepted by network position` survives
a weaker lead, and one whose sentence names a zone may not.

Regenerate it from the corpus with
`uv run python -m evals.harness.run bottleneck`, whose reliance table carries
the same counts.

## What the columns say

| Column | What it is |
| --- | --- |
| `source_unstated`, `destination_unstated` | which endpoint the reference reads as unplaced |
| `protocol`, `authentication`, `encryption` | the flow's own controls, in the blessed model's words |
| `category` | the STRIDE category, or empty for an ASVS record |
| `also_cites` | the other elements the finding names, so a finding with another lead is visible |
| `finding` | the claim, as the reference states it |

## The three shapes a reader is looking for

1. **The finding rests on a stated control.** `01`'s submit-order rows fire on
   "none; accepted by network position"; `10`'s direct-administration rows on
   "no TLS on it". The crossing is why the rule fired and not why the finding
   holds.
2. **The finding's own sentence names a zone.** The elevation-of-privilege rows
   are where this bites: "gains order-writing privilege in the core zone" stops
   parsing if neither endpoint is placed.
3. **The finding rests on structure alone.** Case 11 is the whole of this shape
   — both endpoints unstated and every control unknown on every flow.

## The ruling of 2026-09-18

`undecidable-crossings-rulings.csv` beside this holds the maintainer's reading
of the 26 findings on the flows cases 10 to 13 carry, in their own words.
`retain` means the observation or attack scenario survives without the zone;
`conditional` means an attack prerequisite has to be made explicit; `revise`
means the present wording overclaims.

**Eight retain, fourteen conditional, four revise.** The ruling's own summary:

> None of these remaining candidates needs an invented network zone to justify
> investigation. Several need stronger premises to justify their current attack
> wording. That is the distinction the lead generator and critic should
> preserve.

Two corrections it makes to this file's earlier reading, both accepted: case 11
does **not** carry an unstated endpoint on every flow — the payroll file share's
destination membership is stated — and its findings do **not** rest on structure
alone, because actor roles, privileged operations and payroll data each give a
reason to investigate.

## Provenance

Computed at `2e08836` from `evals/corpus/*/model.json`, `facts.json` and
`claims/*.json`, after the maintainer sitting of 2026-09-18 signed all thirteen
cases. No provider call.
