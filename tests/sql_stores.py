"""The settings of a ``sql`` store and an object store, for a test.

The migration runs once in each test process, as the deploy step runs it, and
each store gets a copy of that database, so a test never shares a store. The
object store is a filesystem root or an ``s3`` bucket. A test of the ``s3``
row takes the ``moto_s3`` fixture, so that moto serves the bucket in the test
process.

A SQLite store needs nothing. A PostgreSQL store needs a server, so the tests
that use one carry the ``postgres`` mark and run in the PostgreSQL lane of CI.
The lane reads these variables:

* ``TEST_POSTGRES_URL``: a user that can create databases, on a server with
  TLS, as a ``postgresql+asyncpg://`` URL with no password. Where it is unset,
  each lane test skips, except where ``REQUIRE_POSTGRES=1``, as in CI, where
  it fails.
* ``TEST_POSTGRES_PASSWORD_FILE``: the file that holds the password of that
  user.
* ``TEST_POSTGRES_CA_FILE``: the CA of the certificate of the server.
* ``TEST_POSTGRES_PLAIN_URL``: the same user on a server without TLS.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import boto3
import pytest
from alembic import command
from sqlalchemy.engine import URL, make_url

from analysis_service.migrations import alembic_config
from analysis_service.objects import S3_KEY_FILES, ObjectStoreName
from analysis_service.sql_store import engine_for

REQUIRE_POSTGRES = os.environ.get("REQUIRE_POSTGRES") == "1"


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def migrate(path: Path) -> None:
    """Run the deploy step on the SQLite database at ``path``."""
    upgrade({"ANALYSIS_DATABASE_URL": sqlite_url(path)})


def in_a_thread(work, *args):
    """Run ``work`` in a thread of its own, and return what it returns.

    Alembic's script and :func:`admin` each run an event loop, and a test can
    ask for a store inside one.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(work, *args).result()


def upgrade(env: dict[str, str]) -> None:
    """Run the deploy step on the database that ``env`` names."""
    in_a_thread(command.upgrade, alembic_config(env), "head")


