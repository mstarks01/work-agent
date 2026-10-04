# 62. A cited row is part of a claim's mechanism

- **Status**: accepted
- **Date**: 2026-10-04
- **Effort**: [#1438](https://github.com/mstarks01/work-agent/issues/1438)
- **Relates to**: [ADR 0060](0060-two-mechanisms-at-one-place-are-two-findings.md),
  whose mechanism this completes.

## Context

ADR 0060 reads a claim's mechanism from the attributes its unknown and absent
grounds cite. A control the source **states** never appears there: with the
assertion pass on, a draft cites it as an assertion row. So case 01's leaked
password, which rests on the stated rows `authorization-grant` and
`credential-custody`, had an empty mechanism, and stayed one finding with the
offline copy that rests on `encryption_at_rest`.

The assertion pass is off by default, so no Baseline draft cites a row (ledger
row `QA-2026-10-03-02-E9`). It is a supported setting, though, and a deployment
that sets `ANALYSIS_ASSERTIONS` meets the gap today.

## Decision

**A cited assertion row adds its control to the mechanism.** The control is the
attribute the row's predicate projects into, so a row and an attribute ground
about one control read alike (`transport-encryption` reads as
`encryption_in_transit`), or the predicate's own name where it projects into
nothing (`credential-custody`). `critic.row_controls` builds the map once per
job from its catalog, by each row's exact ID, which a reader never splits.

The sweep's scorer, the writing instrument, the review queue and the follow-up
comparison pass each job's rows. A job with no catalog passes none, so a
default deployment reads exactly what it read before.

## Consequences

On both archived runs with the assertion pass on (`20260923T-assertions-ab`,
`20260924T-preflight-926`), case 01's two findings now separate, and without
the rows they do not (ledger row `QA-2026-10-03-02-E10`). Case 08's pair took
two different verbs once the pass was on, so its keys already differ.

## Framework parity

- **stride**: case 01's pair is STRIDE.
- **asvs**: nothing changes in practice, because an ASVS claim is keyed by its
  requirement.
