# 95. A stored part that does not parse makes a job unreadable

- **Status**: accepted
- **Date**: 2026-10-16
- **Amends**: [ADR 0065](0065-a-job-record-splits-into-rows-and-two-objects.md),
  on which stored records a route can read
- **Evidence**: the tests in this ADR's pull request, and in the pull
  request that states that an unreadable job has ended

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

**An unreadable job has ended, whatever its stored status**, by the
maintainer's ruling 2026-10-16. `ended` is the one reader of "has this job
ended", and it asks `readable`.

- Admission does not count an unreadable job in flight. Its charge still
  counts against the token budgets.
- Its owner can delete it. The owner check reads the `owner_id` column, so
  another owner's delete answers as for a job that does not exist.
- The retention sweep deletes it, as it deletes any job that has ended.
- No stored status changes. The usage row of a deleted job keeps the stored
  status, and a usage row holds no in-flight slot, because a store deletes
  only a job that has ended.
- `locks_its_parent` asks `ended`. A resumed job locks its parent while it
  has not ended, and after it ended in a terminal status that read its
  answers into a report. So an unreadable child locks its parent only where
  its stored status is `completed` or `awaiting-answers`, and the usage row
  of a deleted child follows the same rule.

## Consequences

A change to a closed set that a row stores needs no version change to keep
the list and the status route working. A job that holds a removed value
stays unreadable, and its owner submits it again.

An unreadable job holds no in-flight slot, and its owner or the retention
sweep can delete it, whatever its stored status. Its parent takes answers
again unless its stored status is terminal and read its answers into a
report. A job of another schema
version is unreadable too, so a job that another version runs in a shared
database is ended for this service. The restart scan still fails a queued or
running job of another schema version, because it can read that job's
envelope.
