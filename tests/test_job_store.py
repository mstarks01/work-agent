"""The ``JobStore`` contract: every backend pair answers these the same way.

Each test gets its store from :func:`new_store`, which takes one parameter for
each backend pair. A new backend adds its parameter there and proves the same
behaviour as ``memory``. Read a store here only through the ``JobStore``
protocol, never through a private attribute or method.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy.pool import NullPool

from analysis_service import jobs, sql_store
from analysis_service.answer_sets import NO_ANSWERS, AnswerSet
from analysis_service.budgets import BudgetPolicy
from analysis_service.fact_answers import FactAnswer
from analysis_service.jobs import (
    JOB_STORES,
    NON_LOCKING_STATUSES,
    RESTART_FAILURE_MESSAGE,
    Admission,
    Checkpoint,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    JobStoreConfigError,
    Resumption,
    build_store,
    same_major,
)
from analysis_service.objects import ObjectStore, build_object_store, object_key
from analysis_service.principals import Identity, Principal
from analysis_service.report import SCHEMA_VERSION, NodeRun, TokenUsage
from analysis_service.sources import Source
from analysis_service.sql_store import DATABASES
from tests import test_resume
from tests.factories import (
    SEEDING_BUDGET,
    admit,
    new_principal,
    sample_report,
    sample_selection,
    valid_model,
)
from tests.sql_stores import OBJECT_ENVS, object_env, postgres_env, sql_env
from tests.test_parsing import ISDIGIT_TRAPS
from tests.test_pause import waiting

POLICY = BudgetPolicy(
    window_seconds=3600,
    max_jobs_per_window=3,
    max_tokens_per_window=1000,
    global_max_tokens_per_window=2000,
)

#: A fact key the store keeps without reading it.
KEY = ("df-1", "encryption_in_transit", "", "", "", "")


#: The settings of a fresh ``sql`` store on each database, under a new
#: directory, with the named object store.
SQL_SETTINGS = {
    "sqlite+aiosqlite": sql_env,
    "postgresql+asyncpg": postgres_env,
}

#: Every backend pair the suite runs on: the job store, then the database and
#: the object store where the job store keeps objects. ``tests/test_backends.py``
#: checks these against the backend tables, so a new row needs a pair here.
BACKEND_PAIRS = (
    "memory",
    *(f"sql/{driver}/{objects}" for driver in SQL_SETTINGS for objects in OBJECT_ENVS),
)

#: The mark of each database that needs a server, so that its pairs run in its
#: CI lane.
LANES = {"postgresql+asyncpg": pytest.mark.postgres}


def _param(pair: str):
    driver = pair.split("/")[1] if "/" in pair else pair
    return pytest.param(pair, marks=LANES.get(driver, ()))


def _settings(request, tmp_path_factory, monkeypatch):
    """A maker of the settings of fresh stores of the pair ``request.param``,
    which keeps objects."""
    _, driver, objects = request.param.split("/")
    object_env(request, objects)
    # Each test runs each call in an event loop of its own, and a pooled
    # connection belongs to the loop that opened it. So the stores here keep
    # no pool. tests/test_postgres.py checks the pool of PostgreSQL.
    for name, database in DATABASES.items():
        monkeypatch.setitem(DATABASES, name, replace(database, pool=NullPool))
    settings = SQL_SETTINGS[driver]
    return lambda: settings(tmp_path_factory.mktemp("store"), objects)


@pytest.fixture(params=[_param(pair) for pair in BACKEND_PAIRS])
def new_store(request, tmp_path_factory, monkeypatch):
    """A maker of fresh stores of one backend pair."""
    if request.param == "memory":
        return InMemoryJobStore
    settings = _settings(request, tmp_path_factory, monkeypatch)
    return lambda: build_store(settings())


@pytest.fixture
def store(new_store):
    return new_store()


class FailingDeletes:
    """An object store whose delete raises ``error`` for each key in ``failing``."""

    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects
        self.failing: set[str] = set()
        self.error: BaseException = PermissionError("the delete is refused")

    def __getattr__(self, name: str):
        return getattr(self._objects, name)

    async def delete(self, key: str) -> bool:
        if key in self.failing:
            raise self.error
        return await self._objects.delete(key)


#: The backend pairs whose job store keeps objects: each pair that names an
#: object store.
OBJECT_PAIRS = tuple(pair for pair in BACKEND_PAIRS if "/" in pair)


@pytest.fixture(params=[_param(pair) for pair in OBJECT_PAIRS])
def held(request, tmp_path_factory, monkeypatch):
    """A fresh store of a pair that keeps objects, and its object store, whose
    deletes a test can make fail."""
    env = _settings(request, tmp_path_factory, monkeypatch)()
    objects = FailingDeletes(build_object_store(env))
    return JOB_STORES[env["ANALYSIS_JOB_STORE"]].build(env, objects), objects


def new_record(subject: str = "alice", reserved: int = 0) -> JobRecord:
    return JobRecord.create(
        owner_id=subject,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        reserved_tokens=reserved,
    )


async def reserving(
    store, rec, *, ceiling: int = 100, budget: BudgetPolicy = POLICY
) -> Admission:
    """Reserve ``rec``, and create its owner's principal where it has none."""
    return await store.reserve(
        rec,
        ceiling=ceiling,
        budget=budget,
        principal=await new_principal(store, rec.owner_id),
    )


def reserve(store, rec, *, ceiling: int = 100, budget: BudgetPolicy = POLICY):
    return asyncio.run(reserving(store, rec, ceiling=ceiling, budget=budget))


def enrol(store, owner: str = "alice") -> None:
    """Give ``owner`` a principal before a race, through a job that holds no
    slot, no token and no parent: a rejected job that measured nothing."""
    record = new_record(owner)
    record.transition("running")
    record.transition("rejected")
    asyncio.run(admit(store, record))


def completed(store, subject="alice", **create) -> str:
    """The ID of a completed job with a report, admitted to ``store``."""
    record = JobRecord.create(
        owner_id=subject,
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        **create,
    )
    record.transition("running")
    record.report = sample_report()
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id


def follow_up(parent: str) -> JobRecord:
    """A report's follow-up of ``parent``, still queued."""
    return JobRecord.create(
        owner_id="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        resumption=Resumption(
            parent_id=parent,
            checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
            follow_up=True,
        ),
        parent_revision=0,
    )


def final(store, parent: str) -> str:
    """The ID of a completed follow-up of ``parent``: a final report."""
    return completed(store, resumption=follow_up(parent).resumption, parent_revision=0)


def amendment(parent: str, revision: int = 0) -> JobRecord:
    """A job an amendment of ``parent`` started, still queued."""
    return JobRecord.create(
        owner_id="alice",
        sources=[Source.description("a web app storing orders")],
        frameworks=sample_selection(),
        amends=parent,
        parent_revision=revision,
        ask_questions=True,
    )


