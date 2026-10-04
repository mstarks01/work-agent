# 61. Two sibling flows are two places

- **Status**: accepted
- **Date**: 2026-10-04
- **Effort**: [#1438](https://github.com/mstarks01/work-agent/issues/1438)
- **Relates to**: [ADR 0060](0060-two-mechanisms-at-one-place-are-two-findings.md),
  whose mechanism this extends.

## Context

A claim's place is its cited elements, with each cited flow folded into its two
endpoints, so that a claim citing a flow and a claim citing the process at its
end are one place. Case 13 draws two flows between one console and one API: the
REST dispatch requests and the live-status WebSocket. The fold sends both to
that pair, so a claim about the WebSocket and a claim about the REST requests
were one place, one key and one finding. Three of the eleven same-key pairs in
ledger row `QA-2026-10-03-02-E2` were these.

## Decision

**A claim's mechanism also holds the sibling flows it cites**
(`critic.cited_channels`): a cited flow that runs between the same two elements
as another flow. Two claims under one key are apart where their controls do not
overlap, or where their cited sibling flows do not
(`critic.distinct_mechanisms`).

**The place half of the scorer's rule reads the same channels**:
`identity.endpoint_subset` refuses two sides that cite non-overlapping sibling
flows. A side that cites only the two elements still matches either sibling.

**No key changes.** Changing the fold itself separates the same pairs, but it
moves every key that cites a sibling flow, which needs an identity version, a
rekey of the vote ledger and older-key resolution for every sitting. Carrying
the channel beside the key, as ADR 0060 carries the controls, moves no key, no
mark and no vote.

## Consequences

Ledger row `QA-2026-10-03-02-E8`: no labelled or reference figure moves, all
three case 13 pairs separate, and one must-find match is removed: case 13's
reference about a WebSocket opened in a duty engineer's name, credited to a
cross-origin request on the other flow, which the #890 ballot ruled
`different`.

Only case 13 has sibling flows in the corpus. A model that draws one flow where
there are two still cannot be told apart, and that is a matter for extraction.

## Framework parity

- **stride**: case 13's three pairs are STRIDE.
- **asvs**: nothing changes in practice, because an ASVS claim is keyed by its
  requirement. The scorer's place half applies to it the same way.
