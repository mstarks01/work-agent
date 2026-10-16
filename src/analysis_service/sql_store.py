"""The ``sql`` job store: jobs in a database, sources and reports in an object store.

ADR 0065 sets the split. A job row holds the record's fields, each JSON part as
text and never as ``jsonb``, so one column type serves every database and the
round trip gives the same bytes. The events are rows of their own. The
sources and the report are objects under :func:`~analysis_service.objects.object_key`,
and the row holds the SHA-256 of each one.

The rules this module keeps:

* **The object first, the row second.** A reader never finds a row that names
  a missing object. Where admission refuses a job, the objects written for it
  go at once. A delete changes the row first and deletes the objects second.
  An object whose delete fails stays, and the orphan sweep
  (:meth:`SqlJobStore.sweep_orphans`) removes it later.
* **The owner on the row before any object.** Each read selects the row by
  job ID and owner ID, and builds each key from that row alone.
* **A digest check on each object read.** A missing object, or one whose
  digest does not match its row, raises :class:`CorruptObject` after one log
  line with the job ID and the owner ID, and the API answers ``500`` with no
  detail. A read can find a row, and then a delete can remove the row and its
  objects before the read gets an object. So where an object is missing, the
  read reads the row again, and a row that is gone reads as a missing job.
* **The service writes a report once.** A save never writes a second report
  over the one a row names.
* **One code path for each database.** SQLAlchemy 2.0 async Core, with the
  facts that differ in a row of :data:`DATABASES` (#1525). Admission takes a
  global lock. On SQLite each write transaction opens with ``BEGIN
  IMMEDIATE``, which is that lock, and serialises every other write with it.
  On PostgreSQL admission takes ``pg_advisory_xact_lock``, and every
  connection uses ``verify-full`` TLS.
* **The service never migrates.** An operator runs
  ``python -m analysis_service.migrations`` as a deploy step, and
  :meth:`SqlJobStore.startup_check` refuses a schema that is not at the latest
  revision.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    CompoundSelect,
    Connection,
    DateTime,
    Dialect,
    ForeignKey,
    Integer,
    MetaData,
    Row,
    Select,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    event,
    func,
    insert,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool, Pool
from sqlalchemy.types import TypeDecorator
from typing_extensions import override

from analysis_service import migrations
from analysis_service.answer_sets import AnswerSet
from analysis_service.aws_identity import platform_session
from analysis_service.budgets import BudgetPolicy
from analysis_service.claims import UnknownKey
from analysis_service.fact_answers import STORED, FactAnswer, SkipKey
from analysis_service.jobs import (
    RESTART_FAILURE_MESSAGE,
    TERMINAL_STATUSES,
    Admission,
    Checkpoint,
    JobEvent,
    JobRecord,
    JobStatus,
    Resumption,
    UsageRow,
    check_bounds,
    check_save,
    envelope_parts,
    is_follow_up,
    locks_its_parent,
    object_digest,
    parent_of,
    stored_report,
)
from analysis_service.objects import (
    ObjectName,
    ObjectStore,
    check_private,
    key_parts,
    object_key,
)
from analysis_service.principals import Identity, Principal
from analysis_service.report import Report
from analysis_service.sources import Source
from analysis_service.store_settings import StoreConfigError, closed_setting
from analysis_service.vendors import (
    SECRET_FILE_SUFFIX,
    ProviderAuthError,
    read_secret,
    refused_var,
)

logger = logging.getLogger(__name__)


class CorruptObject(RuntimeError):
    """An object that a row names is missing, or its digest does not match."""


class _UtcTime(TypeDecorator[datetime]):
    """A time, stored and read in UTC. SQLite keeps no zone, so the store keeps one."""

    impl = DateTime(timezone=True)
    cache_ok = True

    @override
    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @override
    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


class _Json(TypeDecorator[str]):
    """A JSON part of a job row, as text and never as ``jsonb`` (ADR 0065)."""

    impl = Text
    cache_ok = True


METADATA = MetaData()

#: The ``principals`` table (ADR 0064): one row for each pair of issuer and
#: ``sub``, and nothing more.
principals = Table(
    "principals",
    METADATA,
    Column("owner_id", String(64), primary_key=True),
    Column("issuer", Text, nullable=False),
    Column("sub", Text, nullable=False),
    Column("created_at", _UtcTime, nullable=False),
    UniqueConstraint("issuer", "sub"),
)

#: The ``jobs`` table: a column for each field of a :class:`JobRecord` but the
#: two objects and the events.
jobs = Table(
    "jobs",
    METADATA,
    Column("id", String(64), primary_key=True),
    Column(
        "owner_id",
        String(64),
        ForeignKey("principals.owner_id"),
        nullable=False,
        index=True,
    ),
    # What parent_of reads off the record, so admission finds the children of
    # a job with no read inside the JSON.
    Column("parent_id", String(64), index=True),
    Column("schema_version", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", _UtcTime, nullable=False, index=True),
    Column("updated_at", _UtcTime, nullable=False),
    Column("reserved_tokens", BigInteger, nullable=False),
    Column("measured_tokens", BigInteger),
    Column("revision", Integer, nullable=False),
    Column("parent_revision", Integer),
    Column("system_name", Text),
    Column("amends", String(64)),
    Column("error", Text),
    Column("ask_questions", Boolean, nullable=False),
    Column("frameworks", _Json, nullable=False),
    Column("answers", _Json, nullable=False),
    Column("shown_early", _Json, nullable=False),
    Column("corrections", _Json, nullable=False),
    Column("draft", _Json, nullable=False),
    Column("skipped_early", _Json, nullable=False),
    Column("resumption", _Json, nullable=False),
    Column("carried", _Json, nullable=False),
    Column("checkpoint", _Json, nullable=False),
    Column("validation_issues", _Json, nullable=False),
    Column("certification", _Json, nullable=False),
    Column("unreported_nodes", _Json, nullable=False),
    Column("sources_sha256", String(64), nullable=False),
    Column("report_sha256", String(64)),
)

#: The ``job_events`` table: one row for each event, keyed by the job and the
#: event's sequence number.
job_events = Table(
    "job_events",
    METADATA,
    Column("job_id", String(64), ForeignKey("jobs.id"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("event", _Json, nullable=False),
)

#: The usage rows (ADR 0066): what admission still reads of a deleted job.
usage_rows = Table(
    "usage_rows",
    METADATA,
    Column("job_id", String(64), primary_key=True),
    Column(
        "owner_id",
        String(64),
        ForeignKey("principals.owner_id"),
        nullable=False,
        index=True,
    ),
    Column("created_at", _UtcTime, nullable=False, index=True),
    Column("reserved_tokens", BigInteger, nullable=False),
    Column("measured_tokens", BigInteger),
    Column("status", String(32), nullable=False),
    Column("parent_id", String(64), index=True),
)

#: The column of a job row that holds the digest of each object. The table
#: answers for every name of :data:`~analysis_service.objects.ObjectName`.
DIGEST_COLUMNS: dict[ObjectName, str] = {
    "sources.json": "sources_sha256",
    "report.json": "report_sha256",
}


#: How many job IDs one query of the orphan sweep reads. SQLite and asyncpg
#: both take this many bind values in one statement.
_ID_BATCH = 500


def _names(row: Row[Any], name: ObjectName) -> bool:
    """Whether the job row ``row`` names its job's object ``name``.

    **The one rule of a named object.** A row names an object where it holds
    the object's digest. The sources digest is never null, so a row always
    names its sources. A row names its report only after a save wrote the
    report's digest. A usage row names no object. A save hands
    :func:`~analysis_service.jobs.check_save` that same digest column, which
    holds ``None`` exactly where this says the row names no report.
    """
    return row._mapping[DIGEST_COLUMNS[name]] is not None


def _object_keys(row: Row[Any], *, unnamed: bool = False) -> list[str]:
    """The key of each object that the job row ``row`` names. With
    ``unnamed``, the key of each object that its job can hold, named or not."""
    return [
        object_key(row.owner_id, row.id, name)
        for name in DIGEST_COLUMNS
        if unnamed or _names(row, name)
    ]


#: The fields of a record that are objects or event rows, not job columns.
_OUTSIDE_THE_ROW = frozenset({"sources", "report", "events"})
#: The fields a job row holds as JSON text.
_JSON_FIELDS = frozenset(
    column.name for column in jobs.columns if isinstance(column.type, _Json)
)
#: How each JSON field is checked and written: as :class:`JobRecord` holds it.
_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    name: TypeAdapter(JobRecord.model_fields[name].rebuild_annotation())
    for name in _JSON_FIELDS
}
_SOURCES: TypeAdapter[list[Source]] = TypeAdapter(list[Source])


def _json(name: str, value: object) -> str:
    """``value`` as the JSON part ``name`` of a job row, checked as the record checks it."""
    adapter = _ADAPTERS[name]
    return adapter.dump_json(adapter.validate_python(value)).decode()


def _job_row(record: JobRecord) -> dict[str, Any]:
    """The columns of ``record``'s job row, without the object digests.

    Every field but the objects and the events, so a field with no column
    fails the write rather than vanishing from it.
    """
    row = {
        name: _json(name, value) if name in _JSON_FIELDS else value
        for name, value in record
        if name not in _OUTSIDE_THE_ROW
    }
    row["parent_id"] = parent_of(record)
    return row


#: The columns that the restart scan changes when it fails a job: what
#: :meth:`JobRecord.transition` sets, and the error.
_FAILED_COLUMNS = ("status", "measured_tokens", "updated_at", "error")


def _record(
    row: Row[Any],
    sources: bytes | None,
    report: bytes | None,
    events: Sequence[str],
) -> JobRecord:
    """The record a row and its objects hold. An envelope, where ``sources``
    is ``None``, leaves out each part that :func:`envelope_parts` names, and
    never parses one."""
    left_out = envelope_parts(row.schema_version) if sources is None else {}
    data: dict[str, Any] = {}
    for name in JobRecord.model_fields.keys() - _OUTSIDE_THE_ROW:
        value = row._mapping[name]
        if name in left_out:
            data[name] = left_out[name]
        elif name in _JSON_FIELDS:
            data[name] = json.loads(value)
        else:
            data[name] = value
    data["sources"] = None if sources is None else json.loads(sources)
    data["report"] = None if report is None else json.loads(report)
    data["events"] = [json.loads(text) for text in events]
    return JobRecord.model_validate(data, context=STORED)


def _event_rows(job_id: str, events: Sequence[JobEvent]) -> list[dict[str, Any]]:
    return [
        {"job_id": job_id, "seq": event.seq, "event": event.model_dump_json()}
        for event in events
    ]


#: The execution option that marks a write transaction.
_WRITES = "analysis_writes"


def _prepare_sqlite(engine: AsyncEngine) -> None:
    """Open each SQLite write transaction with ``BEGIN IMMEDIATE``.

    The driver's own ``BEGIN`` is off, so the store states each one. A write
    transaction takes the database's write lock when it begins, which makes
    it the global lock of admission, and a write never starts as a read
    that SQLite then refuses to upgrade. Foreign keys are on for each
    connection, because SQLite leaves them off.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(connection: Connection) -> None:
        writes = connection.get_execution_options().get(_WRITES, False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if writes else "BEGIN")


