"""The open facts a report's findings rest on, asked and answered (#1225).

Held against real archived reports for the ranking, because the shapes that
matter — a finding waiting on several facts, a free-text subject, an open
assertion row — are the ones the critic writes. The answers are held against
the gate and against the real graph from ``prepare``.
"""

from __future__ import annotations

import asyncio

import pytest

from analysis_service import graph
from analysis_service.assertions import (
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    assertion_id,
)
from analysis_service.jobs import (
    Checkpoint,
    JobRecord,
    PipelineCompleted,
    Resumption,
)
from analysis_service.links import apply_answers, with_link_answers
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.questions import (
    FactAnswer,
    answered_model,
    check_fact_answers,
    fact_questions,
)
from analysis_service.sources import Source
from tests import test_open_facts
from tests.factories import (
    DESCRIPTION_TEXT,
    sample_selection,
    scripted_pipeline,
    valid_model,
)

DESCRIPTION = Source.description(DESCRIPTION_TEXT)

#: The archived reports and the fixture the open-facts tests read: one of each.
REPORTS = test_open_facts.REPORTS
report = test_open_facts.report


def ask(report):
    catalog = None if report.assertions is None else report.assertions.catalog
    return fact_questions(report.analyses, report.system_model, catalog)


class TestTheRanking:
    def test_answering_every_question_settles_every_waiting_finding(self, report):
        """A finding waits on the open facts its grounds cite, and its verdict's."""
        waiting = sum(
            1
            for block in report.analyses
            for claim in block.all_claims()
            if claim.unknown_grounds()
            or (claim.verdict.status == "needs-info" and claim.verdict.related_unknowns)
        )
        questions = ask(report)
        assert questions[-1].settled_so_far == waiting if questions else waiting == 0

    def test_each_question_settles_at_least_as_many_as_the_last(self, report):
        settled = [question.settled_so_far for question in ask(report)]
        assert settled == sorted(settled)

    def test_the_first_question_is_the_most_cited_fact(self, report):
        questions = ask(report)
        if questions:
            assert questions[0].cited_by == max(q.cited_by for q in questions)

    def test_every_fact_is_asked_once(self, report):
        keys = [question.key for question in ask(report)]
        assert len(keys) == len(set(keys))


def flow_id():
    return valid_model().data_flows[0].id


class TestTheCriticCannotReorderTheEvidence:
    """The evidence section reads grounds only (``QA-2026-09-26-03-E7``)."""

    def rewritten(self, report):
        """The same drafts under a critic that confirmed every one of them."""
        blocks = []
        for block in report.analyses:
            claims = [
                claim.model_copy(
                    update={
                        "verdict": claim.verdict.model_copy(
                            update={"status": "confirmed", "related_unknowns": []}
                        )
                    }
                )
                for claim in block.claims
            ]
            blocks.append(block.model_copy(update={"claims": claims}))
        return report.model_copy(update={"analyses": blocks})

    def evidence(self, report):
        return [q.key for q in ask(report) if q.basis == "evidence"]

    def test_the_evidence_section_is_the_same_whatever_the_verdicts(self, report):
        assert self.evidence(self.rewritten(report)) == self.evidence(report)

    def test_the_evidence_section_comes_first(self, report):
        bases = [q.basis for q in ask(report)]
        assert bases == sorted(bases, key=lambda basis: basis != "evidence")

    def test_a_free_text_fact_is_only_ever_the_critic_s(self, report):
        assert all(q.basis == "critic" for q in ask(report) if q.kind == "subject")


class TestTheAnswerForms:
    def question_for(self, key):
        """A one-finding report is overkill here; the forms read the key alone."""
        from analysis_service.questions import _choices

        return _choices(key, valid_model(), None)

    def test_a_closed_attribute_offers_its_values_but_unknown(self):
        process = valid_model().processes[0].id
        assert self.question_for((process, "exposure", "", "", "")) == (
            "internet-facing",
            "internal",
        )

    def test_a_zone_is_answered_by_a_boundary(self):
        store = valid_model().data_stores[0].id
        boundaries = tuple(b.id for b in valid_model().trust_boundaries)
        assert self.question_for((store, "trust_zone", "", "", "")) == boundaries

    def test_a_mechanism_is_answered_in_words(self):
        assert self.question_for((flow_id(), "encryption_in_transit", "", "", "")) == ()


