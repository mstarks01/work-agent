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
* **A missing bucket** (HTTP 404) **and each other refused** ``buckets.get``.
  :meth:`GcsObjectStore.check` turns each into a :class:`StoreConfigError`,
  so the startup stops.
* **A metadata server that does not answer** while the client reads the
  universe domain of the credential. :meth:`GcsObjectStore.from_env` turns
  the ``GoogleAuthError`` into a :class:`StoreConfigError`.
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
import re
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from typing import NoReturn

from google.api_core.exceptions import (
    GoogleAPICallError,
    NotFound,
    PreconditionFailed,
    RetryError,
)
from google.auth.compute_engine import Credentials
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage

from analysis_service.gcp_identity import platform_credentials
from analysis_service.objects import (
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
    The client reads the universe domain of the credential, which is one
    request to the metadata server."""
    return storage.Client(
        project=None,
        credentials=credentials,
        client_options={"api_endpoint": ENDPOINT},
    )


def _platform_identity(env: Mapping[str, str]) -> Credentials:
    """``iam``: the identity of the metadata server, by
    :func:`~analysis_service.gcp_identity.platform_credentials`."""
    return platform_credentials("the iam mode of the gcs object store")


def _no_key(env: Mapping[str, str]) -> NoReturn:
    """``api_key``: the row has no key mode (ADR 0105)."""
    raise StoreConfigError(
        "the gcs object store has no api_key mode, because a key file and an"
        " HMAC key are out of scope; set ANALYSIS_OBJECT_CREDENTIALS=iam"
    )


#: The credential of each ``ANALYSIS_OBJECT_CREDENTIALS`` value. The table
#: answers for every :class:`CredentialMode`.
CREDENTIALS: dict[CredentialMode, Callable[[Mapping[str, str]], Credentials]] = {
    CredentialMode.API_KEY: _no_key,
    CredentialMode.IAM: _platform_identity,
}


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
        except GoogleAuthError as error:
            raise StoreConfigError(
                f"the gcs object store cannot read the universe domain of its"
                f" credential ({type(error).__name__})"
            ) from None
        return cls(client, bucket)

    async def check(self) -> None:
        """Send one ``buckets.get``. A missing bucket, a refused request, a
        host the service cannot reach or a token it cannot refresh stops the
        startup."""
        name = self._bucket.name
        try:
            await asyncio.to_thread(self._client.get_bucket, name)
        except NotFound:
            raise StoreConfigError(f"the bucket {name!r} does not exist") from None
        except GoogleAPICallError as error:
            raise StoreConfigError(
                f"buckets.get on the bucket {name!r} failed with HTTP {error.code}"
            ) from None
        except (OSError, GoogleAuthError, RetryError) as error:
            raise StoreConfigError(
                f"the gcs object store cannot reach the bucket {name!r} or get a"
                f" token for it ({type(error).__name__})"
            ) from None

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
