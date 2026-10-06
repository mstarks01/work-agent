"""A job that asks questions pauses after its assertion pass and waits (#1252).

With the per-job toggle on, the run stops after the catalog, holds its model
and catalog, and ends in ``awaiting-answers``. The job then waits for a person
for as long as it takes, taking no in-flight slot. Answering it, or asking to
continue without answers, starts a resumed job from what it holds. An
autonomous run, with the toggle off, never pauses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from starlette.requests import ClientDisconnect

from analysis_service import graph
from analysis_service.assertions import AssertionRecord
from analysis_service.frameworks import PACKAGES
from analysis_service.jobs import (
    TERMINAL_STATUSES,
    Checkpoint,
    InMemoryJobStore,
    JobRecord,
    PipelineAwaiting,
    Resumption,
    execute_job,
)
from analysis_service.pipeline import AdkPipelineRunner, entry_of
from analysis_service.report import NodeRun
from analysis_service.sources import Source
from tests.factories import (
    DESCRIPTION_TEXT,
    emitted,
    sample_selection,
    scripted_pipeline,
    valid_model,
)
from tests.test_api import admit, auth, submission
from tests.test_links import catalog_client
from tests.test_resume import LINK, PRINCIPAL, parent_catalog, recorded_entries


def asking_job(
    owner_subject: str = "alice",
    ask_questions: bool = True,
    resumption: Resumption | None = None,
) -> JobRecord:
    return JobRecord.create(
        owner_subject=owner_subject,
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        ask_questions=ask_questions,
        resumption=resumption,
    )


def held() -> Checkpoint:
    return Checkpoint(
        system_model=valid_model(),
        assertions=AssertionRecord(proposed=1, catalog=parent_catalog()),
    )


class TestThePausedRun:
    """The real head-only graph, with scripted models."""

    def test_the_run_stops_after_the_catalog_and_holds_it(self):
        pipeline, _ = scripted_pipeline(
            {
                "extract": emitted(valid_model()),
                graph.ASSERT_NODE: json.dumps({"assertions": []}),
            },
            entry=graph.ENTRY_HEAD_ONLY,
            assertions=True,
        )
        record = asking_job()
        record.transition("running")
        visited: list[str] = []

        async def on_node(node: str) -> None:
            visited.append(node)

        outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))

        assert isinstance(outcome, PipelineAwaiting)
        assert outcome.checkpoint.system_model == valid_model()
        assert graph.ASSERT_NODE in visited
        assert graph.PREPARE_NODE not in visited
        assert outcome.nodes

    def test_a_head_with_no_catalog_pass_stops_after_the_gate(self):
        """A deployment that builds no catalog still pauses, for early questions."""
        pipeline, _ = scripted_pipeline(
            {"extract": emitted(valid_model())},
            entry=graph.ENTRY_HEAD_ONLY,
        )
        record = asking_job()
        record.transition("running")
        visited: list[str] = []

        async def on_node(node: str) -> None:
            visited.append(node)

        outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))

        assert isinstance(outcome, PipelineAwaiting)
        assert outcome.checkpoint.system_model == valid_model()
        assert outcome.checkpoint.assertions is None
        assert graph.PAUSE_NODE in visited
        assert graph.ASSERT_NODE not in visited
        assert graph.CATALOG_NODE not in visited


class TestTheWait:
    def test_a_paused_run_ends_in_awaiting_answers_with_its_checkpoint(self):
        class Pausing:
            async def run(self, job, on_node):
                return PipelineAwaiting(
                    checkpoint=held(),
                    nodes=[NodeRun(node="extract", model="m", duration_ms=10)],
                )

        store = InMemoryJobStore()
        record = asking_job()
        asyncio.run(admit(store, record))
        asyncio.run(execute_job(store, Pausing(), record.id, deadline_seconds=30))
        waiting = asyncio.run(store.get(record.id))

        assert waiting.status == "awaiting-answers"
        assert waiting.checkpoint == held()
        assert waiting.measured_tokens is not None

    def test_a_waiting_job_is_terminal_and_moves_no_further(self):
        assert "awaiting-answers" in TERMINAL_STATUSES
        record = asking_job()
        record.transition("running")
        record.transition("awaiting-answers")
        with pytest.raises(ValueError):
            record.transition("running")

    def test_a_resumed_job_never_pauses(self):
        resumed = asking_job(
            resumption=Resumption(follow_up=False, parent_id="p", checkpoint=held())
        )
        assert asking_job().pauses()
        assert not resumed.pauses()
        assert not asking_job(ask_questions=False).pauses()


def waiting(store, subject="alice", checkpoint=True) -> str:
    record = asking_job(owner_subject=subject)
    record.transition("running")
    record.checkpoint = held() if checkpoint else None
    record.transition("awaiting-answers")
    asyncio.run(admit(store, record))
    return record.id


class TestTheRoutes:
    def test_a_submission_with_questions_runs_the_head_only(self):
        client, _ = catalog_client()
        entries = recorded_entries(client)
        response = client.post(
            "/v1/jobs", json=submission(questions=True), headers=auth()
        )
        assert response.status_code == 201
        assert entries == [graph.ENTRY_HEAD_ONLY]

    def test_an_autonomous_submission_never_pauses(self):
        client, _ = catalog_client()
        entries = recorded_entries(client)
        client.post("/v1/jobs", json=submission(), headers=auth())
        assert entries == [graph.ENTRY_EXTRACT]

    def test_questions_pause_a_job_where_no_catalog_is_built(self):
        from tests.test_api import make_client

        client, _ = make_client()
        entries = recorded_entries(client)
        response = client.post(
            "/v1/jobs", json=submission(questions=True), headers=auth()
        )
        assert response.status_code == 201
        assert entries == [graph.ENTRY_HEAD_ONLY]

    def test_a_link_answer_is_still_refused_where_no_catalog_is_built(self):
        from tests.test_api import make_client

        client, _ = make_client()
        response = client.post(
            "/v1/jobs", json=submission(links=[LINK.model_dump()]), headers=auth()
        )
        assert response.status_code == 400

    def test_a_waiting_job_serves_its_questions_from_its_checkpoint(self):
        client, store = catalog_client()
        job = waiting(store)
        response = client.get(f"/v1/jobs/{job}/questions", headers=auth())
        assert response.status_code == 200
        (question,) = response.json()["link_questions"]
        assert question["principal"] == "customer accounts"

    def test_answers_resume_a_waiting_job_from_its_checkpoint(self):
        client, store = catalog_client()
        job = waiting(store)
        entries = recorded_entries(client)
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"links": [LINK.model_dump()], "revision": 0},
            headers=auth(),
        )
        assert response.status_code == 201
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.resumption.parent_id == job
        assert child.resumption.checkpoint == held()
        assert entries == [graph.ENTRY_RESUME]

    def test_no_answers_continues_a_waiting_job(self):
        client, store = catalog_client()
        job = waiting(store)
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"links": [], "revision": 0}, headers=auth()
        )
        assert response.status_code == 201
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.links == []
        assert [source.kind for source in child.sources] == ["description"]

    def test_a_waiting_job_takes_no_in_flight_slot(self):
        """A person may leave several jobs waiting and still submit."""
        client, store = catalog_client()
        for _ in range(3):
            waiting(store)
        store_ceiling = client.app.state.max_active_jobs
        record = asking_job()
        from tests.test_api import SEEDING_BUDGET

        admission = asyncio.run(
            store.reserve(record, ceiling=min(store_ceiling, 1), budget=SEEDING_BUDGET)
        )
        assert admission.outcome == "admitted"

    def test_another_caller_s_waiting_job_is_not_found(self):
        client, store = catalog_client()
        job = waiting(store, subject="bob")
        assert (
            client.get(f"/v1/jobs/{job}/questions", headers=auth()).status_code == 404
        )


def test_the_link_question_s_principal_is_the_catalog_s():
    """The fixture's principal is the one the paused catalog holds."""
    assert PRINCIPAL in {subject.id for subject in parent_catalog().subjects}


