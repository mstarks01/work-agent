"""The object store: where the ``sql`` job store keeps sources and reports (ADR 0065).

A job's sources and its report are each one object, under the key that
:func:`object_key` gives. The job store writes each object once, writes the
object before the row that names it, and keeps the SHA-256 of each object in
that row. So this module stores bytes and checks nothing about them, and the
job store checks the digest on each read.

A deployment selects a backend with ``ANALYSIS_OBJECT_STORE``. Each value is a
row of :data:`OBJECT_STORES`, and :func:`build_object_store` builds it. An
unset or unknown value stops the startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import stat
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast, get_args

from analysis_service.jobs import JOB_ID_PATTERN, JobStoreConfigError, backend_name

#: The two objects of a job. A job has one report, which the service writes
#: once, so no name carries a revision.
ObjectName = Literal["sources.json", "report.json"]

#: An owner ID is a UUIDv4 that the service generates (ADR 0064). This shape
#: admits only lowercase letters, digits and hyphens, so no part of a key can
#: hold a separator or a dot segment.
_OWNER_ID = re.compile(r"[0-9a-z][0-9a-z-]{0,63}")


def object_key(owner_id: str, job_id: str, name: ObjectName) -> str:
    """The key of one object of a job: ``<owner-id>/<job-id>/<name>``.

    **The one rule for a key.** A key holds only the IDs that the service
    generates, never a name, an email or a ``sub``. A part outside its shape
    raises, so a key never reaches outside the owner's and the job's prefix.
    """
    if not _OWNER_ID.fullmatch(owner_id):
        raise ValueError("an owner ID is not a key part")
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("a job ID is not a key part")
    if name not in get_args(ObjectName):
        raise ValueError(f"{name!r} is not an object name")
    return f"{owner_id}/{job_id}/{name}"


def key_parts(key: str) -> tuple[str, str, ObjectName] | None:
    """The owner ID, the job ID and the object name of ``key``, or ``None``
    where :func:`object_key` cannot give ``key``.

    :func:`object_key` checks each part, so this function holds no rule of
    its own.
    """
    parts = key.split("/")
    if len(parts) != 3:
        return None
    owner_id, job_id, name = parts
    try:
        object_key(owner_id, job_id, cast(ObjectName, name))
    except ValueError:
        return None
    return owner_id, job_id, cast(ObjectName, name)


class ObjectStore(Protocol):
    """Bytes under keys that :func:`object_key` gives."""

    async def check(self) -> None:
        """Raise :class:`JobStoreConfigError` where the store cannot serve.

        The startup runs it after the schema check and before the restart scan.
        """

    async def put(self, key: str, data: bytes) -> None:
        """Keep ``data`` under ``key``. Raise where ``key`` already holds an object."""

    async def get(self, key: str) -> bytes | None:
        """The bytes under ``key``, or ``None`` where it holds none."""

    async def delete(self, key: str) -> bool:
        """Remove the object under ``key``. False where it holds none."""

    def list(self) -> AsyncIterator[tuple[str, datetime]]:
        """Each key in the store, with the time of its last write in UTC.

        The orphan sweep is the only reader. A key can have any shape, so the
        sweep checks each one with :func:`key_parts`.
        """
        ...


class FilesystemObjectStore:
    """Objects as files under one directory that only the service user can open.

    ``ANALYSIS_OBJECT_ROOT`` names the directory. Each directory this store
    makes is ``0700`` and each file ``0600``. A put creates its file
    exclusively, and no read or write opens a file through a symbolic link.
    The listing enters no symbolic link.
    Only the service user can make an entry under the root, which
    :meth:`check` holds.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> FilesystemObjectStore:
        value = env.get("ANALYSIS_OBJECT_ROOT", "")
        if not value:
            raise JobStoreConfigError(
                "set ANALYSIS_OBJECT_ROOT for the filesystem store"
            )
        root = Path(value)
        if not root.is_absolute():
            raise JobStoreConfigError("ANALYSIS_OBJECT_ROOT must be an absolute path")
        return cls(root)

    async def check(self) -> None:
        """Refuse a root that is missing, not a directory, owned by another user,
        or open to a group or to others."""
        try:
            held = os.lstat(self._root)
        except FileNotFoundError:
            raise JobStoreConfigError(
                f"the object root {str(self._root)!r} does not exist"
            ) from None
        if not stat.S_ISDIR(held.st_mode):
            raise JobStoreConfigError(
                f"the object root {str(self._root)!r} is not a directory"
            )
        if held.st_uid != os.geteuid():
            raise JobStoreConfigError(
                f"another user owns the object root {str(self._root)!r}"
            )
        if held.st_mode & 0o077:
            raise JobStoreConfigError(
                f"a group or others can open the object root {str(self._root)!r};"
                " set its mode to 0700"
            )

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*key.split("/"))

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(_write, self._root, key.split("/"), data)

    async def get(self, key: str) -> bytes | None:
        try:
            return await asyncio.to_thread(_read, self._path(key))
        except FileNotFoundError:
            return None

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return False
        # The job's directory goes with its last object.
        with contextlib.suppress(OSError):
            await asyncio.to_thread(path.parent.rmdir)
        return True

    async def list(self) -> AsyncIterator[tuple[str, datetime]]:
        for listed in await asyncio.to_thread(_walk, self._root):
            yield listed


def _walk(root: Path) -> list[tuple[str, datetime]]:
    """Each regular file under ``root``, as its key and the time of its last
    write in UTC.

    The walk enters no symbolic link and returns none, so each key names a
    file under ``root``. A file that a delete removes during the walk is not
    returned.
    """
    found = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = Path(folder, name)
            try:
                held = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(held.st_mode):
                modified = datetime.fromtimestamp(held.st_mtime, UTC)
                found.append((path.relative_to(root).as_posix(), modified))
    return found


def _write(root: Path, parts: list[str], data: bytes) -> None:
    *folders, name = parts
    path = root
    for folder in folders:
        path = path / folder
        with contextlib.suppress(FileExistsError):
            path.mkdir(mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path / name, flags, 0o600), "wb") as file:
        file.write(data)


def _read(path: Path) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as file:
        return file.read()


def _s3_store(env: Mapping[str, str]) -> ObjectStore:
    """The ``s3`` store, from the ``aws`` extra (ADR 0023)."""
    try:
        from analysis_service.s3_objects import S3ObjectStore
    except ModuleNotFoundError as missing:
        raise JobStoreConfigError(
            f"the s3 object store needs the aws extra ({missing.name} is not"
            " installed): install analysis-service[aws]"
        ) from None
    return S3ObjectStore.from_env(env)


ObjectStoreName = Literal["filesystem", "s3"]

#: Each ``ANALYSIS_OBJECT_STORE`` value, and how to build its store from the
#: environment. The table answers for every name of :data:`ObjectStoreName`.
OBJECT_STORES: dict[ObjectStoreName, Callable[[Mapping[str, str]], ObjectStore]] = {
    "filesystem": FilesystemObjectStore.from_env,
    "s3": _s3_store,
}


def build_object_store(env: Mapping[str, str]) -> ObjectStore:
    """Build the object store that ``ANALYSIS_OBJECT_STORE`` names; fail closed."""
    value = env.get("ANALYSIS_OBJECT_STORE", "")
    return OBJECT_STORES[backend_name(value, "ANALYSIS_OBJECT_STORE", OBJECT_STORES)](
        env
    )
