# 31. A claim's identity carries no direction

- **Status**: accepted
- **Date**: 2026-09-10
- **Closes**:
  [issue #652](https://github.com/mstarks01/work-agent/issues/652), split out of
  [#534](https://github.com/mstarks01/work-agent/issues/534) by the boundary
  review.
- **Relates to**:
  [issue #201](https://github.com/mstarks01/work-agent/issues/201), which
  designed the identity rule, and
  [`docs/agents/claim-identity.md`](../agents/claim-identity.md), which holds
  the rule and now holds these numbers.

## Context

A **Claim**'s identity is the framework, the lane, the endpoint-resolved
**Element** IDs and the action verb. Nothing in that tuple says which way the
attacker moves across a boundary.

So two claims merge when both carry `escalate` and share an endpoint, whichever
way the privilege moves. `actions.py` glosses `escalate` as "reaches a privilege
or a zone their position does not carry", and that sentence is true of both
directions.

`evals.harness.verbs.UNSEPARATED` already recorded one merge of this shape, in
case 01's elevation lane. It recorded the merge and the argument for it. It did
not rule on whether the merge was right.

Three measurements answer it, and each one is offline and reproducible from the
tree. They cost no model call.

**A direction is not a field.** `affected_element_ids` is a list whose order no
rule reads, so a claim naming two processes says nothing about which way the
attacker moves between them. Only a claim naming a **Data Flow** states a
direction, through that flow's endpoints. 155 of the 244 corpus claims name
exactly one flow. Three name several and 86 name none, and neither of those
yields the single direction a comparison needs.

**The merge that raised the question runs one way, not two.** Case 01's two
`escalate` claims both name the DMZ-to-core pivot. One cites the flow into the
order service and the process it ends at; the other cites the two processes. The
corpus already treats them as adjacent.

**Neither reading of an absent direction is usable.** Over the 186 labelled
pairs the shipped rule merges today:

| An absent direction read as | New false splits | Candidate merges recovered |
|---|---:|---:|
| a mismatch | 90 | 2 |
| a wildcard | 0 | 0 |

Read as a mismatch, a direction breaks half of what the rule gets right, to
recover two of its three candidate merges. Read as a wildcard, it changes
nothing at all, because in all three surviving merges the coarser side cites no
flow.

## Decision

**A claim's identity stays undirected.** No direction component enters the
tuple, and `VERSION_FOR` does not move.

1. **The case 01 elevation merge is correct.** Both claims name one pivot at two
   grains, and the corpus records the finer one as a must-find and the coarser
   one as expected. `UNSEPARATED` now states that ruling rather than the
   argument for it.

2. **The numbers are pinned, not asserted in prose.**
   `evals.harness.calibration.measure_direction` is the one aggregation.
   `tests/test_evals_identity.py` holds them in `DIRECTION` and asserts them
   exactly, and `tests/test_doc_figure_lints.py` checks the guide still states
   what the code computes.

3. **One reader decides what a direction is.**
   `evals.harness.identity.flow_directions` and `direction_state` sit beside
   `endpoint_form`, which they are derived from. Both test modules call them.

4. **The authority-domain candidate is not priced.** #652's second candidate is
   the authority domain a claim moves between. The **System Model** holds no
   such field, so pricing it needs the field first. Nothing here rules on it.

## Consequences

**A merge that does run two ways reopens this, and lands as a test failure.**
`test_no_surviving_merge_runs_in_two_directions` asserts that no surviving merge
carries a derivable direction on both sides. A fourth merge that did would fail
there, with the two claims in the message. That is the evidence this decision
was made without.

**A blessing pass that re-cites claims against flows reopens it too.** The
coverage counts are asserted, so a corpus that starts naming flows where it
named processes fails `test_a_direction_is_underivable_on_a_third_of_the_corpus`
and asks for the question again.

**A rule that reads a direction is still priceable.** `measure_direction` takes
the matcher whose merges it prices, so a later candidate rule is measured the
same way without a second copy of the aggregation.

## Framework parity

Stated as a property, so it answers for a package nobody has written: **a
package whose claims compose an identity from an action and a place may lack a
component that only prose carries, and this rules that a direction is one of
them.**

- **STRIDE** composes an identity from an action and a place, so this is the
  package the decision is about. Its identity is unchanged.
- **ASVS** composes none of its own. A claim naming a catalog requirement takes
  its identity from that requirement and the place it was ruled in, so no verb
  and no direction enter it. Nothing changes there.

## Licensing

Nothing here reproduces a governed sentence. The vocabulary is this
repository's.
