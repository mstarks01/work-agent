"""A final report takes corrections and runs nothing (#1289, ADR 0054)."""

from __future__ import annotations

import asyncio

from analysis_service.answer_sets import AnswerSet
from analysis_service.claims import Ground, UnknownRef
from analysis_service.fact_answers import FactAnswer, fact_line
from analysis_service.jobs import Checkpoint, JobRecord, Resumption
from analysis_service.report_conditions import corrected_findings
from analysis_service.sources import ANSWERS_LABEL, Source
from tests.factories import (
    DESCRIPTION_TEXT,
    asking_threat,
    sample_report,
    sample_selection,
    sample_threat,
    valid_model,
)
from tests.test_api import auth, make_client

ASKED = UnknownRef(
    element_id=valid_model().data_flows[1].id, attribute="encryption_in_transit"
)
ANSWER = FactAnswer(key=ASKED.key, value="TLS 1.3")


def finished(store, *, final: bool = True) -> str:
    """A completed job whose run read ``ANSWER``; its report is final where asked."""
    from tests.test_api import admit

    report = sample_report([asking_threat(ASKED)])
    record = JobRecord.create(
        owner_subject="alice",
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        answers=AnswerSet(facts=(ANSWER,)),
        resumption=Resumption(
            parent_id="parent",
            checkpoint=Checkpoint(system_model=report.system_model, assertions=None),
            follow_up=final,
        ),
    )
    record.transition("running")
    record.report = report
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id


def correct(client, job, *facts):
    return client.post(
        f"/v1/jobs/{job}/corrections",
        json={"facts": [fact.model_dump(mode="json") for fact in facts]},
        headers=auth(),
    )


class TestTheRoute:
    def test_a_final_report_takes_a_correction_and_names_what_it_reaches(self):
        client, store = make_client()
        job = finished(store)
        back = FactAnswer(key=ASKED.key, value="unknown")

        response = correct(client, job, back)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["corrections"] == [back.model_dump(mode="json")]
        assert body["corrected_findings"] == ["stride/S-01"]
        record = asyncio.run(store.get(job))
        assert record.corrections == [back]
        assert record.answers.facts == (ANSWER,), (
            "the answers the run read stay as they were"
        )
        questions = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert questions["corrections"] == body["corrections"]
        assert questions["corrected_findings"] == ["stride/S-01"]

    def test_a_report_that_is_not_final_refuses_it(self):
        client, store = make_client()
        job = finished(store, final=False)
        response = correct(client, job, FactAnswer(key=ASKED.key, value="none"))
        assert response.status_code == 400
        assert "only a final report" in response.json()["detail"]

    def test_a_fact_the_run_did_not_read_is_refused(self):
        client, store = make_client()
        job = finished(store)
        other = (valid_model().data_flows[1].id, "authentication", "", "", "", "")
        response = correct(client, job, FactAnswer(key=other, value="none"))
        assert response.status_code == 400
        assert "only an answer the report read" in response.json()["detail"]
        assert "('" not in response.json()["detail"], "a label, never a raw key"

    def test_a_correction_that_changes_nothing_is_refused(self):
        client, store = make_client()
        job = finished(store)
        response = correct(client, job, ANSWER)
        assert response.status_code == 400
        assert "change no answer" in response.json()["detail"]


def test_v1_reads_a_follow_up_s_report_as_final():
    """The envelope left out the resumption, so /v1 read every report as not
    final and admitted a second follow-up (#1336)."""
    client, store = make_client()
    job = finished(store)
    body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
    again = client.post(
        f"/v1/jobs/{job}/answers",
        json={
            "facts": [FactAnswer(key=ASKED.key, value="none").model_dump(mode="json")]
        },
        headers=auth(),
    )
    assert body["final"] is True
    assert again.status_code == 400
    assert "this report is final" in again.json()["detail"]


class TestWhichFindingsACorrectionReaches:
    """The one reader of "which findings rest on a corrected fact"."""

    BACK = FactAnswer(key=ASKED.key, value="unknown")

    def reached(self, threat):
        report = sample_report([threat])
        return corrected_findings(report.analyses, [ANSWER], [self.BACK])

    def test_a_finding_that_quotes_the_answer_s_line(self):
        quote = Ground(kind="quote", text=fact_line(ANSWER), source_label=ANSWERS_LABEL)
        assert self.reached(sample_threat(grounds=[quote])) == ("stride/S-01",)

    def test_a_finding_that_grounds_on_the_attribute(self):
        ground = Ground(
            kind="unknown-attribute",
            element_id=ASKED.element_id,
            attribute=ASKED.attribute,
        )
        assert self.reached(sample_threat(grounds=[ground])) == ("stride/S-01",)

    def test_a_finding_that_rests_on_neither_is_not_reached(self):
        assert self.reached(sample_threat()) == ()

    def test_a_quote_from_another_source_is_not_reached(self):
        quote = Ground(kind="quote", text=fact_line(ANSWER), source_label="description")
        assert self.reached(sample_threat(grounds=[quote])) == ()
