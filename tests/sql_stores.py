"""The settings of a ``sql`` store on SQLite and an object store, for a test.

The migration runs once in each test process, as the deploy step runs it, and
each store gets a copy of that database, so a test never shares a store. The
object store is a filesystem root or an ``s3`` bucket. A test of the ``s3``
row takes the ``moto_s3`` fixture, so that moto serves the bucket in the test
process.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

import boto3
from alembic import command

from analysis_service.migrations import alembic_config
from analysis_service.objects import ObjectStoreName
from analysis_service.s3_objects import KEY_FILES
from analysis_service.sql_store import database_url


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def migrate(path: Path) -> None:
    """Run the deploy step on the SQLite database at ``path``.

    In a thread of its own, because Alembic's script runs an event loop, and a
    test can ask for a store inside one.
    """
    url = database_url({"ANALYSIS_DATABASE_URL": sqlite_url(path)})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(command.upgrade, alembic_config(url), "head").result()


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
    for argument, var in KEY_FILES.items():
        path = directory / var.lower()
        path.write_text(TEST_KEYS[argument], encoding="utf-8")
        path.chmod(0o600)
        env[var] = str(path)
    boto3.Session(**TEST_KEYS).client("s3", region_name=TEST_REGION).create_bucket(
        Bucket=env["ANALYSIS_OBJECT_BUCKET"]
    )
    return env


#: The settings of each object store, by its name.
OBJECT_ENVS: dict[ObjectStoreName, Callable[[Path], dict[str, str]]] = {
    "filesystem": filesystem_env,
    "s3": s3_env,
}


def sql_env(directory: Path, objects: ObjectStoreName = "filesystem") -> dict[str, str]:
    """The settings of a fresh ``sql`` store under ``directory``, which is new."""
    database = directory / "jobs.db"
    shutil.copyfile(_migrated(), database)
    return {
        "ANALYSIS_JOB_STORE": "sql",
        "ANALYSIS_DATABASE_URL": sqlite_url(database),
        **OBJECT_ENVS[objects](directory),
    }