class TestReadsAndSaves:
    def test_create_get_roundtrip(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_get_unknown_returns_none(self, store):
        assert asyncio.run(store.get("job-missing")) is None

    def test_reserve_get_roundtrip_keeps_the_record_queued(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.get(record.id)

        fetched = asyncio.run(scenario())
        assert fetched is not None
        assert fetched.status == "queued"

    def test_store_round_trips_a_resumed_job(self, store):
        # An owner ID is a part of each object key, so it takes that shape.
        record = test_resume.TestTheResumedRun().resumed_job()
        record.owner_id = "alice"
        asyncio.run(admit(store, record))
        assert asyncio.run(store.get(record.id)).resumption == record.resumption

    def test_owned_answers_none_for_a_foreign_job(self, store):
        """A foreign job and a missing one are the same answer, so a job id
        cannot be probed for existence."""

        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.owned(record.id, "mallory")

        assert asyncio.run(scenario()) is None

    def test_owned_leaves_the_report_behind(self, store):
        """The status and event routes read the envelope and never the
        analysis. Copying the report for them is the whole cost of a read, and
        a read carries none of the bounds admission does."""

        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            record.report = sample_report()
            record.transition("completed")
            await store.save(record)
            return await store.owned(record.id, "alice"), await store.get(record.id)

        trimmed, whole = asyncio.run(scenario())
        assert trimmed is not None and whole is not None
        assert trimmed.status == "completed"
        assert trimmed.report is None
        assert whole.report is not None, "get still answers with the whole record"

    def test_owned_leaves_the_checkpoint_and_the_resumption_behind(self, store):
        """A waiting job's checkpoint and a resumed job's parent checkpoint are
        each a whole model and catalog, and the envelope reads neither (run 11,
        finding 2). The one route that needs a checkpoint asks for it by name."""

        async def scenario():
            held = Checkpoint(system_model=valid_model(), assertions=None)
            record = new_record()
            record.resumption = Resumption(
                follow_up=False, parent_id="parent", checkpoint=held
            )
            await admit(store, record)
            record.transition("running")
            record.checkpoint = held
            record.transition("awaiting-answers")
            await store.save(record)
            return (
                await store.owned(record.id, "alice"),
                await store.checkpoint(record.id, "alice"),
                await store.checkpoint(record.id, "mallory"),
                await store.resumption(record.id, "alice"),
                await store.resumption(record.id, "mallory"),
            )

        envelope, asked, foreign, resumption, foreign_resumption = asyncio.run(
            scenario()
        )
        assert envelope is not None
        assert envelope.status == "awaiting-answers"
        assert envelope.checkpoint is None
        assert envelope.resumption is None
        assert asked is not None and asked.system_model == valid_model()
        assert foreign is None
        assert resumption is not None and resumption.parent_id == "parent"
        assert foreign_resumption is None

    def test_report_is_one_copy_and_rechecks_ownership(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            record.report = sample_report()
            record.transition("completed")
            await store.save(record)
            mine = await store.report(record.id, "alice")
            mine.system_model.external_entities.clear()
            return (
                mine,
                await store.report(record.id, "alice"),
                await store.report(record.id, "mallory"),
            )

        mutated, again, foreign = asyncio.run(scenario())
        assert again == sample_report(), "the caller's copy aliases nothing stored"
        assert mutated != again
        assert foreign is None

    def test_report_json_serializes_without_a_second_copy(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            record.report = sample_report()
            record.transition("completed")
            await store.save(record)
            return (
                await store.report_json(record.id, "alice"),
                await store.report_json(record.id, "mallory"),
            )

        mine, foreign = asyncio.run(scenario())
        assert isinstance(mine, dict) and mine
        assert foreign is None, "ownership is re-checked, not assumed of the caller"

    def test_report_json_answers_none_when_no_report_is_attached(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.report_json(record.id, "alice")

        assert asyncio.run(scenario()) is None

    def test_events_after_answers_the_unseen_events_as_copies(self, store):
        record = new_record()
        asyncio.run(admit(store, record))
        record.transition("running")
        asyncio.run(store.save(record))
        status, events = asyncio.run(store.events_after(record.id, 1))
        assert status == "running"
        assert [event.seq for event in events] == [2]
        events.clear()
        assert len(asyncio.run(store.events_after(record.id, 0))[1]) == 2
        assert asyncio.run(store.events_after("job-missing", 0)) is None

    def test_save_requires_existing_record(self, store):
        with pytest.raises(ValueError, match="does not exist"):
            asyncio.run(store.save(new_record()))

    def test_mutations_invisible_until_saved(self, store):
        async def scenario():
            record = new_record()
            await admit(store, record)
            record.transition("running")
            before_save = await store.get(record.id)
            await store.save(record)
            after_save = await store.get(record.id)
            return before_save.status, after_save.status

        assert asyncio.run(scenario()) == ("queued", "running")


class TestReserve:
    """The seam the per-caller concurrency ceiling is enforced through (#113).

    It lives on the store rather than beside the API so the ceiling is exactly
    as shared as the deployment's storage is: an in-process counter would be
    per-instance whatever backend was configured, so two instances would enforce
    double the configured number and reset it on every deploy.

    Counting and creating is **one** call (#505). There is no bare count to ask,
    so what these tests read is the count the reservation itself observed.
    """

    @pytest.fixture(autouse=True)
    def _stores(self, new_store):
        self.new_store = new_store

    def counts(self, statuses: list[JobStatus], subject: str = "alice") -> int:
        """How many jobs a fresh reservation for ``subject`` sees in flight."""

        async def scenario():
            store = self.new_store()
            for status in statuses:
                record = new_record()
                await admit(store, record)
                if status != "queued":
                    record.transition("running")
                if status not in ("queued", "running"):
                    record.transition(status)
                await store.save(record)
            probe = JobRecord.create(
                owner_id=subject,
                sources=[Source.description("an app")],
                frameworks=sample_selection(),
            )
            return (await admit(store, probe)).active

        return asyncio.run(scenario())

    def test_an_empty_store_holds_nothing_in_flight(self):
        assert self.counts([]) == 0

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_a_job_before_a_terminal_state_is_in_flight(self, status):
        assert self.counts([status]) == 1

    @pytest.mark.parametrize("status", ["completed", "failed", "rejected"])
    def test_a_terminal_job_is_not(self, status):
        # Every terminal state releases the slot, not just the happy one: a
        # failed run holds no provider capacity, and counting it would let a
        # burst of failures lock a caller out until the process restarted.
        assert self.counts([status]) == 0

    def test_only_the_named_subject_is_counted(self, store):
        async def scenario():
            seen = {}
            for owner in ("alice", "alice", "bob", "alice", "bob"):
                record = JobRecord.create(
                    owner_id=owner,
                    sources=[Source.description("an app")],
                    frameworks=sample_selection(),
                )
                seen[owner] = (await admit(store, record)).active
            return seen["alice"], seen["bob"]

        # alice's third reservation saw her first two; bob's second saw only his
        # first. One store, two budgets.
        assert asyncio.run(scenario()) == (2, 1)

    def test_an_unknown_subject_holds_nothing(self):
        assert self.counts(["queued"], subject="carol") == 0

    def test_the_count_follows_the_records_rather_than_a_counter(self):
        # The records are the truth. A maintained counter is a second copy that
        # drifts the moment one path that ends a job forgets to decrement it.
        assert self.counts(["running"]) == 1
        assert self.counts(["completed"]) == 0

    def test_a_reservation_at_the_ceiling_admits_nothing(self, store):
        async def scenario():
            await admit(store, new_record())
            refused = new_record()
            outcome = await store.reserve(refused, ceiling=1, budget=SEEDING_BUDGET)
            return outcome, await store.get(refused.id)

        outcome, stored = asyncio.run(scenario())
        assert outcome == Admission(outcome="at_ceiling", active=1)
        # A refusal that still wrote the record would be a queue with extra
        # steps: the caller's place in the provider quota would be held anyway.
        assert stored is None

    def test_a_reservation_below_the_ceiling_is_admitted(self, store):
        async def scenario():
            await admit(store, new_record())
            record = new_record()
            return await store.reserve(
                record, ceiling=2, budget=SEEDING_BUDGET
            ), await store.get(record.id)

        outcome, stored = asyncio.run(scenario())
        assert outcome == Admission(outcome="admitted", active=1)
        assert stored is not None

    def test_a_ceiling_another_subject_fills_does_not_refuse_this_one(self, store):
        async def scenario():
            for _ in range(3):
                await admit(store, new_record())
            bob = JobRecord.create(
                owner_id="bob",
                sources=[Source.description("an app")],
                frameworks=sample_selection(),
            )
            return await reserving(store, bob, ceiling=1, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "admitted"

    def test_a_duplicate_id_is_named_rather_than_raised(self, store):
        # The API mints the id, so this is a defect report and not a caller
        # error; it is distinct from at_ceiling so the API can answer 500
        # rather than 429 for it.
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.reserve(record, ceiling=2, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "duplicate"

    def test_a_duplicate_is_settled_before_the_ceiling(self, store):
        # A record the store already holds is inside the budget already.
        # Counting it against the ceiling as well would report at_ceiling for
        # what is really a collision, and send a defect out as a 429.
        async def scenario():
            record = new_record()
            await admit(store, record)
            return await store.reserve(record, ceiling=1, budget=SEEDING_BUDGET)

        assert asyncio.run(scenario()).outcome == "duplicate"

    def test_simultaneous_reservations_never_exceed_the_ceiling(self, store):
        # The check-then-act race, run at a barrier: every task is released at
        # the same moment and they contend for one slot. Two calls -- a count
        # then a create -- would let all of them read 0 before any wrote.
        enrol(store)

        async def scenario():
            start = asyncio.Event()

            async def contend():
                await start.wait()
                return await store.reserve(
                    new_record(), ceiling=2, budget=SEEDING_BUDGET
                )

            racers = [asyncio.create_task(contend()) for _ in range(20)]
            start.set()
            outcomes = await asyncio.gather(*racers)
            return sum(1 for outcome in outcomes if outcome.outcome == "admitted")

        assert asyncio.run(scenario()) == 2

    def test_the_store_holds_exactly_what_it_admitted(self, store):
        # The count and the records cannot disagree: an admitted reservation is
        # a written record, and a refused one is nothing at all.
        enrol(store)

        async def scenario():
            async def contend():
                record = new_record()
                outcome = await store.reserve(record, ceiling=3, budget=SEEDING_BUDGET)
                return record.id, outcome.outcome

            results = await asyncio.gather(
                *(asyncio.create_task(contend()) for _ in range(20))
            )
            written = [
                job_id for job_id, _ in results if await store.get(job_id) is not None
            ]
            admitted = [job_id for job_id, outcome in results if outcome == "admitted"]
            return sorted(written), sorted(admitted)

        written, admitted = asyncio.run(scenario())
        assert written == admitted
        assert len(admitted) == 3


class TestTheRateBound:
    def test_a_burst_of_finished_jobs_still_hits_the_rate(self, store):
        # What the concurrency ceiling cannot do. Each of these finished before
        # the next was sent, so every one of them cleared a ceiling of 1.
        for _ in range(POLICY.max_jobs_per_window):
            rec = new_record()
            reserve(store, rec, ceiling=1)
            rec.transition("running")
            rec.transition("completed")
            asyncio.run(store.save(rec))
        assert reserve(store, new_record(), ceiling=1).outcome == "over_rate"

    def test_the_rate_is_per_subject(self, store):
        for _ in range(POLICY.max_jobs_per_window):
            reserve(store, new_record("alice"))
        assert reserve(store, new_record("bob")).outcome == "admitted"

    def test_a_job_outside_the_window_does_not_count(self, store):
        for _ in range(POLICY.max_jobs_per_window):
            rec = new_record()
            reserve(store, rec)
            # Backdate past the window, as the clock would have.
            stale = rec.model_copy(deep=True)
            stale.created_at = datetime.now(UTC) - timedelta(seconds=7200)
            asyncio.run(store.save(stale))
        assert reserve(store, new_record()).outcome == "admitted"


class TestTheTokenBounds:
    def test_serial_failing_jobs_cannot_outspend_the_token_budget(self, store):
        # The attack the reservation stands against: a caller whose submissions
        # outrun the deadline, each freeing its estimate on the way out.
        budget = POLICY.model_copy(update={"max_jobs_per_window": 100})
        outcomes = []
        for _ in range(4):
            rec = new_record(reserved=400)
            outcome = reserve(store, rec, budget=budget).outcome
            outcomes.append(outcome)
            if outcome != "admitted":
                continue
            rec.transition("running")
            rec.transition("failed")
            asyncio.run(store.save(rec))
        assert outcomes == [
            "admitted",
            "admitted",
            "over_owner_budget",
            "over_owner_budget",
        ]

    def test_one_large_job_can_exhaust_a_budget_a_count_would_admit(self, store):
        # The bound a job count cannot express: this is the first submission,
        # so the rate is nowhere near its limit.
        assert (
            reserve(
                store, new_record(reserved=POLICY.max_tokens_per_window + 1)
            ).outcome
            == "over_owner_budget"
        )

    def test_the_reservation_of_the_job_being_admitted_counts(self, store):
        # Checking only what is already committed would admit a job that takes
        # the window over on its own.
        reserve(store, new_record(reserved=600))
        assert reserve(store, new_record(reserved=600)).outcome == "over_owner_budget"

    def test_settling_below_the_estimate_frees_the_difference(self, store):
        # Reconciliation is what stops a coarse estimate from being a hard cap:
        # a job that reserved 900 and cost 10 leaves the window nearly empty.
        rec = new_record(reserved=900)
        reserve(store, rec)
        rec.transition("running")
        rec.transition("completed")
        rec.measured_tokens = 10
        asyncio.run(store.save(rec))
        assert reserve(store, new_record(reserved=900)).outcome == "admitted"

    def test_the_subject_budget_is_per_subject(self, store):
        reserve(store, new_record("alice", reserved=900))
        assert reserve(store, new_record("bob", reserved=900)).outcome == "admitted"

    def test_the_global_budget_holds_when_the_per_subject_ones_do_not(self, store):
        # Three subjects each well inside their own allowance, together past the
        # deployment's. This is the guardrail a per-owner bound cannot be.
        for subject in ("alice", "bob", "carol"):
            outcome = reserve(store, new_record(subject, reserved=800))
        assert outcome.outcome == "over_global_budget"

    def test_a_global_refusal_reports_the_callers_own_figure(self, store):
        # `owner_tokens` is the caller's; `global_tokens` is the operator's.
        # A caller learning the deployment's load learns about other callers.
        reserve(store, new_record("alice", reserved=800))
        reserve(store, new_record("bob", reserved=800))
        outcome = reserve(store, new_record("carol", reserved=800))
        assert outcome.outcome == "over_global_budget"
        assert outcome.owner_tokens == 0
        assert outcome.global_tokens == 1600


class TestOrderingAndAtomicity:
    def test_the_soonest_clearing_bound_is_the_one_reported(self, store):
        # A submission over several bounds hears about the ceiling first,
        # because that is the one a job of theirs finishing will clear.
        reserve(store, new_record(reserved=900))
        assert (
            reserve(store, new_record(reserved=900), ceiling=1).outcome == "at_ceiling"
        )

    def test_a_refusal_writes_nothing(self, store):
        refused = new_record(reserved=POLICY.max_tokens_per_window + 1)
        reserve(store, refused)
        assert asyncio.run(store.get(refused.id)) is None

    def test_simultaneous_reservations_never_exceed_the_token_budget(self, store):
        # The same barrier the ceiling is tested at, on the budget: a check
        # separated from the insert would let every task read an empty window.
        enrol(store)

        async def scenario():
            start = asyncio.Event()

            async def contend():
                await start.wait()
                return await store.reserve(
                    new_record(reserved=400), ceiling=100, budget=POLICY
                )

            racers = [asyncio.create_task(contend()) for _ in range(20)]
            start.set()
            outcomes = await asyncio.gather(*racers)
            return sum(1 for o in outcomes if o.outcome == "admitted")

        # 1000 // 400 == 2, and the third would take the window past it.
        assert asyncio.run(scenario()) == 2

    def test_a_duplicate_is_settled_before_every_bound(self, store):
        # A record the store already holds is inside the budgets it charged.
        rec = new_record(reserved=900)
        reserve(store, rec)
        assert reserve(store, rec).outcome == "duplicate"


class TestTheParentLock:
    """A resumed job and an amendment's job each lock their parent (ADR 0072)."""

    @pytest.mark.parametrize("child", [follow_up, amendment])
    def test_a_child_in_flight_locks_its_parent(self, store, child):
        parent = waiting(store) if child is amendment else completed(store)
        locking = child(parent)
        asyncio.run(admit(store, locking))
        assert asyncio.run(store.resumed_by(parent, "alice")) == locking.id
        assert asyncio.run(store.resumed_by(parent, "mallory")) is None
        second = reserve(store, child(parent))
        assert second == Admission(outcome="resumed_already", active=0)

    @pytest.mark.parametrize("status", sorted(NON_LOCKING_STATUSES))
    def test_a_child_that_read_nothing_into_a_report_releases_its_parent(
        self, store, status
    ):
        parent = completed(store)
        failed = follow_up(parent)
        asyncio.run(admit(store, failed))
        failed.transition("running")
        failed.transition(status)
        asyncio.run(store.save(failed))
        assert asyncio.run(store.resumed_by(parent, "alice")) is None
        asyncio.run(admit(store, follow_up(parent)))

    def test_a_child_read_off_an_older_revision_is_refused(self, store):
        parent = waiting(store)
        assert asyncio.run(store.save_round(parent, "alice", NO_ANSWERS, [], [], 0))
        moved = reserve(store, amendment(parent, revision=0))
        assert moved == Admission(outcome="parent_moved", active=0)
        asyncio.run(admit(store, amendment(parent, revision=1)))

    def test_a_saved_round_and_an_amendment_never_both_land(self, store):
        """Each one locks the parent's row, so the second one sees the first."""
        parent = waiting(store)

        async def race():
            return await asyncio.gather(
                store.save_round(parent, "alice", NO_ANSWERS, [], [], 0),
                reserving(store, amendment(parent, revision=0)),
            )

        saved, admission = asyncio.run(race())
        assert saved != (admission.outcome == "admitted")


class TestTheSaves:
    def test_the_store_refuses_a_save_against_an_old_revision(self, store):
        job = waiting(store)
        record = asyncio.run(store.get(job))

        def save(revision):
            return asyncio.run(
                store.save_round(job, record.owner_id, NO_ANSWERS, [], [], revision)
            )

        assert save(0)
        assert not save(0)
        assert save(1)

    def test_a_saved_round_keeps_its_answers_and_its_checkpoint(self, store):
        job = waiting(store)
        given = AnswerSet(facts=(FactAnswer(key=KEY, value="unknown"),))
        assert asyncio.run(store.save_round(job, "alice", given, [KEY], [], 0))
        saved = asyncio.run(store.get(job))
        assert saved.round_revision == 1
        assert saved.answers == given
        assert saved.shown_early == [KEY]
        assert saved.checkpoint is not None

    def test_a_round_is_refused_on_a_foreign_finished_or_locked_job(self, store):
        job = waiting(store)
        assert not asyncio.run(store.save_round(job, "mallory", NO_ANSWERS, [], [], 0))
        done = completed(store)
        assert not asyncio.run(store.save_round(done, "alice", NO_ANSWERS, [], [], 0))
        asyncio.run(admit(store, amendment(job)))
        assert not asyncio.run(store.save_round(job, "alice", NO_ANSWERS, [], [], 0))
        assert asyncio.run(store.get(job)).round_revision == 0

    def test_a_draft_lands_once_for_each_revision(self, store):
        job = completed(store)
        draft = AnswerSet(facts=(FactAnswer(key=KEY, value="TLS 1.3"),))
        assert asyncio.run(store.save_draft(job, "alice", draft, 0))
        assert not asyncio.run(store.save_draft(job, "alice", NO_ANSWERS, 0))
        saved = asyncio.run(store.get(job))
        assert saved.round_revision == 1
        assert saved.draft == draft

    def test_a_draft_is_refused_on_a_foreign_final_or_locked_report(self, store):
        job = completed(store)
        assert not asyncio.run(store.save_draft(job, "mallory", NO_ANSWERS, 0))
        final_job = final(store, job)
        assert not asyncio.run(store.save_draft(final_job, "alice", NO_ANSWERS, 0))
        assert not asyncio.run(store.save_draft(job, "alice", NO_ANSWERS, 0))

    def test_a_correction_lands_only_on_a_final_report(self, store):
        corrections = [FactAnswer(key=KEY, value="TLS 1.2")]
        job = completed(store)
        assert not asyncio.run(store.save_corrections(job, "alice", corrections))
        final_job = final(store, job)
        assert not asyncio.run(
            store.save_corrections(final_job, "mallory", corrections)
        )
        assert asyncio.run(store.save_corrections(final_job, "alice", corrections))
        assert asyncio.run(store.get(final_job)).corrections == corrections


class TestADeletedJobLeavesAUsageRow:
    """ADR 0066: a delete removes the content and keeps what admission reads."""

    def test_no_read_serves_a_deleted_job(self, store):
        job = completed(store)
        assert asyncio.run(store.delete(job, "alice")) is not None
        assert asyncio.run(store.get(job)) is None
        assert asyncio.run(store.owned(job, "alice")) is None
        assert asyncio.run(store.report(job, "alice")) is None
        assert asyncio.run(store.events_after(job, 0)) is None
        assert asyncio.run(store.owned_jobs("alice")) == []

    def test_only_the_owner_deletes_and_only_a_terminal_job(self, store):
        job = completed(store)
        queued = new_record()
        asyncio.run(admit(store, queued))
        assert asyncio.run(store.delete(job, "mallory")) is None
        assert asyncio.run(store.delete(queued.id, "alice")) is None
        assert asyncio.run(store.delete("job-missing", "alice")) is None
        assert asyncio.run(store.get(job)) is not None

    def test_a_deleted_job_still_counts_toward_the_rate(self, store):
        job = completed(store)
        asyncio.run(store.delete(job, "alice"))
        one_a_window = BudgetPolicy(
            window_seconds=3600,
            max_jobs_per_window=1,
            max_tokens_per_window=10**9,
            global_max_tokens_per_window=10**9,
        )
        admission = asyncio.run(
            store.reserve(new_record(), ceiling=100, budget=one_a_window)
        )
        assert admission.outcome == "over_rate"

    def test_a_deleted_job_still_counts_toward_the_token_budgets(self, store):
        spent = new_record(reserved=900)
        spent.transition("running")
        spent.transition("failed")
        asyncio.run(admit(store, spent))
        asyncio.run(store.delete(spent.id, "alice"))
        assert reserve(store, new_record(reserved=200)).outcome == "over_owner_budget"
        one_owner = POLICY.model_copy(update={"global_max_tokens_per_window": 1000})
        bob = reserve(store, new_record("bob", reserved=200), budget=one_owner)
        assert bob.outcome == "over_global_budget"

    def test_a_deleted_follow_up_still_locks_its_parent(self, store):
        parent = completed(store)
        child = follow_up(parent)
        child.transition("running")
        child.report = sample_report()
        child.transition("completed")
        asyncio.run(admit(store, child))
        assert asyncio.run(store.delete(child.id, "alice")) is not None
        assert asyncio.run(store.resumed_by(parent, "alice")) == child.id

    def test_the_listing_is_the_owner_s_jobs_oldest_first_as_envelopes(self, store):
        first = completed(store)
        second = completed(store)
        completed(store, subject="bob")
        listed = asyncio.run(store.owned_jobs("alice"))
        assert [record.id for record in listed] == [first, second]
        assert all(record.report is None for record in listed)


def last_updated(store, status: JobStatus, days_ago: int, reserved: int = 0) -> str:
    """The ID of a job in ``status`` whose last update was ``days_ago`` days ago."""
    record = new_record(reserved=reserved)
    if status != "queued":
        record.transition("running")
    if status not in ("queued", "running"):
        record.transition(status)
    record.updated_at = datetime.now(UTC) - timedelta(days=days_ago, minutes=1)
    asyncio.run(admit(store, record))
    return record.id


class ChangedAfterListing:
    """A store whose owner saves a draft on each job just after the sweep lists it."""

    def __init__(self, store) -> None:
        self._store = store

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    async def expired(self, before: datetime) -> list[tuple[str, str]]:
        listed = await self._store.expired(before)
        for job_id, owner_id in listed:
            assert await self._store.save_draft(job_id, owner_id, NO_ANSWERS, 0)
        return listed


class TestTheRetentionSweep:
    """#1530: the sweep deletes each terminal job older than the period, through
    the delete of the route."""

    def test_a_delete_with_a_cutoff_keeps_a_job_updated_since(self, store):
        job = last_updated(store, "completed", days_ago=2)
        cutoff = datetime.now(UTC) - timedelta(days=1)
        assert asyncio.run(store.save_draft(job, "alice", NO_ANSWERS, 0))
        assert asyncio.run(store.delete(job, "alice", updated_before=cutoff)) is None
        assert asyncio.run(store.get(job)) is not None

    def test_a_draft_and_a_delete_with_a_cutoff_never_both_land(self, store):
        """Each one locks the job's row: the job goes, or it keeps its draft."""
        job = last_updated(store, "completed", days_ago=2)
        cutoff = datetime.now(UTC) - timedelta(days=1)

        async def race():
            return await asyncio.gather(
                store.delete(job, "alice", updated_before=cutoff),
                store.save_draft(job, "alice", NO_ANSWERS, 0),
            )

        removed, saved = asyncio.run(race())
        assert (removed is not None) != saved

    def test_it_keeps_a_job_that_changes_after_the_listing(self, store):
        job = last_updated(store, "completed", days_ago=2)
        asyncio.run(jobs.expire_jobs(ChangedAfterListing(store), 1))
        assert asyncio.run(store.get(job)) is not None

    @pytest.mark.parametrize(
        "status", ["completed", "failed", "rejected", "awaiting-answers"]
    )
    def test_it_deletes_a_terminal_job_older_than_the_period(self, store, status):
        old = last_updated(store, status, days_ago=30)
        recent = last_updated(store, status, days_ago=29)
        asyncio.run(jobs.expire_jobs(store, 30))
        assert asyncio.run(store.get(old)) is None
        assert asyncio.run(store.get(recent)) is not None

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_it_never_deletes_a_job_in_flight(self, store, status):
        job = last_updated(store, status, days_ago=400)
        asyncio.run(jobs.expire_jobs(store, 1))
        assert asyncio.run(store.get(job)).status == status

    def test_a_swept_job_keeps_its_usage_row(self, store):
        last_updated(store, "failed", days_ago=2, reserved=900)
        asyncio.run(jobs.expire_jobs(store, 1))
        assert asyncio.run(store.owned_jobs("alice")) == []
        assert reserve(store, new_record(reserved=200)).outcome == "over_owner_budget"

    def test_each_delete_writes_the_line_of_the_route_with_cause_retention(
        self, store, caplog
    ):
        job = last_updated(store, "completed", days_ago=2)
        with caplog.at_level(logging.INFO, logger="analysis_service.jobs"):
            asyncio.run(jobs.expire_jobs(store, 1))
        [line] = caplog.messages
        assert re.fullmatch(
            f"deleted job {job} of owner alice: cause retention,"
            r" \d+ object\(s\) removed",
            line,
        )


def later() -> datetime:
    """A cutoff after now, so that the orphan sweep counts each object as old."""
    return datetime.now(UTC) + timedelta(minutes=1)


def earlier() -> datetime:
    """A cutoff before now, so that the orphan sweep counts each object as young."""
    return datetime.now(UTC) - timedelta(minutes=1)


#: One job a window, so that a second submission is refused.
ONE_A_WINDOW = BudgetPolicy(
    window_seconds=3600,
    max_jobs_per_window=1,
    max_tokens_per_window=10**9,
    global_max_tokens_per_window=10**9,
)

#: Each class of error an object store raises on a failed delete: the
#: filesystem row, then the two error classes of ``boto3``.
DELETE_ERRORS = [
    PermissionError("the delete is refused"),
    ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject"),
    EndpointConnectionError(endpoint_url="https://objects.example"),
]


class TestAFailedObjectDelete:
    """#1673: an object delete that fails leaves the object for the orphan
    sweep. It never undoes the row change, and it never hides an outcome."""

    @pytest.mark.parametrize("error", DELETE_ERRORS)
    def test_the_delete_answers_the_count_it_removed(self, held, error):
        store, objects = held
        job = completed(store)
        sources = object_key("alice", job, "sources.json")
        objects.failing = {sources}
        objects.error = error
        assert asyncio.run(store.delete(job, "alice")) == 1
        assert asyncio.run(store.get(job)) is None
        assert asyncio.run(objects.get(sources)) is not None
        report = object_key("alice", job, "report.json")
        assert asyncio.run(objects.get(report)) is None

    def test_the_usage_row_still_counts(self, held):
        store, objects = held
        job = completed(store)
        objects.failing = {object_key("alice", job, "sources.json")}
        asyncio.run(store.delete(job, "alice"))
        admission = asyncio.run(
            store.reserve(new_record(), ceiling=100, budget=ONE_A_WINDOW)
        )
        assert admission.outcome == "over_rate"

    def test_the_log_names_the_key_and_no_content(self, held, caplog):
        store, objects = held
        job = completed(store)
        sources = object_key("alice", job, "sources.json")
        objects.failing = {sources}
        with caplog.at_level(logging.WARNING, logger="analysis_service.sql_store"):
            asyncio.run(store.delete(job, "alice"))
        [line] = caplog.messages
        assert sources in line
        assert "PermissionError" in line
        assert "orders" not in line

    def test_the_audit_line_counts_the_objects_removed(self, held, caplog):
        store, objects = held
        job = completed(store)
        objects.failing = {object_key("alice", job, "sources.json")}
        with caplog.at_level(logging.INFO, logger="analysis_service.jobs"):
            assert asyncio.run(jobs.audited_delete(store, job, "alice", "owner")) == 1
        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == "analysis_service.jobs"
        ] == [f"deleted job {job} of owner alice: cause owner, 1 object(s) removed"]

    def test_a_cancellation_is_never_caught(self, held):
        store, objects = held
        job = completed(store)
        objects.failing = {object_key("alice", job, "sources.json")}
        objects.error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(store.delete(job, "alice"))

    @pytest.mark.parametrize("error", DELETE_ERRORS)
    def test_a_refused_job_answers_its_refusal(self, held, error):
        store, objects = held
        completed(store)
        refused = new_record()
        sources = object_key("alice", refused.id, "sources.json")
        objects.failing = {sources}
        objects.error = error
        admission = asyncio.run(
            store.reserve(refused, ceiling=100, budget=ONE_A_WINDOW)
        )
        assert admission.outcome == "over_rate"
        assert asyncio.run(objects.get(sources)) is not None
        objects.failing = set()
        assert asyncio.run(store.sweep_orphans(later())) == 1
        assert asyncio.run(objects.get(sources)) is None

    def test_an_admission_that_raises_keeps_its_own_error(self, held):
        store, objects = held
        stranger = new_record("mallory")
        objects.failing = {object_key("mallory", stranger.id, "sources.json")}
        with pytest.raises(ValueError, match="holds no principal"):
            asyncio.run(store.reserve(stranger, ceiling=100, budget=POLICY))


class TestTheOrphanSweep:
    """#1673: the sweep deletes each old object that no row names, and only those."""

    def test_it_removes_nothing_that_a_row_names(self, store):
        job = completed(store)
        assert asyncio.run(store.sweep_orphans(later())) == 0
        assert asyncio.run(store.get(job)).report is not None

    def test_it_deletes_an_old_object_that_no_row_names(self, held):
        store, objects = held
        key = object_key("alice", new_record().id, "sources.json")
        asyncio.run(objects.put(key, b"[]"))
        assert asyncio.run(store.sweep_orphans(later())) == 1
        assert asyncio.run(objects.get(key)) is None

    def test_it_keeps_a_young_object(self, held):
        store, objects = held
        key = object_key("alice", new_record().id, "sources.json")
        asyncio.run(objects.put(key, b"[]"))
        assert asyncio.run(store.sweep_orphans(earlier())) == 0
        assert asyncio.run(objects.get(key)) == b"[]"

    def test_it_keeps_each_object_of_a_job_that_holds_it(self, held):
        store, _ = held
        job = completed(store)
        queued = new_record()
        asyncio.run(admit(store, queued))
        assert asyncio.run(store.sweep_orphans(later())) == 0
        assert asyncio.run(store.get(job)).report is not None
        assert asyncio.run(store.get(queued.id)) is not None

    def test_each_batch_of_the_row_read_keeps_what_its_rows_name(
        self, held, monkeypatch
    ):
        store, objects = held
        monkeypatch.setattr(sql_store, "_ID_BATCH", 1)
        named = [completed(store), completed(store)]
        queued = new_record()
        asyncio.run(admit(store, queued))
        orphan = object_key("alice", new_record().id, "sources.json")
        asyncio.run(objects.put(orphan, b"[]"))
        assert asyncio.run(store.sweep_orphans(later())) == 1
        assert asyncio.run(objects.get(orphan)) is None
        assert all(asyncio.run(store.get(job)).report for job in named)
        assert asyncio.run(store.get(queued.id)) is not None

    def test_it_deletes_an_old_report_that_its_row_does_not_name(self, held):
        """A report put before a save wrote its digest, as a crash leaves it."""
        store, objects = held
        queued = new_record()
        asyncio.run(admit(store, queued))
        report = object_key("alice", queued.id, "report.json")
        asyncio.run(objects.put(report, b"{}"))
        assert asyncio.run(store.sweep_orphans(later())) == 1
        assert asyncio.run(objects.get(report)) is None
        assert asyncio.run(store.get(queued.id)) is not None

    def test_a_row_names_an_object_only_under_its_own_owner(self, held):
        store, objects = held
        queued = new_record()
        asyncio.run(admit(store, queued))
        key = object_key("bob", queued.id, "sources.json")
        asyncio.run(objects.put(key, b"[]"))
        assert asyncio.run(store.sweep_orphans(later())) == 1
        assert asyncio.run(store.get(queued.id)) is not None

    def test_it_never_deletes_a_key_that_the_service_never_writes(self, held):
        store, objects = held
        job_id = new_record().id
        strays = [
            "notes.txt",
            f"alice/{job_id}/notes.json",
            "alice/job-1/sources.json",
            f"Alice/{job_id}/sources.json",
            f"alice/{job_id}/report.json/extra",
            f"tenant/alice/{job_id}/sources.json",
        ]
        for key in strays:
            asyncio.run(objects.put(key, b"kept"))
        assert asyncio.run(store.sweep_orphans(later())) == 0
        assert [asyncio.run(objects.get(key)) for key in strays] == [b"kept"] * 6

    def test_one_pass_writes_one_line_with_its_count(self, held, caplog, monkeypatch):
        store, objects = held
        monkeypatch.setattr(jobs, "ORPHAN_GRACE", timedelta(minutes=-1))
        key = object_key("alice", new_record().id, "sources.json")
        asyncio.run(objects.put(key, b"[]"))
        with caplog.at_level(logging.INFO, logger="analysis_service.jobs"):
            asyncio.run(jobs.remove_orphans(store))
        assert caplog.messages == ["the orphan sweep removed 1 object(s)"]


class SweepSpy(InMemoryJobStore):
    """A ``memory`` store that counts the passes of the orphan sweep, and
    whose first ``failures`` passes raise."""

    def __init__(self, failures: int = 0) -> None:
        super().__init__()
        self.passes = 0
        self.failures = failures

    async def sweep_orphans(self, before: datetime) -> int:
        self.passes += 1
        if self.passes <= self.failures:
            raise OSError("the object store cannot list")
        return 0


async def passes_in_lifespan(store: SweepSpy, passes: int) -> None:
    """Start the store's lifespan, and wait until the orphan sweep ran
    ``passes`` times, for five seconds at most."""
    async with jobs.store_lifespan(store):
        for _ in range(500):
            if store.passes >= passes:
                break
            await asyncio.sleep(0.01)


class TestTheSweepLoop:
    """#1673: the orphan sweep runs on every start-up, with or without a
    retention period, and a failed pass does not stop the loop."""

    def test_the_orphan_sweep_runs_without_a_retention_period(self, monkeypatch):
        monkeypatch.delenv("ANALYSIS_RETENTION_DAYS", raising=False)
        store = SweepSpy()
        asyncio.run(passes_in_lifespan(store, 1))
        assert store.passes == 1

    def test_a_failed_pass_is_logged_and_the_next_pass_runs(self, monkeypatch, caplog):
        monkeypatch.delenv("ANALYSIS_RETENTION_DAYS", raising=False)
        monkeypatch.setattr(jobs, "SWEEP_SECONDS", 0)
        store = SweepSpy(failures=1)
        with caplog.at_level(logging.INFO, logger="analysis_service.jobs"):
            asyncio.run(passes_in_lifespan(store, 2))
        assert store.passes >= 2
        assert "the orphan sweep failed" in caplog.messages

    def test_a_failed_retention_pass_does_not_stop_the_orphan_sweep(self, monkeypatch):
        monkeypatch.setenv("ANALYSIS_RETENTION_DAYS", "30")
        store = SweepSpy()

        async def refused(before: datetime) -> list[tuple[str, str]]:
            raise OSError("the database is down")

        monkeypatch.setattr(store, "expired", refused)
        asyncio.run(passes_in_lifespan(store, 1))
        assert store.passes == 1


class TestTheRetentionSetting:
    """#1530: unset keeps every job, and a value that is not a period refuses."""

    def test_unset_keeps_every_job(self):
        assert jobs.retention_days({}) is None

    @pytest.mark.parametrize("value", ["1", "30", str(jobs.MAX_RETENTION_DAYS)])
    def test_a_whole_number_of_days(self, value):
        assert jobs.retention_days({"ANALYSIS_RETENTION_DAYS": value}) == int(value)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "0",
            "-1",
            "+7",
            " 7",
            "7 ",
            "1.5",
            "1e3",
            "thirty",
            str(jobs.MAX_RETENTION_DAYS + 1),
            *ISDIGIT_TRAPS,
        ],
    )
    def test_any_other_value_stops_the_startup(self, value):
        with pytest.raises(JobStoreConfigError, match="ANALYSIS_RETENTION_DAYS"):
            jobs.retention_days({"ANALYSIS_RETENTION_DAYS": value})


class TestTheRestartScan:
    """#1529: a job in flight at startup fails, and nothing runs it again."""

    def test_it_fails_each_queued_or_running_job_with_the_restart_message(self, store):
        queued = new_record()
        running = new_record()
        running.transition("running")
        for record in (queued, running):
            asyncio.run(admit(store, record))
        failed = asyncio.run(store.fail_in_flight())
        assert sorted(failed) == sorted([queued.id, running.id])
        for job in failed:
            record = asyncio.run(store.get(job))
            assert record.status == "failed"
            assert record.error == RESTART_FAILURE_MESSAGE
            assert (record.events[-1].kind, record.events[-1].status) == (
                "status",
                "failed",
            )
        assert asyncio.run(store.fail_in_flight()) == []

    def test_a_waiting_or_terminal_job_stays_as_it_is(self, store):
        jobs = [completed(store)]
        for status in ("awaiting-answers", "failed", "rejected"):
            record = new_record()
            record.transition("running")
            record.transition(status)
            asyncio.run(admit(store, record))
            jobs.append(record.id)
        before = [asyncio.run(store.get(job)) for job in jobs]
        assert asyncio.run(store.fail_in_flight()) == []
        assert [asyncio.run(store.get(job)) for job in jobs] == before

    def test_a_failed_job_keeps_its_full_charge(self, store):
        # max(measured, reserved): the vendor billed the lost node runs, and a
        # smaller charge would let a restart reset the budget.
        budget = POLICY.model_copy(update={"max_jobs_per_window": 100})
        queued = new_record(reserved=400)
        running = new_record(reserved=400)
        running.transition("running")
        running.unreported_nodes = [
            NodeRun(
                node="extract",
                duration_ms=10,
                usage=TokenUsage(prompt_tokens=10, total_tokens=10),
            )
        ]
        for record in (queued, running):
            reserve(store, record, budget=budget)
        asyncio.run(store.fail_in_flight())
        outcome = reserve(store, new_record(reserved=300), budget=budget).outcome
        assert outcome == "over_owner_budget"

    def test_a_failed_job_frees_its_slot(self, store):
        reserve(store, new_record(), ceiling=1)
        asyncio.run(store.fail_in_flight())
        assert reserve(store, new_record(), ceiling=1).outcome == "admitted"


class TestTheSchemaVersion:
    """Each record keeps the ``SCHEMA_VERSION`` that created it, and
    :func:`~analysis_service.jobs.same_major` reads it (#1532)."""

    def test_a_new_record_holds_the_current_version(self, store):
        record = new_record()
        asyncio.run(admit(store, record))
        held = asyncio.run(store.owned(record.id, "alice"))
        assert held.schema_version == SCHEMA_VERSION
        assert same_major(held.schema_version)

    @pytest.mark.parametrize("version", ["2.10", "1.0"])
    def test_every_read_and_save_keeps_the_version_the_record_states(
        self, store, version
    ):
        job = waiting(store)
        record = asyncio.run(store.get(job))
        record.schema_version = version
        asyncio.run(store.save(record))
        assert asyncio.run(store.save_round(job, "alice", NO_ANSWERS, (), (), 0))
        reads = [
            asyncio.run(store.get(job)),
            asyncio.run(store.owned(job, "alice")),
            *asyncio.run(store.owned_jobs("alice")),
        ]
        assert {read.schema_version for read in reads} == {version}

    def test_the_restart_scan_keeps_the_version_of_a_job_it_fails(self, store):
        record = new_record()
        record.schema_version = "2.10"
        asyncio.run(admit(store, record))
        assert asyncio.run(store.fail_in_flight()) == [record.id]
        failed = asyncio.run(store.get(record.id))
        assert (failed.status, failed.schema_version) == ("failed", "2.10")

    @pytest.mark.parametrize(
        ("version", "readable"),
        [
            ("9.4", True),
            ("9.1", True),  # an older minor of this major
            ("9.7", True),
            ("9", True),
            ("8.4", False),  # an older major
            ("10.0", False),  # a newer major
            ("90.4", False),  # a major that starts with this one
            ("", False),
            (" 9.4", False),
        ],
    )
    def test_same_major_compares_the_major_of_what_the_store_returns(
        self, store, monkeypatch, version, readable
    ):
        monkeypatch.setattr(jobs, "SCHEMA_VERSION", "9.4")
        record = new_record()
        record.schema_version = version
        asyncio.run(admit(store, record))
        held = asyncio.run(store.owned(record.id, "alice"))
        assert same_major(held.schema_version) is readable


class TestPrincipals:
    """ADR 0064: a principal is an issuer and a ``sub``, and admission creates it."""

    def identity(self, issuer: str = "https://one.example") -> Identity:
        return Identity(issuer=issuer, sub="alice")

    def first(
        self, store, principal: Principal, *, ceiling: int = 100, reserved: int = 0
    ) -> Admission:
        """Reserve a first job of ``principal``'s identity, with the principal."""
        record = new_record(principal.owner_id, reserved=reserved)
        return asyncio.run(
            store.reserve(record, ceiling=ceiling, budget=POLICY, principal=principal)
        )

    def test_an_unknown_identity_holds_no_principal(self, store):
        assert asyncio.run(store.principal(self.identity())) is None

    def test_the_first_admission_keeps_the_principal_and_nothing_more(self, store):
        principal = Principal.new(self.identity())
        assert self.first(store, principal).outcome == "admitted"
        held = asyncio.run(store.principal(self.identity()))
        assert held == principal
        assert {name for name in vars(held)} == {
            "owner_id",
            "issuer",
            "sub",
            "created_at",
        }

    @pytest.mark.parametrize(
        ("bounds", "outcome"),
        [
            ({"ceiling": 0}, "at_ceiling"),
            ({"reserved": POLICY.max_tokens_per_window + 1}, "over_owner_budget"),
        ],
    )
    def test_a_refused_admission_leaves_no_principal(self, store, bounds, outcome):
        principal = Principal.new(self.identity())
        assert self.first(store, principal, **bounds).outcome == outcome
        assert asyncio.run(store.principal(self.identity())) is None

    def test_two_issuers_of_one_sub_are_two_principals(self, store):
        one = Principal.new(self.identity("https://one.example"))
        two = Principal.new(self.identity("https://two.example"))
        self.first(store, one)
        self.first(store, two)
        assert asyncio.run(store.principal(one.identity)) == one
        assert asyncio.run(store.principal(two.identity)) == two
        assert one.owner_id != two.owner_id
        mine = asyncio.run(store.owned_jobs(one.owner_id))
        assert [record.owner_id for record in mine] == [one.owner_id]

    def test_a_second_new_principal_of_one_identity_is_refused(self, store):
        held = Principal.new(self.identity())
        self.first(store, held)
        late = Principal.new(self.identity())
        record = new_record(late.owner_id)
        admission = asyncio.run(
            store.reserve(record, ceiling=100, budget=POLICY, principal=late)
        )
        assert admission == Admission(outcome="principal_exists", active=0)
        assert asyncio.run(store.get(record.id)) is None
        assert asyncio.run(store.principal(self.identity())) == held

    def test_a_later_job_of_the_owner_needs_no_principal(self, store):
        principal = Principal.new(self.identity())
        self.first(store, principal)
        later = new_record(principal.owner_id)
        admission = asyncio.run(store.reserve(later, ceiling=100, budget=POLICY))
        assert admission.outcome == "admitted"
        assert asyncio.run(store.principal(self.identity())) == principal

    def test_a_job_whose_owner_holds_no_principal_is_a_defect(self, store):
        with pytest.raises(ValueError, match="holds no principal"):
            asyncio.run(store.reserve(new_record("nobody"), ceiling=100, budget=POLICY))

    def test_a_job_of_another_owner_than_its_principal_is_a_defect(self, store):
        principal = Principal.new(self.identity())
        with pytest.raises(ValueError, match="another owner"):
            asyncio.run(
                store.reserve(
                    new_record("nobody"),
                    ceiling=100,
                    budget=POLICY,
                    principal=principal,
                )
            )
        assert asyncio.run(store.principal(self.identity())) is None
