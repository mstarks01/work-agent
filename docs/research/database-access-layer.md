# Database access layer, migrations and IAM auth for PostgreSQL (#1525)

**Question.** Which database access layer and migration tool does the service
use for PostgreSQL and SQLite? The map is #1522. Decision ticket #1523 sets
the frame: the whole `JobStore` becomes one new `_FACTORIES` backend,
PostgreSQL holds job state and principals, SQLite does the same on one host,
and `reserve` and a unique principal need transactions.

**Sources.** Each claim cites the page or the source file that owns it. The
docs read are SQLAlchemy 2.0 (release 2.0.54), Alembic 1.20.0, the asyncpg
API reference and `master` source, the PostgreSQL 18 manual, the SQLite
manual, the Python 3.14 `sqlite3` docs, and the AWS, Google Cloud and
Microsoft pages named below. The repo has no SQLAlchemy, asyncpg or Alembic
installed today; `aiosqlite==0.22.1` is present as a transitive dependency.
No paid API was called.

## Recommendation

1. Use **SQLAlchemy 2.0 Core in async mode** with the `postgresql+asyncpg`
   and `sqlite+aiosqlite` dialects. One code path serves both engines. Two
   small per-dialect pieces stay: the admission lock and the SQLite `BEGIN`
   hook.
2. Use **Alembic** with `render_as_batch=True`. Run `alembic upgrade head` as
   a separate deploy step. At startup the service compares the database
   revision with the script head and refuses to start on a mismatch.
3. Get IAM tokens through a **table keyed by the declared mechanism**, one row
   for each of `rds-iam`, `cloudsql-iam` and `entra-id`. RDS and Entra ID pass
   an async password callable to asyncpg. Cloud SQL uses the Cloud SQL Python
   Connector through `async_creator`.
4. Build one `ssl.SSLContext` with `check_hostname=True` and the provider's CA
   bundle, and pass it as asyncpg's `ssl` argument. Never rely on asyncpg's
   default `prefer`.
5. In `reserve`, serialise admission with a **transaction-level advisory
   lock** on PostgreSQL and **`BEGIN IMMEDIATE`** on SQLite. The duplicate
   check is the primary key on `jobs.id`.

## 1. SQLAlchemy Core (async) against raw `asyncpg` plus `aiosqlite`

