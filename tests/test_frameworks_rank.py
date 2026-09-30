"""Each package ranks its own claims (#1289, design note A), on one shared scale."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from analysis_service.bands import UNRANKED, Band, band_of
from analysis_service.claims import Rating, Severity
from analysis_service.frameworks import PACKAGES, _declaration_issues
from analysis_service.frameworks.asvs import ASVS
from analysis_service.frameworks.stride import STRIDE
from tests.factories import sample_threat


def _threat(likelihood: Rating, impact: Rating):
    return sample_threat(
        severity=Severity(likelihood=likelihood, impact=impact, justification="a test")
    )


#: Claims of each package, one for each band it can rank into where it can.
#: Keyed by package, so a package added to ``PACKAGES`` fails here until it
#: says how its claims rank.
SAMPLE_CLAIMS = {
    "stride": [
        _threat(likelihood, impact)
        for likelihood in ("low", "medium", "high")
        for impact in ("low", "medium", "high")
    ],
    "asvs": [
        SimpleNamespace(id=claim_id)
        for claim_id in ("v5.0.0-1.2.5", "v5.0.0-1.1.1", "v5.0.0-99.99")
    ],
}


def test_every_package_has_sample_claims():
    assert set(SAMPLE_CLAIMS) == set(PACKAGES)


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_a_package_ranks_into_its_declared_bands_from_the_top(name):
    """A mixed job compares two packages' orders, so each one is the band's
    position from its package's top, and every top band is 0 (ADR 0055)."""
    package = PACKAGES[name]
    for claim in SAMPLE_CLAIMS[name]:
        band = package.rank(claim)
        assert band == band_of(package.bands, band.label)
    assert band_of(package.bands, package.bands[0]).order == 0


def test_unranked_is_below_every_band():
    lowest = min(-len(package.bands) for package in PACKAGES.values())
    assert UNRANKED.order < lowest


@pytest.mark.parametrize(
    ("bands", "issue"),
    [((), "bands is empty"), (("high", "high"), "names a band twice")],
)
def test_a_package_without_a_usable_band_list_is_refused(bands, issue):
    assert any(
        issue in found for found in _declaration_issues(replace(STRIDE, bands=bands))
    )


@pytest.mark.parametrize(
    ("likelihood", "impact", "band"),
    [("low", "low", Band(-3, "low")), ("high", "high", Band(0, "critical"))],
)
def test_stride_ranks_by_the_lane_s_severity_band(likelihood, impact, band):
    assert STRIDE.rank(_threat(likelihood, impact)) == band


@pytest.mark.parametrize(
    ("claim_id", "band"),
    [
        ("v5.0.0-1.2.5", Band(0, "level 1")),
        ("v5.0.0-1.1.1", Band(-1, "level 2")),
        ("v5.0.0-99.99", Band(-3, "")),
    ],
)
def test_asvs_ranks_level_1_first(claim_id, band):
    assert ASVS.rank(SimpleNamespace(id=claim_id)) == band


def test_the_highest_bands_of_stride_and_asvs_rank_equal():
    """In a job that selects both, a critical threat and a level 1
    requirement are the most important findings of their packages."""
    critical = STRIDE.rank(_threat("high", "high"))
    level_1 = ASVS.rank(SimpleNamespace(id="v5.0.0-1.2.5"))
    assert critical.order == level_1.order
