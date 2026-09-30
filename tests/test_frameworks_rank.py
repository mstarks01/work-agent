"""Each package ranks its own claims (#1289, design note A)."""

from __future__ import annotations

import pytest

from analysis_service.frameworks import PACKAGES, Band
from analysis_service.frameworks.asvs import ASVS
from analysis_service.frameworks.stride import STRIDE


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_every_package_ranks_its_claims(name):
    assert callable(PACKAGES[name].rank)


@pytest.mark.parametrize(
    ("likelihood", "impact", "band"),
    [("low", "low", Band(0, "low")), ("high", "high", Band(3, "critical"))],
)
def test_stride_ranks_by_the_lane_s_severity_band(likelihood, impact, band):
    from analysis_service.claims import Severity
    from tests.factories import sample_threat

    threat = sample_threat(
        severity=Severity(likelihood=likelihood, impact=impact, justification="a test")
    )
    assert STRIDE.rank(threat) == band


@pytest.mark.parametrize(
    ("claim_id", "band"),
    [
        ("v5.0.0-1.2.5", Band(3, "level 1")),
        ("v5.0.0-1.1.1", Band(2, "level 2")),
        ("v5.0.0-99.99", Band(0, "")),
    ],
)
def test_asvs_ranks_level_1_first(claim_id, band):
    from types import SimpleNamespace

    assert ASVS.rank(SimpleNamespace(id=claim_id)) == band