class TestTheEventStreamCap:
    """A stream stays open until its job is terminal, and nothing bounded how
    many one caller held open (run 11).

    Driven at the ASGI level: the test client waits for a whole body, so it
    cannot hold a stream open. Both disconnect paths are driven, because under
    ASGI 2.4 a disconnect raises before a background task runs.
    """

    @staticmethod
    def queued(store, subject="alice") -> str:
        record = asking_job(owner_subject=subject)
        asyncio.run(admit(store, record))
        return record.id

    @staticmethod
    async def open_stream(app, job, token, spec):
        """Open one stream; return its status, a way to leave, and its task."""
        started, gone = asyncio.Event(), asyncio.Event()
        status = []
        requested = False

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if gone.is_set():
                raise OSError("the client left")
            if message["type"] == "http.response.start":
                status.append(message["status"])
                started.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/v1/jobs/{job}/events",
            "raw_path": f"/v1/jobs/{job}/events".encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"authorization", f"Bearer {token}".encode()),
            ],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        }
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(started.wait(), 5)
        return status[0], gone, task

    @pytest.mark.parametrize("spec", ["2.0", "2.4"])
    def test_a_subject_past_the_cap_is_refused_and_every_slot_comes_back(self, spec):
        from analysis_service.api import MAX_EVENT_STREAMS_PER_SUBJECT

        client, store = catalog_client()
        app = client.app
        mine = self.queued(store)
        theirs = self.queued(store, subject="bob")

        async def scenario():
            held = [
                await self.open_stream(app, mine, "alice-token", spec)
                for _ in range(MAX_EVENT_STREAMS_PER_SUBJECT)
            ]
            assert [status for status, _, _ in held] == [200] * len(held)
            refused, _, done = await self.open_stream(app, mine, "alice-token", spec)
            await done
            assert refused == 429
            other, leave, task = await self.open_stream(app, theirs, "bob-token", spec)
            assert other == 200
            for _, gone, stream in [*held, (other, leave, task)]:
                gone.set()
                # Under ASGI 2.4 the closing frame goes to a client that left,
                # and a server handles the disconnect that raises.
                with contextlib.suppress(ClientDisconnect):
                    await asyncio.wait_for(stream, 5)
            return dict(app.state.event_streams)

        assert asyncio.run(scenario()) == {}