#: The setting that names the database.
_URL = "ANALYSIS_DATABASE_URL"
#: The setting that selects a row of :data:`DATABASE_CREDENTIALS`.
_CREDENTIALS = "ANALYSIS_DATABASE_CREDENTIALS"
#: The setting that names the file of the database password.
_PASSWORD_FILE = "ANALYSIS_DATABASE_PASSWORD" + SECRET_FILE_SUFFIX
#: The setting that names a private CA.
_CA_FILE = "ANALYSIS_DATABASE_CA_FILE"
#: The setting that selects a row of :data:`DATABASE_IAM`.
_IAM = "ANALYSIS_DATABASE_IAM"
#: The setting that names the AWS region of an RDS database.
_REGION = "ANALYSIS_DATABASE_REGION"

#: What asyncpg takes as a password: the text, or an async callable that it
#: awaits for each new connection.
Password = str | Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class DatabaseIam:
    """One provider of IAM tokens for a database: a row of :data:`DATABASE_IAM`.

    No field has a default, so a new row states every fact.
    """

    #: The extra that installs the library of the provider (ADR 0023).
    extra: str
    #: Builds the token callable for the database at the URL. It imports the
    #: library of the provider, so a missing library raises
    #: :class:`ModuleNotFoundError` here, at startup.
    token: Callable[[URL, Mapping[str, str]], Callable[[], Awaitable[str]]]


