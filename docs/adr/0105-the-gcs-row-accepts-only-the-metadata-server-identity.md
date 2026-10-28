# 105. The gcs row accepts only the metadata server identity

- **Status**: accepted
- **Date**: 2026-10-28
- **Map**: [#1991](https://github.com/mstarks01/work-agent/issues/1991), ticket
  [#1995](https://github.com/mstarks01/work-agent/issues/1995)
- **Relates to**: [ADR 0103](0103-gcs-uses-its-own-row-on-the-native-client.md)

## Context

The `iam` mode of the `s3` row and the `rds` token of a database read the AWS
identity through `aws_identity.platform_session`. That function refuses each
credential that a platform did not issue, so a static key works only in the
`api_key` mode. The `iam` mode of the Bedrock vendor row does not call it.

`google.auth.default()` has no parameter that limits its chain. It reads
`GOOGLE_APPLICATION_CREDENTIALS` first, then the gcloud user file, then the
metadata server. The type of the credential that it returns names the source.
The metadata server of Cloud Run, GCE and GKE Workload Identity returns
`google.auth.compute_engine.Credentials`. Workload Identity Federation returns
a subclass of `google.auth.external_account.Credentials`, and a federation
file can name an executable that makes the token. `storage.Client` calls
`google.auth.default()` itself unless the caller passes a credential.

The Vertex vendor row passes no credential, so litellm accepts each identity
that ADC finds, including a key file and a gcloud user login.

## Decision

**The `gcs` row has one credential mode, `iam`, and that mode accepts only a
credential from the metadata server.**

- `ANALYSIS_OBJECT_CREDENTIALS` stays required. `api_key` stops the startup,
  and the message says that a key file and an HMAC key are out of scope.
- A new module, `gcp_identity`, holds `platform_credentials(label)`. It calls
  `google.auth.default()` with the scope
  `https://www.googleapis.com/auth/devstorage.read_write`. It refuses each
  result that is not a `compute_engine.Credentials`. The `gcs` row passes the
  result to `storage.Client`.
- The error names the label, the accepted type and the type that ADC found. It
  names no credential. When `GOOGLE_APPLICATION_CREDENTIALS` is set, the error
  names that variable, because ADC reads it first.
- The `gcs` row is the only reader of `gcp_identity`. The Vertex row keeps
  the ADC chain, as the Bedrock row keeps the `boto3` chain.

## Considered options

- **Accept Workload Identity Federation too.** Rejected: the destination
  names Cloud Run and GKE, and both reach the metadata server. Federation
  serves a workload outside Google Cloud. One more type in the accepted set
  adds it later.
- **Accept each identity that ADC finds, as the Vertex row does.** Rejected:
  a key file or a user login then writes the sources and the reports, and the
  `s3` row's `iam` mode refuses the same credentials.
- **Read the identity for the Vertex row through the same module.**
  Rejected: it changes the litellm adapter path and stops each Vertex
  deployment that uses a key file or a user login. That is outside the
  destination of the map.
- **Use the platform default scope.** Rejected: on Cloud Run the default is
  `cloud-platform`, and a token that leaks then reaches every API the
  identity can use.

## Consequences

A developer machine with a gcloud login or a key file cannot run the `gcs`
row. A deployment that sets `GOOGLE_APPLICATION_CREDENTIALS` for the Vertex
row cannot also run the `gcs` row in the same process. The IAM roles on the
bucket stay the main access control, and the scope only limits a token that
leaks.

Google's Knative docs show that the metadata token path accepts a `scopes`
parameter. Nobody has checked that Cloud Run, GKE Workload Identity and GCE
honour it.
