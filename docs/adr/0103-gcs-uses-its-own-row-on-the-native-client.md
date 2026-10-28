# 103. GCS uses its own row on the native client

- **Status**: accepted
- **Date**: 2026-10-28
- **Map**: [#1991](https://github.com/mstarks01/work-agent/issues/1991), ticket
  [#1993](https://github.com/mstarks01/work-agent/issues/1993)

## Context

The `s3` object store writes each object with `PutObject` and
`If-None-Match: *`, so a put never replaces an object (ADR 0065). The GCS XML
API accepts `If-None-Match` only on a read. So the `s3` row cannot make a
create-only write on GCS, and the store does not stop the write: the put can
replace the object with no error.

The XML API has its own create-only header, `x-goog-if-generation-match: 0`.
A `before-sign` handler can add it to a `boto3` request. That route adds no
package, but it needs a credential that `boto3` does not support on GCS
(HMAC keys, which are a static secret, or a bearer token that our own code
refreshes). It also needs a GCS branch in the startup check, because
`GetBucketVersioning` does not show soft delete. No live test confirms the
header or the bearer token, and `fake-gcs-server` ignores preconditions on the
XML route.

## Decision

**GCS is its own row, `gcs`, on the `google-cloud-storage` client. The `s3`
row does not serve GCS.**

- The put is `upload_from_string` with `if_generation_match=0`, and GCS
  documents the 412 on a conflict. The emulator checks this precondition on
  the JSON route, so an offline test covers the create-only rule.
- The client is synchronous, so each call runs in `asyncio.to_thread`, as in
  the `s3` row.
- The row always passes `api_endpoint`, so `STORAGE_EMULATOR_HOST` and
  `API_ENDPOINT_OVERRIDE` cannot redirect it. It reads no endpoint setting.
- The row reads the shared `ANALYSIS_OBJECT_BUCKET`, with its own bucket
  shape, and the shared `ANALYSIS_OBJECT_CREDENTIALS`.
- The client is in an extra named `gcs`.
- The `s3` row refuses an endpoint host that does not accept `If-None-Match`
  on a write, from one table. The table holds the GCS hosts and the Backblaze
  B2 hosts.

## Consequences

The service carries a second object-store client, about eight packages, in an
extra that only a `gcs` deployment installs. A GCS deployment does not use
HMAC keys.