**One code path serves both engines with SQLAlchemy.** The asyncio extension
supports "Core and ORM usage ... using asyncio-compatible dialects". Its own
examples use `create_async_engine("sqlite+aiosqlite://")` and
`create_async_engine("postgresql+asyncpg://...")` with the same
`AsyncConnection.execute()` API.
[asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

Raw `asyncpg` and raw `aiosqlite` have different APIs and different SQL
parameter styles (`$1` against `?`). A store on the raw drivers needs two
implementations of each query. That doubles the code that the owner check
lives in, and #1523 point 2 rejects that shape for the report store.

The differences that remain under SQLAlchemy:

| Topic | `postgresql+asyncpg` | `sqlite+aiosqlite` |
| --- | --- | --- |
| I/O | Real non-blocking I/O. | "a wrapper around pysqlite that uses a background thread for each connection. It does not actually use non-blocking IO" ([sqlite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)). |
| Isolation levels | `READ COMMITTED`, `REPEATABLE READ`, `SERIALIZABLE`, `AUTOCOMMIT` ([postgresql](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)). | "SQLAlchemy only includes built-in support for 'AUTOCOMMIT'" ([sqlite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)). |
| Transaction start | The driver emits `BEGIN`. | Legacy mode "fails to emit BEGIN for SELECT statements". A `begin` event hook must emit it (section 5). |
| `FOR UPDATE` | Emitted. | The compiler emits nothing: `for_update_clause` returns `""` ([source, `rel_2_0`](https://github.com/sqlalchemy/sqlalchemy/blob/rel_2_0/lib/sqlalchemy/dialects/sqlite/base.py)). |
| Statement cache | Prepared statements cached per connection; stale after DDL from another process (`InvalidCachedStatementError`). | None. |

Events on an async engine attach to `AsyncEngine.sync_engine`. The SQLite page
uses this form for aiosqlite.
[asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

## 2. Migrations

**Alembic is the migration tool for SQLAlchemy.** It supports asyncio in
`env.py`: `async_engine_from_config(..., poolclass=pool.NullPool)`, then
`await connection.run_sync(do_run_migrations)`.
[cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html)

**Alembic can run at startup or as a separate step.** For startup, the
cookbook shares a connection: set `cfg.attributes["connection"]` and call
`command.upgrade(cfg, "head")`. The async form runs this through
`await conn.run_sync(run_upgrade, cfg)`.
[cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html)
The separate step is the `alembic upgrade head` command.

**SQLite needs batch mode.** SQLite "has almost no support for the ALTER
statement". Alembic's batch mode copies the table to a new one and renames
it. `render_as_batch=True` "is safe to use in all cases", and it acts only on
SQLite by default. Batch mode does not work while foreign keys are enforced,
so `PRAGMA foreign_keys` must be off during the operation. Constraints need a
`naming_convention`.
[batch](https://alembic.sqlalchemy.org/en/latest/batch.html)

**Pick: a separate step, with a startup check.** A migration at startup runs
DDL under the service's own database role, so the role needs DDL rights. A
separate step lets the deployment give DDL rights to a migration role only.
The startup check (`MigrationContext.get_current_revision()` against
`ScriptDirectory.get_current_head()`) fails closed, in the same way that
`build_store` fails closed on an unknown backend. This also suits #1523
point 11: one instance today, with no block on more, where a startup
migration on several instances would race.

## 3. IAM authentication for each managed PostgreSQL

All three tokens are short-lived. Each one is checked when a connection opens.
So the pool must get a fresh token for each **new** connection, and must not
store a token in the URL.

| Provider | How Python gets the token | Lifetime | Checked when | TLS |
| --- | --- | --- | --- | --- |
| **AWS RDS IAM** | `boto3` `rds.generate_db_auth_token(DBHostname, Port, DBUsername, Region)`. It signs locally with SigV4 and makes no network call ([boto3](https://docs.aws.amazon.com/boto3/latest/reference/services/rds/client/generate_db_auth_token.html)). | "Each token has a lifetime of 15 minutes." | Connect only: the token "doesn't affect the session after it is established" ([RDS IAM](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.html)). | The docs show TLS in every example with `global-bundle.pem`, but do not state it as a rule ([RDS SSL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)). |
| **Cloud SQL IAM** | Automatic IAM auth through the Cloud SQL Python Connector, `enable_iam_auth=True`; the docs "strongly recommend" automatic over manual. | OAuth 2.0 access token, "valid for only one hour". | "Any new connections or logins created after this time fail." | "Unencrypted connections are rejected." ([Cloud SQL IAM](https://docs.cloud.google.com/sql/docs/postgres/iam-authentication)) |
| **Azure Entra ID** | `azure-identity` `DefaultAzureCredential().get_token("https://ossrdbms-aad.database.windows.net/.default").token`, used as the password ([connect-python](https://learn.microsoft.com/en-us/azure/postgresql/connectivity/connect-python)). | "valid for 5 to 60 minutes"; the Python sample comment says 24 hours. The two pages disagree. | Not stated; the docs say to get the token "just before connecting" ([Entra config](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-configure-sign-in-azure-ad-authentication)). | Required by default; `require_secure_transport=OFF` turns it off ([TLS](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking-ssl-tls)). |

**Yes, the driver can get a fresh token for each connection.** asyncpg: a
password "may be either a string, or a callable that returns a string. If a
callable is provided, it will be called each time a new connection is
established" (since 0.21.0).
[asyncpg API](https://magicstack.github.io/asyncpg/current/api/index.html)
The source awaits the result when it is awaitable, so an `async def` works
([`connect_utils.py`](https://github.com/MagicStack/asyncpg/blob/master/asyncpg/connect_utils.py),
`if callable(params.password)` then `if inspect.isawaitable(password)`).

SQLAlchemy gives two seams:

- `do_connect`: the event "is also an ideal way to dynamically insert an
  authentication token". It edits `cparams` in place for each new connection.
  [engines](https://docs.sqlalchemy.org/en/20/core/engines.html)
  The handler is synchronous. That suits RDS, because the boto3 call signs
  locally. A blocking Azure token call in this handler blocks the event loop.
- `connect_args={"password": <async callable>}`: SQLAlchemy passes
  `connect_args` "directly to the DBAPI's `connect()` method"
  ([engines](https://docs.sqlalchemy.org/en/20/core/engines.html)), and
  asyncpg awaits the callable. This suits Entra ID with
  `azure.identity.aio.DefaultAzureCredential`, which caches the token.
  That this passes through SQLAlchemy's asyncpg dialect unchanged is a
  reading of the source, not a documented statement. A build test must
  prove it.

Cloud SQL is different. The connector owns the connection, the token refresh
and the TLS. SQLAlchemy uses it through
`create_async_engine("postgresql+asyncpg://", async_creator=lambda:
connector.connect_async("project:region:instance", "asyncpg", user=...,
db=..., enable_iam_auth=True))`, after `create_async_connector()`. The README
notes that asyncpg "does not currently support automatic failover" (the
DNS-based failover).
[connector README](https://github.com/GoogleCloudPlatform/cloud-sql-python-connector)

Each mechanism discovers its material through the provider SDK: the AWS
credential chain, Application Default Credentials, or `DefaultAzureCredential`.
This matches the rule from map #491 that #1523 point 9 repeats. A static
password is a fourth declared mechanism and never the default.

Limits to record in the spec:

- RDS: the token is about 1 KB; a custom Route 53 name cannot sign the token;
  the instance needs 300 to 1000 MiB extra memory for IAM auth
  ([RDS IAM](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.html)).
- Cloud SQL: IAM logins are capped at 12,000 a minute for each instance, and
  user names are lowercase. A service-account user is its email without
  `.gserviceaccount.com`.
- Azure: the user name is the UPN or the managed-identity name and is
  case-sensitive.

## 4. TLS with certificate checks

**asyncpg.** `ssl='verify-full'` will "only try an SSL connection, verify
that the server certificate is issued by a trusted CA and that the requested
server host name matches that in the certificate". An `ssl.SSLContext` with
`check_hostname = True` is "equivalent to sslmode=verify-full". The default
is `'prefer'`, which falls back to plain text. `'require'` ignores
certificate errors.
[asyncpg API](https://magicstack.github.io/asyncpg/current/api/index.html)
Since 0.25.0 a DSN `sslmode` does not load the system root CAs, so a DSN
`verify-full` also needs `sslrootcert`.

libpq defines `verify-full` as the only mode with man-in-the-middle
protection that does not depend on CA policy, and says the default `prefer`
"is not recommended in secure deployments".
[libpq SSL](https://www.postgresql.org/docs/current/libpq-ssl.html)

For each provider:

- **RDS**: trust `global-bundle.pem` from
  `truststore.pki.rds.amazonaws.com`. Do not add the intermediate CAs, because
  RDS rotates the server certificates.
  [RDS SSL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)
- **Cloud SQL**: the connector does TLS 1.3 and identity checks itself, and
  needs no certificate file.
  [connector README](https://github.com/GoogleCloudPlatform/cloud-sql-python-connector)
- **Azure**: trust the DigiCert Global Root G2 and Microsoft RSA Root CA 2017
  roots, and not the intermediates. The page recommends full verification.
  The Python quickstart uses `sslmode=require`, which the same TLS page says
  is open to a man-in-the-middle attack. Do not copy the quickstart.
  [TLS](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking-ssl-tls)
- **SQLite**: a local file. TLS does not apply.

**Pick:** pass `connect_args={"ssl": ctx}`, where
`ctx = ssl.create_default_context(cafile=<bundle>)`. `create_default_context`
sets `check_hostname=True` and `CERT_REQUIRED`. The CA bundle path is a
declared setting. A missing setting is an error, not a fallback to `prefer`.

## 5. The atomic check-then-write for `reserve`

`InMemoryJobStore.reserve` (`src/analysis_service/jobs.py`) does these steps as
one step:

1. Refuse a duplicate job ID.
2. Refuse a second resumed job of one parent (`_resumed_by`).
3. Count the owner's active jobs against `ceiling`.
4. Count the owner's jobs that started in the window.
5. Sum the owner's tokens in the window.
6. Sum **all owners'** tokens in the window.
7. Insert the record.

Its docstring says a networked backend "needs the count and the insert inside
one transaction or one conditional write". Step 6 reads rows of every owner.
So a lock on the owner's row alone does not make admission atomic: two owners
can both pass the global budget at once.

**PostgreSQL options:**

- **`SELECT ... FOR UPDATE`** locks "the rows retrieved by the SELECT
  statement". It does not lock rows that do not exist yet, so it cannot stop
  a concurrent insert by itself. It works only on a gate row that every
  admission locks.
  [explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html)
- **SERIALIZABLE** makes the count-then-insert safe. The manual's worked
  example is this exact pattern. The application "must be prepared to retry
  transactions due to serialization failures" (SQLSTATE `40001`). A
  sequential scan takes a relation-level predicate lock, so the global sum
  makes failures more frequent.
  [transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html)
- **A transaction-level advisory lock** (`pg_advisory_xact_lock(<constant>)`)
  serialises admissions at `READ COMMITTED`. The lock is released "at the end
  of the transaction". Under `READ COMMITTED` each statement after the lock
  sees the rows that earlier admissions committed. The database does not
  enforce the lock, so every writer of admission rows must take it.
  [explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html)

**SQLite:** `BEGIN IMMEDIATE` "causes the database connection to start a new
write immediately". It fails with `SQLITE_BUSY` if another write is active.
After it succeeds, "no subsequent operations in that transaction will ever
fail with an SQLITE_BUSY error". SQLite allows one writer at a time.
[transactions](https://www.sqlite.org/lang_transaction.html),
[isolation](https://www.sqlite.org/isolation.html)
The `sqlite3` driver's legacy mode opens a transaction only before the first
DML statement, so the count would run outside the write lock
([sqlite3](https://docs.python.org/3/library/sqlite3.html)). The fix is
SQLAlchemy's documented hook: set `dbapi_connection.isolation_level = None` in
a `connect` event on `engine.sync_engine`, and emit the `BEGIN` in a `begin`
event ([sqlite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)). The
docs emit `"BEGIN"`; emitting `"BEGIN IMMEDIATE"` there is this note's
inference. `aiosqlite.connect` passes its keyword arguments to
`sqlite3.connect` (`aiosqlite/core.py` in 0.22.1), so `timeout` sets the wait
on a busy lock.

**Pick:** the advisory lock on PostgreSQL and `BEGIN IMMEDIATE` on SQLite.
Both give the same property as the in-memory store: one admission at a time,
with no retry loop. Admission is one short transaction for each submission,
so a global queue costs little on one instance. SERIALIZABLE is the fallback
if admission load grows. It then needs a bounded retry on `40001`.

The primary key on `jobs.id` settles step 1. A unique index on the resumed
parent cannot settle step 2, because a failed or rejected resumed job frees
the parent (`_resumed_by`). Step 2 stays a read inside the locked
transaction. The principals table from #1523 point 4 needs a unique
constraint on `(issuer, sub)` and an `INSERT ... ON CONFLICT DO NOTHING`
followed by a `SELECT`. Both engines support `ON CONFLICT`, and SQLAlchemy
exposes it in each dialect's `insert()`.

## Open points for the spec

- A build test must prove that an async `password` callable in `connect_args`
  reaches asyncpg through SQLAlchemy unchanged.
- The Azure token lifetime is stated two ways in Microsoft's own pages. The
  design must not depend on the lifetime, since the pool asks for a token on
  each new connection.
- The docs do not say whether an open Azure or Cloud SQL connection outlives
  its token. Set `pool_recycle` below the shortest token lifetime only if a
  live test shows that it does not.
