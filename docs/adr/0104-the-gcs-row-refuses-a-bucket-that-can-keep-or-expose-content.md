# 104. The gcs row refuses a bucket that can keep or expose content

- **Status**: accepted
- **Date**: 2026-10-28
- **Map**: [#1991](https://github.com/mstarks01/work-agent/issues/1991), ticket
  [#1994](https://github.com/mstarks01/work-agent/issues/1994)
- **Relates to**: [ADR 0066](0066-a-deleted-job-leaves-a-usage-row.md), whose
  delete removes all content, and
  [ADR 0103](0103-gcs-uses-its-own-row-on-the-native-client.md)

## Context

A delete of a job removes its content (ADR 0066). On GCS, five bucket settings
can stop that. Versioning keeps a deleted object as a noncurrent version. Soft
delete keeps it, restorable, for 7 to 90 days, and GCS turns soft delete on for
7 days on each new bucket. A retention policy, Bucket Lock, a default
event-based hold and Object Retention Lock make a delete fail.

The sources and the report of a job are sensitive. A bucket with fine-grained
ACLs gives each new object the bucket's default object ACL, and the service
sets no ACL of its own.

One `buckets.get` call returns each of these fields. The call needs
`storage.buckets.get`, and `roles/storage.objectUser` does not hold it. The
`s3` row reads only versioning, because some S3 stores do not answer a call
for the other settings.

## Decision

**At the startup, the `gcs` row reads the bucket with `buckets.get` and stops
the startup on each of these states:**

- `versioning.enabled` is true.
- `softDeletePolicy.retentionDurationSeconds` is more than 0.
- `retentionPolicy` has a period. This includes a locked policy.
- `defaultEventBasedHold` is true.
- `objectRetention.mode` is `Enabled`.
- `iamConfiguration.publicAccessPrevention` is not `enforced`.
- `iamConfiguration.uniformBucketLevelAccess.enabled` is not true.

A refused `buckets.get` stops the startup, and the message names
`storage.buckets.get` and `roles/storage.bucketViewer`.

The row does not read per-object holds or retain-until times. A delete that
fails goes to the sweep. The row does not read lifecycle rules, because a rule
can remove content early but cannot keep it.

## Considered options

- **Accept soft delete and document the window.** Rejected: a deleted job's
  content stays restorable, against ADR 0066.
- **List all versions at the startup**, to find a bucket that had versioning
  on in the past. Rejected: the list reads the whole bucket at each startup.
  The docs tell the operator to use a bucket that never had versioning on, as
  the `s3` docs do.
- **Document public access only, as the `s3` row does.** Rejected: GCS returns
  both access fields in the same call, so the check costs no extra request.

## Consequences

A new bucket fails the startup until the operator sets its soft delete
duration to 0. An organization policy that requires soft delete stops the row
in that organization. The service identity needs a bucket role beside the
object role. A setting that changes after the startup is not seen until the
next startup.
