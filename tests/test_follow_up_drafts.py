"""A report's follow-up saves its answers in batches, then runs once (#1542 F3, ADR 0070).

At 2fae7d3, 201 schema-valid conditional threats with distinct unknown
subjects made the follow-up ask 201 questions, and the answer request admitted
at most 200 facts. A follow-up could not save a batch without running, and its
first run spent the one follow-up. These tests build that report and answer
it through the ``/v1`` routes.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from analysis_service.answer_round import ANSWER_LIMITS
from analysis_service.api import AnswersSubmission
from analysis_service.claims import UnknownRef, Verdict
from analysis_service.fact_answers import MAX_FACT_ANSWERS, FactAnswer
from analysis_service.jobs import JobRecord
from analysis_service.links import MAX_LINK_ANSWERS, LinkAnswer
from analysis_service.sources import SourceLimits
from tests.factories import sample_report, sample_selection, sample_threat
from tests.test_answer_admission import TestTheRoundRevision
from tests.test_api import admit, auth, make_client
from tests.test_questions import DESCRIPTION

ROOM = SourceLimits(max_total_bytes=1_000_000, max_sources=5)


def _subject(n: int) -> list[str]:
    return ["", "", "", f"Distinct fact {n}", "", ""]


def _finished(store, count=MAX_FACT_ANSWERS + 1, earlier=()):
    """A finished job whose report waits on ``count`` distinct open facts, the
    issue's own construction, having read the ``earlier`` answers."""
    claims = [
        sample_threat(
            threat_id=f"S-{n:03}",
            verdict=Verdict(
                status="needs-info",
                reason="Missing fact",
                related_unknowns=[UnknownRef(subject=f"Distinct fact {n}")],
            ),
        )
        for n in range(count)
    ]
    record = JobRecord.create(
        owner_subject="alice",
        sources=[DESCRIPTION],
        frameworks=sample_selection(),
        facts=list(earlier),
    )
    record.transition("running")
    record.report = sample_report(claims)
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id


def _client():
    return make_client(limits=ROOM)


def _questions(client, job):
    return client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()


def _post(client, job, body):
    return TestTheRoundRevision().post(client, job, **body)


def _facts(numbers, value="known"):
    return [{"key": _subject(n), "value": f"{value} {n}"} for n in numbers]


def _started(store, job):
    """The job this one's answers started, or ``None``."""
    return asyncio.run(store.resumed_by(job, "alice"))


class TestTheLimitsAgree:
    def test_the_report_asks_more_than_one_request_carries_and_says_so(self):
        client, store = _client()
        body = _questions(client, _finished(store))
        assert len(body["fact_questions"]) == MAX_FACT_ANSWERS + 1
        assert body["answer_limits"] == dict(ANSWER_LIMITS)
        assert body["answer_limits"]["facts"] == MAX_FACT_ANSWERS

    @pytest.mark.parametrize(
        ("count", "status"),
        [
            (MAX_FACT_ANSWERS - 1, 200),
            (MAX_FACT_ANSWERS, 200),
            (MAX_FACT_ANSWERS + 1, 422),
        ],
    )
    def test_one_request_carries_at_most_the_limit(self, count, status):
        client, store = _client()
        job = _finished(store)
        response = _post(
            client, job, {"facts": _facts(range(count)), "save": True, "revision": 0}
        )
        assert response.status_code == status, response.text

    @pytest.mark.parametrize("edits", [1, 2])
    def test_an_earlier_answer_edit_counts_in_its_batch(self, edits):
        """Two earlier answers stay asked as edits; with 199 new answers, one
        edit fits a request and two do not."""
        earlier = [
            FactAnswer(key=tuple(_subject(n)), value="unknown")
            for n in range(MAX_FACT_ANSWERS + 1, MAX_FACT_ANSWERS + 3)
        ]
        client, store = _client()
        job = _finished(store, count=MAX_FACT_ANSWERS + 3, earlier=earlier)
        new = _facts(range(MAX_FACT_ANSWERS - 1))
        changed = [
            {"key": list(answer.key), "value": "now known"}
            for answer in earlier[:edits]
        ]
        response = _post(
            client, job, {"facts": new + changed, "save": True, "revision": 0}
        )
        assert response.status_code == (200 if edits == 1 else 422), response.text

    @pytest.mark.parametrize(
        ("count", "fits"),
        [
            (MAX_LINK_ANSWERS - 1, True),
            (MAX_LINK_ANSWERS, True),
            (MAX_LINK_ANSWERS + 1, False),
        ],
    )
    def test_one_request_carries_at_most_the_link_limit(self, count, fits):
        links = [
            LinkAnswer(principal=f"principal {n}", element="none").model_dump()
            for n in range(count)
        ]
        if fits:
            AnswersSubmission.model_validate({"links": links})
        else:
            with pytest.raises(ValidationError, match="at most 50"):
                AnswersSubmission.model_validate({"links": links})

    def test_a_job_holds_at_most_its_held_ceiling(self, monkeypatch):
        """The ceiling on what a job holds is checked on the composed answers,
        across every batch."""
        from analysis_service import answer_round

        monkeypatch.setattr(answer_round, "MAX_HELD_FACTS", 150)
        client, store = _client()
        job = _finished(store)
        first = _post(
            client, job, {"facts": _facts(range(100)), "save": True, "revision": 0}
        )
        assert first.status_code == 200, first.text
        second = _post(
            client,
            job,
            {"facts": _facts(range(100, 151)), "save": True, "revision": 1},
        )
        assert second.status_code == 400
        assert "holds at most 150 fact answers" in second.json()["detail"]


