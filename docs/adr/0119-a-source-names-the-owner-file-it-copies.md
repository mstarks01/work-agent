# 119. A Source names the Owner File it copies

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2060](https://github.com/mstarks01/work-agent/issues/2060), on the
  Owner File map [#2057](https://github.com/mstarks01/work-agent/issues/2057)
- **Relates to**: [ADR 0001](0001-sources-replace-description.md), which sets
  the Source; [ADR 0118](0118-an-owner-file-is-an-object-and-a-row.md), which
  sets the store of an **Owner File**

## Context

A job that uses an Owner File keeps its own copy of the text as a **Source**.
The Source must record which Owner File it came from. A Source already has a
SHA-256: `Source.digest()` computes it from the text, and each report holds it
in `SourceRef.sha256`. The copy is exact, so the Owner File's SHA-256 always
equals the digest of the copied text.

`POST /analyze` and `POST /v1/jobs` take the same `sources` field, and
`JobAnswers.submit` is the one reader of a Submission for both routes.

## Decision

**A Source gets one field, `owner_file_id`.** Its type is `str | None`, with
the pattern `file-[0-9a-f]{32}`. `None` means that the Source did not come
from an Owner File. The SHA-256 is the value that `Source.digest()` computes,
so no field holds a second copy of it.

**The report does not name the Owner File.** `SourceRef` keeps `kind`,
`label` and `sha256`, and the report page does not change. The label already
shows the Owner File's name.

**`SCHEMA_VERSION` stays `"3.2"`.** The report does not change. A 3.2 record
that has no `owner_file_id` parses with `None`, and that value is correct,
because no Owner File existed when the record was written.

**The server reads the text, and the browser sends only the IDs.**
`Submission` gets `owner_files`, a list of file IDs, and the `/analyze` body
gets the same key. `JobAnswers.submit` reads each Owner File through the store
with the owner ID. The store checks the owner on the row and checks the digest
of the object. The reader then makes a Source from the row's kind, the row's
name as the label, the object's text and the file ID. These Sources go after
the body's Sources, and then the label and byte limit checks run on the full
list. A missing ID and an ID of another owner get the same refusal.

**One rule refuses a caller's `owner_file_id`.** `refuse_owner_file_sources`
stands beside `refuse_answers_source`, and `JobAnswers.submit` calls it on the
body's Sources before it adds the Owner File Sources. The refusal is a
`Refused`, so `/v1` returns `400`. `JobSubmission` gets no `owner_files` key,
so a `/v1` body that sends it gets `422`, as for each other unknown key.

**A Resumed Job and an Amendment copy the parent's Sources without a change.**
Each keeps `owner_file_id` and the copied text, and neither reads the store
again. A follow-up job with `answers_from` carries no Sources, so an Owner
File that the owner selects again gives the current text. When the web app
opens an analysis again, the payload does not join an Owner File Source's
text into `description`. It lists the ID, the label and the kind of each of
those Sources under `owner_files`, and the read-only form shows the label and
the kind of each one under "Files used". The list comes from the job's own
Sources, so it stays correct after the owner deletes or replaces the Owner
File.

## Considered options

- **A second field for the SHA-256.** Rejected: it stores a value that code
  computes from the text, and a validator must then check that the two agree.
- **`owner_file_id` in `SourceRef`.** Rejected: the ID has no meaning to a
  reader of a shared report, it points to nothing after a delete, and `/v1`
  reports would hold a field that only the web app fills.
- **A bump to 3.3.** Rejected: each 3.2 job would then get `409` on the
  answers, amend and `answers_from` routes, and no 3.2 record needs it.
- **The browser sends the text, and the server compares the digests.**
  Rejected: the client then supplies a text that the server must check, and
  one store read already gives the text and checks its digest.
- **A separate wire model of a Source without `owner_file_id`.** Rejected:
  then two Source models exist, and `_Submission` can no longer use the
  `/v1` field. The `answers` kind already uses one model and one refusal rule.

## Consequences

A delete or a new upload of an Owner File changes no job, no Resumed Job and
no Amendment. A Submission that names a deleted Owner File is refused. `/v1`
can get `owner_files` later as a thin route, because the reader lives in
`analysis_service`. An analysis that opens again shows the name that each
Owner File had when its job ran, which can differ from the current name in
Files.
