"""The job store backends, and what runs over the store a deployment selects.

:func:`build_store` builds the job store that ``ANALYSIS_JOB_STORE`` names,
from a row of :data:`JOB_STORES`. :func:`store_lifespan` checks it, fails each
job in flight, and runs the retention and orphan sweeps while an app serves.
:func:`audited_delete` is the one delete that the routes and the sweeps call.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from analysis_service.jobs import InMemoryJobStore, JobStore
from analysis_service.objects import (
    OBJECT_STORE_VAR,
    OBJECT_STORES,
    ObjectStore,
    build_object_store,
)
from analysis_service.parsing import ascii_int
from analysis_service.store_settings import (
    DATABASE_URL_VAR,
    DATABASE_VARIABLES,
    StoreConfigError,
    closed_setting,
    refuse_unread,
)

logger = logging.getLogger(__name__)


JobStoreName = Literal["memory", "sql"]

#: The setting that selects a row of :data:`JOB_STORES`.
JOB_STORE_VAR = "ANALYSIS_JOB_STORE"
#: The setting of the retention period, which :func:`retention_days` reads.
RETENTION_DAYS_VAR = "ANALYSIS_RETENTION_DAYS"


@dataclass(frozen=True)
class JobStoreBackend:
    """One ``ANALYSIS_JOB_STORE`` value: a row of :data:`JOB_STORES`.

    No field has a default, so a new row states every fact.
    """

    #: Builds the store from the environment and the object store, which is
    #: ``None`` where :attr:`objects` is False.
    build: Callable[[Mapping[str, str], ObjectStore | None], JobStore]
    #: Whether the store keeps sources and reports in an object store. Where
    #: True, ``ANALYSIS_OBJECT_STORE`` is required; where False, it is refused,
    #: so a half-durable configuration cannot start.
    objects: bool
    #: The ``ANALYSIS_DATABASE_*`` and ``ANALYSIS_OBJECT_*`` settings that this
    #: store reads itself. A store that keeps objects leaves each other one to
    #: the rows of the object store and the database, which refuse what they do
    #: not read. A store that keeps none refuses each other one. The Settings
    #: page shows only these.
    reads: tuple[str, ...]


def _sql_store(env: Mapping[str, str], objects: ObjectStore | None) -> JobStore:
    """The ``sql`` store, from the ``sql`` extra (ADR 0023)."""
    try:
        from analysis_service.sql_store import SqlJobStore
    except ModuleNotFoundError as missing:
        raise StoreConfigError(
            f"the sql job store needs the sql extra ({missing.name} is not installed):"
            " install analysis-service[sql]"
        ) from None
    if objects is None:
        raise StoreConfigError("the sql job store needs an object store")
    return SqlJobStore.from_env(env, objects)


#: Each job store a deployment can select. The table answers for every name of
#: :data:`JobStoreName`.
JOB_STORES: dict[JobStoreName, JobStoreBackend] = {
    "memory": JobStoreBackend(
        build=lambda env, objects: InMemoryJobStore(), objects=False, reads=()
    ),
    "sql": JobStoreBackend(
        build=_sql_store,
        objects=True,
        reads=(DATABASE_URL_VAR, OBJECT_STORE_VAR),
    ),
}


def job_store_name(env: Mapping[str, str]) -> JobStoreName:
    """The row of :data:`JOB_STORES` that ``env`` selects; fail closed.

    **The one reader of which job store an env selects.**
    """
    return closed_setting(env.get(JOB_STORE_VAR, ""), JOB_STORE_VAR, JOB_STORES)


def build_store(env: Mapping[str, str] = os.environ) -> JobStore:
    """Select and construct the configured job store; fail closed.

    The backend is chosen by ``ANALYSIS_JOB_STORE`` at deploy time — never by the
    request — so an empty or unknown value raises rather than silently falling
    back to non-durable, per-instance ``memory`` storage that loses every job on
    restart and isolates jobs behind a load balancer. The object store follows
    the row: ``ANALYSIS_OBJECT_STORE`` is required where the row keeps objects
    and refused, with each database setting, where it does not. This builds the
    store and opens no connection; :meth:`JobStore.startup_check` checks what
    the store reaches.
    """
    name = job_store_name(env)
    backend = JOB_STORES[name]
    if backend.objects:
        return backend.build(env, build_object_store(env))
    refuse_unread(
        env,
        ("ANALYSIS_DATABASE_", "ANALYSIS_OBJECT_"),
        backend.reads,
        f"the {name} job store",
    )
    return backend.build(env, None)


#: Why a job is deleted: its owner asked, its retention period passed, or the
#: web app's workspace deleted it to make room for a new job.
DeleteCause = Literal["owner", "retention", "workspace"]


async def audited_delete(
    store: JobStore,
    job_id: str,
    owner_id: str,
    cause: DeleteCause,
    *,
    updated_before: datetime | None = None,
) -> int | None:
    """Delete the owned job with :meth:`JobStore.delete`, and log the delete.

    **The one writer of the delete log line** (ADR 0066). The delete route and
    the retention sweep both delete through it. The line names the job ID, the
    owner ID, the cause and the number of objects removed, and no content.
    ``updated_before`` goes to :meth:`JobStore.delete`. Returns what
    :meth:`JobStore.delete` returns.
    """
    removed = await store.delete(job_id, owner_id, updated_before=updated_before)
    if removed is not None:
        logger.info(
            "deleted job %s of owner %s: cause %s, %d object(s) removed",
            job_id,
            owner_id,
            cause,
            removed,
        )
    return removed


#: The longest retention period: a century. The bound keeps the cutoff of a
#: sweep inside the range of ``datetime``.
MAX_RETENTION_DAYS = 36_500


def retention_days(env: Mapping[str, str] = os.environ) -> int | None:
    """The retention period from ``ANALYSIS_RETENTION_DAYS``, in days.

    ``None`` where the setting is unset: the service keeps every job (#1530).
    A value that is not a whole number from 1 to :data:`MAX_RETENTION_DAYS`
    raises, so the startup stops.
    """
    value = env.get(RETENTION_DAYS_VAR)
    if value is None:
        return None
    days = ascii_int(value, max_digits=len(str(MAX_RETENTION_DAYS)))
    if days is None or not 1 <= days <= MAX_RETENTION_DAYS:
        raise StoreConfigError(
            f"{RETENTION_DAYS_VAR} must be a whole number of days from 1 to"
            f" {MAX_RETENTION_DAYS}, or unset to keep every job"
        )
    return days


def env_variables() -> tuple[str, ...]:
    """Every setting that the store loaders read, once each.

    The job store, the retention period, the reads of each row of
    :data:`JOB_STORES` and :data:`~analysis_service.objects.OBJECT_STORES`,
    and the database settings. A change to any of them takes effect only
    after a restart (ADR 0093).
    """
    names = [JOB_STORE_VAR, RETENTION_DAYS_VAR]
    for job_store in JOB_STORES.values():
        names += job_store.reads
    for object_store in OBJECT_STORES.values():
        names += object_store.reads
    names += DATABASE_VARIABLES
    return tuple(dict.fromkeys(names))


async def expire_jobs(store: JobStore, days: int) -> None:
    """Delete each terminal job whose last update is more than ``days`` days old.

    One pass of the retention sweep (#1530). Each delete goes through
    :func:`audited_delete` with the cause ``retention``, so the job keeps its
    usage row and the log gets the line of the delete route. A ``queued`` or
    ``running`` job is never expired. Each delete carries the cutoff, so a
    job that its owner changed after the listing stays.
    """
    before = datetime.now(UTC) - timedelta(days=days)
    for job_id, owner_id in await store.expired(before):
        await audited_delete(
            store, job_id, owner_id, "retention", updated_before=before
        )


#: How old an object must be before the orphan sweep can delete it.
#:
#: A store writes an object before the row that names it: ``reserve`` puts
#: the sources and then admits the job, and ``save`` puts the report and then
#: writes its digest. In that window no row names the object yet. The window
#: lasts seconds, and admission waits for its global lock in it. One day is
#: far longer than that window, and longer than a clock difference between
#: the service and the object store. A save can put a report over an older
#: orphan report, but only while its job is not terminal, and the sweep keeps
#: each object of a job that is not terminal.
ORPHAN_GRACE = timedelta(days=1)


async def remove_orphans(store: JobStore) -> None:
    """One pass of the orphan sweep: delete each object older than
    :data:`ORPHAN_GRACE` that no row names, and log the count."""
    removed = await store.sweep_orphans(datetime.now(UTC) - ORPHAN_GRACE)
    logger.info("the orphan sweep removed %d object(s)", removed)


#: How often the sweeps run. A job outlives its retention period by one
#: interval at most.
SWEEP_SECONDS = 3600


async def _sweep(store: JobStore, days: int | None) -> None:
    """Run the sweeps now and then each :data:`SWEEP_SECONDS`, until the app
    stops: the retention sweep where ``days`` is set, then the orphan sweep.

    A failed sweep is logged, and the next sweep runs.
    """
    while True:
        if days is not None:
            try:
                await expire_jobs(store, days)
            except Exception:
                logger.exception("the retention sweep failed")
        try:
            await remove_orphans(store)
        except Exception:
            logger.exception("the orphan sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)


@asynccontextmanager
async def store_lifespan(
    store: JobStore, env: Mapping[str, str]
) -> AsyncIterator[None]:
    """Check the store, then fail each job in flight, before an app takes a request.

    **The one startup of a job store**: the ``/v1`` service and the web app
    both run it as the lifespan of their app, after they built the store from
    the backend settings. ``env`` is the mapping that the app built the store
    from, and the retention setting is read from it. The order is: the
    retention setting, the store's startup check (the schema revision, then
    the object store), the restart scan (#1529), then requests. A failed check stops the startup. The sweeps
    start after the restart scan and stop with the app: the orphan sweep
    always, because an owner delete can also leave an orphan, and the
    retention sweep where a retention period is set.
    """
    days = retention_days(env)
    await store.startup_check()
    # The scan is correct for a single instance only. It fails every queued or
    # running job in the store, so with a second live instance on one store it
    # would fail the jobs that instance still runs.
    failed = await store.fail_in_flight()
    if failed:
        logger.warning(
            "restart scan failed %d job(s) in flight: %s", len(failed), failed
        )
    sweep = asyncio.create_task(_sweep(store, days))
    try:
        yield
    finally:
        sweep.cancel()
        with suppress(asyncio.CancelledError):
            await sweep
