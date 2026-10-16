# 94. A dismissed ground is not a fact a finding waits on

- **Status**: accepted
- **Date**: 2026-10-16
- **Amends**: [ADR 0056](0056-the-findings-that-wait-decide-the-follow-up-order.md),
  on which facts a conditional finding waits on
- **Evidence**: PR #1786 and the offline replay in this ADR's pull request

## Context

A draft can cite an open fact in its grounds that its argument does not use.
The critic then names that fact in `immaterial_unknowns`, and
`critic.complete_rulings` leaves it out of `related_unknowns`. The finding's
card showed only the facts in `related_unknowns`. The follow-up joined those
facts with every open ground. So a dismissed ground ranked the order, named
the finding as one that waits on it, and had to be answered before the count
covered the finding. ADR 0056 did not say which set is right.

## Decision

**A ground the critic dismissed is not a fact a finding waits on**, by the
maintainer's ruling 2026-10-16. A finding waits on the facts its `needs-info`
verdict names in `related_unknowns`. `RuledClaim.waits_on` is the one reader.
The card, the open-facts grouping, the follow-up's counts and the follow-up's
order call it.

No other ground counts. `complete_rulings` already adds to `related_unknowns`
every unknown ground of a draft that the critic did not dismiss. So the
verdict holds each open ground that the finding rests on.

**A dismissed ground is still asked**, because every open fact is asked
(ADR 0046). It goes with the facts no waiting finding needs, last on the page
(ADR 0056). It keeps the basis `evidence`, because a draft's own grounds cite
it.

## Consequences

The card and the follow-up name the same facts for each finding, and a test
holds them equal over the archived reports.

Over the 52 reports of the four Baselines, every report asks the same
questions. 12 questions name no finding now, and move to the closed section.
The findings covered at 3, 5 and 10 questions rise by 3, 3 and 1. Every
change falls in the 13 reports of Baseline 6bff717. The pull request lists
the full figures.

The live effect is unmeasured. A critic sampled again can dismiss other
grounds, so this order moves with the critic as ADR 0056's order does.
