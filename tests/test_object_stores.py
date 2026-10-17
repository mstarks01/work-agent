"""The object stores: what each row of ``OBJECT_STORES`` owes, and the ``s3`` row.

``tests/test_job_store.py`` runs the whole store contract on each object store.
This module holds what that contract cannot say: the bytes, the missing key and
the second put on each row, then each setting and each startup refusal of the
``s3`` row, and the worker thread of each ``boto3`` call. moto serves the S3
API in the test process, so no test reaches a network.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import boto3
import pytest
from moto.core import set_initial_no_auth_action_count

from analysis_service.aws_identity import PLATFORM_METHODS, platform_session
from analysis_service.objects import (
    S3_KEY_FILES,
    ObjectStore,
    ObjectStoreName,
    build_object_store,
    object_key,
)
from analysis_service.store_backends import build_store
from analysis_service.store_settings import StoreConfigError
from tests.sql_stores import (
    TEST_KEYS,
    TEST_REGION,
    filesystem_env,
    object_env,
    s3_env,
    sql_env,
)
from tests.test_sql_store import start

KEY = object_key(
    "0b3c4f6e-1d2a-4b5c-8d7e-9f0a1b2c3d4e", f"job-{'a' * 32}", "report.json"
)
SOURCES = object_key(
    "0b3c4f6e-1d2a-4b5c-8d7e-9f0a1b2c3d4e", f"job-{'b' * 32}", "sources.json"
)


@pytest.fixture(params=get_args(ObjectStoreName))
def objects(request, tmp_path) -> ObjectStore:
    """A fresh object store of each row."""
    row = object_env(request, request.param)
    return build_object_store(row.settings(tmp_path))


class TestEachRow:
    """Every row keeps bytes, writes a key once and reports a missing key."""

    def test_it_returns_the_bytes_it_keeps(self, objects):
        asyncio.run(objects.put(KEY, b'{"a": 1}'))
        assert asyncio.run(objects.get(KEY)) == b'{"a": 1}'

    def test_a_missing_key_holds_nothing(self, objects):
        assert asyncio.run(objects.get(KEY)) is None
        assert asyncio.run(objects.delete(KEY)) is False

    def test_a_key_is_written_once(self, objects):
        asyncio.run(objects.put(KEY, b"first"))
        with pytest.raises(FileExistsError):
            asyncio.run(objects.put(KEY, b"second"))
        assert asyncio.run(objects.get(KEY)) == b"first"

    def test_a_delete_removes_the_object(self, objects):
        asyncio.run(objects.put(KEY, b"data"))
        assert asyncio.run(objects.delete(KEY)) is True
        assert asyncio.run(objects.get(KEY)) is None

    def test_the_listing_holds_each_key_and_the_time_of_its_put(self, objects):
        start = datetime.now(UTC) - timedelta(minutes=1)
        for key in (KEY, SOURCES):
            asyncio.run(objects.put(key, b"data"))
        listed = asyncio.run(listing(objects))
        end = datetime.now(UTC) + timedelta(minutes=1)
        assert set(listed) == {KEY, SOURCES}
        assert all(start < modified < end for modified in listed.values())

    def test_the_listing_holds_no_deleted_key(self, objects):
        assert asyncio.run(listing(objects)) == {}
        asyncio.run(objects.put(KEY, b"data"))
        asyncio.run(objects.delete(KEY))
        assert asyncio.run(listing(objects)) == {}


async def listing(objects: ObjectStore) -> dict[str, datetime]:
    """Each key that ``objects`` lists, with the time it gives for the key."""
    return {key: modified async for key, modified in objects.list()}


def test_the_filesystem_listing_enters_and_holds_no_symbolic_link(tmp_path):
    outside = tmp_path / "outside"
    (outside / SOURCES).parent.mkdir(parents=True)
    (outside / SOURCES).write_bytes(b"secret")
    objects = build_object_store(filesystem_env(tmp_path))
    root = tmp_path / "objects"
    asyncio.run(objects.put(KEY, b"data"))
    (root / "linked-owner").symlink_to(outside / SOURCES.split("/")[0])
    (root / KEY).parent.joinpath("sources.json").symlink_to(outside / SOURCES)
    assert set(asyncio.run(listing(objects))) == {KEY}


class TestTheFilesystemFollowsNoLink:
    """No put, read or delete passes through a symbolic link at a folder of a
    key, so none reaches the files the link points to."""

    @pytest.fixture(params=["owner", "job"])
    def linked(self, request, tmp_path):
        """A store whose owner folder or job folder of ``KEY`` is a link to a
        folder outside the root that holds ``KEY``'s file."""
        outside = tmp_path / "outside"
        (outside / KEY).parent.mkdir(parents=True)
        (outside / KEY).write_bytes(b"secret")
        objects = build_object_store(filesystem_env(tmp_path))
        owner, job, _ = KEY.split("/")
        root = tmp_path / "objects"
        if request.param == "owner":
            (root / owner).symlink_to(outside / owner)
        else:
            (root / owner).mkdir(mode=0o700)
            (root / owner / job).symlink_to(outside / owner / job)
        return objects, outside

    def files(self, outside: Path) -> dict[str, bytes]:
        return {
            path.relative_to(outside).as_posix(): path.read_bytes()
            for path in outside.rglob("*")
            if path.is_file()
        }

    def test_a_put_is_refused(self, linked):
        objects, outside = linked
        beside = KEY.replace("report.json", "sources.json")
        with pytest.raises(OSError):
            asyncio.run(objects.put(beside, b"data"))
        assert self.files(outside) == {KEY: b"secret"}

    def test_a_read_is_refused(self, linked):
        objects, _ = linked
        with pytest.raises(OSError):
            asyncio.run(objects.get(KEY))

    def test_a_delete_is_refused(self, linked):
        objects, outside = linked
        with pytest.raises(OSError):
            asyncio.run(objects.delete(KEY))
        assert self.files(outside) == {KEY: b"secret"}


