"""``run.py question-prior``: the early-question prior, counted from a sweep."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from analysis_service.claims import UnknownRef
from analysis_service.early_questions import QUESTION_PRIOR, PriorError, load_prior
from evals.harness.artifact import load_artifact
from evals.harness.bundle import reports_dir
from evals.harness.modes import EvalRunError
from evals.harness.question_prior import command_question_prior, count, samples_of
from tests.factories import valid_model
from tests.test_evals_descendants import archive  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)

BASELINE = "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
STORE = "store:orders-db"


def test_a_rate_is_citations_per_element_of_the_type():
    model = valid_model()
    cited = [
        [UnknownRef(element_id=STORE, attribute="encryption_at_rest")],
        [UnknownRef(element_id=STORE, question="audit-evidence")],
        [UnknownRef(subject="whether queries are bound")],
    ]
    rates = count([("c", model, cited), ("c", model, cited[:1])])
    assert rates == {
        "DataStore": {"encryption_at_rest": 1.0, "audit-evidence": 0.5},
    }


def test_a_mixed_entry_and_an_element_the_model_lacks_are_not_counted():
    mixed = UnknownRef(element_id=STORE, attribute="encryption_at_rest", subject="x")
    elsewhere = UnknownRef(element_id="store:nowhere", attribute="encryption_at_rest")
    assert count([("c", valid_model(), [[mixed, elsewhere]])]) == {}


def test_the_command_writes_one_row_and_keeps_the_others(tmp_path):
    out = tmp_path / "prior.json"
    out.write_text(
        json.dumps(
            {
                "asvs": {
                    "record": None,
                    "runs": ["kept"],
                    "revision": "r",
                    "cases": 1,
                    "rates": {"Process": {"authentication": 0.5}},
                },
            }
        )
    )
    args = argparse.Namespace(
        artifact=Path(BASELINE),
        framework="stride",
        replay=[],
        out=out,
        corpus="evals/corpus",
    )
    assert command_question_prior(args) == 0
    table = load_prior(out)
    assert table["asvs"].runs == ("kept",)
    assert table["asvs"].record is None
    assert table["stride"].runs == (BASELINE,)
    record = table["stride"].record
    assert record is not None
    assert record["command"] == "question-prior"
    assert record["commit"] == table["stride"].revision
    assert BASELINE in {entry["path"] for entry in record["inputs"]}
    assert table["stride"].cases == 13
    assert table["stride"].rates["DataFlow"]["authentication"] > 0


def test_a_finished_case_with_no_report_is_named(archive, case):  # noqa: F811
    """The artifact records the case as finished, so its report was lost."""
    (reports_dir(archive) / f"{case.id}.report.json").unlink()

    with pytest.raises(EvalRunError, match=f"{case.id}: .* no report"):
        samples_of(load_artifact(archive), [], "stride", [case.id])


@pytest.mark.parametrize(
    ("table", "named"),
    [
        ({"strid": {"record": None, "rates": {}}}, "'strid'"),
        ({"stride": {"rates": {}}}, "record"),
        ({"stride": {"record": "r", "rates": {}}}, "record"),
        ({"stride": ["rates"]}, "row"),
        (
            {"stride": {"record": None, "rates": {"Datastore": {"technology": 0.1}}}},
            "'Datastore'",
        ),
        (
            {"stride": {"record": None, "rates": {"DataStore": {"technology": "0.1"}}}},
            "DataStore",
        ),
        (
            {"stride": {"record": None, "rates": {"DataStore": {"technology": -0.1}}}},
            "DataStore",
        ),
        ({"stride": {"record": None, "rates": ["DataStore"]}}, "rates"),
    ],
)
def test_a_prior_no_list_can_be_ranked_from_is_refused(tmp_path, table, named):
    """A misspelled type or framework would load and then rank nothing."""
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(PriorError, match=named) as raised:
        load_prior(path)
    assert str(path) in str(raised.value)


#: The rows of the shipped prior that no recorded run counted, with the reason.
UNRECORDED_ROWS = {
    "asvs": (
        "the archived sweep records 03-batch-data-pipeline as finished and holds"
        " no report for it, so the command refuses the sweep and cannot count"
        " the row again"
    ),
}


def test_each_shipped_row_without_a_record_is_declared():
    """A row with no record is declared with the reason, and a declared row
    that gains a record fails, so the list holds only rows still unrecorded."""
    unrecorded = {name for name, row in QUESTION_PRIOR.items() if row.record is None}
    assert unrecorded == UNRECORDED_ROWS.keys()
