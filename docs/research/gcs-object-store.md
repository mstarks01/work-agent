# GCS facts for a create-only object store (#1992)

**Question.** Which facts decide the design of an `ANALYSIS_OBJECT_STORE=gcs`
row? The row must keep the `ObjectStore` protocol in
`src/analysis_service/objects.py`: a create-only `put`, a `get`, a `delete`
that returns False for a missing key, a `list` with the last-write time in UTC,
and a startup `check`. ADR 0066 needs a delete to remove the content.

**Date and versions.** 2026-10-28. Repository `fe5c9af3`.
`google-cloud-storage==3.17.1`, `google-auth==2.56.0` (the version in
`uv.lock`), `google-api-core==2.43.0`, `google-cloud-core==2.8.0`,
`botocore==1.43.89`, `litellm==1.104.0`, `fake-gcs-server` v1.56.1,
`obstore==0.12.1`. The Google Cloud docs moved to `docs.cloud.google.com`; each
link below is the new address.

**Method.** The library facts come from the wheel source of each version above.
Each link points to the same file at the same tag. The cloud facts come from
the Google Cloud docs. No call went to a live bucket. The paid budget was $0,
and no GCS credential exists on this machine. So each fact about live GCS
behaviour comes from the docs only, and the doc marks it as such.

This file states facts. It does not choose a design for #1993-#1996.

Source abbreviations:

- `GCS` = <https://github.com/googleapis/google-cloud-python/blob/google-cloud-storage-v3.17.1/packages/google-cloud-storage/google/cloud/storage/>
- `AUTH` = <https://github.com/googleapis/google-cloud-python/blob/google-auth-v2.56.0/packages/google-auth/google/auth/>
- `CORE` = <https://github.com/googleapis/google-cloud-python/blob/google-api-core-v2.43.0/packages/google-api-core/google/api_core/>
- `FAKE` = <https://github.com/fsouza/fake-gcs-server/blob/v1.56.1/>

## 1. The native `google-cloud-storage` client