@cache
def _migrated() -> Path:
    directory = tempfile.mkdtemp(prefix="sql-store-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    path = Path(directory) / "migrated.db"
    migrate(path)
    return path


def filesystem_env(directory: Path) -> dict[str, str]:
    """The settings of a fresh object root under ``directory``."""
    root = directory / "objects"
    root.mkdir(mode=0o700)
    return {"ANALYSIS_OBJECT_STORE": "filesystem", "ANALYSIS_OBJECT_ROOT": str(root)}


#: The key pair of each test bucket. moto accepts any pair.
TEST_KEYS = {
    "aws_access_key_id": "test-access-key-id",
    "aws_secret_access_key": "test-secret-access-key",
}
TEST_REGION = "us-east-1"


def s3_env(directory: Path) -> dict[str, str]:
    """The settings of a fresh bucket, with the key pair in two files under
    ``directory``. moto must serve the S3 API: take the ``moto_s3`` fixture."""
    assert os.environ.get("AWS_CONFIG_FILE") == os.devnull, "take moto_s3 first"
    env = {
        "ANALYSIS_OBJECT_STORE": "s3",
        "ANALYSIS_OBJECT_BUCKET": f"jobs-{uuid.uuid4().hex}",
        "ANALYSIS_OBJECT_REGION": TEST_REGION,
        "ANALYSIS_OBJECT_CREDENTIALS": "api_key",
    }
    for argument, var in S3_KEY_FILES.items():
        path = directory / var.lower()
        path.write_text(TEST_KEYS[argument], encoding="utf-8")
        path.chmod(0o600)
        env[var] = str(path)
    boto3.Session(**TEST_KEYS).client("s3", region_name=TEST_REGION).create_bucket(
        Bucket=env["ANALYSIS_OBJECT_BUCKET"]
    )
    return env


@dataclass(frozen=True)
class ObjectStoreEnv:
    """How a test reaches one object store: a row of :data:`OBJECT_ENVS`.

    No field has a default, so a new row states every fact.
    """

    #: The settings of a fresh store under a new directory.
    settings: Callable[[Path], dict[str, str]]
    #: The fixtures that a test takes before it builds the store.
    fixtures: tuple[str, ...]


#: Each object store, by its name. The table answers for every name of
#: :data:`ObjectStoreName`.
OBJECT_ENVS: dict[ObjectStoreName, ObjectStoreEnv] = {
    "filesystem": ObjectStoreEnv(settings=filesystem_env, fixtures=()),
    "s3": ObjectStoreEnv(settings=s3_env, fixtures=("moto_s3",)),
}


def object_env(
    request: pytest.FixtureRequest, objects: ObjectStoreName
) -> ObjectStoreEnv:
    """The row of ``objects``, after ``request`` takes each fixture it needs."""
    row = OBJECT_ENVS[objects]
    for fixture in row.fixtures:
        request.getfixturevalue(fixture)
    return row


def sql_env(directory: Path, objects: ObjectStoreName = "filesystem") -> dict[str, str]:
    """The settings of a fresh ``sql`` store on SQLite under ``directory``,
    which is new."""
    database = directory / "jobs.db"
    shutil.copy(_migrated(), database)
    return {
        "ANALYSIS_JOB_STORE": "sql",
        "ANALYSIS_DATABASE_URL": sqlite_url(database),
        **OBJECT_ENVS[objects].settings(directory),
    }


def database_of(env: dict[str, str]) -> str:
    """The path of the SQLite database that ``env`` names."""
    return env["ANALYSIS_DATABASE_URL"].partition(":///")[2]


def lane_url() -> URL:
    """``TEST_POSTGRES_URL``. Where it is unset, the test skips, or fails
    where ``REQUIRE_POSTGRES=1``."""
    value = os.environ.get("TEST_POSTGRES_URL", "").strip()
    if not value:
        reason = "TEST_POSTGRES_URL is unset, so the PostgreSQL lane cannot run"
        if REQUIRE_POSTGRES:
            pytest.fail(f"REQUIRE_POSTGRES=1, and {reason}")
        pytest.skip(reason)
    return make_url(value)


def lane_env(url: URL | None = None, **settings: str) -> dict[str, str]:
    """The database settings of the lane server, with the password credentials.

    ``url`` replaces ``TEST_POSTGRES_URL``, and each of ``settings`` replaces
    or adds a setting. An empty value removes a setting.
    """
    url = lane_url() if url is None else url
    env = {
        "ANALYSIS_DATABASE_URL": url.render_as_string(hide_password=False),
        "ANALYSIS_DATABASE_CREDENTIALS": "password",
        "ANALYSIS_DATABASE_PASSWORD_FILE": os.environ["TEST_POSTGRES_PASSWORD_FILE"],
        "ANALYSIS_DATABASE_CA_FILE": os.environ["TEST_POSTGRES_CA_FILE"],
    }
    env.update(settings)
    return {name: value for name, value in env.items() if value}


def admin(*statements: str) -> None:
    """Run each statement on the lane server, outside a transaction."""

    async def run() -> None:
        engine = engine_for(lane_env())
        try:
            async with engine.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                for statement in statements:
                    await connection.exec_driver_sql(statement)
        finally:
            await engine.dispose()

    in_a_thread(asyncio.run, run())


_DATABASES: list[str] = []


def _drop_databases() -> None:
    if _DATABASES:
        admin(
            *(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)' for name in _DATABASES)
        )


def new_database(template: str = "template1") -> str:
    """The name of a new database on the lane server, a copy of ``template``.

    The process drops it when it exits.
    """
    if not _DATABASES:
        atexit.register(_drop_databases)
    name = f"lane_{uuid.uuid4().hex}"
    _DATABASES.append(name)
    admin(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    return name


@cache
def _migrated_on_postgres() -> str:
    name = new_database()
    upgrade(lane_env(lane_url().set(database=name)))
    return name


def postgres_env(
    directory: Path, objects: ObjectStoreName = "filesystem"
) -> dict[str, str]:
    """The settings of a fresh ``sql`` store on PostgreSQL, with its object
    store under ``directory``, which is new."""
    url = lane_url().set(database=new_database(_migrated_on_postgres()))
    return {
        "ANALYSIS_JOB_STORE": "sql",
        **lane_env(url),
        **OBJECT_ENVS[objects].settings(directory),
    }
