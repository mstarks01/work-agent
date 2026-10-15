# 90. A correction names the revision it read

- **Status**: accepted
- **Date**: 2026-10-15
- **Amends**: [ADR 0070](0070-a-follow-up-saves-a-draft-in-batches.md), whose
  revision rule covers a save and a draft
- **Relates to**: [ADR 0054](0054-a-report-offers-one-follow-up.md), under
  which a final report takes corrections
- **Evidence**: `tests/test_job_store.py`
  (`test_two_corrections_that_read_one_revision_land_once`),
  `tests/test_corrections.py` and `tests/test_webapp_questions.py`

## Context

A final report takes corrections (ADR 0054). A correction reads the
corrections that the job holds, merges the new answers onto them, and writes
the whole list back. The write did not check what the correction read.

Two corrections that read one state both got `200`. The second write held only
what the second correction read, so the first correction was lost. An offline
run on 2026-10-15 reproduced this: one correction changed
`encryption_in_transit`, another changed `authentication`, both saves
returned `True`, and the job held only the `authentication` correction.

ADR 0070 gives a save and a draft a revision check, but not a correction.

## Decision

**A correction names the revision it read.** `POST
/v1/jobs/{id}/corrections` and the web app's `/correct/{run}` take a
required `revision`. The answer state refuses a missing revision with `400`
and an earlier one with `409`.

**The store checks the revision and writes the next one in one step**, as it
does for a save and a draft. Both job stores keep this contract. Of two
corrections that read one revision, only the first lands, and the second gets
`409`.

**The reply names the next revision.** The API returns `revision` with the
corrections. The report page reads the revision from its `corrections`
payload, and it reloads after each correction.

## Consequences

A client that sends a correction must send the `revision` of the questions it
read. A request without one gets `422` from the API. This is a hard change to
the request contract with no default, because a default would admit the lost
update that this ADR removes.

A correction moves the revision of a final report. A final report takes no
draft and no follow-up, so no other save of that job reads the revision.