class TestTheChecks:
    def test_an_attribute_the_element_lacks_is_refused(self):
        wrong = FactAnswer(key=(flow_id(), "exposure", "", "", ""), value="internal")
        with pytest.raises(ValueError, match="no attribute"):
            check_fact_answers([wrong], valid_model(), None)

    def test_a_value_outside_the_choices_is_refused(self):
        process = valid_model().processes[0].id
        wrong = FactAnswer(key=(process, "exposure", "", "", ""), value="everywhere")
        with pytest.raises(ValueError, match="not one of"):
            check_fact_answers([wrong], valid_model(), None)

    def test_an_open_row_the_catalog_lacks_is_refused(self):
        wrong = FactAnswer(key=("", "", "missing-row", "", ""), value="required")
        with pytest.raises(ValueError, match="no open assertion row"):
            check_fact_answers([wrong], valid_model(), AssertionCatalog())

    def test_a_subject_answer_is_free_text(self):
        fine = FactAnswer(
            key=("", "", "", "whether queries are bound", ""), value="yes"
        )
        check_fact_answers([fine], valid_model(), None)

    def test_an_answer_is_one_line(self):
        with pytest.raises(ValueError):
            FactAnswer(key=("", "", "", "q", ""), value="two\nlines")


def test_an_attribute_answer_is_written_onto_the_model_and_noted():
    answer = FactAnswer(
        key=(flow_id(), "encryption_in_transit", "", "", ""), value="TLS 1.3"
    )
    model = answered_model(valid_model(), [answer])
    flow = model.data_flows[0]
    assert flow.encryption_in_transit == "TLS 1.3"
    assert "TLS 1.3" in flow.notes


def test_an_assertion_answer_replaces_its_open_row_and_passes_the_gate():
    subject = "principal:customer-accounts"
    open_row = Assertion(
        subject=subject,
        predicate="mfa-requirement",
        value=UNKNOWN,
        basis="stated",
        reason="silent",
    )
    catalog = AssertionCatalog(
        subjects=[Subject(id=subject, type="principal", label="customer accounts")],
        entries=[open_row],
    )
    answer = FactAnswer(key=("", "", assertion_id(open_row), "", ""), value="absent")
    written, issues = apply_answers(catalog, valid_model(), [], [answer])
    sources = with_link_answers([DESCRIPTION], [], [answer])
    record = AssertionRecord.over(
        written,
        valid_model(),
        {source.label: source.text for source in sources},
        proposed=1,
    )

    assert issues == []
    assert record.issues == []
    (row,) = record.catalog.entries
    assert (row.value, row.basis) == ("absent", "stated")


class TestTheResumedRunWithoutACatalog:
    """A deployment that builds no catalog can still take fact answers."""

    def test_an_attribute_answer_reaches_the_report(self):
        answer = FactAnswer(
            key=(flow_id(), "encryption_in_transit", "", "", ""), value="TLS 1.3"
        )
        record = JobRecord.create(
            owner_subject="idp|user-1",
            sources=with_link_answers([DESCRIPTION], [], [answer]),
            frameworks=sample_selection(),
            facts=[answer],
            resumption=Resumption(
                parent_id="p",
                checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
            ),
        )
        record.transition("running")
        pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME)

        async def on_node(node: str) -> None:
            return None

        outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))

        assert isinstance(outcome, PipelineCompleted)
        assert outcome.report.system_model.data_flows[0].encryption_in_transit == (
            "TLS 1.3"
        )
        assert outcome.report.assertions is None


class TestTheRoutes:
    """Fact answers need no catalog, so they work on a default deployment."""

    def completed(self, store):
        from tests.factories import sample_report
        from tests.test_api import admit

        record = JobRecord.create(
            owner_subject="alice", sources=[DESCRIPTION], frameworks=sample_selection()
        )
        record.transition("running")
        record.report = sample_report()
        record.transition("completed")
        asyncio.run(admit(store, record))
        return record.id

    def test_a_fact_answer_resumes_a_job_where_no_catalog_is_built(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = self.completed(store)
        answer = {
            "key": [flow_id(), "encryption_in_transit", "", "", ""],
            "value": "TLS 1.3",
        }
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"facts": [answer]}, headers=auth()
        )
        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.facts == [FactAnswer.model_validate(answer)]
        assert child.resumption.checkpoint.assertions is None

    def test_a_fact_the_report_does_not_hold_is_refused(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = self.completed(store)
        wrong = {"key": [flow_id(), "exposure", "", "", ""], "value": "internal"}
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"facts": [wrong]}, headers=auth()
        )
        assert response.status_code == 400

    def test_the_questions_route_lists_the_fact_questions(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = self.completed(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert "fact_questions" in body
        assert isinstance(body["fact_questions"], list)