def _rds_token(url: URL, env: Mapping[str, str]) -> Callable[[], Awaitable[str]]:
    """An RDS IAM token for each new connection (#1525).

    ``boto3`` signs the token locally with the credentials of the platform
    identity (:func:`~analysis_service.aws_identity.platform_session`), so a
    static key pair stops the startup. A token lasts 15 minutes, and RDS
    checks it only when a connection opens. The signature runs in a worker
    thread, because a refresh of the credentials goes over the network.
    ``url`` holds the port that the connection uses (:func:`_postgres`).
    """
    region = env.get(_REGION, "").strip()
    if not region:
        raise StoreConfigError(f"set {_REGION} for an RDS IAM token")
    session = platform_session("the rds token of the database")
    client = session.client("rds", region_name=region)

    async def token() -> str:
        return await asyncio.to_thread(
            client.generate_db_auth_token,
            DBHostname=url.host,
            Port=url.port,
            DBUsername=url.username,
            Region=region,
        )

    return token


DatabaseIamName = Literal["rds"]

#: Each provider of IAM tokens that ``ANALYSIS_DATABASE_IAM`` can name. The
#: table answers for every name of :data:`DatabaseIamName`.
DATABASE_IAM: dict[DatabaseIamName, DatabaseIam] = {
    "rds": DatabaseIam(extra="aws", token=_rds_token),
}


def _password_file(url: URL, env: Mapping[str, str]) -> Password:
    """The password in the file that ``ANALYSIS_DATABASE_PASSWORD_FILE`` names.

    This file is the only way to give the password. The settings of the IAM
    credentials stop the startup.
    """
    for setting in (_IAM, _REGION):
        if env.get(setting, "").strip():
            raise StoreConfigError(
                f"password credentials use no IAM token; unset {setting}"
            )
    try:
        password = read_secret(env, _PASSWORD_FILE)
    except ProviderAuthError as error:
        raise StoreConfigError(str(error)) from None
    if not password:
        raise StoreConfigError(
            f"set {_PASSWORD_FILE} to a file that holds the database password"
        )
    return password


def _iam_token(url: URL, env: Mapping[str, str]) -> Password:
    """The token callable of the provider that ``ANALYSIS_DATABASE_IAM`` names."""
    if env.get(_PASSWORD_FILE, "").strip():
        raise StoreConfigError(
            f"IAM credentials use no password; unset {_PASSWORD_FILE}"
        )
    name = closed_setting(env.get(_IAM, ""), _IAM, DATABASE_IAM)
    iam = DATABASE_IAM[name]
    try:
        return iam.token(url, env)
    except ModuleNotFoundError as missing:
        raise StoreConfigError(
            f"the {name} IAM token needs {missing.name}, which is not installed:"
            f" install analysis-service[{iam.extra}]"
        ) from None


DatabaseCredentialMode = Literal["password", "iam"]

#: How a connection gets its password, for each value of
#: ``ANALYSIS_DATABASE_CREDENTIALS``. The table answers for every name of
#: :data:`DatabaseCredentialMode`.
DATABASE_CREDENTIALS: dict[
    DatabaseCredentialMode, Callable[[URL, Mapping[str, str]], Password]
] = {
    "password": _password_file,
    "iam": _iam_token,
}


def _verify_full(env: Mapping[str, str]) -> ssl.SSLContext:
    """A TLS context that checks the chain and the host name of the server.

    This is libpq's ``verify-full``, and no setting turns it off. Where
    ``ANALYSIS_DATABASE_CA_FILE`` names a private CA, the context trusts that
    CA only. Where the setting is unset, the context trusts the system CA
    bundle.
    """
    ca_file = env.get(_CA_FILE, "").strip() or None
    try:
        return ssl.create_default_context(cafile=ca_file)
    except OSError as error:
        # A missing or unreadable file (FileNotFoundError, PermissionError,
        # IsADirectoryError) and a file that holds no PEM certificate
        # (ssl.SSLError) are each an OSError.
        raise StoreConfigError(
            f"{_CA_FILE} names a file that this service cannot read as a CA"
            f" ({type(error).__name__})"
        ) from None


def _postgres(url: URL, env: Mapping[str, str]) -> dict[str, Any]:
    """The connect arguments of PostgreSQL: the port, ``verify-full`` TLS and a
    password.

    The URL must name a user, a network host and a database, because asyncpg
    uses no TLS on a Unix socket, and it fills each argument that the store
    does not pass from the ``PG*`` variables of the process. For the same
    reason the store always passes the port, and the token of the IAM
    credentials is signed for that port. The service refuses a query in the
    URL, because asyncpg takes it as connect arguments, and a ``host`` there
    can name a Unix socket.
    """
    if url.query:
        raise StoreConfigError(f"{_URL} for PostgreSQL takes no query; remove it")
    if not url.username or not url.host or url.host.startswith("/"):
        raise StoreConfigError(f"{_URL} for PostgreSQL needs a user and a network host")
    if not url.database:
        raise StoreConfigError(f"{_URL} for PostgreSQL names no database")
    url = url.set(port=url.port or 5432)
    bare = refused_var(_PASSWORD_FILE)
    if env.get(bare, "").strip():
        raise StoreConfigError(
            f"the database password comes only from the file that {_PASSWORD_FILE}"
            f" names; unset {bare}"
        )
    credentials = closed_setting(
        env.get(_CREDENTIALS, ""), _CREDENTIALS, DATABASE_CREDENTIALS
    )
    return {
        "port": url.port,
        "ssl": _verify_full(env),
        "password": DATABASE_CREDENTIALS[credentials](url, env),
    }


