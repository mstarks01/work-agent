"""The settings of a ``sql`` store on SQLite and a filesystem root, for a test.

The migration runs once in each test process, as the deploy step runs it, and
each store gets a copy of that database, so a test never shares a store.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

from alembic import command

from analysis_service.migrations import alembic_config
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


def sql_env(directory: Path) -> dict[str, str]:
    """The settings of a fresh ``sql`` store under ``directory``, which is new."""
    database = directory / "jobs.db"
    shutil.copyfile(_migrated(), database)
    root = directory / "objects"
    root.mkdir(mode=0o700)
    return {
        "ANALYSIS_JOB_STORE": "sql",
        "ANALYSIS_DATABASE_URL": sqlite_url(database),
        "ANALYSIS_OBJECT_STORE": "filesystem",
        "ANALYSIS_OBJECT_ROOT": str(root),
    }