def test_the_s3_listing_reads_every_page(env):
    """``ListObjectsV2`` returns 1000 keys at most in one page."""
    objects = build_object_store(env)
    client = boto3.Session(**TEST_KEYS).client("s3", region_name=TEST_REGION)
    keys = {f"{i:04}" for i in range(1001)}
    for key in keys:
        client.put_object(Bucket=env["ANALYSIS_OBJECT_BUCKET"], Key=key, Body=b"")
    assert set(asyncio.run(listing(objects))) == keys


@pytest.fixture
def env(moto_s3, tmp_path: Path) -> dict[str, str]:
    """The settings of a fresh ``sql`` store on SQLite and an ``s3`` bucket."""
    return sql_env(tmp_path, "s3")


def changed(env: dict[str, str], change: dict[str, str | None]) -> dict[str, str]:
    for var, value in change.items():
        if value is None:
            env.pop(var, None)
        else:
            env[var] = value
    return env


class TestTheSettings:
    """Each unusable ``s3`` setting stops the build of the store."""

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"ANALYSIS_OBJECT_BUCKET": None}, "set ANALYSIS_OBJECT_BUCKET"),
            ({"ANALYSIS_OBJECT_REGION": " "}, "set ANALYSIS_OBJECT_REGION"),
            ({"ANALYSIS_OBJECT_BUCKET": "Jobs"}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_BUCKET": "jobs_1"}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_BUCKET": "j" * 64}, "ANALYSIS_OBJECT_BUCKET must match"),
            (
                {"ANALYSIS_OBJECT_REGION": "EU-WEST-1"},
                "ANALYSIS_OBJECT_REGION must match",
            ),
            (
                {"ANALYSIS_OBJECT_ENDPOINT": "http://objects.example"},
                "must be an https URL",
            ),
            (
                {"ANALYSIS_OBJECT_ENDPOINT": "objects.example"},
                "must be an https URL",
            ),
            (
                {"ANALYSIS_OBJECT_ENDPOINT": "https://user:hunter2@objects.example"},
                "and nothing more",
            ),
            (
                {"ANALYSIS_OBJECT_ENDPOINT": "https://objects.example/bucket"},
                "and nothing more",
            ),
            (
                {"ANALYSIS_OBJECT_ENDPOINT": "https://objects.example?k=hunter2"},
                "and nothing more",
            ),
            ({"ANALYSIS_OBJECT_ENDPOINT": "https://"}, "and nothing more"),
            (
                {"ANALYSIS_OBJECT_ENDPOINT": "https://objects.example:port"},
                "is not a URL",
            ),
            ({"ANALYSIS_OBJECT_ADDRESSING": "dns"}, "unknown value for"),
            ({"ANALYSIS_OBJECT_CREDENTIALS": None}, "set ANALYSIS_OBJECT_CREDENTIALS"),
            ({"ANALYSIS_OBJECT_CREDENTIALS": "token"}, "unknown value for"),
            (
                {"ANALYSIS_OBJECT_ACCESS_KEY_ID_FILE": None},
                "needs ANALYSIS_OBJECT_ACCESS_KEY_ID_FILE",
            ),
            (
                {"ANALYSIS_OBJECT_SECRET_ACCESS_KEY_FILE": "/nonexistent/hunter2"},
                "cannot read \\(FileNotFoundError\\)",
            ),
            (
                {"ANALYSIS_OBJECT_SECRET_ACCESS_KEY": "hunter2"},
                "unset ANALYSIS_OBJECT_SECRET_ACCESS_KEY,",
            ),
            (
                {"ANALYSIS_OBJECT_CREDENTIALS": "iam"},
                "reads no static key; unset ANALYSIS_OBJECT_ACCESS_KEY_ID_FILE",
            ),
            (
                {"ANALYSIS_OBJECT_ROOT": "/srv/objects"},
                "s3 object store takes no ANALYSIS_OBJECT_ROOT",
            ),
            (
                {"ANALYSIS_OBJECT_ENDPIONT": "http://objects.example"},
                "s3 object store takes no ANALYSIS_OBJECT_ENDPIONT",
            ),
        ],
    )
    def test_an_unusable_setting(self, env, change, message):
        with pytest.raises(StoreConfigError, match=message) as refused:
            build_store(changed(env, change))
        assert "hunter2" not in str(refused.value)

    def test_an_empty_key_file(self, env):
        Path(env["ANALYSIS_OBJECT_ACCESS_KEY_ID_FILE"]).write_text("\n")
        with pytest.raises(
            StoreConfigError, match="ANALYSIS_OBJECT_ACCESS_KEY_ID_FILE names is empty"
        ):
            build_store(env)

    @pytest.mark.parametrize("addressing", ["virtual", "path", "PATH", ""])
    def test_a_usable_store_starts(self, env, addressing):
        start(changed(env, {"ANALYSIS_OBJECT_ADDRESSING": addressing}))

    def test_an_endpoint_from_the_environment_is_ignored(self, env, monkeypatch):
        """``boto3`` would send each request to it, and pass by the https check."""
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:9")
        monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://127.0.0.1:9")
        start(env)