class TestThePausePathIsCertified:
    """The gate checks the head's nodes against the manifest, and a waiting job
    asks the gate before it serves its questions (#1294, item 1)."""

    FP = "a" * 64

    def gate(self, blessed=(), require_certified=True):
        from analysis_service.certification import (
            MANIFEST_VERSION,
            BlessedManifest,
            CertificationGate,
        )

        return CertificationGate(
            manifest=BlessedManifest(
                version=MANIFEST_VERSION,
                tiers={"base": frozenset(blessed), "strong": frozenset(blessed)},
            ),
            tier_of=lambda _node: "base",
            require_certified=require_certified,
        )

    def paused(self, gate):
        pipeline, _ = scripted_pipeline(
            {
                "extract": emitted(valid_model()),
                graph.ASSERT_NODE: json.dumps({"assertions": []}),
            },
            entry=graph.ENTRY_HEAD_ONLY,
            assertions=True,
        )
        record = asking_job()
        record.transition("running")

        async def on_node(node: str) -> None:
            del node

        runner = AdkPipelineRunner(pipeline, certification=gate)
        return asyncio.run(runner.run(record, on_node))

    def test_the_head_is_certified_at_the_pause(self):
        outcome = self.paused(self.gate())
        assert isinstance(outcome, PipelineAwaiting)
        assert outcome.certification is not None
        assert not outcome.certification.certified
        head = {node.node for node in outcome.certification.uncertified}
        assert head == {graph.EXTRACT_NODE, graph.ASSERT_NODE}

    def test_a_head_the_manifest_blesses_is_certified(self):
        first = self.paused(self.gate())
        blessed = {node.fingerprint for node in first.certification.uncertified}
        outcome = self.paused(self.gate(blessed))
        assert outcome.certification.certified
        assert outcome.certification.complete

    @pytest.mark.parametrize("parent_certified", [True, False])
    def test_a_resumed_run_answers_for_its_parent_too(self, parent_certified):
        from analysis_service.certification import CertifyResult, UncertifiedNode

        pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME)
        parent = (
            CertifyResult(certified=True)
            if parent_certified
            else CertifyResult(
                certified=False,
                uncertified=(UncertifiedNode(node="extract", fingerprint=self.FP),),
            )
        )
        job = asking_job(
            ask_questions=False,
            resumption=Resumption(
                follow_up=False,
                parent_id="job-parent",
                checkpoint=held(),
                certification=parent,
            ),
        )
        # The certifier reads a run's node and fingerprint and nothing else.
        runs = [
            NodeRun.model_construct(node=node, execution_fingerprint=self.FP)
            for node in pipeline.node_sampling
        ]
        runner = AdkPipelineRunner(pipeline, certification=self.gate({self.FP}))
        result = runner._certify(job, runs)
        assert result.certified is parent_certified
        assert result.complete

    def test_a_waiting_job_s_questions_and_answers_follow_the_gate(self):
        from analysis_service.certification import CertifyResult, UncertifiedNode

        client, store = catalog_client()
        client.app.state.certification = self.gate()
        job = waiting(store)
        record = store._records[job]
        record.certification = CertifyResult(
            certified=False,
            uncertified=(UncertifiedNode(node="extract", fingerprint=self.FP),),
        )
        questions = client.get(f"/v1/jobs/{job}/questions", headers=auth())
        answers = client.post(
            f"/v1/jobs/{job}/answers", json={"links": [], "revision": 0}, headers=auth()
        )
        assert questions.status_code == 409
        assert answers.status_code == 409
        assert questions.json()["uncertified_nodes"][0]["node"] == "extract"

    def test_the_resumed_job_carries_its_parent_s_verdict(self):
        from analysis_service.certification import CertifyResult

        client, store = catalog_client()
        job = waiting(store)
        store._records[job].certification = CertifyResult(certified=True)
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"links": [], "revision": 0}, headers=auth()
        )
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.resumption.certification == CertifyResult(certified=True)


@pytest.mark.parametrize(
    "frameworks", [*((name,) for name in PACKAGES), tuple(PACKAGES)], ids=str
)
def test_the_pause_path_runs_the_graphs_two_sweeps_bless(frameworks):
    """A node's fingerprint binds its graph's instruction digest, so the pause
    path is blessed only by the sweeps whose graphs it runs. Architecture.md
    names them: the paused head is the ``heads`` sweep's graph, and the
    resumed run is the ``analysis`` sweep's."""
    from evals.harness.modes import MODE_ENTRIES

    assert MODE_ENTRIES["heads"] == graph.ENTRY_HEAD_ONLY
    assert entry_of(asking_job()) == graph.ENTRY_HEAD_ONLY
    resumed, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME, frameworks=frameworks)
    analysed, _ = scripted_pipeline(
        {}, entry=MODE_ENTRIES["analysis"], frameworks=frameworks
    )
    assert resumed.instruction_sha256 == analysed.instruction_sha256
