"""Pooling per-case coverage rows into the only view worth reading."""

import pytest

from evals.harness.coverage import aggregate_coverage, coverage_totals
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES
from stride_service.report import LaneCoverage


def row(category, **overrides):
    fields = {
        "category": category,
        "drafts": 1,
        "rules": 2,
        "rules_fired": 1,
        "candidates": 4,
        "candidates_cited": 1,
        "elements": 10,
        "elements_cited": 3,
        "boundary_crossings": 2,
        "boundary_crossings_cited": 1,
        "unknown_controls": 5,
        "unknown_controls_cited": 0,
    }
    return LaneCoverage(**{**fields, **overrides})


def lane_for(category, lanes):
    return next(lane for lane in lanes if lane.category == category)


@pytest.fixture
def two_cases():
    return [row("spoofing"), row("spoofing", candidates_cited=3, drafts=2)]


def test_a_lane_the_sweep_never_reported_still_gets_a_row(two_cases):
    lanes = aggregate_coverage(two_cases)

    assert [lane.category for lane in lanes] == list(STRIDE_CATEGORIES)
    assert lane_for("tampering", lanes).cases == 0


def test_pooling_sums_the_counts_rather_than_averaging_the_rates(two_cases):
    """A three-element case must not outweigh a forty-element one."""
    lane = lane_for("spoofing", aggregate_coverage(two_cases))

    assert lane.cases == 2
    assert lane.drafts == 3
    assert lane.totals["candidates"] == 8
    assert lane.totals["candidates_cited"] == 4
    assert lane.cited_rate("candidates", "candidates_cited") == 0.5


def test_a_lane_offered_nothing_rates_zero_rather_than_dividing_by_it():
    # Cited moves with offered: a lane handed no candidates cannot have cited
    # one, and LaneCoverage refuses the pair that says otherwise.
    lane = lane_for(
        "spoofing",
        aggregate_coverage([row("spoofing", candidates=0, candidates_cited=0)]),
    )

    assert lane.cited_rate("candidates", "candidates_cited") == 0.0


def test_rules_fired_is_the_unambiguous_number(two_cases):
    """Unlike a citation rate, a rule firing nowhere means the rule reads nothing."""
    lanes = aggregate_coverage(two_cases + [row("tampering", rules_fired=0, rules=2)])

    assert lane_for("tampering", lanes).rules_fired_rate == 0.0
    assert lane_for("spoofing", lanes).rules_fired_rate == 0.5


def test_totals_fold_every_lane_into_one_line(two_cases):
    totals = coverage_totals(aggregate_coverage(two_cases + [row("repudiation")]))

    assert totals["cases"] == 2  # the busiest lane, not the sum over lanes
    assert totals["candidates"] == 12
    assert totals["cited_rates"]["unknown_controls_cited"] == 0.0


def test_an_empty_sweep_reports_six_empty_lanes_not_an_empty_table():
    lanes = aggregate_coverage([])

    assert len(lanes) == len(STRIDE_CATEGORIES)
    assert coverage_totals(lanes)["candidates"] == 0
