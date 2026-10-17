# 96. A name with no slug is refused on both routes

- **Status**: accepted
- **Date**: 2026-10-17
- **Related**: [ADR 0089](0089-a-mistyped-flow-endpoint-follows-the-one-element-with-its-slug.md),
  which gives both routes one rule for a mistyped flow endpoint
- **Evidence**: `tests/test_compact.py`
  (`TestBothRoutesFollowAnEndpointByOneRule::test_the_routes_refuse_one_set_of_names`),
  `tests/test_graph.py` (`test_validate_sends_a_name_that_slugs_to_empty_to_repair`),
  `tests/test_validation.py` (`test_a_name_that_slugs_to_empty_is_refused`),
  and the offline replay in this ADR's pull request

## Context

An **Element ID** is the type prefix and the slug of the element's name.
`normalize_name` makes the slug. It raises on a name with no Latin letter or
digit after it folds the case, for example `数据库` or `!!!`. Such a name has
no derived ID.

The two extraction routes treated this name in two ways:

- The full route admitted it. The element kept the ID that the model emitted,
  and the gate did not compare that ID with a derived one. In round 33, a
  trust boundary carried the ID `process:ghost`, and the readers of the ID
  prefix read it as a Process.
- The compact route refused it at the schema with a pattern, `NAMEABLE`. A
  schema fault on the compact route ends the job without a repair pass. The
  pattern also disagreed with `normalize_name`: it refused `İ` and the Kelvin
  sign, which slug to `i` and `k`.

## Decision

**A name with no slug is refused on both routes**, by the maintainer's ruling
2026-10-17.

- `derive_element_id`, through `normalize_name`, is the one reader of "has
  this name a slug". The validity gate asks it for each element, and reports
  each element without a derived ID as `empty-slug`. The compact schema has no
  name pattern of its own.
- The refusal is a gate issue, not a schema fault, on both routes. So both
  routes send it to the repair pass.
- `empty-slug` is in `RENAMING_CODES`. The repair gives the element a new
  name, and so a new ID, so the flows through the element are in the repair
  scope.
- `prompts/extract.md` rule 3 tells the model that a `name` needs a Latin
  letter or digit, and to keep a name in another script in `notes`.

## Consequences

- Every element that the gate admits carries the ID that its class and name
  derive. The ID prefix of an admitted element is always its class's
  `id_prefix`, so a reader of the prefix agrees with a reader of the class.
- The gate check that compared only the prefix of an underived ID is gone.
  The new rule refuses each element that check could see.
- `references.canonical` no longer meets two element IDs that differ only in
  case. Its uniqueness guard still applies to source labels.
- On the compact route, an element with no slug keeps its provisional
  `ref_` ID in the model that the repair pass reads. The repair names the
  element, and `normalize_element_ids` then derives its ID and moves each
  reference to it.
- A name with no slug costs one repair pass on each route. The compact route
  ended such a job before, and the full route admitted it. The live cost of
  these extra passes is unmeasured.
- Archived models can hold a name with no slug, so the eval readers of
  archived models keep their own guard for it. No archived emission or corpus
  file holds such a name today.