class TestTheDraft:
    def test_a_follow_up_larger_than_a_request_saves_in_batches_and_runs_once(self):
        client, store = _client()
        job = _finished(store)
        first = _post(
            client,
            job,
            {"facts": _facts(range(MAX_FACT_ANSWERS)), "save": True, "revision": 0},
        )
        assert first.json() == {"job_id": job, "saved": True, "revision": 1}
        second = _post(
            client,
            job,
            {
                "facts": _facts([MAX_FACT_ANSWERS]),
                "save": True,
                "revision": 1,
            },
        )
        assert second.json()["revision"] == 2
        held = _questions(client, job)
        assert len(held["draft_facts"]) == MAX_FACT_ANSWERS + 1
        assert held["resumed_by"] is None and not held["final"]
        assert _started(store, job) is None, "a save runs nothing"

        started = _post(client, job, {"links": [], "facts": [], "revision": 2})
        assert started.status_code == 201, started.text
        child = asyncio.run(store.get(started.json()["job_id"]))
        assert len(child.facts) == MAX_FACT_ANSWERS + 1
        assert child.resumption is not None and child.resumption.follow_up

    def test_a_later_batch_changes_an_answer_an_earlier_batch_saved(self):
        client, store = _client()
        job = _finished(store, count=3)
        _post(client, job, {"facts": _facts([0, 1]), "save": True, "revision": 0})
        _post(
            client, job, {"facts": _facts([1], "changed"), "save": True, "revision": 1}
        )
        draft = {
            tuple(fact["key"]): fact["value"]
            for fact in _questions(client, job)["draft_facts"]
        }
        assert draft == {tuple(_subject(0)): "known 0", tuple(_subject(1)): "changed 1"}

    def test_a_save_that_read_an_earlier_revision_is_refused(self):
        """Of two tabs that read one revision, only the first save lands."""
        client, store = _client()
        job = _finished(store, count=3)
        landed = _post(client, job, {"facts": _facts([0]), "save": True, "revision": 0})
        late = _post(client, job, {"facts": _facts([1]), "save": True, "revision": 0})
        assert landed.status_code == 200 and late.status_code == 409
        draft = _questions(client, job)["draft_facts"]
        assert [fact["key"] for fact in draft] == [_subject(0)]

    def test_a_save_names_its_revision(self):
        client, store = _client()
        job = _finished(store, count=3)
        response = _post(client, job, {"facts": _facts([0]), "save": True})
        assert response.status_code == 400

    def test_a_second_run_is_refused_and_a_failed_run_can_run_again(self):
        """The run is the one follow-up: a second, from another tab or a retry,
        is refused while the first holds the report. Where the first fails, the
        draft is still there to run again."""
        client, store = _client()
        job = _finished(store, count=3)
        _post(client, job, {"facts": _facts([0, 1]), "save": True, "revision": 0})
        first = _post(client, job, {"links": [], "facts": [], "revision": 1})
        again = _post(client, job, {"links": [], "facts": [], "revision": 1})
        assert first.status_code == 201 and again.status_code == 409

        spent = asyncio.run(store.get(first.json()["job_id"]))
        asyncio.run(store.save(spent.model_copy(update={"status": "failed"})))
        held = _questions(client, job)
        assert held["resumed_by"] is None and len(held["draft_facts"]) == 2
        retried = _post(client, job, {"links": [], "facts": [], "revision": 1})
        assert retried.status_code == 201, retried.text

    def test_a_run_adds_this_submission_over_the_draft(self):
        client, store = _client()
        job = _finished(store, count=3)
        _post(client, job, {"facts": _facts([0, 1]), "save": True, "revision": 0})
        started = _post(client, job, {"facts": _facts([1, 2], "final"), "revision": 1})
        child = asyncio.run(store.get(started.json()["job_id"]))
        assert {fact.key[3]: fact.value for fact in child.facts} == {
            "Distinct fact 0": "known 0",
            "Distinct fact 1": "final 1",
            "Distinct fact 2": "final 2",
        }

    def test_a_draft_of_nothing_new_is_kept_but_its_run_is_refused(self):
        """A save is not a run: "I don't know" for everything saves, and the
        run that would read nothing new is refused without being spent."""
        client, store = _client()
        job = _finished(store, count=2)
        unknown = [{"key": _subject(0), "value": "unknown"}]
        saved = _post(client, job, {"facts": unknown, "save": True, "revision": 0})
        assert saved.status_code == 200, saved.text
        refused = _post(client, job, {"links": [], "facts": [], "revision": 1})
        assert refused.status_code == 400
        assert "still available" in refused.json()["detail"]
        assert _started(store, job) is None


class TestASaveDuringARunIsNotLost:
    """A run reads the draft, and a save that lands before its admission is
    not dropped in silence (#1542 Package C, checkpoint review c1)."""

    def test_a_save_between_the_read_and_the_admission_refuses_the_run(self):
        client, store = _client()
        job = _finished(store, count=3)
        late = FactAnswer(key=tuple(_subject(2)), value="late")
        reserve = store.reserve

        async def racing(record, **bounds):
            # A second tab's save lands after the run composed its answers.
            assert await store.save_draft(job, "alice", [], [late], 0)
            return await reserve(record, **bounds)

        store.reserve = racing
        response = _post(client, job, {"facts": _facts([0]), "revision": 0})
        assert response.status_code == 409, response.text
        assert _started(store, job) is None
        assert late in asyncio.run(store.owned(job, "alice")).draft_facts
