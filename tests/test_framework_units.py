"""The units a Control row can name, on each **Framework Package** (ADR 0123).

A package declares ``units``, its closed set of unit rows, and ``unit_of``, the
unit one of its claims sits in. The package gate checks the table when the
package is built, :func:`~analysis_service.frameworks.check_unit` is the one
check of a row's framework and unit, and a replay over the archived reports
holds ``unit_of`` to the set.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from analysis_service.claims import Claim
from analysis_service.frameworks import (
    PACKAGES,
    FrameworkPackage,
    FrameworkPackageError,
    Unit,
    check_unit,
    validate_package,
)
from analysis_service.frameworks.asvs.catalog import CHAPTERS, REQUIREMENTS
from evals.harness.bundle import UnreadableReport, read_report
from tests.factories import PROJECT_ROOT

ARCHIVED_REPORTS = sorted(
    path
    for root in ("evals/baselines", "evals/runs")
    for path in (PROJECT_ROOT / root).rglob("*.report.json")
)


def _members(package: FrameworkPackage) -> dict[str, object]:
    return {field.name: getattr(package, field.name) for field in fields(package)}


@pytest.mark.parametrize("member", ["units", "unit_of"])
def test_a_package_that_lacks_a_unit_member_cannot_be_built(member):
    members = _members(PACKAGES["stride"])
    del members[member]

    with pytest.raises(TypeError, match=member):
        FrameworkPackage(**members)


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_each_package_declares_units_and_unit_of(name):
    package = PACKAGES[name]

    assert package.units
    assert all(isinstance(unit, Unit) for unit in package.units)
    assert callable(package.unit_of)


def test_a_unit_in_an_undeclared_lane_fails_when_the_package_is_built():
    stride = PACKAGES["stride"]
    stray = Unit(id="phishing", grain="lane", lane="phishing", inside=None)

    with pytest.raises(FrameworkPackageError, match="phishing"):
        replace(stride, units=(*stride.units, stray))


def test_an_inside_link_outside_the_set_fails_when_the_package_is_built():
    stride = PACKAGES["stride"]
    orphan = Unit(id="spoofing-x", grain="lane", lane="spoofing", inside="nowhere")

    with pytest.raises(FrameworkPackageError, match="nowhere"):
        replace(stride, units=(*stride.units, orphan))


def test_a_unit_declared_twice_fails_when_the_package_is_built():
    stride = PACKAGES["stride"]

    with pytest.raises(FrameworkPackageError, match="spoofing"):
        replace(stride, units=(*stride.units, stride.units[0]))


def test_a_package_with_an_empty_unit_set_passes_the_gate():
    package = replace(PACKAGES["stride"], units=())

    validate_package(package, PROJECT_ROOT / "frameworks" / "stride")


def test_stride_offers_its_six_lanes_and_nothing_finer():
    stride = PACKAGES["stride"]

    assert [unit.id for unit in stride.units] == list(stride.lanes)
    assert {(unit.grain, unit.inside) for unit in stride.units} == {("lane", None)}


def test_the_asvs_table_holds_each_chapter_section_and_requirement():
    units = {unit.id: unit for unit in PACKAGES["asvs"].units}
    by_grain = {
        grain: {unit.id for unit in units.values() if unit.grain == grain}
        for grain in ("chapter", "section", "requirement")
    }

    assert [len(by_grain[grain]) for grain in by_grain] == [17, 80, 345]
    assert len(units) == 442
    assert by_grain["chapter"] == {chapter.id for chapter in CHAPTERS}
    assert by_grain["section"] == {req.section for req in REQUIREMENTS}
    assert by_grain["requirement"] == {req.id for req in REQUIREMENTS}
    assert units["V2.1.1"].inside == "V2.1"
    assert units["V2.1"].inside == "V2"
    assert units["V2"].inside is None
    assert units["V2.1.1"].lane == "validation-and-business-logic"


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_the_unit_check_accepts_each_unit_of_each_package(name):
    for unit in PACKAGES[name].units:
        check_unit(name, unit.id)


def test_the_unit_check_refuses_a_framework_outside_packages():
    with pytest.raises(ValueError, match="'linddun'"):
        check_unit("linddun", "spoofing")


def test_the_unit_check_refuses_a_unit_outside_the_package_s_set():
    with pytest.raises(ValueError, match="'V99.1'"):
        check_unit("asvs", "V99.1")


def test_the_unit_check_refuses_another_package_s_unit():
    with pytest.raises(ValueError, match="'spoofing'"):
        check_unit("asvs", "spoofing")


# --- The replay over the archived reports ------------------------------------


def _archived_claims() -> list[Claim]:
    """Each claim of each archived report that today's reader accepts.

    A report that today's ``Report`` refuses holds an older schema, and no reader
    migrates an archived report, so the replay leaves it out.
    """
    claims: list[Claim] = []
    for path in ARCHIVED_REPORTS:
        try:
            report = read_report(path)
        except UnreadableReport:
            continue
        claims += [claim for block in report.analyses for claim in block.claims]
    return claims


def _stray_units(
    packages: dict[str, FrameworkPackage], claims: list[Claim]
) -> list[tuple[str, str]]:
    """Each claim ID whose ``unit_of`` names no unit of its package."""
    return [
        (claim.id, unit)
        for claim in claims
        if (unit := packages[claim.framework].unit_of(claim))
        not in {known.id for known in packages[claim.framework].units}
    ]


@pytest.fixture(scope="module")
def archived_claims() -> list[Claim]:
    return _archived_claims()


def test_the_replay_reads_claims_of_each_package(archived_claims):
    assert {claim.framework for claim in archived_claims} == set(PACKAGES)


def test_unit_of_names_a_unit_of_the_set_for_each_archived_claim(archived_claims):
    assert _stray_units(dict(PACKAGES), archived_claims) == []


def test_the_replay_fails_where_unit_of_leaves_the_set(archived_claims):
    stride = PACKAGES["stride"]
    drifted = replace(stride, unit_of=lambda claim: f"{claim.id}-elsewhere")

    strays = _stray_units({**PACKAGES, "stride": drifted}, archived_claims)

    assert strays
    assert {claim_id for claim_id, _ in strays} <= {
        claim.id for claim in archived_claims if claim.framework == "stride"
    }
