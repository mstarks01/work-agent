"""The PostgreSQL row of the ``sql`` store: its settings, its TLS and its pool.

The settings tests need no server. The tests marked ``postgres`` run in the
PostgreSQL lane of CI, against a server with TLS and a server without it (see
``tests/sql_stores.py``). The contract suite in ``tests/test_job_store.py``
also runs on PostgreSQL in that lane.
"""

from __future__ import annotations

import asyncio
import os
import ssl
import sys
import types
from pathlib import Path
from secrets import token_hex
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from analysis_service.sql_store import (
    DATABASE_IAM,
    DATABASES,
    METADATA,
    DatabaseIam,
    database_url,
    engine_for,
)
from analysis_service.store_settings import StoreConfigError
from tests.sql_stores import admin, lane_env, lane_url, new_database, upgrade

URL = "postgresql+asyncpg://analysis@db.internal:5432/jobs"


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    password = tmp_path / "password"
    password.write_text("hunter2\n")
    return {
        "ANALYSIS_DATABASE_URL": URL,
        "ANALYSIS_DATABASE_CREDENTIALS": "password",
        "ANALYSIS_DATABASE_PASSWORD_FILE": str(password),
    }


@pytest.fixture
def iam_env(env) -> dict[str, str]:
    del env["ANALYSIS_DATABASE_PASSWORD_FILE"]
    return env | {
        "ANALYSIS_DATABASE_CREDENTIALS": "iam",
        "ANALYSIS_DATABASE_IAM": "rds",
        "ANALYSIS_DATABASE_REGION": "eu-west-1",
    }


def connect_args(env: dict[str, str]) -> dict:
    """The connect arguments that the PostgreSQL row gives the driver."""
    return DATABASES["postgresql+asyncpg"].connect(database_url(env), env)


class TestTheSettings:
    def test_a_connection_gets_verify_full_tls_and_the_password_in_the_file(self, env):
        args = connect_args(env)
        assert set(args) == {"port", "ssl", "password"}
        assert args["password"] == "hunter2"
        assert args["ssl"].check_hostname
        assert args["ssl"].verify_mode == ssl.CERT_REQUIRED

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (
                {"ANALYSIS_DATABASE_CREDENTIALS": None},
                "set ANALYSIS_DATABASE_CREDENTIALS",
            ),
            ({"ANALYSIS_DATABASE_CREDENTIALS": "trust"}, "unknown value 'trust'"),
            (
                {"ANALYSIS_DATABASE_PASSWORD_FILE": None},
                "set ANALYSIS_DATABASE_PASSWORD_FILE",
            ),
            (
                {"ANALYSIS_DATABASE_PASSWORD_FILE": "/nonexistent/password"},
                r"cannot read \(FileNotFoundError\)",
            ),
            (
                {"ANALYSIS_DATABASE_PASSWORD": "hunter2"},
                "unset ANALYSIS_DATABASE_PASSWORD$",
            ),
            (
                {"ANALYSIS_DATABASE_IAM": "rds"},
                "with password credentials takes no ANALYSIS_DATABASE_IAM; unset it$",
            ),
            (
                {"ANALYSIS_DATABASE_REGION": "eu-west-1"},
                (
                    "with password credentials takes no ANALYSIS_DATABASE_REGION;"
                    " unset it$"
                ),
            ),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+asyncpg://analysis:hunter2@db/x"},
                "holds a password",
            ),
            ({"ANALYSIS_DATABASE_URL": URL + "?ssl=disable"}, "takes no query"),
            (
                {"ANALYSIS_DATABASE_URL": URL + "?host=/run/postgresql"},
                "takes no query",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+asyncpg://analysis@/jobs"},
                "needs a user and a network host",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+asyncpg://db.internal/jobs"},
                "needs a user and a network host",
            ),
            (
                {"ANALYSIS_DATABASE_URL": "postgresql+asyncpg://analysis@db.internal"},
                "names no database",
            ),
            (
                {"ANALYSIS_DATABASE_CA_FILE": "/nonexistent/ca.pem"},
                r"cannot read as a CA \(FileNotFoundError\)",
            ),
            (
                {"ANALYSIS_DATABASE_CA_FILE": os.devnull},
                r"cannot read as a CA \(SSLError\)",
            ),
            (
                {"ANALYSIS_DATABASE_CAFILE": "/etc/ssl/ca.pem"},
                (
                    "PostgreSQL database with password credentials takes no"
                    " ANALYSIS_DATABASE_CAFILE; unset it$"
                ),
            ),
        ],
    )
    def test_an_unusable_setting_stops_the_startup(self, env, change, message):
        for name, value in change.items():
            if value is None:
                del env[name]
            else:
                env[name] = value
        with pytest.raises(StoreConfigError, match=message) as refused:
            engine_for(env)
        assert "hunter2" not in str(refused.value)

    @pytest.mark.parametrize(
        ("url", "port"),
        [
            ("postgresql+asyncpg://analysis@db.internal:6432/jobs", 6432),
            ("postgresql+asyncpg://analysis@db.internal/jobs", 5432),
        ],
    )
    def test_the_port_of_the_url_or_the_default_reaches_the_driver(
        self, env, url, port
    ):
        env["ANALYSIS_DATABASE_URL"] = url
        assert connect_args(env)["port"] == port

    def test_the_libpq_variables_do_not_change_the_connection(self, env, monkeypatch):
        """asyncpg fills each argument that the store does not pass from the
        ``PG*`` variables, so the store passes each one that names a server."""
        env["ANALYSIS_DATABASE_URL"] = "postgresql+asyncpg://analysis@db.internal/jobs"
        clean = connect_args(env)
        libpq = {"PGPORT": "6543", "PGDATABASE": "other", "PGHOST": "elsewhere"}
        for name, value in libpq.items():
            monkeypatch.setenv(name, value)
        args = connect_args(env | libpq)
        assert args["port"] == clean["port"] == 5432
        assert args["password"] == clean["password"]
        url = database_url(env | libpq)
        assert (url.host, url.database) == ("db.internal", "jobs")

    def test_an_empty_password_file_stops_the_startup(self, env, tmp_path):
        empty = tmp_path / "empty"
        empty.write_text("\n")
        env["ANALYSIS_DATABASE_PASSWORD_FILE"] = str(empty)
        with pytest.raises(StoreConfigError, match="holds the database password"):
            engine_for(env)


