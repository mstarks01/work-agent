# 99. A flow is a sibling only in its own graph

- **Status**: accepted
- **Date**: 2026-10-22
- **Relates to**: [ADR 0061](0061-two-sibling-flows-are-two-places.md), which
  stays in force for the flows of one graph
- **Evidence**: ledger rows `QA-2026-10-22-01-E8` and `QA-2026-10-22-01-E11`;
  `tests/test_evals_reference.py`
  (`test_a_renamed_flow_is_one_place_with_the_flow_it_names`,
  `test_two_blessed_siblings_stay_two_places`)

## Context

In an end-to-end run, the extraction names the flows itself. `flows_by_case`
builds one map per case from the corpus model and the analysed model, so that a
draft's citation of an extraction flow resolves to its two endpoints (#949).

Where the extraction names a corpus flow another way, the map holds two IDs
between the same two elements. `sibling_flows` read the merged map, so it
counted the two IDs as two sibling flows. ADR 0061 then made them two places,
and the scorer refused a draft that states the reference's threat on the
reference's flow.

Row `QA-2026-10-22-01-E8` reproduced this on case 01. Row
`QA-2026-10-22-01-E11` measured it on the 5 archived end-to-end sweeps: the
rule refused 10 must-finds of 37 and 32 matches.

## Decision

**Each flow's siblings come from the graph that supplies its endpoints.**
`flows_by_case` returns a `CaseFlows` for each case: the merged map, and the
sibling flows of the corpus graph and of the analysed graph, each read alone.
Where both graphs hold one ID, the corpus graph decides, as it decides the
endpoints.

`identity.channels` is the scorer's one reader of a claim's channels.
`identity.endpoint_subset` and the review queue call it. A plain map is one
graph, so its siblings are read from the whole map, as before.

## Consequences

- On the 5 end-to-end sweeps, `run.py score` matches 28 must-finds of 37 in
  place of 18. No match is lost.
- In every mode that injects the blessed model, both graphs are the corpus
  model, so no figure moves. The emissions replay is unchanged.
- Where the corpus draws two sibling flows and the extraction draws one flow
  between the same elements, a claim on the extraction's flow matches either
  sibling. This is the limit ADR 0061 already states for a model that draws one
  flow where there are two.

## Framework parity

- **stride**: the change. STRIDE claims cite flows, and the place half of the
  identity rule reads their channels.
- **asvs**: nothing changes in practice. An ASVS claim is keyed by its
  requirement, and no end-to-end ASVS sweep cites a renamed flow.
