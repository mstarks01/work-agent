"""``run.py question-prior``: the early-question prior, counted from a sweep."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis_service.claims import UnknownRef
from analysis_service.early_questions import load_prior
from evals.harness.question_prior import command_question_prior, count
from tests.factories import valid_model

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
    assert table["stride"].runs == (BASELINE,)
    assert table["stride"].cases == 13
    assert table["stride"].rates["DataFlow"]["authentication"] > 0
