# 95. A stored part that does not parse makes a job unreadable

- **Status**: accepted
- **Date**: 2026-10-16
- **Amends**: [ADR 0065](0065-a-job-record-splits-into-rows-and-two-objects.md),
  on which stored records a route can read
- **Evidence**: the tests in this ADR's pull request

## Context

A job row stores values from closed sets: the code of each validation issue,
the status, the kind of each event, and the fields of the certification. The
schema version does not cover these values, because the status route and the
report gate read them for a record of any version (ADR 0065). So a change to
one of these sets does not change the schema version.

When a closed set loses a member, a stored row can hold a value that the set
no longer lists. The envelope read then raised a `ValidationError`. One such
row stopped the run list, the status route and the report route with a `500`,
and a row with such a status stopped the restart scan at startup.

## Decision

**A stored job whose envelope does not parse is unreadable, as a job of
another schema version is**, by the maintainer's ruling 2026-10-16.

- The store returns an `UnreadableRecord` in place of the envelope. It holds
  the columns that parse: the ID, the owner ID, the schema version, the name,
  the times, and the status where the status parses.
- `readable` is the one rule of a readable record. It refuses a record of
  another schema version and an `UnreadableRecord`. Each route that parses a
  stored record asks it, and refuses the job with `UnreadableSchema` (`409`).
- A part that a route reads by name after `readable`, such as the sources,
  the report or the checkpoint, is unreadable in the same way where it does
  not parse.
- The run list and `GET /v1/jobs/{id}` show the job with the status
  `unreadable`. Each page of the job returns `409`. One row never stops a
  list.
- A certification that does not parse makes the job unreadable, so the
  report route returns `409` and never serves the report.
- No value becomes another member of its set, and no migration rewrites the
  row. The restart scan leaves an unreadable job as stored and logs it.

## Consequences

A change to a closed set that a row stores needs no version change to keep
the list and the status route working. A job that holds a removed value
stays unreadable, and its owner submits it again.

`DELETE /v1/jobs/{id}` reads the status where it parses, so the owner can
delete an unreadable job in a terminal status. Where the status does not
parse, the delete returns `409`. The retention sweep also reads the status,
so it keeps such a job.

An unreadable job that is queued or running keeps its status. Admission
counts it against its owner's in-flight jobs until a person removes the row.
