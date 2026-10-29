"""The ``gcs`` object store: its settings, its identity check and its startup check.

``tests/test_object_stores.py`` runs what each row must give on ``gcs`` too,
and ``tests/test_job_store.py`` runs the whole store contract on it. Those
tests and the worker thread test here carry the ``gcs`` mark and need
``fake-gcs-server`` (see ``tests/sql_stores.py``). Each other test here needs
no server: a patched ``google.auth.default`` gives each credential type, and
no test reaches a network.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading

import google.cloud
import pytest
import requests
from google.api_core.exceptions import Forbidden, NotFound, RetryError
from google.auth import identity_pool, impersonated_credentials
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.cloud import storage
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account

from analysis_service import gcs_objects
from analysis_service.gcp_identity import STORAGE_SCOPE
from analysis_service.objects import build_object_store
from analysis_service.store_settings import StoreConfigError
from tests.sql_stores import PLATFORM_CREDENTIAL, gcs_env
from tests.test_object_stores import changed, every_call

#: The settings of a ``gcs`` store that builds with no server.
SETTINGS = {
    "ANALYSIS_OBJECT_STORE": "gcs",
    "ANALYSIS_OBJECT_BUCKET": "analysis-jobs",
    "ANALYSIS_OBJECT_CREDENTIALS": "iam",
}


@pytest.fixture
def asked(monkeypatch) -> list[dict]:
    """A metadata server credential from ``google.auth.default``, and the
    arguments of each call to it."""
    calls: list[dict] = []

    def default(**kwargs):
        calls.append(kwargs)
        return PLATFORM_CREDENTIAL, None

    monkeypatch.setattr("google.auth.default", default)
    return calls


@pytest.fixture
def env(asked) -> dict[str, str]:
    return dict(SETTINGS)


class TestTheSettings:
    """Each unusable ``gcs`` setting stops the build of the store."""

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"ANALYSIS_OBJECT_BUCKET": None}, "set ANALYSIS_OBJECT_BUCKET"),
            ({"ANALYSIS_OBJECT_BUCKET": " "}, "set ANALYSIS_OBJECT_BUCKET"),
            ({"ANALYSIS_OBJECT_BUCKET": "Jobs"}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_BUCKET": "jo"}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_BUCKET": "j" * 64}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_BUCKET": "-jobs"}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_BUCKET": "jobs_"}, "ANALYSIS_OBJECT_BUCKET must match"),
            ({"ANALYSIS_OBJECT_CREDENTIALS": None}, "set ANALYSIS_OBJECT_CREDENTIALS"),
            ({"ANALYSIS_OBJECT_CREDENTIALS": "token"}, "unknown value for"),
            (
                {"ANALYSIS_OBJECT_CREDENTIALS": "api_key"},
                "no api_key mode, because a key file and an HMAC key are out of scope",
            ),
            *(
                ({var: value}, f"gcs object store takes no {var}")
                for var, value in [
                    ("ANALYSIS_OBJECT_REGION", "us-east1"),
                    ("ANALYSIS_OBJECT_ENDPOINT", "https://objects.example"),
                    ("ANALYSIS_OBJECT_ADDRESSING", "path"),
                    ("ANALYSIS_OBJECT_ACCESS_KEY_ID_FILE", "/run/hunter2"),
                    ("ANALYSIS_OBJECT_SECRET_ACCESS_KEY_FILE", "/run/hunter2"),
                    ("ANALYSIS_OBJECT_SECRET_ACCESS_KEY", "hunter2"),
                    ("ANALYSIS_OBJECT_ROOT", "/srv/objects"),
                ]
            ),
        ],
    )
    def test_an_unusable_setting(self, env, change, message):
        with pytest.raises(StoreConfigError, match=message) as refused:
            build_object_store(changed(env, change))
        assert "hunter2" not in str(refused.value)

    @pytest.mark.parametrize("bucket", ["abc", "jobs.example_1", "a" * 63, "0-0"])
    def test_a_usable_store_builds(self, env, bucket):
        build_object_store(changed(env, {"ANALYSIS_OBJECT_BUCKET": bucket}))

    def test_the_endpoint_in_the_environment_is_ignored(self, env, monkeypatch):
        """The client would send each request to either one."""
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9")
        monkeypatch.setenv("API_ENDPOINT_OVERRIDE", "http://127.0.0.1:9")
        made: list[storage.Client] = []
        make_client = gcs_objects.make_client

        def kept(credentials):
            made.append(make_client(credentials))
            return made[-1]

        monkeypatch.setattr(gcs_objects, "make_client", kept)
        build_object_store(env)
        [client] = made
        assert client._connection.build_api_url("/b/analysis-jobs").startswith(
            "https://storage.googleapis.com/"
        )


class TestTheIdentity:
    """``iam`` reads only the identity of the metadata server (ADR 0105)."""

    def test_the_metadata_server_passes_with_the_one_scope(self, env, asked):
        build_object_store(env)
        assert asked == [{"scopes": [STORAGE_SCOPE]}]

    @pytest.mark.parametrize(
        "kind",
        [
            service_account.Credentials,
            user_credentials.Credentials,
            impersonated_credentials.Credentials,
            identity_pool.Credentials,
        ],
    )
    def test_each_other_type_stops_the_startup(self, kind, monkeypatch):
        # Only the type is read, so no instance needs a key or a source.
        found = object.__new__(kind)
        monkeypatch.setattr("google.auth.default", lambda **_: (found, None))
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        with pytest.raises(StoreConfigError) as refused:
            build_object_store(dict(SETTINGS))
        message = str(refused.value)
        assert "the iam mode of the gcs object store" in message
        assert "google.auth.compute_engine.Credentials" in message
        assert f"found {kind.__module__}.{kind.__qualname__}" in message
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in message

    def test_no_credential_stops_the_startup(self, monkeypatch):
        def default(**_):
            raise DefaultCredentialsError("hunter2 was not found")

        monkeypatch.setattr("google.auth.default", default)
        with pytest.raises(StoreConfigError, match="found no credential") as refused:
            build_object_store(dict(SETTINGS))
        assert "hunter2" not in str(refused.value)

    def test_a_user_file_names_its_variable_and_not_its_path(
        self, tmp_path, monkeypatch
    ):
        """The real chain reads the file, and sends no request to do so."""
        path = tmp_path / "hunter2.json"
        path.write_text(
            json.dumps(
                {
                    "type": "authorized_user",
                    "client_id": "client.apps.example",
                    "client_secret": "hunter2-secret",
                    "refresh_token": "hunter2-token",
                }
            )
        )
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
        with pytest.raises(StoreConfigError) as refused:
            build_object_store(dict(SETTINGS))
        message = str(refused.value)
        assert "found google.oauth2.credentials.Credentials" in message
        assert "reads GOOGLE_APPLICATION_CREDENTIALS first, so unset it" in message
        assert "hunter2" not in message and str(tmp_path) not in message


#: A ``buckets.get`` resource with every value that the row needs.
PASSING_BUCKET = {
    "kind": "storage#bucket",
    "name": "analysis-jobs",
    "versioning": {"enabled": False},
    "softDeletePolicy": {"retentionDurationSeconds": "0"},
    "defaultEventBasedHold": False,
    "iamConfiguration": {
        "publicAccessPrevention": "enforced",
        "uniformBucketLevelAccess": {"enabled": True},
    },
}


def answer(monkeypatch, resource: dict) -> None:
    """Make ``get_bucket`` return the ``Bucket`` that the library builds from
    ``resource``, as a ``buckets.get`` response would."""

    def get_bucket(client, name):
        bucket = storage.Bucket(client, name=name)
        bucket._set_properties(resource)
        return bucket

    monkeypatch.setattr(storage.Client, "get_bucket", get_bucket)


class TestTheStartupCheck:
    """A refused ``buckets.get`` or a refused bucket state stops the startup,
    with no provider text (ADR 0104)."""

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (NotFound("hunter2"), "the bucket 'analysis-jobs' does not exist"),
            (
                Forbidden("hunter2"),
                (
                    r"refused \(HTTP 403\); grant the service identity"
                    r" storage\.buckets\.get on the bucket, which"
                    r" roles/storage\.bucketViewer holds"
                ),
            ),
            (requests.ConnectionError("hunter2"), r"\(ConnectionError\)"),
            (RefreshError("hunter2"), r"\(RefreshError\)"),
            (RetryError("hunter2", None), r"\(RetryError\)"),
        ],
    )
    def test_a_refused_check(self, env, monkeypatch, error, message):
        def get_bucket(client, name):
            raise error

        monkeypatch.setattr(storage.Client, "get_bucket", get_bucket)
        objects = build_object_store(env)
        with pytest.raises(StoreConfigError, match=message) as refused:
            asyncio.run(objects.check())
        assert "hunter2" not in str(refused.value)

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"versioning": {"enabled": True}}, "versioning.enabled must be false"),
            (
                {"softDeletePolicy": {"retentionDurationSeconds": "604800"}},
                (
                    "softDeletePolicy.retentionDurationSeconds must be 0 (gcloud"
                    " storage buckets update gs://analysis-jobs --clear-soft-delete)"
                ),
            ),
            (
                {"retentionPolicy": {"retentionPeriod": "86400"}},
                "retentionPolicy.retentionPeriod must be unset",
            ),
            (
                {"retentionPolicy": {"retentionPeriod": "86400", "isLocked": True}},
                "retentionPolicy.retentionPeriod must be unset",
            ),
            ({"defaultEventBasedHold": True}, "defaultEventBasedHold must be false"),
            (
                {"objectRetention": {"mode": "Enabled"}},
                "objectRetention.mode must be unset",
            ),
            *(
                (
                    {"iamConfiguration": iam},
                    "iamConfiguration.publicAccessPrevention must be enforced",
                )
                for iam in [
                    {
                        "publicAccessPrevention": "inherited",
                        "uniformBucketLevelAccess": {"enabled": True},
                    },
                    {"uniformBucketLevelAccess": {"enabled": True}},
                ]
            ),
            *(
                (
                    {"iamConfiguration": iam},
                    "iamConfiguration.uniformBucketLevelAccess.enabled must be true",
                )
                for iam in [
                    {
                        "publicAccessPrevention": "enforced",
                        "uniformBucketLevelAccess": {"enabled": False},
                    },
                    {"publicAccessPrevention": "enforced"},
                ]
            ),
        ],
    )
    def test_a_refused_bucket_state(self, env, monkeypatch, change, message):
        answer(monkeypatch, {**PASSING_BUCKET, **change})
        objects = build_object_store(env)
        with pytest.raises(StoreConfigError) as refused:
            asyncio.run(objects.check())
        assert str(refused.value).endswith(f": {message}")

    def test_each_refused_state_is_named(self, env, monkeypatch):
        answer(monkeypatch, {"name": "analysis-jobs", "versioning": {"enabled": True}})
        objects = build_object_store(env)
        with pytest.raises(StoreConfigError) as refused:
            asyncio.run(objects.check())
        assert str(refused.value).endswith(
            ": versioning.enabled must be false;"
            " iamConfiguration.publicAccessPrevention must be enforced;"
            " iamConfiguration.uniformBucketLevelAccess.enabled must be true"
        )

    def test_a_bucket_with_every_needed_value_starts_the_store(self, env, monkeypatch):
        answer(monkeypatch, PASSING_BUCKET)
        asyncio.run(build_object_store(env).check())


@pytest.mark.gcs
def test_a_missing_bucket_stops_the_startup(fake_gcs):
    env = changed(dict(SETTINGS), {"ANALYSIS_OBJECT_BUCKET": "no-such-bucket"})
    with pytest.raises(StoreConfigError, match="'no-such-bucket' does not exist"):
        asyncio.run(build_object_store(env).check())


@pytest.mark.gcs
def test_each_client_call_runs_in_a_worker_thread(fake_gcs, tmp_path, monkeypatch):
    """A client call blocks its thread, so it never runs on the event loop."""
    calls: list[tuple[str, int]] = []
    send = requests.Session.request

    def request(session, method, url, *args, **kwargs):
        calls.append((method, threading.get_ident()))
        return send(session, method, url, *args, **kwargs)

    objects = build_object_store(gcs_env(tmp_path))
    monkeypatch.setattr(requests.Session, "request", request)

    loop = asyncio.run(every_call(objects))
    assert {method for method, _ in calls} == {"GET", "POST", "DELETE"}
    assert len(calls) == 5
    assert loop not in {thread for _, thread in calls}


def test_without_the_client_the_startup_names_the_gcs_extra(monkeypatch):
    """The image without the ``gcs`` extra: the client cannot be imported."""
    monkeypatch.setitem(sys.modules, "google.cloud.storage", None)
    monkeypatch.delattr(google.cloud, "storage")
    monkeypatch.delitem(sys.modules, "analysis_service.gcs_objects", raising=False)
    with pytest.raises(StoreConfigError, match=r"install analysis-service\[gcs\]"):
        build_object_store(dict(SETTINGS))


def test_gcs_env_refuses_to_run_without_the_emulator(tmp_path):
    with pytest.raises(AssertionError, match="fake_gcs"):
        gcs_env(tmp_path)
