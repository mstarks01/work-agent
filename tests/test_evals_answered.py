"""The ``answered`` mode: ``analysis`` mode with a case's signed answers (#1225).

The claims worth testing are that the answers reach the lanes the way they
reach a resumed production job, that the report analyses the model with the
answers written in, and that a case without signed answers, or with an answer
its model cannot take, is refused before anything is spent.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service.fact_answers import FactAnswer
from analysis_service.graph import (
    ENTRY_PREPARE,
    STATE_FRAMEWORK_OPTIONS,
    analyze_node_name,
)
from analysis_service.jobs import Checkpoint, JobRecord, Resumption
from analysis_service.links import ANSWERS_LABEL
from analysis_service.pipeline import _seeded_state
from evals.harness import modes, run
from tests.factories import sample_selection
from tests.test_evals_modes import build, case  # noqa: F401  (fixture)

STORE = "store:orders-db"
AT_REST = FactAnswer(key=(STORE, "encryption_at_rest", "", "", "", ""), value="AES-256")
KIND = FactAnswer(
    key=(STORE, "", "", "", "audit-evidence", ""), value="an append-only audit table"
)


def test_the_lanes_read_the_answers_and_the_model_holds_them(case):  # noqa: F811
    models: dict = {}
    pipeline = build(case, ENTRY_PREPARE, models)

    report = asyncio.run(modes.run_answered(case, pipeline, [AT_REST, KIND])).report

    store = report.system_model.get(STORE)
    assert store is not None and store.encryption_at_rest == "AES-256"
    assert ANSWERS_LABEL in {source.label for source in report.input.sources}
    spoofing = models[analyze_node_name("stride", "spoofing")].seen[0]
    assert "an append-only audit table" in spoofing


def test_the_mode_seeds_the_run_a_resumed_job_seeds(case, monkeypatch):  # noqa: F811
    """The mode seeded the model and not the answers, so the catalog pass
    never wrote an answer over its open row nor removed a row the answer
    superseded. The two seeds are held against each other."""
    seeded: dict = {}

    async def captured(pipeline, sources, extra_state):
        seeded.update(extra_state)
        raise modes.EvalRunError("captured")

    monkeypatch.setattr(modes, "run_graph", captured)
    with pytest.raises(modes.EvalRunError, match="captured"):
        asyncio.run(modes.run_answered(case, object(), [AT_REST, KIND]))
    job = JobRecord.create(
        owner_subject="idp|user-1",
        sources=list(case.sources),
        frameworks=sample_selection(),
        facts=[AT_REST, KIND],
        resumption=Resumption(
            follow_up=False,
            parent_id="job-parent",
            checkpoint=Checkpoint(system_model=case.model, assertions=None),
        ),
    )
    del seeded[STATE_FRAMEWORK_OPTIONS]
    assert seeded == _seeded_state(job)


def signed_file(tmp_path, case_id, answers, signed_by="mstarks01"):
    (tmp_path / f"{case_id}.json").write_text(
        json.dumps(
            {
                "signed_by": signed_by,
                "answers": [answer.model_dump(mode="json") for answer in answers],
            }
        )
    )


class TestTheSignedAnswers:
    def test_signed_answers_load(self, tmp_path, case):  # noqa: F811
        signed_file(tmp_path, case.id, [AT_REST])
        loaded = run.signed_answers([case], tmp_path)
        assert loaded[case.id].answers == (AT_REST,)
        assert loaded[case.id].signed_by == "mstarks01"

    def test_an_unsigned_file_is_refused(self, tmp_path, case):  # noqa: F811
        signed_file(tmp_path, case.id, [AT_REST], signed_by=None)
        with pytest.raises(modes.EvalRunError, match="no signed answers"):
            run.signed_answers([case], tmp_path)

    def test_a_case_with_no_file_is_refused(self, tmp_path, case):  # noqa: F811
        with pytest.raises(modes.EvalRunError, match=case.id):
            run.signed_answers([case], tmp_path)

    def test_an_answer_the_model_cannot_take_is_refused(self, tmp_path, case):  # noqa: F811
        wrong = FactAnswer(
            key=("store:nowhere", "", "", "", "audit-evidence", ""), value="x"
        )
        signed_file(tmp_path, case.id, [wrong])
        with pytest.raises(modes.EvalRunError, match="no question"):
            run.signed_answers([case], tmp_path)
