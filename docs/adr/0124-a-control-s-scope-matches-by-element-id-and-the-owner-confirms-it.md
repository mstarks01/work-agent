# 124. A Control's scope matches by element ID, and the owner confirms it

- **Status**: accepted
- **Date**: 2026-11-03
- **Effort**: [#2115](https://github.com/mstarks01/work-agent/issues/2115), on the
  Controls map [#2111](https://github.com/mstarks01/work-agent/issues/2111)
- **Relates to**: [ADR 0116](0116-an-owner-maps-a-carried-answer-s-element-to-a-new-one.md),
  whose match rule and choice list this decision reuses;
  [ADR 0113](0113-a-shared-answer-covers-the-elements-a-person-ticks.md), whose
  unticked start this decision departs from;
  [ADR 0039](0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md), rule 1

## Context

A Control names what it protects: the whole application, one zone by name or
one element by name (ADR 0123). Each job extracts a new model, and an element
ID follows the element's name. A carried answer meets the same problem, and
ADR 0116 settled it: the owner states a rename, the service never guesses one,
and a similar name proposes nothing.

A zone holds many elements. A shared answer at the pause starts with every
element unticked (ADR 0113), because the page built that box, not the owner.

## Decision

**A scope stores the element type, the name and an owner-written alias list.**
A match is equality of the ID that `make_element_id` builds from the type and
the name with an element ID of the new model. A match tries the name, then
each alias. A similar name proposes nothing. When the owner maps a scope to
another element at the pause, the page offers "keep this name", which
appends the new name as an alias; the service never writes an alias on its
own. A duplicate alias, and an alias that another scope of the same type
holds, are refused.

**The owner ticks Controls at submission and confirms each match at the
pause.** A submission holds no model: the service extracts it after the
submission. So the submission names the Controls, the job copies them, and
the pause offers each copy with its match against the extracted model. A job
that does not pause takes the default resolution: a name or an alias match
applies, a zone covers its ticked elements, and a scope with no match is left
out with its reason.

**An element scope covers `entity`, `process` and `store`; a zone scope
covers `boundary`.** A flow is not a scope. Its identity is two endpoints and
a label, so a name alone is not an ID, and an endpoint rename moves it.

**A zone scope fans out, ticked.** At the pause the page lists each element
whose `trust_zone` holds the matched boundary's ID, ticked, and the owner may
untick one. The start differs from ADR 0113 because the owner authored this
scope. An element whose zone the service inferred into the zone is listed
unticked with the reason `zone-inferred`. An unplaced element of a zoned type
is listed unticked with the reason `no-zone-stated`. A tick states that the
Control covers the element, and it states no zone.

**No match is a listed reason from a closed set.** `missing-element` when no
element of the type carries the name or an alias, and `empty-zone` when the
zone matched and no element holds it. Beside `missing-element`, the page
offers the elements of the scope's type, as `rename_choices` does. An
application-scoped Control always matches.

**A pause save carries `control_scopes`, one entry per applied Control**: the
Control's ID, the element it resolves to, and for a zone the element IDs the
owner ticked. The service refuses an entry that crosses the scope's type, that
names an element the model does not have, that ticks an element outside the
candidate list, and two entries for one Control. Two Controls may resolve to
one element, because a system has many protections on one part.

**The job keeps the scope as authored and the outcome.** The copy holds the
type, the name and the aliases at copy time. A pause save writes one
resolution per copy to the job row's `control_scopes`, and a job with no
save takes the default of each copy: the match outcome from `application`,
`matched-by-name`, `matched-by-alias`, `owner-mapped`, the element ID the
scope resolved to, and for a zone each covered element with its reason and its
tick; or, for a Control the owner left out, the reason `missing-element`,
`empty-zone` or `unticked`. The copy keeps a left-out Control's document, so a
later pause save can tick it again. One reader resolves the copy to its
element IDs, and the fact writer and the critic both call it.

## Considered options

- **A similarity match.** Rejected: fewer clicks, but a guessed placement.
- **A service-written alias.** Rejected: an edit of the owner's record on a
  one-job click.
- **Every zone element unticked at the start.** Rejected: ADR 0113's rule,
  but the owner wrote this scope.
- **One Control per element.** Rejected: ADR 0116's one-to-one map refuses a
  WAF and a rate limiter on one process.
- **A flow scope.** Rejected: covers transport, but triples the name and alias
  rules for the part of the model that moves most. A Control on a flow is in
  the map's fog.

## Consequences

The corpus treats a supported name as a naming difference and not an error,
and the alias list is that seam. A Control whose scope names a flow cannot be
written until the fog clears.
