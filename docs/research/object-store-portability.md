# Object store features and the Python client across providers (#1524)

Read 2026-10-06 against vendor documentation, PyPI metadata and `uv pip
compile` runs beside `litellm==1.104.0`, repo `289efea3`. Nothing here called a
storage provider, so no claim below is a live probe. Map #1522; the decisions
it builds on are in #1523.

## The question

Which S3-API features does each major object store support, and which Python
client does the service use? The ticket asks four things: the feature matrix,
the Azure Blob gap, the credential modes, and a comparison of async clients.

## The short answer

1. **The service uses no object store client today.** `boto3` arrives only
   because `litellm==1.104.0` requires `boto3>=1.43.1,<2.0` for Bedrock.
2. **"Speaks the S3 API" does not reach two of the six stores cleanly.** Azure
   Blob has no S3 API. GCS speaks S3 only with HMAC keys, which are static.
3. **Conditional writes are not universal.** GCS through its XML API and
   Backblaze B2 do not document `If-None-Match` on a PUT.
4. **Recommendation: use `obstore`.** It has native S3, GCS and Azure backends
   in one async MIT library. It refuses plain HTTP by default. It discovers
   workload identity on all three clouds, so GCS needs no HMAC key and Azure
   needs no gateway.

## 1. Feature matrix

"Default SSE" means that the store encrypts a new object with no request
header and no bucket setting.

| Store | Default SSE | Lifecycle expiry | Versioning / object lock | `If-None-Match` / `If-Match` on PUT |
| --- | --- | --- | --- | --- |
| AWS S3 | Yes, SSE-S3, cannot be disabled [1] | Yes | Yes / Yes | Yes / Yes [2] |
| GCS (XML interop) | Yes, always [3] | Yes, but in a Google XML body, not the S3 body [4] | Yes / Bucket Lock and Object Retention Lock [5] | No / No. Only `x-goog-if-generation-match` works on a PUT [6] |
| Azure Blob | Yes, cannot be disabled [7] | Yes, by prefix [8] | Yes / Yes, WORM policies [9] | Not S3. Native `If-None-Match: *` and `If-Match` on Put Blob [10] |
| Cloudflare R2 | Yes, AES-256 [11] | Yes, S3 body [12] | No versioning / Bucket locks, not S3 Object Lock [13][14] | Yes / Yes [13] |
| Backblaze B2 | No. SSE-B2 is a bucket setting you must enable [15] | Yes, S3 body, versioning on, day-based only [16] | Yes / Yes [17] | Not documented [18]; one third-party report says B2 rejects `If-None-Match` [19] |
| MinIO | Only with a KMS (KES) configured [20] | Yes | Yes / Yes | Yes / Yes [20] |

What the matrix means for the spec:

