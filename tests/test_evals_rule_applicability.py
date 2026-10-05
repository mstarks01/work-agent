"""The applicability benchmark: labelled fixtures and the question simulation."""

from __future__ import annotations

import json
from typing import Any

import pytest

from analysis_service.answer_round import EARLY_RULES
from analysis_service.capabilities import CAPABILITIES, lineage
from evals.harness.rule_applicability import (
    FIXTURES_DIR,
    Fixture,
    load_fixtures,
    report,
    score_fixture,
    simulate_questions,
)

FIXTURES = load_fixtures()

#: The test classes #1291 asks the benchmark to hold, each by one fixture.
CLASSES = {
    "explicit-positive",
    "explicit-negative",
    "silence",
    "conflicting",
    "answer-resolution",
    "role-specific",
    "nested",
    "unconditional",
    "conformance-bait",
}


def _fixture(**fields: Any) -> Fixture:
    base: dict[str, Any] = {
        "name": "x",
        "test_class": "silence",
        "drafted_by": "agent",
        "signed_by": None,
        "framework": "asvs",
        "options": {"level": 1},
        "statements": (),
        "answers": {},
        "expected": {},
        "truth": {},
    }
    return Fixture(**{**base, **fields})


def test_every_test_class_has_a_fixture():
    assert {fixture.test_class for fixture in FIXTURES} == CLASSES


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.name)
def test_the_rule_meets_each_fixture(fixture):
    score = score_fixture(fixture)
    assert score.correct == score.adjudicated
    assert score.false_exclusions == ()
    assert score.covered and score.repeatable


def test_a_false_exclusion_is_counted():
    from analysis_service.system_model import CapabilityStatement

    fixture = _fixture(
        statements=(
            CapabilityStatement(
                capability="oauth",
                state="absent",
                source_excerpt="x",
                source_label="Fixture source",
            ),
        ),
        expected={"V10.4.1": "applicable", "V10.4.2": "unknown"},
    )
    assert score_fixture(fixture).false_exclusions == ("V10.4.1", "V10.4.2")


def test_a_label_above_the_level_is_refused():
    with pytest.raises(ValueError, match="do not select"):
        score_fixture(_fixture(expected={"V17.1.1": "unknown"}))


def test_no_fixture_here_is_signed_by_its_drafter():
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["signed_by"] != raw["drafted_by"], path.name


def test_the_simulation_asks_a_part_only_under_a_yes():
    run = simulate_questions(
        _fixture(options={"level": 2}, truth={"oauth": "absent", "webrtc": "absent"})
    )
    # Every OAuth and WebRTC part waits behind a "no" and is not asked.
    assert run.avoided
    assert {"oauth", "webrtc"}.isdisjoint(
        ancestor for key in run.keys for ancestor in lineage(key)
    )
    assert run.conformance == 0


def test_the_simulation_asks_in_the_rounds_a_pause_serves():
    """#1291: a later round is read off the earlier answers, within the limit."""
    truth = dict.fromkeys(CAPABILITIES, "present") | {
        "oauth-client": "absent",
        "oauth-authorization-server": "absent",
        "webrtc": "absent",
    }
    run = simulate_questions(_fixture(options={"level": 3}, truth=truth))
    assert run.rounds > 1
    assert run.asked <= EARLY_RULES["capability"].limit


def test_the_simulation_settles_what_the_truth_answers():
    (fixture,) = [fixture for fixture in FIXTURES if fixture.truth]
    run = simulate_questions(fixture)
    assert run.unknown_after < run.unknown_before
    assert sum(run.settled) == run.unknown_before - run.unknown_after
    last = max(asked for asked, count in enumerate(run.settled, start=1) if count)
    assert run.to_reach(1.0) == last


def test_the_report_pools_signed_and_unsigned_apart():
    figures = report(FIXTURES)
    assert figures["pooled"]["signed"]["adjudicated"] == sum(
        score["adjudicated"] for score in figures["fixtures"] if score["signed"]
    )
