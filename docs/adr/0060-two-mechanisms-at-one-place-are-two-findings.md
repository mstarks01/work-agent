# 60. Two mechanisms at one place are two findings

- **Status**: accepted
- **Date**: 2026-10-04
- **Effort**: [#1438](https://github.com/mstarks01/work-agent/issues/1438)
- **Relates to**: [ADR 0031](0031-a-claim-identity-carries-no-direction.md),
  whose key this keeps, and
  [ADR 0059](0059-the-critic-rules-each-draft-and-rejects-no-duplicate.md),
  which removed the step that deleted one of each pair.

## Context

A STRIDE claim's key is its framework, lane, verb and folded place. Two
different findings can share that key: a fake server impersonating an API to
its clients, and a fake caller impersonating a client to the API. In four
Baselines, 11 of about 890 draft keys held two different findings (ledger row
`QA-2026-10-03-02-E2`).

The ledger kept one live vote per key and voter, so a vote on one finding of a
pair replaced the vote on the other. The maintainer ruled that the identity
rule must tell mechanisms apart without a convenient synonym: a new verb for
each pair would only move the problem to the next pair.

## Decision

**A claim's mechanism is the set of controls its own grounds say are unstated
or missing**: the attribute of each unknown-attribute and absent-attribute
ground (`critic.grounded_mechanism`). It is read from fields, never prose.

**Two claims under one key are two findings where both mechanisms name
something and they do not overlap** (`critic.distinct_mechanisms`). Overlap
rather than equality, because one finding cites a slightly different set from
run to run. Ledger row `QA-2026-10-03-02-E7`:

| Rule | Same-key pairs separated (of 11) | One finding split across runs (of 165) |
| --- | ---: | ---: |
| The sets differ | 8 | 39 |
| Both name something and do not overlap | 5 | 3 |

**The key does not change.** The mechanism rides beside it:

- `Components.mechanism` stores it on every vote. A vote stored without it
  reads as empty, and an empty mechanism binds, so archived votes keep their
  findings.
- `Ledger.current` keeps one live vote per key, voter and mechanism: a later
  vote replaces an earlier one only where their mechanisms are not distinct.
  `Ledger.verdicts_for` is the one reader of which vote answers a finding.
- The review queue asks one question per key and mechanism, and an item's
  `key` names it to the page.
- The scorer, the writing instrument, the agreement standings and the
  follow-up comparison read the same rule.

## Consequences

Five of the eleven measured pairs separate: every opposite-direction
impersonation. The other six cite overlapping controls, such as an MQTT flood
and a Pub/Sub flood that both rest on no stated limit, and no grounded field
tells them apart. They stay one finding under this rule.

A reference claim carries no grounds, so its mechanism is empty and the scorer
matches as before. Scoring a draft against the wrong direction of a reference
needs a mechanism on the reference, which no reference records.

The relation is not transitive. A finding whose mechanism overlaps two
distinct ones joins the first, and its vote answers the later of the two.

## Framework parity

- **stride**: every measured pair is STRIDE.
- **asvs**: nothing changes, because an ASVS claim is keyed by its
  requirement, so two mechanisms map to two requirements and two keys. Its
  mechanism still rides on its votes, and it never makes two ASVS claims one.
