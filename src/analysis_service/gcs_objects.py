"""The ``gcs`` object store: objects in a Google Cloud Storage bucket (ADR 0103).

The ``gcs`` extra holds ``google-cloud-storage``, and
:mod:`analysis_service.objects` imports this module only where a deployment
selects ``gcs``. The client is synchronous, so each call to it runs in a
worker thread and never blocks the event loop. Like every row of
:data:`~analysis_service.objects.OBJECT_STORES`, this store keeps and returns
bytes and checks nothing about them.

The client errors this module handles, and what each one becomes:

* **A missing key.** A read or a delete raises ``NotFound``.
  :meth:`GcsObjectStore.get` returns ``None``, and
  :meth:`GcsObjectStore.delete` returns False.
* **A key that holds an object.** A put with ``if_generation_match=0``
  raises ``PreconditionFailed``, and :meth:`GcsObjectStore.put` raises
  :class:`FileExistsError`, as the ``filesystem`` row does.
* **A missing bucket** (HTTP 404), **a refused** ``buckets.get`` (HTTP 403)
  **and each other failed** ``buckets.get``. :meth:`GcsObjectStore.check`
  turns each into a :class:`StoreConfigError`, so the startup stops. The 403
  message names the permission and the role that the row needs.
* **A metadata server that does not answer** while the client reads the
  universe domain of the credential, **or a key that names another universe
  domain.** :meth:`GcsObjectStore.from_env` turns the ``GoogleAuthError`` or
  the ``ValueError`` into a :class:`StoreConfigError`.
* **A host that the service cannot reach, or a token it cannot refresh.**
  ``requests`` raises an :class:`OSError`, ``google.auth`` raises a
  ``GoogleAuthError``, and a retry that runs out raises a ``RetryError``.
  :meth:`GcsObjectStore.check` turns each into a :class:`StoreConfigError`
  that names the error class.

After the startup, each other error of a call goes to the caller unchanged,
and the API answers ``500``. No message of this module repeats a key, a
credential or the text of a provider's error.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from google.api_core.exceptions import (
    Forbidden,
    GoogleAPICallError,
    NotFound,
    PreconditionFailed,
    RetryError,
)
from google.auth.credentials import Credentials
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage
from google.oauth2 import service_account

from analysis_service.config_files import read_secret_file, refuse_bare_secret
from analysis_service.gcp_identity import STORAGE_SCOPE, platform_credentials
from analysis_service.objects import (
    GCS_KEY_FILE_VAR,
    OBJECT_BUCKET_VAR,
    OBJECT_CREDENTIALS_VAR,
    OBJECT_STORES,
)
from analysis_service.store_settings import (
    StoreConfigError,
    closed_setting,
    refuse_unread,
)
from analysis_service.vendors import CredentialMode

#: A GCS bucket name: 3 to 63 lower-case letters, digits, hyphens,
#: underscores and dots, with a letter or digit at each end.
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")

#: The one host of the store. The client takes it from here, so
#: ``STORAGE_EMULATOR_HOST`` and ``API_ENDPOINT_OVERRIDE`` cannot move it.
ENDPOINT = "https://storage.googleapis.com"


def make_client(credentials: Credentials) -> storage.Client:
    """**The one maker of a** ``storage.Client``: the credential that the
    deployment's mode gives, the endpoint :data:`ENDPOINT` and no project.
    The client reads the universe domain of the credential: one request to
    the metadata server in the ``iam`` mode, or the field of the key in the
    ``api_key`` mode. A universe domain that is not the one of
    :data:`ENDPOINT` raises ``ValueError``."""
    return storage.Client(
        project=None,
        credentials=credentials,
        client_options={"api_endpoint": ENDPOINT},
    )


def _platform_identity(env: Mapping[str, str]) -> Credentials:
    """``iam``: the identity of the metadata server, by
    :func:`~analysis_service.gcp_identity.platform_credentials`. A key file
    stops the startup too, so a key works only where the deployment declares
    ``api_key``."""
    if env.get(GCS_KEY_FILE_VAR, "").strip():
        raise StoreConfigError(
            f"the iam mode of the gcs object store reads no key; unset"
            f" {GCS_KEY_FILE_VAR}, or set ANALYSIS_OBJECT_CREDENTIALS=api_key"
        )
    return platform_credentials("the iam mode of the gcs object store")


#: The most bytes of a key file. A service account key is about 2.4 KB.
MAX_KEY_BYTES = 16 * 1024


def _service_account(env: Mapping[str, str]) -> Credentials:
    """``api_key``: the service account key in the file that
    :data:`~analysis_service.objects.GCS_KEY_FILE_VAR` names (ADR 0108).

    The file passes the permission rule of a secret file. It is UTF-8 JSON of
    at most :data:`MAX_KEY_BYTES`, and line breaks are allowed. Its ``type``
    is ``service_account``, because another kind of credential file can name
    an executable that makes the token. No message repeats the content.
    """
    content = read_secret_file(env, GCS_KEY_FILE_VAR, StoreConfigError)
    if content is None:
        raise StoreConfigError(
            f"the api_key mode of the gcs object store needs {GCS_KEY_FILE_VAR};"
            " it is unset"
        )
    if len(content) > MAX_KEY_BYTES:
        raise StoreConfigError(
            f"the file that {GCS_KEY_FILE_VAR} names is longer than"
            f" {MAX_KEY_BYTES:,} bytes"
        )
    try:
        info = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise StoreConfigError(
            f"the file that {GCS_KEY_FILE_VAR} names is not UTF-8 JSON"
        ) from None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise StoreConfigError(
            f"the file that {GCS_KEY_FILE_VAR} names is not a service account"
            " key; its type must be service_account"
        )
    try:
        return service_account.Credentials.from_service_account_info(
            info, scopes=[STORAGE_SCOPE]
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreConfigError(
            f"the service account key in the file that {GCS_KEY_FILE_VAR} names"
            f" is not usable ({type(exc).__name__})"
        ) from None


#: The credential of each ``ANALYSIS_OBJECT_CREDENTIALS`` value. The table
#: answers for every :class:`CredentialMode`.
CREDENTIALS: dict[CredentialMode, Callable[[Mapping[str, str]], Credentials]] = {
    CredentialMode.API_KEY: _service_account,
    CredentialMode.IAM: _platform_identity,
}


@dataclass(frozen=True)
class BucketRule:
    """A bucket state that stops the startup (ADR 0104): a row of
    :data:`BUCKET_RULES`."""

    #: The field of the ``buckets.get`` resource.
    field: str
    #: True where the bucket is in the refused state.
    refuses: Callable[[storage.Bucket], bool]
    #: The value that the row needs, for the message. ``{bucket}`` becomes
    #: the name of the bucket.
    needs: str


#: Each bucket state where a delete can keep the content, or where the content
#: can become public. The row reads no per-object hold, no retain-until time
#: and no lifecycle rule.
BUCKET_RULES = (
    BucketRule(
        field="versioning.enabled",
        refuses=lambda bucket: bucket.versioning_enabled,
        needs="false",
    ),
    BucketRule(
        field="softDeletePolicy.retentionDurationSeconds",
        refuses=lambda bucket: bool(
            bucket.soft_delete_policy.retention_duration_seconds
        ),
        needs="0 (gcloud storage buckets update gs://{bucket} --clear-soft-delete)",
    ),
    BucketRule(
        field="retentionPolicy.retentionPeriod",
        refuses=lambda bucket: bucket.retention_period is not None,
        needs="unset",
    ),
    BucketRule(
        field="defaultEventBasedHold",
        refuses=lambda bucket: bool(bucket.default_event_based_hold),
        needs="false",
    ),
    BucketRule(
        field="objectRetention.mode",
        refuses=lambda bucket: bucket.object_retention_mode == "Enabled",
        needs="unset",
    ),
    BucketRule(
        field="iamConfiguration.publicAccessPrevention",
        refuses=lambda bucket: (
            bucket.iam_configuration.public_access_prevention != "enforced"
        ),
        needs="enforced",
    ),
    BucketRule(
        field="iamConfiguration.uniformBucketLevelAccess.enabled",
        refuses=lambda bucket: (
            not bucket.iam_configuration.uniform_bucket_level_access_enabled
        ),
        needs="true",
    ),
)


class GcsObjectStore:
    """Objects in one bucket, through a ``storage.Client``.

    Each put is create-only, by the generation precondition 0. The client
    verifies the TLS certificate of :data:`ENDPOINT`, and no setting turns
    that off. :meth:`check` sends the first request to the bucket.
    """

    def __init__(self, client: storage.Client, bucket: str) -> None:
        self._client = client
        self._bucket = client.bucket(bucket)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GcsObjectStore:
        refuse_bare_secret(env, GCS_KEY_FILE_VAR, StoreConfigError)
        refuse_unread(
            env,
            ("ANALYSIS_OBJECT_",),
            OBJECT_STORES["gcs"].reads,
            "the gcs object store",
        )
        bucket = env.get(OBJECT_BUCKET_VAR, "").strip()
        if not bucket:
            raise StoreConfigError(f"set {OBJECT_BUCKET_VAR} for the gcs object store")
        if _BUCKET.fullmatch(bucket) is None:
            raise StoreConfigError(f"{OBJECT_BUCKET_VAR} must match {_BUCKET.pattern}")
        mode = closed_setting(
            env.get(OBJECT_CREDENTIALS_VAR, ""), OBJECT_CREDENTIALS_VAR, CREDENTIALS
        )
        credentials = CREDENTIALS[mode](env)
        try:
            client = make_client(credentials)
        except (GoogleAuthError, ValueError) as error:
            raise StoreConfigError(
                f"the gcs object store cannot use the universe domain of its"
                f" credential ({type(error).__name__}); it uses only {ENDPOINT}"
            ) from None
        return cls(client, bucket)

    async def check(self) -> None:
        """Send one ``buckets.get``. A missing bucket, a refused request, a
        host the service cannot reach, a token it cannot refresh or a state of
        :data:`BUCKET_RULES` stops the startup."""
        name = self._bucket.name
        try:
            bucket = await asyncio.to_thread(self._client.get_bucket, name)
        except NotFound:
            raise StoreConfigError(f"the bucket {name!r} does not exist") from None
        except Forbidden:
            raise StoreConfigError(
                f"buckets.get on the bucket {name!r} was refused (HTTP 403); grant"
                " the service identity storage.buckets.get on the bucket, which"
                " roles/storage.bucketViewer holds"
            ) from None
        except GoogleAPICallError as error:
            raise StoreConfigError(
                f"buckets.get on the bucket {name!r} failed with HTTP {error.code}"
            ) from None
        except (OSError, GoogleAuthError, RetryError) as error:
            raise StoreConfigError(
                f"the gcs object store cannot reach the bucket {name!r} or get a"
                f" token for it ({type(error).__name__})"
            ) from None
        if refused := [
            f"{rule.field} must be {rule.needs.format(bucket=name)}"
            for rule in BUCKET_RULES
            if rule.refuses(bucket)
        ]:
            raise StoreConfigError(
                f"the gcs object store refuses the bucket {name!r}, because a"
                f" delete can keep its content or its content can become public:"
                f" {'; '.join(refused)}"
            )

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(_put, self._bucket.blob(key), data)

    async def get(self, key: str) -> bytes | None:
        try:
            return await asyncio.to_thread(self._bucket.blob(key).download_as_bytes)
        except NotFound:
            return None

    async def delete(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._bucket.delete_blob, key)
        except NotFound:
            return False
        return True

    async def list(self) -> AsyncIterator[tuple[str, datetime]]:
        """Each key in the bucket, with the time of its write. Each page is
        one request, read in a worker thread."""
        pages = self._client.list_blobs(self._bucket).pages
        while (page := await asyncio.to_thread(next, pages, None)) is not None:
            for blob in page:
                yield blob.name, blob.time_created


def _put(blob: storage.Blob, data: bytes) -> None:
    """Write ``data`` where the key holds no object, in one conditional request."""
    try:
        blob.upload_from_string(data, if_generation_match=0)
    except PreconditionFailed:
        raise FileExistsError("the key already holds an object") from None
