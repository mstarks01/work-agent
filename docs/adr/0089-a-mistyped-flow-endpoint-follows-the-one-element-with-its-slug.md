# 89. A mistyped flow endpoint follows the one element with its slug

- **Status**: accepted
- **Date**: 2026-10-15
- **Evidence**: `tests/test_system_model.py`
  (`test_an_endpoint_mistyped_follows_the_one_element_with_its_slug`,
  `test_an_endpoint_whose_slug_two_elements_carry_stays_dangling`);
  `tests/test_compact.py` (`TestBothRoutesFollowAnEndpointByOneRule`);
  `evals/experiments/QA-2026-10-15-03.jsonl` (E1)

## Context

An element ID is its type prefix and the slug of its name, and a flow names
its endpoints by ID. A model can list an element under one type and write a
flow endpoint under another: in QA-2026-10-15-03-E1, three of five sweeps on
case 14 declared `entity:partner-app` and wrote `process:partner-app` as a
flow source. The gate reported `invalid-reference`, the model had no repair
pass for it, and the job lost the whole case.

The mismatch is one the slug settles. The model named the thing; it mistyped
the prefix. `normalize_element_ids` already rewrites every reference to a
derived ID because an ID is a function of the name.

## Decision

A flow endpoint that names no element, but whose slug exactly one zoned
element carries under another type prefix, follows that element. The flow's
own ID is then derived from the followed endpoints as before. The rule reads
only the `entity`, `process` and `store` prefixes, because a flow endpoint
has no other legal type.

`follow_by_slug` in `src/analysis_service/system_model.py` is the one reader
of the rule, and both extraction routes call it. The full route calls it in
`normalize_element_ids` over element IDs. The compact route calls it in
`expand` over refs, with the tags `e`, `p` and `s`, before a ref resolves to
its element. A ref is the model's own handle and need not be the slug of the
name, so the compact route matches the ref's slug, not the name's.

These endpoints are left as written, so the gate reports them:

- a slug two zoned elements carry, because the rule never chooses between
  two elements;
- a slug no zoned element carries, including a slug only a trust boundary
  or a flow carries;
- an endpoint whose prefix is not a flow endpoint's type;
- an endpoint that two elements arrived with: on the full route an emitted
  ID two elements carried, and on the compact route a ref two elements
  claimed. Such an endpoint names elements ambiguously, and the gate reports
  it beside its `duplicate-id` or `duplicate-ref`.

No assumption row records the follow: it decides nothing the model did not
state, which is the test every rewrite in `normalize_element_ids` meets.

## Consequences

- A model that is consistent in its names and inconsistent in one prefix
  passes the gate instead of spending the job.
- A reference to a slug the model gave two types stays a gate finding. The
  normalizer never chooses between two elements.
- The alignment in `evals/harness/alignment.py` reads the normalized model,
  so a replay of an archived emission with this shape now pairs the flow
  where it once read `endpoint_unaligned`.
