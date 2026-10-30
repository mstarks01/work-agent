# 108. The gcs row reads a service account key in its api_key mode

- **Status**: accepted
- **Date**: 2026-10-30
- **Amends**: [ADR 0105](0105-the-gcs-row-accepts-only-the-metadata-server-identity.md)

## Context

ADR 0105 gives the `gcs` row one credential mode, `iam`, which accepts only
the identity of the metadata server. So the row runs only on Cloud Run, GKE
with Workload Identity or GCE. A developer machine, another cloud and a host
on site cannot run it. The `s3` row has an `api_key` mode that reads a static
key pair from two private files.

The native client accepts one static key: a service account key file. ADR 0103
already rules out an HMAC key, because only the S3 interop API takes one, and
that API accepts no `If-None-Match`. A credential file names its kind in its
`type` field. An `external_account` file can name an executable or a URL that
the client uses to get a token, and an `authorized_user` file holds a person's
gcloud login.

Google downloads a service account key as JSON on several lines. The one
secret rule (`secret_refusal`) refuses a line break.

A static key has risks that the metadata server identity does not have:

- The key does not expire unless an organization policy sets an expiry.
- The key is a file on disk, so a backup, an image or a copy of the settings
  directory can leak it.
- A person who holds the key can get a token for each API that the account can
  use. The scope that the service asks for limits only the service's own token.
- Many organizations block key creation with
  `iam.disableServiceAccountKeyCreation`.

## Decision

**The `api_key` mode of the `gcs` row reads one service account key file, and
it accepts no other kind of credential file.**

- `ANALYSIS_OBJECT_SERVICE_ACCOUNT_KEY_FILE` names the file, and the `api_key`
  mode requires it. `ANALYSIS_OBJECT_SERVICE_ACCOUNT_KEY`, which would hold the
  key itself, stops the startup. The `iam` mode refuses the file setting, as
  the `iam` mode of the `s3` row refuses its key files.
- The row reads the file through `read_secret_file`, the one reader of a
  secret file, so the permission rule of a secret file applies. The file is at
  most 16 KiB of UTF-8 JSON, and line breaks are allowed. The JSON must be an
  object whose `type` is `service_account`. Each other file stops the startup.
- The row builds the credential with
  `service_account.Credentials.from_service_account_info` and the one scope
  `https://www.googleapis.com/auth/devstorage.read_write`, and it gives the
  credential to `make_client`. It does not call `google.auth.default()`, so
  `GOOGLE_APPLICATION_CREDENTIALS` has no effect on this mode.
- No message repeats the content of the file. A message names the setting and
  the rule that the file breaks. The permission rule names the path, as it
  does for each secret file.
- The Settings page shows the key row only in the `api_key` mode of the `gcs`
  row. Its key field writes the key text to a new private file, as it does for
  the `s3` keys.

The `iam` mode keeps the rule of ADR 0105.

## Considered options

- **Accept `authorized_user` too.** Rejected: a person's login then writes the
  sources and the reports, which ADR 0105 rejects for the `iam` mode.
- **Accept each kind that `google.auth.load_credentials_from_file` reads.**
  Rejected: an `external_account` file can run an executable as the service,
  so a person who can change the file can run a command.
- **Keep the one secret rule, and require the key on one line.** Rejected: the
  downloaded file then stops the startup, and a JSON reader still needs its own
  parse step.
- **Read the key from `GOOGLE_APPLICATION_CREDENTIALS` through ADC.**
  Rejected: ADC accepts each kind of file, and the Vertex row reads the same
  variable.

## Consequences

The `gcs` row runs outside Google Cloud. The operator owns the key: create a
service account for the service alone, grant it roles on the bucket only, set
a key expiry where the organization allows one, and rotate the key. An
organization that blocks key creation can use only the `iam` mode.
