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
from pathlib import Path

import google.cloud
import pytest
import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from google.api_core.exceptions import Forbidden, NotFound, RetryError
from google.auth import compute_engine, identity_pool, impersonated_credentials
from google.auth.exceptions import (
    DefaultCredentialsError,
    GoogleAuthError,
    RefreshError,
)
from google.cloud import storage
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account

from katra import gcs_objects
from katra.gcp_identity import STORAGE_SCOPE
from katra.objects import GCS_KEY_FILE_VAR, build_object_store
from katra.store_settings import StoreConfigError
from tests.sql_stores import PLATFORM_CREDENTIAL, gcs_env
from tests.test_object_stores import changed, every_call

#: The settings of a ``gcs`` store that builds with no server.
SETTINGS = {
    "KATRA_OBJECT_STORE": "gcs",
    "KATRA_OBJECT_BUCKET": "analysis-jobs",
    "KATRA_OBJECT_CREDENTIALS": "iam",
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
            ({"KATRA_OBJECT_BUCKET": None}, "set KATRA_OBJECT_BUCKET"),
            ({"KATRA_OBJECT_BUCKET": " "}, "set KATRA_OBJECT_BUCKET"),
            ({"KATRA_OBJECT_BUCKET": "Jobs"}, "KATRA_OBJECT_BUCKET must match"),
            ({"KATRA_OBJECT_BUCKET": "jo"}, "KATRA_OBJECT_BUCKET must match"),
            ({"KATRA_OBJECT_BUCKET": "j" * 64}, "KATRA_OBJECT_BUCKET must match"),
            ({"KATRA_OBJECT_BUCKET": "-jobs"}, "KATRA_OBJECT_BUCKET must match"),
            ({"KATRA_OBJECT_BUCKET": "jobs_"}, "KATRA_OBJECT_BUCKET must match"),
            ({"KATRA_OBJECT_CREDENTIALS": None}, "set KATRA_OBJECT_CREDENTIALS"),
            ({"KATRA_OBJECT_CREDENTIALS": "token"}, "unknown value for"),
            (
                {"KATRA_OBJECT_CREDENTIALS": "api_key"},
                "needs KATRA_OBJECT_SERVICE_ACCOUNT_KEY_FILE; it is unset",
            ),
            (
                {"KATRA_OBJECT_SERVICE_ACCOUNT_KEY": "hunter2"},
                "unset KATRA_OBJECT_SERVICE_ACCOUNT_KEY, which this service refuses",
            ),
            *(
                ({var: value}, f"gcs object store takes no {var}")
                for var, value in [
                    ("KATRA_OBJECT_REGION", "us-east1"),
                    ("KATRA_OBJECT_ENDPOINT", "https://objects.example"),
                    ("KATRA_OBJECT_ADDRESSING", "path"),
                    ("KATRA_OBJECT_ACCESS_KEY_ID_FILE", "/run/hunter2"),
                    ("KATRA_OBJECT_SECRET_ACCESS_KEY_FILE", "/run/hunter2"),
                    ("KATRA_OBJECT_SECRET_ACCESS_KEY", "hunter2"),
                    ("KATRA_OBJECT_ROOT", "/srv/objects"),
                    ("KATRA_OBJECT_SERVICE_ACCOUNT_KEY_FILE", "/run/hunter2"),
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
        build_object_store(changed(env, {"KATRA_OBJECT_BUCKET": bucket}))

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

    def test_a_credential_of_another_universe_stops_the_startup(self, monkeypatch):
        """The metadata server can give a credential of another universe."""
        other = compute_engine.Credentials(universe_domain="example.com")
        monkeypatch.setattr("google.auth.default", lambda **_: (other, None))
        with pytest.raises(
            StoreConfigError,
            match=r"cannot build its client .*another universe domain",
        ):
            build_object_store(dict(SETTINGS))

    def test_a_universe_domain_it_cannot_read_stops_the_startup(self, env, monkeypatch):
        """The credential asks the metadata server for its universe domain."""

        def unreachable(credentials):
            raise GoogleAuthError("metadata server unreachable")

        monkeypatch.setattr("katra.gcs_objects.make_client", unreachable)
        with pytest.raises(
            StoreConfigError, match=r"cannot read the universe domain .*GoogleAuthError"
        ):
            build_object_store(env)

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


def key_env(tmp_path, content: str | bytes, mode: int = 0o600) -> dict[str, str]:
    """The settings of the ``api_key`` mode, with ``content`` in its key file."""
    path = tmp_path / "key.json"
    if isinstance(content, str):
        content = content.encode()
    path.write_bytes(content)
    path.chmod(mode)
    return {
        **SETTINGS,
        "KATRA_OBJECT_CREDENTIALS": "api_key",
        GCS_KEY_FILE_VAR: str(path),
    }


class TestTheApiKeyMode:
    """``api_key`` reads one service account key file (ADR 0108)."""

    @pytest.mark.parametrize("indent", [2, None])
    def test_a_service_account_key_passes_with_the_one_scope(
        self, tmp_path, key_info, indent, monkeypatch
    ):
        """The downloaded file has line breaks, and a key that the Settings
        page writes has none. The mode never calls the ADC chain."""

        def default(**_):
            raise AssertionError("the api_key mode called google.auth.default")

        monkeypatch.setattr("google.auth.default", default)
        store = build_object_store(
            key_env(tmp_path, json.dumps(key_info, indent=indent))
        )
        credentials = store._client._credentials
        assert isinstance(credentials, service_account.Credentials)
        assert credentials.service_account_email == key_info["client_email"]
        assert credentials.scopes == [STORAGE_SCOPE]

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("", "is not UTF-8 JSON"),
            ("hunter2", "is not UTF-8 JSON"),
            (b"\xff\xfe", "is not UTF-8 JSON"),
            ("[" * 100_000, "is longer than 16,384 bytes"),
            ('["hunter2"]', "its type must be service_account"),
            ('{"private_key": "hunter2"}', "its type must be service_account"),
        ],
    )
    def test_a_file_that_is_not_a_key_stops_the_startup(
        self, tmp_path, content, message
    ):
        with pytest.raises(StoreConfigError, match=message) as refused:
            build_object_store(key_env(tmp_path, content))
        assert "hunter2" not in str(refused.value)

    def test_json_too_deep_to_parse_stops_the_startup(
        self, tmp_path, key_info, monkeypatch
    ):
        """The depth at which the parser raises ``RecursionError`` depends on
        the interpreter, so the parser raises it here directly."""

        def too_deep(*_, **__):
            raise RecursionError

        monkeypatch.setattr("katra.gcs_objects.json.loads", too_deep)
        with pytest.raises(StoreConfigError, match="is not UTF-8 JSON"):
            build_object_store(key_env(tmp_path, json.dumps(key_info)))

    @pytest.mark.parametrize(
        "kind",
        [
            {"type": "authorized_user", "refresh_token": "hunter2"},
            {
                "type": "external_account",
                "credential_source": {"executable": {"command": "/bin/hunter2"}},
            },
            {"type": "impersonated_service_account", "hunter2": "x"},
        ],
    )
    def test_another_kind_of_credential_file_stops_the_startup(self, tmp_path, kind):
        with pytest.raises(
            StoreConfigError, match="is not a service account key"
        ) as refused:
            build_object_store(key_env(tmp_path, json.dumps(kind)))
        assert "hunter2" not in str(refused.value)

    def test_a_key_with_a_broken_private_key_stops_the_startup(
        self, tmp_path, key_info
    ):
        broken = key_info | {"private_key": "-----BEGIN PRIVATE KEY-----\nhunter2"}
        with pytest.raises(StoreConfigError, match="is not usable") as refused:
            build_object_store(key_env(tmp_path, json.dumps(broken)))
        assert "hunter2" not in str(refused.value)

    @pytest.mark.parametrize(
        "private_key",
        [ec.generate_private_key(ec.SECP256R1()), ed25519.Ed25519PrivateKey.generate()],
    )
    def test_a_key_that_is_not_rsa_stops_the_startup(
        self, tmp_path, key_info, private_key
    ):
        """The library loads the key, and only a signature finds it."""
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        other = key_info | {"private_key": pem}
        with pytest.raises(StoreConfigError, match="is not usable") as refused:
            build_object_store(key_env(tmp_path, json.dumps(other)))
        assert "PRIVATE KEY" not in str(refused.value)

    def test_a_key_of_an_unsupported_algorithm_stops_the_startup(
        self, tmp_path, key_info, monkeypatch
    ):
        """``cryptography`` raises ``UnsupportedAlgorithm`` for a key type it
        cannot load, and that error is not a ``ValueError``."""

        def unsupported(*_, **__):
            raise UnsupportedAlgorithm("hunter2")

        monkeypatch.setattr(
            service_account.Credentials, "from_service_account_info", unsupported
        )
        with pytest.raises(
            StoreConfigError, match=r"is not usable \(UnsupportedAlgorithm\)"
        ) as refused:
            build_object_store(key_env(tmp_path, json.dumps(key_info)))
        assert "hunter2" not in str(refused.value)

    @pytest.mark.parametrize("universe", ["example.com", "", None, 7])
    def test_a_key_of_another_universe_stops_the_startup(
        self, tmp_path, key_info, universe
    ):
        """An empty or null domain also makes the credential sign its own
        token and skip the token_uri."""
        other = key_info | {"universe_domain": universe}
        with pytest.raises(StoreConfigError, match="must have no universe_domain"):
            build_object_store(key_env(tmp_path, json.dumps(other)))

    def test_a_key_with_no_universe_domain_passes(self, tmp_path, key_info):
        other = {k: v for k, v in key_info.items() if k != "universe_domain"}
        build_object_store(key_env(tmp_path, json.dumps(other)))

    @pytest.mark.parametrize(
        "token_uri",
        [None, "https://hunter2.example/token", "http://oauth2.googleapis.com/token"],
    )
    def test_a_key_with_another_token_uri_stops_the_startup(
        self, tmp_path, key_info, token_uri
    ):
        other = {k: v for k, v in key_info.items() if k != "token_uri"}
        if token_uri is not None:
            other["token_uri"] = token_uri
        with pytest.raises(
            StoreConfigError, match="must have the token_uri"
        ) as refused:
            build_object_store(key_env(tmp_path, json.dumps(other)))
        assert "hunter2" not in str(refused.value)

    def test_a_key_that_others_can_read_stops_the_startup(self, tmp_path, key_info):
        with pytest.raises(StoreConfigError, match="lets other users read it"):
            build_object_store(key_env(tmp_path, json.dumps(key_info), 0o644))

    def test_a_missing_file_stops_the_startup(self, tmp_path, key_info):
        env = key_env(tmp_path, json.dumps(key_info))
        Path(env[GCS_KEY_FILE_VAR]).unlink()
        with pytest.raises(StoreConfigError, match="cannot read"):
            build_object_store(env)


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
    env = changed(dict(SETTINGS), {"KATRA_OBJECT_BUCKET": "no-such-bucket"})
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
    monkeypatch.delitem(sys.modules, "katra.gcs_objects", raising=False)
    with pytest.raises(StoreConfigError, match=r"install katra\[gcs\]"):
        build_object_store(dict(SETTINGS))


def test_gcs_env_refuses_to_run_without_the_emulator(tmp_path):
    with pytest.raises(AssertionError, match="fake_gcs"):
        gcs_env(tmp_path)
