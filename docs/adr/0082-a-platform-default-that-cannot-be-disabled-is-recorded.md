# 82. A platform default that cannot be disabled is recorded, not asked

- **Status**: accepted
- **Date**: 2026-10-13
- **Relates to**: [ADR 0081](0081-a-question-kind-is-asked-once-for-the-system.md),
  whose questions a recorded default removes; `QA-2026-10-13-05`, which
  measured what the example still leaves open

## Context

A description that names DynamoDB, a BigQuery dataset or the Stripe API has
already said something the owner is then asked again: DynamoDB encrypts every
table at rest and cannot be told not to, and Stripe refuses a request that is
not over HTTPS or carries no API key. Extraction records what the text states,
and the extraction prompt rightly keeps a vendor's usual behaviour out of it,
so these attributes stay `unknown` and become questions. The maintainer asked
on 2026-10-13 for secure-by-design defaults that cannot be disabled to be
assumed rather than asked.

## Decision

**A closed table in code names each default:** the service, how a description
names it, the attribute, the value, the vendor's statement in our words, and
where the vendor documents it (`platform_defaults.PLATFORM_DEFAULTS`). Only a
property the vendor documents as always on and impossible to disable is in it.
On 2026-10-13 that was: encryption at rest for Amazon S3 (new objects since
2023), DynamoDB, CloudWatch Logs, Secrets Manager, Google Cloud's managed
stores, Azure Storage, managed disks and Cosmos DB, and Snowflake; TLS on
flows to Snowflake; HTTPS and an API key on flows to Stripe; TLS on flows to a
hosted OAuth or OpenID Connect provider (Auth0, Okta, Entra ID, Cognito),
which RFC 6749 requires of its authorization and token endpoints.

**Defaults that can be turned off are not in it,** however common: RDS and
Aurora encryption, SQS and SNS encryption, EBS encryption, CloudFront's
protocol policy, Azure SQL's transparent encryption, and TLS on every AWS API,
which AWS documents for some services and not all.

**Code writes a default after the validity gate,** into the model the
questions, the catalog and the analysis read, and never into what extraction
emitted (`graph.validate_extraction`). It fills only an attribute whose control
state is unverified, so a stated value, "none" included, always wins. Each
value takes an Assumption whose basis is the vendor's statement and its link. A
name that marks a look-alike — S3-compatible, an emulator, MinIO, LocalStack,
self-hosted, on-prem — matches nothing, and the names are product names, so
"our cloud storage" or "a pub/sub queue" matches nothing either.

## Consequences

The extraction eval still measures what the model read, because the
extract-only graph has no gate. An analysis-mode eval seeds the blessed model
and is unchanged. A head-mode run sees the defaults: four stores of the blessed
references (case 02's device registry and telemetry lake, case 03's claims
warehouse, case 04's inference log) keep `encryption_at_rest` as `unknown`
where the table now records the Google Cloud default, and they await the
maintainer's ruling.

A vendor can change a default. The table names each source so a reader can
check it, and a row whose vendor no longer documents the property must be
removed. The table is reviewed knowledge, not a measurement; how many questions
it removes on real descriptions is unmeasured.