**The client is synchronous. A row calls it through `asyncio.to_thread`, as the
`s3` row calls `boto3`.** The `Client` sends each request through a
`requests` session (`GCS/client.py`, the `_http` parameter, lines 93-96). The
package has an `asyncio` module, but that module is a gRPC client
(`GCS/asyncio/async_grpc_client.py`). It needs the `grpc` extra (`grpcio`,
`grpcio-status`, `grpc-google-iam-v1`; `METADATA` lines 31-39). Its
`AsyncGrpcClient` has only `get_object` and `delete_object` (lines 164 and
234). Its write path is `AsyncAppendableObjectWriter`, which writes an
"Appendable Object" (`GCS/asyncio/async_appendable_object_writer.py`, line
104). The module has no list call. The third-party `gcloud-aio-storage` 9.6.5
(MIT) is an async client
(<https://pypi.org/project/gcloud-aio-storage/9.6.5/>). Its precondition
support is **unconfirmed**.

**Create-only write.** `Blob.upload_from_string(data, if_generation_match=0)`
sends `ifGenerationMatch=0` (`GCS/blob.py`, line 3235). GCS runs the request
only where no live object has the name
(<https://docs.cloud.google.com/storage/docs/request-preconditions>). The doc
also says that the value `0` is valid only for a write of object data.

**The conflict error.** GCS answers `412 Precondition Failed` (same page). The
upload path catches `InvalidResponse` and calls `_raise_from_invalid_response`
(`GCS/blob.py`, lines 2874-2875 and 5219-5240). That function raises
`google.api_core.exceptions.from_http_status(...)`, and 412 maps to
`google.api_core.exceptions.PreconditionFailed` (`CORE/exceptions.py`, line
352).

**Retry of the write.** `upload_from_string` uses `retry=DEFAULT_RETRY` by
default (`GCS/blob.py`, line 3247). `DEFAULT_RETRY` retries 429, 500, 502, 503,
504 and connection errors (`GCS/retry.py`, lines 31-41 and 75). If a first
attempt succeeds and its response is lost, the retry gets 412. So a `put` can
raise `PreconditionFailed` for its own write. The `s3` row has the same
property under `If-None-Match: *` and the `botocore` retries.

**A delete that reports a missing object.** `Bucket.delete_blob` and
`Blob.delete` raise `google.cloud.exceptions.NotFound` for a missing object
(`GCS/bucket.py`, line 1768 and its docstring; `GCS/blob.py`, lines 788 and
846-848). `NotFound` is `google.api_core.exceptions.NotFound`, HTTP 404
(`CORE/exceptions.py`, line 314). So one call answers both "removed" and
"missing". The `s3` row needs `HeadObject` first, because `DeleteObject`
succeeds on a missing key.

**A get that reports a missing object.** `Blob.download_as_bytes` raises
`NotFound` (`GCS/blob.py`, line 1443, and its docstring at line 1547). The
download path calls `_raise_from_invalid_response` too (lines 4731-4732).

**A list with the last-write time.** `Client.list_blobs(bucket)` pages through
the objects (`GCS/client.py`, line 1362). It returns live objects only: soft
deleted objects need `soft_deleted=True`, and noncurrent versions need
`versions=True`. Each `Blob` has `time_created` and `updated`
(`GCS/blob.py`, lines 4996 and 5011). Both parse through
`_rfc3339_nanos_to_datetime`, which returns a datetime with `tzinfo=UTC`
(<https://github.com/googleapis/google-cloud-python/blob/google-cloud-core-v2.8.0/packages/google-cloud-core/google/cloud/_helpers/__init__.py>,
lines 273-305). The JSON API defines `timeCreated` as the creation time of the
object, which "doesn't change". It defines `updated` as the last change to the
object *metadata* (<https://docs.cloud.google.com/storage/docs/json_api/v1/objects>).
A create-only object has one write, so `timeCreated` is the time of that write.

**An endpoint from the environment.** The `Client` reads the endpoint in this
order: `client_options.api_endpoint`, then `STORAGE_EMULATOR_HOST`, then
`API_ENDPOINT_OVERRIDE` (`GCS/client.py`, lines 191-247;
`GCS/_helpers.py`, lines 49 and 96-110). Where `STORAGE_EMULATOR_HOST` is set
and no credential is passed, the client uses `AnonymousCredentials`
(`GCS/client.py`, lines 252-256). An explicit `api_endpoint` wins over both
variables. This is the same class of fact as `AWS_ENDPOINT_URL`, which the
`s3` row turns off with `ignore_configured_endpoint_urls=True`.

## 2. The S3-interop route

**GCS does not accept `If-None-Match` or `If-Match` on a write.** The XML API
reference lists both headers as valid only on `GET Object` and `HEAD Object`
(<https://docs.cloud.google.com/storage/docs/xml-api/reference-headers>). The
preconditions page says the same
(<https://docs.cloud.google.com/storage/docs/request-preconditions>). So the
current `s3` row, which sends `IfNoneMatch="*"`, cannot make a create-only
write on GCS.

**GCS documents `x-goog-if-generation-match: 0` as a common XML header.** The
reference says the request runs only where the object does not exist, and a
mismatch gives 412 (same reference page). The `PUT Object` page does not list
the header, but it points to the common headers
(<https://docs.cloud.google.com/storage/docs/xml-api/put-object-upload>).

**`boto3` can add the header before the signature.** `botocore` emits
`before-sign.<service>.<operation>` before it signs a request
(<https://github.com/boto/botocore/blob/1.43.89/botocore/signers.py>, lines 43
and 158). The boto3 guide documents `before-call`, whose handler can add a
header to `params['headers']`, with an example
(<https://docs.aws.amazon.com/boto3/latest/guide/events.html>). A header that
a handler adds at either event goes into the signature. **Unconfirmed:**
whether live GCS honours the header on a `PutObject` that `boto3` signs. No
live test ran.

**The XML API accepts an OAuth 2.0 token.** The `Authorization` header takes
`Bearer` and an OAuth 2.0 token, an HMAC form (`GOOG1` or `AWS`), or a V4
signature (<https://docs.cloud.google.com/storage/docs/xml-api/reference-headers>;
<https://docs.cloud.google.com/storage/docs/authentication>). The
interoperability page describes S3 tools with HMAC keys only
(<https://docs.cloud.google.com/storage/docs/interoperability>).

**`boto3` has no S3 path that sends a bearer token.** A client with
`signature_version=botocore.UNSIGNED` signs nothing
(`botocore/signers.py`, lines 167, 209 and 229). A `before-send` handler could
then set `Authorization: Bearer <token>`. That handler must also fetch and
refresh the token through `google-auth`. **Unconfirmed:** that GCS accepts
such a request from `boto3`, and how other `botocore` headers (for example the
default checksum headers) behave on GCS. No live test ran.

## 3. Credentials

**`google.auth.default()` has no parameter that limits its chain.** Its
signature is `default(scopes, request, quota_project_id, default_scopes)`
(`AUTH/_default.py`, line 593). It tries four sources in order
(lines 702-711):

1. `GOOGLE_APPLICATION_CREDENTIALS`. The file can hold the type
   `authorized_user`, `service_account`, `external_account`,
   `external_account_authorized_user`, `impersonated_service_account` or
   `gdch_service_account` (lines 38-50 and 251-292).
2. The gcloud application-default file from
   `gcloud auth application-default login`: a user credential.
3. The App Engine first-generation identity.
4. The metadata server, through `_get_gce_credentials` (line 390).

The ADC page gives the same order
(<https://docs.cloud.google.com/docs/authentication/application-default-credentials>).

**So a row limits the chain by the type of the credential that `default()`
returns.** Each source returns its own class:

| Source | Class |
| --- | --- |
| Metadata server (Cloud Run, GCE, GKE) | `google.auth.compute_engine.credentials.Credentials` (`AUTH/compute_engine/credentials.py`, line 43) |
| Workload Identity Federation file | a subclass of `google.auth.external_account.Credentials` (`AUTH/external_account.py`, line 90): `identity_pool`, `aws` or `pluggable` |
| User credential (gcloud ADC file or an `authorized_user` file) | `google.oauth2.credentials.Credentials` |
| Service account key file | `google.oauth2.service_account.Credentials` |
| Impersonation file | `google.auth.impersonated_credentials.Credentials` |
| Workforce user file | `google.auth.external_account_authorized_user.Credentials` |

A check that accepts only the first two classes refuses a user credential and
a key file. The type check must run on the result, because
`GOOGLE_APPLICATION_CREDENTIALS` comes first and wins on Cloud Run too.

**GKE Workload Identity Federation reaches the client as a metadata-server
credential.** The pod asks the Compute Engine metadata address, and the GKE
metadata server answers in its place
(<https://docs.cloud.google.com/kubernetes-engine/docs/concepts/workload-identity>).
So `default()` returns the `compute_engine` class there too, through source 4.

**The storage client calls `default()` itself where it gets no credential.**
`storage.Client(credentials=None)` passes `None` to `ClientWithProject`, which
calls `google.auth.default(scopes=scopes)`
(<https://github.com/googleapis/google-cloud-python/blob/google-cloud-core-v2.8.0/packages/google-cloud-core/google/cloud/client/__init__.py>,
line 218). So a row that checks the type must resolve the credential first
and pass it to `storage.Client(credentials=...)`.

**The Vertex row reads ADC with no type check.** The row passes a project and
a location and no credential (`src/analysis_service/vendors.py`, lines
1341-1384). The comment at lines 1354-1363 states that `google.auth.default()`
resolves any identity, including an operator's own
`GOOGLE_APPLICATION_CREDENTIALS`. In `litellm==1.104.0`,
`VertexBase.load_auth` takes the default path when it gets no credential, and
`_credentials_from_default_auth` calls `google_auth.default(scopes=scopes)`
(`litellm/llms/vertex_ai/vertex_llm_base.py`, lines 123, 210 and 276-282, in
the 1.104.0 wheel). No code in `src/` imports `google.auth`. So the Vertex row
accepts a user credential and a key file now. The `s3` row's `iam` mode does
not: `aws_identity.platform_session` accepts only `container-role`, `iam-role`
and `assume-role-with-web-identity`.

## 4. Bucket states that keep a deleted object

Each bucket field below comes from `buckets.get`, which needs
`storage.buckets.get`. Each object field comes from `objects.get` or
`objects.list`, which need `storage.objects.get` or `storage.objects.list`.

| State | What a delete does | Field | Client property | Read call and permission |
| --- | --- | --- | --- | --- |
| Soft delete | The object becomes soft-deleted and stays for the retention duration. Only list and restore are possible in that time. | `softDeletePolicy.retentionDurationSeconds` | `Bucket.soft_delete_policy.retention_duration_seconds` (`GCS/bucket.py`, lines 2725 and 4340) | `buckets.get`, `storage.buckets.get` |
| Object versioning | The live object becomes a noncurrent version and stays. | `versioning.enabled` | `Bucket.versioning_enabled` (line 3143) | `buckets.get`, `storage.buckets.get` |
| Retention policy | A delete of an object younger than the period fails with `403 retentionPolicyNotMet`. | `retentionPolicy.retentionPeriod` | `Bucket.retention_period` (line 3024) | `buckets.get`, `storage.buckets.get` |
| Bucket Lock | A locked policy cannot be removed or made shorter. | `retentionPolicy.isLocked` | `Bucket.retention_policy_locked` (line 3011) | `buckets.get`, `storage.buckets.get` |
| Default event-based hold | Each new object gets an event-based hold. | `defaultEventBasedHold` | `Bucket.default_event_based_hold` (line 2579) | `buckets.get`, `storage.buckets.get` |
| Event-based or temporary hold | The object cannot be deleted or replaced while the hold applies. | object `eventBasedHold`, `temporaryHold` | `Blob.event_based_hold`, `Blob.temporary_hold` (`GCS/blob.py`, lines 4763 and 4969) | `objects.get`, `storage.objects.get` |
| Object Retention Lock | An object cannot be deleted or replaced before its retain-until time. The bucket feature cannot be turned off. | bucket `objectRetention.mode`; object `retention.retainUntilTime` | `Bucket.object_retention_mode` (line 3283); `Blob.retention` (`GCS/blob.py`, line 5061) | `buckets.get` and `objects.get` |

Sources:
<https://docs.cloud.google.com/storage/docs/json_api/v1/buckets>,
<https://docs.cloud.google.com/storage/docs/json_api/v1/objects>,
<https://docs.cloud.google.com/storage/docs/soft-delete>,
<https://docs.cloud.google.com/storage/docs/use-soft-delete>,
<https://docs.cloud.google.com/storage/docs/object-versioning>,
<https://docs.cloud.google.com/storage/docs/bucket-lock>,
<https://docs.cloud.google.com/storage/docs/object-holds>,
<https://docs.cloud.google.com/storage/docs/object-lock>.

**Soft delete is on by default.** GCS sets a soft delete policy on each new
bucket, with a retention duration of 7 days. Zonal Rapid buckets are the
exception. A duration of 0 turns soft delete off. Other values go from 7 to 90
days. A soft-deleted object costs storage until its window ends. Lifecycle
rules do not act on soft-deleted objects
(<https://docs.cloud.google.com/storage/docs/soft-delete>).

**A policy change does not release objects that are soft-deleted already.**
They keep the duration that applied when the delete happened (same page). So a
bucket that had soft delete keeps older deletes after the operator sets 0.
This is the same shape as `Suspended` versioning in the `s3` row.

**Versioning and soft delete add up.** With versioning on, a delete makes the
live object noncurrent. A delete of the noncurrent version then makes it
soft-deleted
(<https://docs.cloud.google.com/storage/docs/object-versioning>). A suspended
versioning setting keeps the versions that exist.

**A hold and a retention date are per object.** The bucket fields do not show
them, except `defaultEventBasedHold`. The XML API does not return the hold
state with object metadata
(<https://docs.cloud.google.com/storage/docs/object-holds>).

**The object roles do not hold `storage.buckets.get`.**
`roles/storage.objectUser` and `roles/storage.objectAdmin` hold
`storage.objects.create`, `.delete`, `.get` and `.list`, and not
`storage.buckets.get`. `roles/storage.bucketViewer`,
`roles/storage.legacyBucketReader`, `roles/storage.legacyBucketWriter` and
`roles/storage.admin` hold it
(<https://docs.cloud.google.com/storage/docs/access-control/iam-roles>). So a
startup check that reads the bucket fields needs a second role or a custom
role beside the object role.

## 5. Emulators

**`fake-gcs-server` honours `ifGenerationMatch` on the JSON API.**
`wrapUploadPreconditions` reads `ifGenerationMatch` and
`ifGenerationNotMatch` from the query (`FAKE/fakestorage/upload.go`, lines
417-443). The simple, multipart and resumable uploads pass the condition to
`createObject` (lines 466-486, 609-640 and 677-804). The memory backend checks
the condition under its lock and returns `PreConditionFailed`
(`FAKE/internal/backend/memory.go`, lines 258-270). The filesystem backend does
the same (`FAKE/internal/backend/fs.go`, lines 237-262). The JSON response maps
`PreConditionFailed` to HTTP 412 (`FAKE/fakestorage/json_response.go`, lines
108-109). The XML-style signed upload passes `backend.NoConditions{}`
(`FAKE/fakestorage/upload.go`, line 537). So the emulator does not test the
S3-interop route.

**`fake-gcs-server` has no soft delete.** A GitHub code search of the default
branch finds no `softDeleted` and no `softDeletePolicy`. It finds
`eventBasedHold` in the bucket and backend code. **Unconfirmed:** whether the
emulator returns `softDeletePolicy` or `retentionPolicy` in bucket metadata.
This rests on the absence of a code match, not on a test.

**CI can run it as a service container.** The image entry point is
`/bin/fake-gcs-server -data /data`, and it exposes port 4443
(`FAKE/Dockerfile`). Each flag also reads an environment variable, for example
`FAKE_GCS_SCHEME`, `FAKE_GCS_BACKEND` and `FAKE_GCS_PORT`
(`FAKE/internal/config/config.go`, lines 96-122). The default scheme is
`https` with a self-signed certificate, and the default backend is
`filesystem`. A GitHub Actions service accepts `env`, `ports`, `options`,
`command` and `entrypoint`
(<https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax>).
So `env` alone can set the scheme and the backend. The release is v1.56.1 of
2026-08-24, BSD-2-Clause, and the repository is active.

**The storage client finds the emulator through `STORAGE_EMULATOR_HOST` or an
explicit `api_endpoint`** (see question 1).

## 6. The dependency cost

**`google-cloud-storage` adds eight packages and changes no locked version.**
A scratch copy of `pyproject.toml` and `uv.lock` took
`uv add --optional gcs google-cloud-storage` with uv 0.11.29. The lock gained:

| Package | Version | Wheel size (bytes) |
| --- | --- | --- |
| `google-cloud-storage` | 3.17.1 | 345,706 |
| `google-api-core` | 2.43.0 | 223,488 |
| `google-cloud-core` | 2.8.0 | 31,048 |
| `google-resumable-media` | 2.11.0 | 81,515 |
| `google-crc32c` | 1.9.0 | 36,350 (cp313, manylinux x86_64) |
| `googleapis-common-protos` | 1.76.0 | 307,707 |
| `proto-plus` | 1.29.0 | 50,786 |
| `protobuf` | 7.36.2 | 343,223 (abi3, manylinux x86_64) |

The sum is about 1.4 MB of wheels. The lock already holds `google-auth` 2.56.0
and `requests` 2.34.2 through `google-adk` and `google-genai`.

**No pin conflicts.** The requirements come from PyPI metadata
(<https://pypi.org/pypi/google-cloud-storage/3.17.1/json>,
<https://pypi.org/pypi/google-api-core/2.43.0/json>):

- `google-cloud-storage` needs `google-auth>=2.26.1,<3` and `requests>=2.22.0,<3`.
- `google-api-core` needs `requests>=2.33.0,<3`, `protobuf>=6.33.5,<8` and
  `opentelemetry-api>=1.44.0,<2`. The `override-dependencies` entry
  `opentelemetry-api==1.45.1` in `pyproject.toml` meets the last one.
- Each package needs Python 3.10 or later. The project needs 3.11 or later.

The `grpc` extra, which the async client needs, adds `grpcio`,
`grpcio-status` and `grpc-google-iam-v1`. That extra was not resolved.

For comparison, `obstore==0.12.1` adds one package. Its dependency is
`typing-extensions` on Python before 3.13, and its manylinux x86_64 wheel is
5,591,012 bytes. It resolved in the same scratch lock with no change to a
locked version.

## 7. `obstore`

**The reason for `boto3` was maturity.** The amendment on #1523 says: the
rejected alternative was `obstore`, and the owner chose "the mature, widely
used client"
(<https://github.com/mstarks01/work-agent/issues/1523>). The same amendment
defers native GCS and Azure backends to a separate effort.

**`obstore` is still before 1.0.** #1524 read 0.11.1. Since then, 0.12.0
(2026-10-15) and 0.12.1 (2026-10-27) came out
(<https://github.com/developmentseed/obstore/releases>). 0.12.0 dropped
Python 3.10 and moved to pyo3 0.30. 0.12.1 keeps the `version` field in `get`
and `head` metadata. The PyPI classifier is still `Development Status :: 4 -
Beta` (<https://pypi.org/pypi/obstore/json>). The repository has 829 stars, is
MIT and is active. So the maturity fact behind the ruling has not changed.

**`obstore` has a create-only put.** `PutMode` is `"create"`, `"overwrite"`
or an `UpdateVersion`
(<https://github.com/developmentseed/obstore/blob/py-v0.12.1/obstore/python/obstore/_put.pyi>,
lines 40-60). **Unconfirmed:** the request that `"create"` sends to GCS. The
Rust `object_store` source was not read.

**One fact changed after the ruling: `boto3` cannot reach GCS create-only
now.** The ruling expected `boto3` to reach other stores "that speak the S3
API". The GCS XML API refuses `If-None-Match` on a write (question 2). So a GCS
row needs a second client in any case: `google-cloud-storage`, `obstore`, or
`boto3` with an extra header and an unconfirmed live result. This fact does not
reverse the maturity reason. It changes the cost of each side: the native
Google client adds eight packages beside the `boto3` that the `aws` extra
already holds.

## What bears on each ticket

- **#1993-#1996 in general.** The native client is synchronous, raises
  `PreconditionFailed` and `NotFound`, and lists `time_created` in UTC.
- **A credential ticket.** `google.auth.default()` cannot be limited by a
  parameter. A type check on its result can accept the metadata-server class
  and the external-account classes only. The Vertex row has no such check now.
- **A startup-check ticket.** Soft delete is on by default for 7 days. The
  check reads it with `storage.buckets.get`, which the object roles do not
  hold. Holds and object retention are per object.
- **An endpoint rule.** `STORAGE_EMULATOR_HOST` and `API_ENDPOINT_OVERRIDE`
  redirect the client unless the row passes `api_endpoint`.
- **A CI ticket.** `fake-gcs-server` tests the create-only JSON write and runs
  as a service container. It does not test soft delete or the XML route.

## Unconfirmed

- Live GCS behaviour of `x-goog-if-generation-match: 0` on a `boto3`
  `PutObject`, and of a bearer token on a `boto3` request.
- How the default `botocore` checksum headers behave on GCS.
- Precondition support in `gcloud-aio-storage`.
- The GCS request that `obstore` sends for `mode="create"`.
- Soft delete and retention fields in `fake-gcs-server` bucket metadata.