def iam(env: dict[str, str]) -> dict[str, str]:
    """``env`` in the ``iam`` mode, with no key file."""
    return changed(
        env,
        {"ANALYSIS_OBJECT_CREDENTIALS": "iam", **dict.fromkeys(S3_KEY_FILES.values())},
    )


class TestTheIamMode:
    """``iam`` reads only a credential that a platform issued."""

    def test_a_web_identity_starts(self, env, no_static_key, monkeypatch, tmp_path):
        """moto serves ``AssumeRoleWithWebIdentity`` in the test process."""
        token = tmp_path / "token"
        token.write_text("a-workload-token")
        monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token))
        monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/analysis")
        start(iam(env))

    def test_a_static_pair_in_the_environment_stops_the_startup(self, env):
        """moto puts a static key pair in the environment."""
        with pytest.raises(StoreConfigError, match="found credentials from env$"):
            build_store(iam(env))

    def test_a_static_profile_stops_the_startup(
        self, env, no_static_key, monkeypatch, tmp_path
    ):
        profile = tmp_path / "credentials"
        profile.write_text(
            "[default]\n"
            f"aws_access_key_id = {TEST_KEYS['aws_access_key_id']}\n"
            f"aws_secret_access_key = {TEST_KEYS['aws_secret_access_key']}\n"
        )
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(profile))
        with pytest.raises(
            StoreConfigError, match="found credentials from shared-credentials-file$"
        ) as refused:
            build_store(iam(env))
        assert TEST_KEYS["aws_secret_access_key"] not in str(refused.value)

    def test_no_credentials_stop_the_startup(self, env, no_static_key):
        with pytest.raises(StoreConfigError, match="found no credentials$"):
            build_store(iam(env))

    @pytest.mark.parametrize("method", sorted(PLATFORM_METHODS))
    def test_each_platform_method_passes(self, method, monkeypatch):
        credentials = SimpleNamespace(method=method)
        monkeypatch.setattr(boto3.Session, "get_credentials", lambda _: credentials)
        assert platform_session("a label").get_credentials() is credentials

    def test_the_api_key_mode_reads_the_files(self, env):
        """moto's pair in the environment differs from the pair in the files."""
        assert os.environ["AWS_ACCESS_KEY_ID"] != TEST_KEYS["aws_access_key_id"]
        store = build_object_store(env)
        frozen = store._client._request_signer._credentials.get_frozen_credentials()
        assert frozen.access_key == TEST_KEYS["aws_access_key_id"]