def _sqlite_file(url: URL) -> Path:
    """The file of a SQLite database: an absolute path, or the startup stops."""
    path = Path(url.database or "")
    if not path.is_absolute():
        raise StoreConfigError(f"{_URL} must name a SQLite file by an absolute path")
    return path


def _sqlite(url: URL, env: Mapping[str, str]) -> dict[str, Any]:
    """No connect arguments: a SQLite database is a local file.

    It takes no credentials and no TLS, so each database setting but the URL
    stops the startup. The file holds content, so it must pass
    :func:`~analysis_service.objects.check_private`, as the object root does.
    """
    stray = sorted(
        name
        for name, value in env.items()
        if name.startswith("ANALYSIS_DATABASE_") and name != _URL and value.strip()
    )
    if stray:
        raise StoreConfigError(
            f"a SQLite database takes no {', '.join(stray)}; unset it"
        )
    check_private(_sqlite_file(url), "the database file", directory=False)
    return {}


def _create_sqlite(url: URL) -> None:
    """Make the database file with mode ``0600`` where there is none.

    SQLite makes a missing file with the mode that the umask gives, which
    can let every local user read it.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        os.close(os.open(_sqlite_file(url), flags, 0o600))
    except FileExistsError:
        pass


def _no_creation(url: URL) -> None:
    """Make nothing: the operator makes the database on its server."""


def _no_preparation(engine: AsyncEngine) -> None:
    """Set nothing on a new engine."""


async def _held_by_begin_immediate(connection: AsyncConnection) -> None:
    """Take no lock: ``BEGIN IMMEDIATE`` took the write lock of the database."""


#: The key of the advisory lock that admission takes on PostgreSQL. The value
#: is arbitrary, and no other lock in the database uses it.
_ADMISSION_LOCK_KEY = 1525


async def _advisory_lock(connection: AsyncConnection) -> None:
    """Take the advisory lock of admission until the transaction ends (#1525).

    Admission sums the tokens of every owner, so a lock on one row cannot
    serialise it. At ``READ COMMITTED``, each statement after the lock sees
    each admission that committed before it.
    """
    await connection.execute(select(func.pg_advisory_xact_lock(_ADMISSION_LOCK_KEY)))


@dataclass(frozen=True)
class Database:
    """One database the ``sql`` store runs on: a row of :data:`DATABASES`.

    No field has a default, so a new row states every fact.
    """

    #: The connection pool.
    pool: type[Pool]
    #: What the store sets on a new engine before its first connection.
    prepare: Callable[[AsyncEngine], None]
    #: The connect arguments of the driver for the URL and the settings. It
    #: checks each setting that a connection reads, so a wrong one stops the
    #: startup.
    connect: Callable[[URL, Mapping[str, str]], dict[str, Any]]
    #: Takes the global lock of admission, in the write transaction of
    #: admission.
    admission_lock: Callable[[AsyncConnection], Awaitable[None]]
    #: Makes the database where there is none. Only the migration step calls
    #: it, through :func:`create_database`; the service never makes one.
    create: Callable[[URL], None]


DatabaseDriver = Literal["sqlite+aiosqlite", "postgresql+asyncpg"]

#: Each database a deployment can name in ``ANALYSIS_DATABASE_URL``, keyed by
#: the URL's driver name. The table answers for every name of
#: :data:`DatabaseDriver`.
DATABASES: dict[DatabaseDriver, Database] = {
    # A connection opens a file, so a pool saves little, and a connection
    # that no pool keeps belongs to no event loop.
    "sqlite+aiosqlite": Database(
        pool=NullPool,
        prepare=_prepare_sqlite,
        connect=_sqlite,
        admission_lock=_held_by_begin_immediate,
        create=_create_sqlite,
    ),
    # The server checks a password or an IAM token only when a connection
    # opens, so a pooled connection stays open after its token expires, and
    # asyncpg calls the token callable for each new connection (#1525).
    "postgresql+asyncpg": Database(
        pool=AsyncAdaptedQueuePool,
        prepare=_no_preparation,
        connect=_postgres,
        admission_lock=_advisory_lock,
        create=_no_creation,
    ),
}


def database_url(env: Mapping[str, str]) -> URL:
    """``ANALYSIS_DATABASE_URL``, checked; fail closed.

    A URL that holds a password stops the startup, so a password never sits
    in an environment variable. No message repeats the URL.
    """
    value = env.get(_URL, "").strip()
    if not value:
        raise StoreConfigError(f"set {_URL} for the sql job store")
    try:
        url = make_url(value)
    except ArgumentError:
        raise StoreConfigError(f"{_URL} is not a database URL") from None
    if url.password is not None or any("password" in key.lower() for key in url.query):
        raise StoreConfigError(f"{_URL} holds a password; remove it from the URL")
    _database(url)
    return url


def _database(url: URL) -> Database:
    """The row of :data:`DATABASES` for the driver of ``url``."""
    return DATABASES[closed_setting(url.drivername, f"the driver of {_URL}", DATABASES)]


def create_database(env: Mapping[str, str]) -> None:
    """Make the database that the settings name where there is none, by the
    ``create`` field of its row. The migration step calls it first."""
    url = database_url(env)
    _database(url).create(url)


def engine_for(env: Mapping[str, str]) -> AsyncEngine:
    """The engine of the database that the settings name, with the facts of its row.

    It checks every database setting, and it opens no connection.
    """
    url = database_url(env)
    database = _database(url)
    engine = create_async_engine(
        url, poolclass=database.pool, connect_args=database.connect(url, env)
    )
    database.prepare(engine)
    return engine


def _revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _held(job_id: str) -> CompoundSelect:
    """The ID ``job_id`` where a job row or a usage row holds it. A deleted
    job keeps its ID, so a new job never takes it."""
    return union_all(
        select(jobs.c.id).where(jobs.c.id == job_id),
        select(usage_rows.c.job_id).where(usage_rows.c.job_id == job_id),
    )


def _children(parent_id: str) -> CompoundSelect:
    """Every job that names ``parent_id`` as its parent, with its status."""
    return union_all(
        select(jobs.c.id, jobs.c.status).where(jobs.c.parent_id == parent_id),
        select(usage_rows.c.job_id, usage_rows.c.status).where(
            usage_rows.c.parent_id == parent_id
        ),
    )


def _counted(since: datetime) -> CompoundSelect:
    """The admission view of each job a bound can count: those in flight, and
    those that started inside the window."""
    columns = ("owner_id", "created_at", "reserved_tokens", "measured_tokens")

    def view(table: Table, job_id: Column[Any]) -> Select[Any]:
        return select(
            job_id.label("job_id"),
            *(table.c[name] for name in columns),
            table.c.status,
            table.c.parent_id,
        ).where(
            or_(
                table.c.created_at >= since,
                table.c.status.not_in(sorted(TERMINAL_STATUSES)),
            )
        )

    return union_all(view(jobs, jobs.c.id), view(usage_rows, usage_rows.c.job_id))


class SqlJobStore:
    """:class:`~analysis_service.jobs.JobStore` on a database and an object store."""

    def __init__(self, engine: AsyncEngine, objects: ObjectStore) -> None:
        self._engine = engine
        self._objects = objects
        self._admission_lock = _database(engine.url).admission_lock

    @classmethod
    def from_env(cls, env: Mapping[str, str], objects: ObjectStore) -> SqlJobStore:
        return cls(engine_for(env), objects)

    @asynccontextmanager
    async def _transaction(
        self, *, writes: bool = False
    ) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as connection:
            if writes:
                await connection.execution_options(**{_WRITES: True})
            async with connection.begin():
                yield connection

    async def startup_check(self) -> None:
        """Refuse a schema that is not at the latest revision, then check the
        object store. The service never migrates."""
        async with self._transaction() as connection:
            current = await connection.run_sync(_revision)
        head = migrations.head()
        if current != head:
            raise StoreConfigError(
                f"the database schema is at revision {current}, and this service"
                f" needs {head}: run python -m analysis_service.migrations first"
            )
        await self._objects.check()

    async def _object(
        self, row: Row[Any], name: ObjectName, digest: str
    ) -> bytes | None:
        """The object ``name`` of the job ``row`` names, checked against ``digest``.

        ``None`` where the object is missing because a delete removed the row
        after the caller read it. A delete changes the row first, so a row
        that is still there names an object that must be there.
        """
        data = await self._objects.get(object_key(row.owner_id, row.id, name))
        if data is None:
            async with self._transaction() as connection:
                held = await connection.scalar(
                    select(jobs.c.id).where(jobs.c.id == row.id)
                )
            if held is None:
                return None
        if data is None or not hmac.compare_digest(object_digest(data), digest):
            logger.error(
                "job %s of owner %s: its %s object is missing or fails its digest"
                " check",
                row.id,
                row.owner_id,
                name,
            )
            raise CorruptObject(row.id)
        return data

    async def _lock(self, connection: AsyncConnection, job_id: str) -> None:
        """Lock the row of ``job_id`` for this transaction. On SQLite the
        transaction's write lock already holds it."""
        await connection.execute(
            select(jobs.c.id).where(jobs.c.id == job_id).with_for_update()
        )

    async def _resumed_by(
        self, connection: AsyncConnection, parent_id: str
    ) -> str | None:
        """The child of ``parent_id`` that locks it, by :func:`locks_its_parent`."""
        children = await connection.execute(_children(parent_id))
        return next(
            (child for child, status in children if locks_its_parent(status)), None
        )

    async def reserve(
        self,
        record: JobRecord,
        *,
        ceiling: int,
        budget: BudgetPolicy,
        principal: Principal | None = None,
    ) -> Admission:
        """Check every admission bound and insert, in one write transaction.

        The objects go first, so the row never names a missing one, and they
        go again at once where admission refuses the job or raises. A failed
        object delete there changes neither the refusal nor the error.
        """
        if principal is not None and principal.owner_id != record.owner_id:
            raise ValueError(f"job {record.id} names another owner than its principal")
        async with self._transaction() as connection:
            held = await connection.scalar(_held(record.id))
        if held is not None:
            return Admission(outcome="duplicate", active=0)
        objects: dict[ObjectName, bytes] = {
            "sources.json": _SOURCES.dump_json(record.whole_sources())
        }
        if record.report is not None:
            objects["report.json"] = stored_report(record.report)
        keys = []
        try:
            for name, data in objects.items():
                key = object_key(record.owner_id, record.id, name)
                await self._objects.put(key, data)
                keys.append(key)
            admission = await self._admit(
                record,
                ceiling=ceiling,
                budget=budget,
                principal=principal,
                digests={name: object_digest(data) for name, data in objects.items()},
            )
        except BaseException:
            await self._remove(keys)
            raise
        if admission.outcome != "admitted":
            await self._remove(keys)
        return admission

    async def _remove(self, keys: Sequence[str]) -> int:
        """Delete the object under each key, and return how many the store removed.

        A failed delete is logged with its key, and the next key follows. The
        object then stays for the orphan sweep. Each caller calls this after
        its row change committed, or in place of a row change, so no error of
        an object delete can change the outcome of the caller. The catch is
        :class:`Exception`, because the rows raise errors with no common base
        below it: :class:`OSError` on a filesystem, and ``ClientError`` or
        ``BotoCoreError`` from ``boto3``. A cancellation is not an
        :class:`Exception`, so it goes to the caller.
        """
        removed = 0
        for key in keys:
            try:
                removed += await self._objects.delete(key)
            except Exception as error:  # noqa: BLE001 -- the docstring states why
                logger.warning(
                    "the object store did not delete the object %s (%s);"
                    " the orphan sweep removes it",
                    key,
                    type(error).__name__,
                )
        return removed

    async def _admit(
        self,
        record: JobRecord,
        *,
        ceiling: int,
        budget: BudgetPolicy,
        principal: Principal | None,
        digests: Mapping[ObjectName, str],
    ) -> Admission:
        async with self._transaction(writes=True) as connection:
            await self._admission_lock(connection)
            if await connection.scalar(_held(record.id)) is not None:
                return Admission(outcome="duplicate", active=0)
            if principal is None:
                owner = select(principals.c.owner_id).where(
                    principals.c.owner_id == record.owner_id
                )
                if await connection.scalar(owner) is None:
                    raise ValueError(f"owner {record.owner_id} holds no principal")
            elif await self._principal(connection, principal.identity) is not None:
                return Admission(outcome="principal_exists", active=0)
            parent = parent_of(record)
            if parent is not None:
                await self._lock(connection, parent)
                revision = await connection.scalar(
                    select(jobs.c.revision).where(
                        jobs.c.id == parent, jobs.c.owner_id == record.owner_id
                    )
                )
                if revision is None:
                    return Admission(outcome="parent_missing", active=0)
                if await self._resumed_by(connection, parent) is not None:
                    return Admission(outcome="resumed_already", active=0)
                if revision != record.parent_revision:
                    return Admission(outcome="parent_moved", active=0)
            counted = await connection.execute(_counted(budget.window_start()))
            held = [UsageRow(**row._mapping) for row in counted]
            admission = check_bounds(record, held, ceiling=ceiling, budget=budget)
            if admission.outcome != "admitted":
                return admission
            if principal is not None:
                await connection.execute(
                    insert(principals).values(
                        owner_id=principal.owner_id,
                        issuer=principal.issuer,
                        sub=principal.sub,
                        created_at=principal.created_at,
                    )
                )
            await connection.execute(
                insert(jobs).values(
                    {
                        **_job_row(record),
                        "sources_sha256": digests["sources.json"],
                        "report_sha256": digests.get("report.json"),
                    }
                )
            )
            await connection.execute(
                insert(job_events), _event_rows(record.id, record.events)
            )
        return admission

    async def _principal(
        self, connection: AsyncConnection, identity: Identity
    ) -> Principal | None:
        found = await connection.execute(
            select(principals).where(
                principals.c.issuer == identity.issuer,
                principals.c.sub == identity.sub,
            )
        )
        row = found.one_or_none()
        return None if row is None else Principal(**row._mapping)

    async def principal(self, identity: Identity) -> Principal | None:
        """The principal of ``identity``, or ``None``. Creates nothing."""
        async with self._transaction() as connection:
            return await self._principal(connection, identity)

    async def _load(self, query: Select[Any], *, whole: bool) -> JobRecord | None:
        async with self._transaction() as connection:
            row = (await connection.execute(query)).one_or_none()
            if row is None:
                return None
            events = await connection.scalars(
                select(job_events.c.event)
                .where(job_events.c.job_id == row.id)
                .order_by(job_events.c.seq)
            )
            texts = events.all()
        if not whole:
            return _record(row, None, None, texts)
        sources = await self._object(row, "sources.json", row.sources_sha256)
        if sources is None:
            return None
        report = None
        if _names(row, "report.json"):
            report = await self._object(row, "report.json", row.report_sha256)
            if report is None:
                return None
        return _record(row, sources, report, texts)

    async def get(self, job_id: str) -> JobRecord | None:
        return await self._load(select(jobs).where(jobs.c.id == job_id), whole=True)

    async def owned(self, job_id: str, owner_id: str) -> JobRecord | None:
        """The record ``owner_id`` owns, as an envelope, or ``None``.

        It reads no object, and parses no part that the schema version covers.
        """
        query = select(jobs).where(jobs.c.id == job_id, jobs.c.owner_id == owner_id)
        return await self._load(query, whole=False)

    async def sources(self, job_id: str, owner_id: str) -> list[Source] | None:
        """The owned job's sources. The owner is checked on the row, before
        the object is read."""
        async with self._transaction() as connection:
            found = await connection.execute(
                select(jobs.c.id, jobs.c.owner_id, jobs.c.sources_sha256).where(
                    jobs.c.id == job_id, jobs.c.owner_id == owner_id
                )
            )
            row = found.one_or_none()
        if row is None:
            return None
        data = await self._object(row, "sources.json", row.sources_sha256)
        return None if data is None else _SOURCES.validate_json(data)

    async def _report(self, job_id: str, owner_id: str) -> bytes | None:
        async with self._transaction() as connection:
            found = await connection.execute(
                select(jobs.c.id, jobs.c.owner_id, jobs.c.report_sha256).where(
                    jobs.c.id == job_id, jobs.c.owner_id == owner_id
                )
            )
            row = found.one_or_none()
        if row is None or not _names(row, "report.json"):
            return None
        return await self._object(row, "report.json", row.report_sha256)

    async def report_json(self, job_id: str, owner_id: str) -> dict[str, Any] | None:
        """The owned job's report as the object holds it, parsed as JSON only.

        It never parses the report as a :class:`Report`, so a report from an
        older schema version reads as stored.
        """
        data = await self._report(job_id, owner_id)
        return None if data is None else json.loads(data)

    async def report(self, job_id: str, owner_id: str) -> Report | None:
        data = await self._report(job_id, owner_id)
        return (
            None if data is None else Report.model_validate_json(data, context=STORED)
        )

    async def _part(self, name: str, job_id: str, owner_id: str) -> Any:
        async with self._transaction() as connection:
            text = await connection.scalar(
                select(jobs.c[name]).where(
                    jobs.c.id == job_id, jobs.c.owner_id == owner_id
                )
            )
        return (
            None
            if text is None
            else _ADAPTERS[name].validate_json(text, context=STORED)
        )

    async def checkpoint(self, job_id: str, owner_id: str) -> Checkpoint | None:
        return await self._part("checkpoint", job_id, owner_id)

    async def resumption(self, job_id: str, owner_id: str) -> Resumption | None:
        return await self._part("resumption", job_id, owner_id)

    async def resumed_by(self, job_id: str, owner_id: str) -> str | None:
        """The ID of the owned job's resumed job that locks it, or ``None``."""
        async with self._transaction() as connection:
            owned = await connection.scalar(
                select(jobs.c.id).where(
                    jobs.c.id == job_id, jobs.c.owner_id == owner_id
                )
            )
            if owned is None:
                return None
            return await self._resumed_by(connection, job_id)

    async def events_after(
        self, job_id: str, seen: int
    ) -> tuple[JobStatus, list[JobEvent]] | None:
        """The job's status and the events past ``seen``, and nothing else."""
        async with self._transaction() as connection:
            status = await connection.scalar(
                select(jobs.c.status).where(jobs.c.id == job_id)
            )
            if status is None:
                return None
            texts = await connection.scalars(
                select(job_events.c.event)
                .where(job_events.c.job_id == job_id, job_events.c.seq > seen)
                .order_by(job_events.c.seq)
            )
            return status, [JobEvent.model_validate_json(text) for text in texts]

    async def save(self, record: JobRecord) -> None:
        """Write the record back, and the events it gained.

        The sources are written once, at admission. A save puts the report
        where the row names none. A save that stopped after its put and before
        its row write left a report object that no row names. A later save
        puts its report there again.
        """
        async with self._transaction() as connection:
            found = await connection.execute(
                select(jobs.c.id, jobs.c.owner_id, jobs.c.report_sha256).where(
                    jobs.c.id == record.id
                )
            )
            row = found.one_or_none()
            texts = await connection.scalars(
                select(job_events.c.event)
                .where(job_events.c.job_id == record.id)
                .order_by(job_events.c.seq)
            )
            held = [JobEvent.model_validate_json(text) for text in texts]
        if row is None:
            raise ValueError(f"job {record.id!r} does not exist")
        check_save(record, row.report_sha256, held)
        values = _job_row(record)
        if record.report is not None and not _names(row, "report.json"):
            data = stored_report(record.report)
            key = object_key(row.owner_id, row.id, "report.json")
            try:
                await self._objects.put(key, data)
            except FileExistsError:
                # No row names the object, so it is an orphan. The job is not
                # terminal until the row write below, so the orphan sweep
                # keeps the new object.
                await self._objects.delete(key)
                await self._objects.put(key, data)
            values["report_sha256"] = object_digest(data)
        await self._write(record, values)

    async def _write(self, record: JobRecord, values: Mapping[str, Any]) -> None:
        """Write ``values`` to the job row of ``record``, and the events that
        ``record`` gained, in one write transaction."""
        async with self._transaction(writes=True) as connection:
            await connection.execute(
                update(jobs).where(jobs.c.id == record.id).values(values)
            )
            stored = await connection.scalar(
                select(func.count())
                .select_from(job_events)
                .where(job_events.c.job_id == record.id)
            )
            gained = record.events[stored or 0 :]
            if gained:
                await connection.execute(
                    insert(job_events), _event_rows(record.id, gained)
                )

    async def save_answers(
        self,
        job_id: str,
        owner_id: str,
        answers: AnswerSet,
        shown: Sequence[UnknownKey],
        skipped: Sequence[SkipKey],
        revision: int,
    ) -> bool:
        """Keep a waiting job's saved answers, as one conditional ``UPDATE`` on
        the revision, under the lock on the job's row."""
        values = {
            "answers": _json("answers", answers),
            "shown_early": _json("shown_early", list(shown)),
            "skipped_early": _json("skipped_early", list(skipped)),
        }
        async with self._transaction(writes=True) as connection:
            await self._lock(connection, job_id)
            if await self._resumed_by(connection, job_id) is not None:
                return False
            saved = await connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.owner_id == owner_id,
                    jobs.c.status == "awaiting-answers",
                    jobs.c.revision == revision,
                )
                .values(
                    {
                        **values,
                        "revision": jobs.c.revision + 1,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
            return saved.rowcount == 1

    async def _report_state(
        self, connection: AsyncConnection, job_id: str, owner_id: str
    ) -> Literal["offers-follow-up", "final"] | None:
        """Whether the owned, completed job's report still offers its follow-up
        or is final (ADR 0054), or ``None`` where the job is not the owner's or
        has no report."""
        text = await connection.scalar(
            select(jobs.c.resumption)
            .where(
                jobs.c.id == job_id,
                jobs.c.owner_id == owner_id,
                jobs.c.status == "completed",
            )
            .with_for_update()
        )
        if text is None:
            return None
        resumption = _ADAPTERS["resumption"].validate_json(text, context=STORED)
        return "final" if is_follow_up(resumption) else "offers-follow-up"

    async def save_draft(
        self,
        job_id: str,
        owner_id: str,
        draft: AnswerSet,
        revision: int,
    ) -> bool:
        """Keep a finished report's draft answers, as one conditional
        ``UPDATE`` on the revision (ADR 0070)."""
        value = _json("draft", draft)
        async with self._transaction(writes=True) as connection:
            state = await self._report_state(connection, job_id, owner_id)
            if state != "offers-follow-up":
                return False
            if await self._resumed_by(connection, job_id) is not None:
                return False
            saved = await connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.revision == revision)
                .values(
                    draft=value,
                    revision=jobs.c.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            return saved.rowcount == 1

    async def save_corrections(
        self,
        job_id: str,
        owner_id: str,
        corrections: Sequence[FactAnswer],
        revision: int,
    ) -> bool:
        """Keep a final report's corrections, as one conditional ``UPDATE``
        on the revision (ADR 0090)."""
        value = _json("corrections", list(corrections))
        async with self._transaction(writes=True) as connection:
            if await self._report_state(connection, job_id, owner_id) != "final":
                return False
            saved = await connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.revision == revision)
                .values(
                    corrections=value,
                    revision=jobs.c.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            return saved.rowcount == 1

    async def owned_jobs(self, owner_id: str) -> list[JobRecord]:
        """Every job of the owner, as envelopes, oldest first."""
        async with self._transaction() as connection:
            found = await connection.scalars(
                select(jobs.c.id)
                .where(jobs.c.owner_id == owner_id)
                .order_by(jobs.c.created_at, jobs.c.id)
            )
            job_ids = found.all()
        listed = [await self.owned(job_id, owner_id) for job_id in job_ids]
        return [record for record in listed if record is not None]

    async def delete(
        self, job_id: str, owner_id: str, *, updated_before: datetime | None = None
    ) -> int | None:
        """Turn the owned job's row into a usage row, then delete its objects
        (ADR 0066). The number of objects removed, or ``None`` where the job
        is not the owner's, not terminal or not last updated before
        ``updated_before``, and nothing changes."""
        query = (
            select(jobs)
            .where(jobs.c.id == job_id, jobs.c.owner_id == owner_id)
            .with_for_update()
        )
        if updated_before is not None:
            query = query.where(jobs.c.updated_at < updated_before)
        async with self._transaction(writes=True) as connection:
            found = await connection.execute(query)
            row = found.one_or_none()
            if row is None or row.status not in TERMINAL_STATUSES:
                return None
            await connection.execute(
                insert(usage_rows).values(
                    job_id=row.id,
                    owner_id=row.owner_id,
                    created_at=row.created_at,
                    reserved_tokens=row.reserved_tokens,
                    measured_tokens=row.measured_tokens,
                    status=row.status,
                    parent_id=row.parent_id,
                )
            )
            await connection.execute(
                delete(job_events).where(job_events.c.job_id == job_id)
            )
            await connection.execute(delete(jobs).where(jobs.c.id == job_id))
        return await self._remove(_object_keys(row))

    async def _kept_keys(self, job_ids: Sequence[str]) -> set[str]:
        """The key of each object that the orphan sweep keeps for the job rows
        of ``job_ids``: each object a terminal job's row names, and each
        object of a job that is not terminal.

        A save of a job that is not terminal can put its report before the
        row write that names it, and over an orphan report of the job. The
        row write makes the job terminal, so no object that a later save can
        name is ever deleted. One read transaction, with :data:`_ID_BATCH` job
        IDs in each query.
        """
        columns = (jobs.c[column] for column in DIGEST_COLUMNS.values())
        query = select(jobs.c.id, jobs.c.owner_id, jobs.c.status, *columns)
        kept = set()
        async with self._transaction() as connection:
            for start in range(0, len(job_ids), _ID_BATCH):
                batch = job_ids[start : start + _ID_BATCH]
                for row in await connection.execute(query.where(jobs.c.id.in_(batch))):
                    in_flight = row.status not in TERMINAL_STATUSES
                    kept.update(_object_keys(row, unnamed=in_flight))
        return kept

    async def sweep_orphans(self, before: datetime) -> int:
        """Delete each object last written before ``before`` that no job row
        keeps, and return how many the store removed.

        The sweep lists every key first. A key that :func:`object_key` cannot
        give stays, because the service never writes one. Then one read
        transaction finds the keys that the rows of the listed job IDs keep
        (:meth:`_kept_keys`). A row keeps a key only under its own owner ID.
        The sweep deletes each other listed key.
        """
        listed = []
        async for key, modified in self._objects.list():
            parts = key_parts(key)
            if parts is not None and modified < before:
                listed.append((key, parts[1]))
        kept = await self._kept_keys(sorted({job_id for _, job_id in listed}))
        return await self._remove([key for key, _ in listed if key not in kept])

    async def expired(self, before: datetime) -> list[tuple[str, str]]:
        async with self._transaction() as connection:
            found = await connection.execute(
                select(jobs.c.id, jobs.c.owner_id)
                .where(
                    jobs.c.status.in_(sorted(TERMINAL_STATUSES)),
                    jobs.c.updated_at < before,
                )
                .order_by(jobs.c.updated_at, jobs.c.id)
            )
            return [(row.id, row.owner_id) for row in found]

    async def fail_in_flight(self) -> list[str]:
        """Fail each queued or running job with :data:`RESTART_FAILURE_MESSAGE`.

        The restart scan (#1529), through :meth:`JobRecord.transition` as on
        every store, so each failed job keeps its full charge and gets a
        status event. It reads each job as an envelope and writes only the
        columns that the transition and the error change, so it fails a job
        of any schema version. A job in flight holds no report.
        """
        async with self._transaction() as connection:
            found = await connection.execute(
                select(jobs.c.id, jobs.c.owner_id)
                .where(jobs.c.status.not_in(sorted(TERMINAL_STATUSES)))
                .order_by(jobs.c.created_at, jobs.c.id)
            )
            in_flight = found.all()
        failed = []
        for job_id, owner_id in in_flight:
            record = await self.owned(job_id, owner_id)
            if record is None:
                continue
            record.transition("failed")
            record.error = RESTART_FAILURE_MESSAGE
            await self._write(
                record, {name: getattr(record, name) for name in _FAILED_COLUMNS}
            )
            failed.append(job_id)
        return failed
