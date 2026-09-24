"""The answer-leak guard over the inputs the audit's diagnostic conditions feed.

A guard that reads clean on the corpus says nothing until it is shown to fail,
so each detectable shape has a positive control beside the corpus check.
"""

import json
import shutil

import pytest

from evals.harness.leakage import answer_leaks
from evals.verify_corpus import case_dirs, check_case

CLAIM = {
    "claim": "The order service reaches a PostgreSQL store with no stated query discipline.",
    "tier": "must-find",
    "notes": "tech:database fires on store:orders-db and nothing settles it.",
}


def test_a_clean_input_carries_nothing() -> None:
    assert answer_leaks([CLAIM], {"model.json": '{"notes": "a worker"}'}) == []


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (
            "THE ORDER SERVICE reaches a PostgreSQL store with no\nstated query discipline.",
            "claim",
        ),
        ("tech:database fires on store:orders-db and nothing settles it.", "rationale"),
        ("see V1.2.4 for why", "requirement identifier V1.2.4"),
        ("this one is a Must-Find", "tier label"),
    ],
)
def test_each_detectable_shape_is_found(text: str, kind: str) -> None:
    leaks = answer_leaks([CLAIM], {"facts.json": text})

    assert len(leaks) == 1
    assert leaks[0].startswith("facts.json: carries")
    assert kind in leaks[0]


def test_a_short_rationale_is_too_generic_to_be_an_answer() -> None:
    short = {**CLAIM, "notes": "see above"}

    assert answer_leaks([short], {"model.json": "see above"}) == []


def test_the_expected_tier_is_an_ordinary_word() -> None:
    assert answer_leaks([], {"model.json": "the expected volume"}) == []


def test_no_corpus_case_feeds_an_answer_to_generation() -> None:
    leaks = [
        problem
        for case_dir in case_dirs()
        for problem in check_case(case_dir)
        if ": carries " in problem
    ]

    assert leaks == []


def test_the_corpus_lint_reports_a_planted_leak(tmp_path) -> None:
    """The lint reads the leak through its own path, not only the function."""
    source = case_dirs()[0]
    case = tmp_path / source.name
    shutil.copytree(source, case)
    claims = json.loads(next((case / "claims").glob("*.json")).read_text())
    model = json.loads((case / "model.json").read_text())
    model["processes"][0]["notes"] = claims[0]["claim"]
    (case / "model.json").write_text(json.dumps(model))

    assert any("model.json: carries a reference claim" in p for p in check_case(case))