class TestTheStartupCheck:
    """``HeadBucket`` runs at the startup, and each refusal stops it."""

    def test_a_missing_bucket(self, env):
        env["ANALYSIS_OBJECT_BUCKET"] = "jobs-missing"
        with pytest.raises(StoreConfigError, match="'jobs-missing' does not exist"):
            start(env)

    def test_a_wrong_credential(self, env):
        """moto checks each key against its own IAM, which holds no key."""
        with pytest.raises(StoreConfigError, match="refuses access"):
            set_initial_no_auth_action_count(0)(start)(env)

    def test_an_endpoint_the_service_cannot_reach(self, env, monkeypatch):
        # One attempt and no retries, so the test does not wait for a back-off.
        monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
        env["ANALYSIS_OBJECT_ENDPOINT"] = "https://127.0.0.1:9"
        with pytest.raises(StoreConfigError, match="EndpointConnectionError"):
            start(env)


def test_each_boto3_call_runs_in_a_worker_thread(env, monkeypatch):
    """A ``boto3`` call blocks its thread, so it never runs on the event loop."""
    calls: list[tuple[str, int]] = []
    make_client = boto3.Session.client

    def client(session, *args, **kwargs):
        made = make_client(session, *args, **kwargs)
        made.meta.events.register(
            "before-call.s3",
            lambda model, **_: calls.append((model.name, threading.get_ident())),
        )
        return made

    monkeypatch.setattr(boto3.Session, "client", client)
    objects = build_object_store(env)

    async def every_call() -> int:
        await objects.check()
        await objects.put(KEY, b"data")
        await objects.get(KEY)
        await objects.delete(KEY)
        await listing(objects)
        return threading.get_ident()

    loop = asyncio.run(every_call())
    assert {name for name, _ in calls} == {
        "HeadBucket",
        "PutObject",
        "GetObject",
        "HeadObject",
        "DeleteObject",
        "ListObjectsV2",
    }
    assert loop not in {thread for _, thread in calls}


def test_without_boto3_the_startup_names_the_aws_extra(tmp_path, monkeypatch):
    """The image without the ``aws`` extra: ``boto3`` cannot be imported."""
    monkeypatch.setitem(sys.modules, "boto3", None)
    monkeypatch.delitem(sys.modules, "analysis_service.s3_objects", raising=False)
    env = {"ANALYSIS_OBJECT_STORE": "s3"}
    with pytest.raises(StoreConfigError, match=r"install analysis-service\[aws\]"):
        build_object_store(env)


def test_s3_env_refuses_to_run_without_moto(tmp_path):
    with pytest.raises(AssertionError, match="moto_s3"):
        s3_env(tmp_path)