class StubRds:
    """An RDS client that signs nothing, and records each token request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def generate_db_auth_token(self, **request: object) -> str:
        self.requests.append(request)
        return f"token-{len(self.requests)}"


def stub_boto3(monkeypatch, rds: StubRds, clients: list | None = None) -> None:
    """Put in place a ``boto3`` whose session holds the role of an instance,
    and whose RDS client is ``rds``. Each client it builds goes in ``clients``."""

    class Session:
        def get_credentials(self) -> types.SimpleNamespace:
            return types.SimpleNamespace(method="iam-role")

        def client(self, service: str, region_name: str) -> StubRds:
            if clients is not None:
                clients.append((service, region_name))
            return rds

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=Session))


class TestTheIamCredentials:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"ANALYSIS_DATABASE_IAM": None}, "set ANALYSIS_DATABASE_IAM"),
            ({"ANALYSIS_DATABASE_IAM": "entra"}, "unknown value 'entra'"),
            ({"ANALYSIS_DATABASE_REGION": None}, "set ANALYSIS_DATABASE_REGION"),
            (
                {"ANALYSIS_DATABASE_PASSWORD_FILE": "/run/secrets/password"},
                (
                    "with iam credentials takes no ANALYSIS_DATABASE_PASSWORD_FILE;"
                    " unset it$"
                ),
            ),
        ],
    )
    def test_an_unusable_setting_stops_the_startup(self, iam_env, change, message):
        for name, value in change.items():
            if value is None:
                del iam_env[name]
            else:
                iam_env[name] = value
        with pytest.raises(StoreConfigError, match=message):
            engine_for(iam_env)

    def test_a_static_pair_in_the_environment_stops_the_startup(self, iam_env, moto_s3):
        """moto puts a static key pair in the environment."""
        with pytest.raises(StoreConfigError, match="found credentials from env$"):
            engine_for(iam_env)

    def test_a_web_identity_signs_the_token(
        self, iam_env, no_static_key, monkeypatch, tmp_path
    ):
        """moto serves ``AssumeRoleWithWebIdentity`` in the test process."""
        token = tmp_path / "token"
        token.write_text("a-workload-token")
        monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token))
        monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/analysis")
        signed = asyncio.run(connect_args(iam_env)["password"]())
        assert signed.startswith("db.internal:5432/?Action=connect")

    def test_a_missing_boto3_names_the_aws_extra(self, iam_env, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)
        with pytest.raises(
            StoreConfigError, match=r"needs boto3.*analysis-service\[aws\]"
        ):
            engine_for(iam_env)

    def test_each_call_signs_a_new_rds_token_for_the_url(self, iam_env, monkeypatch):
        rds = StubRds()
        clients: list[tuple[str, str]] = []
        stub_boto3(monkeypatch, rds, clients)
        token = connect_args(iam_env)["password"]
        assert [asyncio.run(token()), asyncio.run(token())] == ["token-1", "token-2"]
        assert clients == [("rds", "eu-west-1")]
        assert rds.requests[0] == {
            "DBHostname": "db.internal",
            "Port": 5432,
            "DBUsername": "analysis",
            "Region": "eu-west-1",
        }

    def test_the_token_is_signed_for_the_port_that_the_driver_gets(
        self, iam_env, monkeypatch
    ):
        rds = StubRds()
        stub_boto3(monkeypatch, rds)
        monkeypatch.setenv("PGPORT", "6543")
        iam_env["ANALYSIS_DATABASE_URL"] = (
            "postgresql+asyncpg://analysis@db.internal/jobs"
        )
        args = connect_args(iam_env)
        asyncio.run(args["password"]())
        assert rds.requests[0]["Port"] == args["port"] == 5432


def stub_token(monkeypatch, tokens: list[str]) -> list[str]:
    """Make the RDS row return the last of ``tokens``. Each token it returns."""
    returned: list[str] = []

    async def token() -> str:
        returned.append(tokens[-1])
        return tokens[-1]

    monkeypatch.setitem(
        DATABASE_IAM, "rds", DatabaseIam(extra="aws", token=lambda url, env: token)
    )
    return returned


def iam_lane_env(url) -> dict[str, str]:
    return lane_env(
        url,
        ANALYSIS_DATABASE_CREDENTIALS="iam",
        ANALYSIS_DATABASE_PASSWORD_FILE="",
        ANALYSIS_DATABASE_IAM="rds",
    )


async def select_one(env: dict[str, str]) -> int:
    engine = engine_for(env)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(select(1))
    finally:
        await engine.dispose()


@pytest.mark.postgres
class TestTheLane:
    @pytest.fixture(autouse=True)
    def _server(self) -> None:
        """Skip, or fail where ``REQUIRE_POSTGRES=1``, before a test reads the lane."""
        lane_url()

    def test_each_connection_uses_tls(self):
        query = text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")

        async def scenario() -> bool:
            engine = engine_for(lane_env())
            try:
                async with engine.connect() as connection:
                    return await connection.scalar(query)
            finally:
                await engine.dispose()

        assert asyncio.run(scenario()) is True

    def test_a_server_without_tls_is_refused(self):
        plain = make_url(os.environ["TEST_POSTGRES_PLAIN_URL"])
        with pytest.raises(ConnectionError, match="rejected SSL upgrade"):
            asyncio.run(select_one(lane_env(plain)))

    def test_a_certificate_for_another_host_is_refused(self):
        other_host = lane_url().set(host="127.0.0.1")
        with pytest.raises(ssl.SSLCertVerificationError, match="IP address mismatch"):
            asyncio.run(select_one(lane_env(other_host)))

    def test_a_certificate_from_an_untrusted_ca_is_refused(self):
        system_bundle = lane_env(ANALYSIS_DATABASE_CA_FILE="")
        with pytest.raises(ssl.SSLCertVerificationError):
            asyncio.run(select_one(system_bundle))

    def test_the_iam_token_callable_runs_on_each_new_connection(self, monkeypatch):
        """#1525: an async password callable reaches asyncpg through SQLAlchemy."""
        password = Path(os.environ["TEST_POSTGRES_PASSWORD_FILE"]).read_text().strip()
        returned = stub_token(monkeypatch, [password])

        async def scenario() -> None:
            engine = engine_for(iam_lane_env(lane_url()))
            try:
                async with engine.connect() as first, engine.connect() as second:
                    await first.scalar(select(1))
                    await second.scalar(select(1))
                async with engine.connect() as pooled:
                    await pooled.scalar(select(1))
            finally:
                await engine.dispose()

        asyncio.run(scenario())
        assert len(returned) == 2

    def test_a_pooled_connection_outlives_its_expired_token(self, monkeypatch):
        """#1525: the server checks a token only when a connection opens.

        An expired password stands in for an expired IAM token.
        """
        role = f"lane_{uuid4().hex}"
        first, second = token_hex(16), token_hex(16)
        admin(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{first}'")
        tokens = [first]
        returned = stub_token(monkeypatch, tokens)

        async def scenario() -> None:
            engine = engine_for(iam_lane_env(lane_url().set(username=role)))
            try:
                async with engine.connect() as opened:
                    await opened.scalar(select(1))
                admin(f"ALTER ROLE \"{role}\" VALID UNTIL '-infinity'")
                async with engine.connect() as pooled:
                    assert await pooled.scalar(select(1)) == 1
                    with pytest.raises(asyncpg.InvalidPasswordError):
                        async with engine.connect():
                            pass
                    admin(
                        f"ALTER ROLE \"{role}\" PASSWORD '{second}'"
                        " VALID UNTIL 'infinity'"
                    )
                    tokens.append(second)
                    async with engine.connect() as fresh:
                        assert await fresh.scalar(select(1)) == 1
            finally:
                await engine.dispose()

        try:
            asyncio.run(scenario())
        finally:
            admin(f'DROP ROLE IF EXISTS "{role}"')
        assert returned == [first, first, second]

    def test_the_migration_creates_the_tables_the_store_reads(self):
        """Two readers of one schema: the Alembic tree and the store's tables."""
        env = lane_env(lane_url().set(database=new_database()))
        upgrade(env)

        async def scenario() -> list:
            engine = engine_for(env)
            try:
                async with engine.connect() as connection:
                    return await connection.run_sync(
                        lambda sync: compare_metadata(
                            MigrationContext.configure(sync), METADATA
                        )
                    )
            finally:
                await engine.dispose()

        assert asyncio.run(scenario()) == []
