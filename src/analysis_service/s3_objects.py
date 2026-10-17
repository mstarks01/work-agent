"""The ``s3`` object store: objects in a bucket of a store that speaks the S3 API.

The ``aws`` extra holds ``boto3`` (ADR 0023), and :mod:`analysis_service.objects`
imports this module only where a deployment selects ``s3``. ``boto3`` is
synchronous, so each call to it runs in a worker thread and never blocks the
event loop. Like every row of :data:`~analysis_service.objects.OBJECT_STORES`,
this store keeps and returns bytes and checks nothing about them.

The ``boto3`` errors this module handles, and what each one becomes:

* **A missing key.** ``GetObject`` raises a ``ClientError`` with the code
  ``NoSuchKey``, and :meth:`S3ObjectStore.get` returns ``None``.
  ``HeadObject`` has no response body, so its ``ClientError`` carries only the
  HTTP status 404, and :meth:`S3ObjectStore.delete` returns False.
* **A key that holds an object.** A conditional ``PutObject`` raises a
  ``ClientError`` with the code ``PreconditionFailed``, and
  :meth:`S3ObjectStore.put` raises :class:`FileExistsError`, as the
  ``filesystem`` row does.
* **A missing bucket** (HTTP 404) **and a refused access** (HTTP 403).
  :meth:`S3ObjectStore.check` turns each into a :class:`StoreConfigError`
  that names the cause, so the startup stops.
* **An endpoint that the service cannot reach, or a request it cannot sign.**
  ``botocore`` raises a ``BotoCoreError``: for example
  ``EndpointConnectionError``, ``SSLError`` or ``NoCredentialsError``.
  :meth:`S3ObjectStore.check` turns each into a :class:`StoreConfigError`
  that names the error class.

After the startup, each other error of a call goes to the caller unchanged,
and the API answers ``500``. No message of this module repeats a key, a secret
or the text of a provider's error.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from analysis_service.aws_identity import platform_session
from analysis_service.objects import OBJECT_STORES, S3_KEY_FILES
from analysis_service.store_settings import (
    StoreConfigError,
    closed_setting,
    refuse_unread,
)
from analysis_service.vendors import (
    REGION,
    CredentialMode,
    ProviderAuthError,
    read_secret,
    refused_var,
)

#: An S3 bucket name: 3 to 63 lower-case letters, digits, dots and hyphens,
#: with a letter or digit at each end.
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")

#: How a request names the bucket: in the host name, or in the path. The
#: values are the ``addressing_style`` values of ``botocore``.
Addressing = Literal["virtual", "path"]


def _key_pair(env: Mapping[str, str]) -> boto3.Session:
    """``api_key``: a session on the static key pair, from the two files only."""
    pair = {}
    for argument, var in S3_KEY_FILES.items():
        try:
            value = read_secret(env, var)
        except ProviderAuthError as error:
            raise StoreConfigError(str(error)) from None
        if not value:
            raise StoreConfigError(
                f"the api_key mode of the s3 object store needs {var}; it is unset"
            )
        pair[argument] = value
    return boto3.Session(**pair)


def _platform_identity(env: Mapping[str, str]) -> boto3.Session:
    """``iam``: a session on the identity of the platform, by
    :func:`~analysis_service.aws_identity.platform_session`. A key file stops
    the startup too, so a static key works only where the deployment declares
    ``api_key``."""
    for var in S3_KEY_FILES.values():
        if env.get(var, "").strip():
            raise StoreConfigError(
                f"the iam mode of the s3 object store reads no static key;"
                f" unset {var}, or set ANALYSIS_OBJECT_CREDENTIALS=api_key"
            )
    return platform_session("the iam mode of the s3 object store")


#: The ``boto3`` session of each ``ANALYSIS_OBJECT_CREDENTIALS`` value. The
#: table answers for every :class:`CredentialMode`.
CREDENTIALS: dict[CredentialMode, Callable[[Mapping[str, str]], boto3.Session]] = {
    CredentialMode.API_KEY: _key_pair,
    CredentialMode.IAM: _platform_identity,
}


def _refuse_stray_settings(env: Mapping[str, str]) -> None:
    """Refuse a key in the environment by name, then any other unread setting."""
    for var in S3_KEY_FILES.values():
        bare = refused_var(var)
        if env.get(bare, "").strip():
            raise StoreConfigError(
                f"the s3 object store reads its key from the file that {var}"
                f" names; unset {bare}, which this service refuses"
            )
    refuse_unread(
        env, ("ANALYSIS_OBJECT_",), OBJECT_STORES["s3"].reads, "the s3 object store"
    )


def _session(env: Mapping[str, str]) -> boto3.Session:
    value = env.get("ANALYSIS_OBJECT_CREDENTIALS", "")
    mode = closed_setting(value, "ANALYSIS_OBJECT_CREDENTIALS", CREDENTIALS)
    return CREDENTIALS[mode](env)


def _endpoint(env: Mapping[str, str]) -> str | None:
    """``ANALYSIS_OBJECT_ENDPOINT``, or ``None`` for the AWS endpoint of the
    region. Only ``https://`` with a host and an optional port passes, so an
    object never crosses the network without TLS, and no message repeats the
    value, which can hold a password."""
    value = env.get("ANALYSIS_OBJECT_ENDPOINT", "").strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        # A port that is not a number raises here.
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise StoreConfigError("ANALYSIS_OBJECT_ENDPOINT is not a URL") from None
    if parts.scheme != "https":
        raise StoreConfigError("ANALYSIS_OBJECT_ENDPOINT must be an https URL")
    extra = (
        parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    )
    if not host or extra:
        raise StoreConfigError(
            "ANALYSIS_OBJECT_ENDPOINT must hold https://, a host and an optional"
            " port, and nothing more"
        )
    return value


def _required(env: Mapping[str, str], var: str, shape: re.Pattern[str]) -> str:
    value = env.get(var, "").strip()
    if not value:
        raise StoreConfigError(f"set {var} for the s3 object store")
    if shape.fullmatch(value) is None:
        raise StoreConfigError(f"{var} must match {shape.pattern}")
    return value


def _status(error: ClientError) -> int | None:
    status: int | None = error.response.get("ResponseMetadata", {}).get(
        "HTTPStatusCode"
    )
    return status


def _code(error: ClientError) -> str | None:
    code: str | None = error.response.get("Error", {}).get("Code")
    return code


#: What a refused ``HeadBucket`` means, by its HTTP status. ``HeadBucket`` has
#: no response body, so the status is the only sign.
_REFUSALS: dict[int, str] = {
    403: (
        "the object store refuses access to the bucket {bucket!r}: check"
        " ANALYSIS_OBJECT_CREDENTIALS, the key files and the policy of the bucket"
    ),
    404: "the bucket {bucket!r} does not exist",
}


class S3ObjectStore:
    """Objects in one bucket, through a ``boto3`` client.

    The client verifies the TLS certificate of the endpoint, and no setting
    turns that off. It ignores each endpoint that the environment or a
    profile of ``boto3`` names. Building the client sends no request;
    :meth:`check` sends the first one.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> S3ObjectStore:
        _refuse_stray_settings(env)
        bucket = _required(env, "ANALYSIS_OBJECT_BUCKET", _BUCKET)
        region = _required(env, "ANALYSIS_OBJECT_REGION", REGION)
        addressing = closed_setting(
            env.get("ANALYSIS_OBJECT_ADDRESSING", "").strip() or "virtual",
            "ANALYSIS_OBJECT_ADDRESSING",
            dict.fromkeys(get_args(Addressing)),
        )
        client = _session(env).client(
            "s3",
            region_name=region,
            endpoint_url=_endpoint(env),
            config=Config(
                s3={"addressing_style": addressing},
                # ``AWS_ENDPOINT_URL`` and the endpoint of a profile would
                # pass by the ``https`` check of ``ANALYSIS_OBJECT_ENDPOINT``.
                ignore_configured_endpoint_urls=True,
            ),
        )
        return cls(client, bucket)

    async def check(self) -> None:
        """Send ``HeadBucket``. A missing bucket, a refused credential or an
        endpoint the service cannot reach stops the startup."""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except ClientError as error:
            status = _status(error)
            reason = _REFUSALS.get(
                status or 0,
                "HeadBucket on the bucket {bucket!r} failed with HTTP {status}:"
                " check ANALYSIS_OBJECT_REGION and ANALYSIS_OBJECT_ENDPOINT",
            )
            raise StoreConfigError(
                reason.format(bucket=self._bucket, status=status)
            ) from None
        except BotoCoreError as error:
            raise StoreConfigError(
                f"the s3 object store cannot reach or sign a request to the bucket"
                f" {self._bucket!r} ({type(error).__name__})"
            ) from None

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(_put, self._client, self._bucket, key, data)

    async def get(self, key: str) -> bytes | None:
        return await asyncio.to_thread(_get, self._client, self._bucket, key)

    async def delete(self, key: str) -> bool:
        return await asyncio.to_thread(_delete, self._client, self._bucket, key)

    async def list(self) -> AsyncIterator[tuple[str, datetime]]:
        """Each key in the bucket, one ``ListObjectsV2`` page at a time."""
        token: dict[str, str] = {}
        while True:
            page = await asyncio.to_thread(
                self._client.list_objects_v2, Bucket=self._bucket, **token
            )
            for listed in page.get("Contents", []):
                yield listed["Key"], listed["LastModified"]
            if not page.get("IsTruncated"):
                break
            token = {"ContinuationToken": page["NextContinuationToken"]}


def _put(client: Any, bucket: str, key: str, data: bytes) -> None:
    """Write ``data`` where ``key`` holds no object, in one conditional request."""
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch="*")
    except ClientError as error:
        if _code(error) == "PreconditionFailed":
            raise FileExistsError("the key already holds an object") from None
        raise


def _get(client: Any, bucket: str, key: str) -> bytes | None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _code(error) == "NoSuchKey":
            return None
        raise
    with response["Body"] as body:
        data: bytes = body.read()
    return data


def _delete(client: Any, bucket: str, key: str) -> bool:
    """Remove the object under ``key``. ``DeleteObject`` succeeds on a missing
    key too, so ``HeadObject`` asks first, for the count of the delete log line."""
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _status(error) == 404:
            return False
        raise
    client.delete_object(Bucket=bucket, Key=key)
    return True
