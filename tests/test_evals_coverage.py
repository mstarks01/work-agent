"""Pooling per-case coverage rows into the only view worth reading."""

import pytest

from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.report import LaneCoverage
from evals.harness.coverage import aggregate_coverage, coverage_totals


def row(lane_name, framework="stride", **overrides):
    """One report row, tagged with the block it was read off.

    The report's own row carries no framework — it sits inside that framework's
    block — so the sweep pairs them as it collects and the pooled row is keyed
    by ``(framework, lane)``.
    """
    fields = {
        "lane": lane_name,
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
    return (framework, LaneCoverage(**{**fields, **overrides}))


def lane_for(lane_name, lanes, framework="stride"):
    return next(
        lane for lane in lanes if lane.lane == lane_name and lane.framework == framework
    )


@pytest.fixture
def two_cases():
    return [row("spoofing"), row("spoofing", candidates_cited=3, drafts=2)]


def test_a_lane_the_sweep_never_reported_still_gets_a_row(two_cases):
    lanes = aggregate_coverage(two_cases, ["stride"])

    assert [lane.lane for lane in lanes] == list(STRIDE_CATEGORIES)
    assert lane_for("tampering", lanes).cases == 0


def test_pooling_sums_the_counts_rather_than_averaging_the_rates(two_cases):
    """A three-element case must not outweigh a forty-element one."""
    lane = lane_for("spoofing", aggregate_coverage(two_cases, ["stride"]))

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
        aggregate_coverage(
            [row("spoofing", candidates=0, candidates_cited=0)], ["stride"]
        ),
    )

    assert lane.cited_rate("candidates", "candidates_cited") == 0.0


def test_rules_fired_is_the_unambiguous_number(two_cases):
    """Unlike a citation rate, a rule firing nowhere means the rule reads nothing."""
    lanes = aggregate_coverage(
        two_cases + [row("tampering", rules_fired=0, rules=2)], ["stride"]
    )

    assert lane_for("tampering", lanes).rules_fired_rate == 0.0
    assert lane_for("spoofing", lanes).rules_fired_rate == 0.5


def test_totals_fold_every_lane_into_one_line(two_cases):
    totals = coverage_totals(
        aggregate_coverage(two_cases + [row("repudiation")], ["stride"])
    )

    assert totals["cases"] == 2  # the busiest lane, not the sum over lanes
    assert totals["candidates"] == 12
    assert totals["cited_rates"]["unknown_controls_cited"] == 0.0


def test_an_empty_sweep_reports_six_empty_lanes_not_an_empty_table():
    """A silent lane is a finding; the table must not be able to hide one.

    The framework list is what the sweep *built*, so this holds even when no row
    came back at all — which is exactly the run where a reader most needs to see
    that six lanes produced nothing.
    """
    lanes = aggregate_coverage([], ["stride"])

    assert len(lanes) == len(STRIDE_CATEGORIES)
    assert coverage_totals(lanes)["candidates"] == 0


def test_a_framework_the_sweep_never_ran_contributes_no_rows():
    """A STRIDE-only sweep prints six rows, not twenty-three.

    A lane belongs to whichever package declares it, so a package nobody ran has
    no silent lanes to report — and padding the table with them would read as a
    framework that produced nothing when it was never asked to.
    """
    stride_only = aggregate_coverage([row("spoofing")], ["stride"])
    both = aggregate_coverage([row("spoofing")], ["stride", "asvs"])

    assert {lane.framework for lane in stride_only} == {"stride"}
    assert {lane.framework for lane in both} == {"stride", "asvs"}
    assert len(both) > len(stride_only)


def test_two_packages_declaring_one_lane_name_stay_apart():
    """The key is ``(framework, lane)``, never the slug alone.

    ``CONTEXT.md`` is explicit that two packages may declare a lane of the same
    name. STRIDE's categories and ASVS's chapters do not collide today, and
    relying on that is the one-package assumption
    ``docs/agents/framework-parity.md`` exists to catch — so the separation is
    asserted rather than left to the vocabularies happening not to overlap.
    """
    lanes = aggregate_coverage(
        [row("spoofing", drafts=7), row("authentication", "asvs", drafts=3)],
        ["stride", "asvs"],
    )

    assert lane_for("spoofing", lanes).drafts == 7
    assert lane_for("authentication", lanes, "asvs").drafts == 3