- **Encryption at rest (#1523 point 7).** Four stores encrypt by default. B2
  and MinIO need a bucket or server setting. The backend row must state that
  setting, and a start-up check can read it.
- **Retention (#1523 point 5).** Every store can expire by prefix. Lifecycle
  does not delete the PostgreSQL rows, so a lifecycle rule alone leaves
  orphan rows. A service-side sweep that the index drives is the portable
  rule. Lifecycle can stay as a backstop.
- **Write-once revisions (#1523 point 6).** The key for each revision comes
  from the service, so one instance never writes a key twice. A conditional
  write is a guard, not the mechanism. The spec must not require it, because
  two stores cannot give it through the S3 API.
- **Hard delete (#1523 point 8).** Some stores keep a deleted object for a
  period. GCS soft delete is on by default for 7 days [21]. S3 and B2 keep
  noncurrent versions when versioning is on. The row must state the recovery
  window, because "hard delete" through the API does not end it.
- **Object lock** conflicts with hard delete. The spec must not require it.

## 2. The Azure Blob gap

Azure Blob does not speak the S3 API. Its own API has every feature the
service needs (rows above).

**A native backend closes the gap at low cost.** `obstore` has an Azure store
in the same package as its S3 and GCS stores [22]. `azure-storage-blob`
12.31.0 (MIT) is the vendor alternative. A native backend is one more row in
the backend table and one more client code path, if the client is not
`obstore`.

**A gateway closes it at a higher cost.** MinIO removed its Azure gateway in
`RELEASE.2022-10-29T06-21-33Z` [23]. S3Proxy (Apache-2.0) still translates S3
to Azure. It needs a Java 17 service beside this one. On Azure it refuses
`If-Match`, keeps only `If-None-Match: *`, and has no versioning, object lock
or lifecycle [24]. So a gateway adds a process to deploy and patch, and it
loses features that the native API has.

## 3. Credential discovery per provider

| Store | Modes with no static secret | Static modes |
| --- | --- | --- |
| AWS S3 | Web identity (IRSA), container credentials, EC2 instance role [25] | Access key pair |
| GCS (XML interop, S3 SDK) | **None.** An S3 SDK signs with SigV4 and needs an HMAC key [26] | HMAC key, long-lived until deleted [27] |
| GCS (native JSON API) | Application default credentials, workload identity, metadata server [22] | Service account key file |
| Azure Blob | Managed identity, workload identity, Azure CLI [22] | Account key, SAS |
| Cloudflare R2 | **None** found. Temporary credentials derive from a parent API token [28] | API-token key pair |
| Backblaze B2 | **None.** The B2 page lists IAM roles as unsupported [17] | Application key |
| MinIO | STS `AssumeRoleWithWebIdentity` with an OIDC token [29] | Access key pair |

**GCS interop does need HMAC keys.** The XML API itself also accepts an OAuth
bearer token [30], but no S3 SDK sends one. An HMAC key is a static secret. A
GCP organisation can block HMAC keys with the `restrictAuthTypes` constraint
[27]. So GCS through S3 conflicts with the map #491 rule, and the conflict is
real. The native GCS backend removes it.

**R2 and B2 have only static modes.** The map #491 rule still holds for them.
A deployment must declare the static mode, and the service must refuse to
start when no mode is declared. Their rows have no default mode.

## 4. Async Python clients

Each candidate was resolved beside `litellm==1.104.0` with `uv pip compile
--python-version 3.12`.

| Client | Version, licence | Resolves beside litellm 1.104.0 | Stores | Endpoint override | Credential discovery | TLS |
| --- | --- | --- | --- | --- | --- | --- |
| `aioboto3` | 15.5.0 (2025-10-30), Apache-2.0 | **No.** It pins `aiobotocore[boto3]==2.25.1`, which needs `boto3<1.40.62`. Unpinned, uv falls back to `aioboto3==7.0.0` from 2020-03-17 | S3 API only | `endpoint_url` | botocore chain [25] | Accepts `http://` |
| `aiobotocore` | 3.9.2 (2026-10-01), Apache-2.0 | Yes, but it pins `botocore>=1.43.101,<1.43.107`, so `boto3` locks to 1.43.106. The lock holds 1.43.89 today | S3 API only | `endpoint_url` | botocore chain | Accepts `http://` |
| `boto3` in a thread | 1.43.108, Apache-2.0 | Yes, already installed | S3 API only | `endpoint_url` | botocore chain | Accepts `http://` |
| `obstore` | 0.11.1 (2026-08-21), MIT | Yes, no botocore pin | S3, GCS native, Azure native, local | `endpoint` [31] | Native per cloud, or a `boto3`, `google-auth` or `azure-identity` provider [22] | `allow_http` is `false` by default [32] |

Notes:

- All three botocore clients send SigV4 only. So on GCS they need HMAC keys,
  and on Azure they need a gateway.
- `boto3` and its async forms take `IfNoneMatch` and `IfMatch` on
  `put_object` [33]. `obstore` takes `mode` on `put` and uses the same headers
  on S3 (`conditional_put="etag"`, the default) [31].
- Since December 2024 the AWS SDKs send a CRC checksum on each upload by
  default (`request_checksum_calculation=WHEN_SUPPORTED`) [34]. A third-party
  store must accept those headers. Test this per store, not per client.
- `obstore` is before 1.0. Pin it exactly, as the repo pins `litellm`.

## Recommendation

Use `obstore` as the one client, with three native backends (S3, GCS, Azure)
and the filesystem for a single host. Each store is a row that states its
default SSE, its conditional-write support, its soft-delete window and its
credential modes.

The counterargument: `boto3` in a thread adds no dependency, and the repo
already carries it. But it cannot reach Azure without a gateway, and it cannot
reach GCS without a static key. Those two gaps break decision 9 of #1523 on
two of the three large clouds. `aiobotocore` has the same gaps and adds a
botocore pin that each `litellm` bump must clear.

Two follow-ups for the spec:

- Change "storage that speaks the S3 API" to "object storage through one
  client with native S3, GCS and Azure backends".
- Do not use MinIO as a production row. Its community repository is archived,
  AGPLv3, and source-only [35]. It can stay a local test target.

## Sources

1. AWS, [Default encryption FAQ](https://docs.aws.amazon.com/AmazonS3/latest/userguide/default-encryption-faq.html)
2. AWS, [Conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
3. Google, [Default encryption](https://docs.cloud.google.com/storage/docs/encryption/default-keys)
4. Google, [XML API: PUT bucket lifecycle](https://docs.cloud.google.com/storage/docs/xml-api/put-bucket-lifecycle)
5. Google, [Object Retention Lock](https://docs.cloud.google.com/storage/docs/object-lock)
6. Google, [XML API headers](https://docs.cloud.google.com/storage/docs/xml-api/reference-headers): `If-Match` and `If-None-Match` apply to GET and HEAD Object
7. Microsoft, [Azure Storage encryption for data at rest](https://learn.microsoft.com/en-us/azure/storage/common/storage-service-encryption)
8. Microsoft, [Lifecycle management overview](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-overview)
9. Microsoft, [Immutable storage overview](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview)
10. Microsoft, [Conditional headers for Blob service operations](https://learn.microsoft.com/en-us/rest/api/storageservices/specifying-conditional-headers-for-blob-service-operations)
11. Cloudflare, [R2 data security](https://developers.cloudflare.com/r2/reference/data-security/): plain HTTP is accepted unless a custom domain enforces HTTPS
12. Cloudflare, [R2 object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)
13. Cloudflare, [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
14. Cloudflare, [R2 bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/)
15. Backblaze, [Server-side encryption](https://www.backblaze.com/docs/cloud-storage-server-side-encryption)
16. Backblaze, [S3 Put Lifecycle Configuration](https://www.backblaze.com/apidocs/s3-put-lifecycle-configuration)
17. Backblaze, [S3-compatible API](https://www.backblaze.com/docs/cloud-storage-s3-compatible-api)
18. Backblaze, [S3 PutObject](https://www.backblaze.com/apidocs/s3-put-object): the header list has no conditional header
19. Proxmox, [pbs-devel patch, July 2025](https://lists.proxmox.com/pipermail/pbs-devel/2025-July/014234.html) (secondary)
20. MinIO, [AIStor S3 API compatibility](https://docs.min.io/aistor/developers/s3-api-compatibility/)
21. Google, [Soft delete](https://docs.cloud.google.com/storage/docs/soft-delete)
22. obstore, [Authentication](https://developmentseed.org/obstore/latest/authentication/)
23. MinIO, [Deprecation of the MinIO gateway](https://www.min.io/blog/deprecation-of-the-minio-gateway)
24. S3Proxy, [README](https://github.com/gaul/s3proxy)
25. AWS, [boto3 credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
26. Google, [Interoperability with other storage providers](https://docs.cloud.google.com/storage/docs/interoperability)
27. Google, [HMAC keys](https://docs.cloud.google.com/storage/docs/authentication/hmackeys)
28. Cloudflare, [R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)
29. MinIO, [AssumeRoleWithWebIdentity](https://docs.min.io/aistor/developers/security-token-service/assumerolewithwebidentity/)
30. Google, [XML API overview](https://docs.cloud.google.com/storage/docs/xml-api/overview)
31. obstore, [S3Store](https://developmentseed.org/obstore/latest/api/store/aws/)
32. object_store, [ClientOptions](https://docs.rs/object_store/latest/object_store/client/struct.ClientOptions.html)
33. AWS, [boto3 `put_object`](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)
34. AWS, [Data integrity protections for Amazon S3](https://docs.aws.amazon.com/sdkref/latest/guide/feature-dataintegrity.html)
35. MinIO, [minio/minio repository](https://github.com/minio/minio): archived 2026-04-25, "THIS REPOSITORY IS NO LONGER MAINTAINED"

PyPI JSON metadata gave each version, date, licence and pin in section 4.
