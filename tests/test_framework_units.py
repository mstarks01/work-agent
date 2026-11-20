"""The units a Control row can name, on each **Framework Package** (ADR 0123).

A package declares ``units``, its closed set of unit rows, and
``control_unit_of``, the unit one of its claims sits in. The package gate checks
the table when the package is built,
:func:`~analysis_service.frameworks.check_unit` checks a row's framework and
unit, and a replay over the Baseline reports and each ASVS reference record of
the corpus holds ``control_unit_of`` to the set through that check.
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
    package_for,
    validate_package,
)
from analysis_service.frameworks.asvs.catalog import CHAPTERS, REQUIREMENTS
from analysis_service.frameworks.asvs.record import DraftRequirementRuling
from evals.harness.artifact import CORPUS_DIR
from evals.harness.bundle import read_report
from evals.harness.reference import load_corpus
from tests.factories import PROJECT_ROOT, carrying

BASELINE_REPORTS = sorted((PROJECT_ROOT / "evals/baselines").rglob("*.report.json"))
_KEY_OF = {req.id: req.key for req in REQUIREMENTS}


def _members(package: FrameworkPackage) -> dict[str, object]:
    return {field.name: getattr(package, field.name) for field in fields(package)}


@pytest.mark.parametrize("member", ["units", "control_unit_of"])
def test_a_package_that_lacks_a_unit_member_cannot_be_built(member):
    members = _members(PACKAGES["stride"])
    del members[member]

    with pytest.raises(TypeError, match=member):
        FrameworkPackage(**members)


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_each_package_declares_units_and_control_unit_of(name):
    package = PACKAGES[name]

    assert package.units
    assert all(isinstance(unit, Unit) for unit in package.units)
    assert callable(package.control_unit_of)


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


# --- The unit replay ---------------------------------------------------------


def _replayed_claims() -> list[Claim]:
    """The claims of each Baseline report, and a claim for each ASVS reference.

    The Baselines hold the committed claims of a run. They are STRIDE only, so
    each ASVS reference record of the corpus becomes a claim whose ID the
    package's own ID rule composes.
    """
    claims: list[Claim] = []
    for path in BASELINE_REPORTS:
        report = read_report(path)
        claims += [claim for block in report.analyses for claim in block.claims]
    asvs = PACKAGES["asvs"]
    claims += [
        DraftRequirementRuling.model_construct(
            id=asvs.compose_id(ref.chapter, _KEY_OF[ref.requirement]),
            framework="asvs",
        )
        for case in load_corpus(CORPUS_DIR)
        for ref in case.requirements_for("asvs").values()
    ]
    return claims


def _stray_units(claims: list[Claim]) -> list[tuple[str, str]]:
    """Each claim whose ``control_unit_of`` gives a unit that ``check_unit`` refuses."""
    strays = []
    for claim in claims:
        unit = package_for(claim.framework).control_unit_of(claim)
        try:
            check_unit(claim.framework, unit)
        except ValueError:
            strays.append((claim.id, unit))
    return strays


@pytest.fixture(scope="module")
def replayed_claims() -> list[Claim]:
    return _replayed_claims()


def test_the_replay_reads_claims_of_each_package(replayed_claims):
    assert {claim.framework for claim in replayed_claims} == set(PACKAGES)


def test_control_unit_of_names_a_unit_of_the_set_for_each_replayed_claim(
    replayed_claims,
):
    assert _stray_units(replayed_claims) == []


def test_the_replay_fails_where_control_unit_of_leaves_the_set(
    monkeypatch, replayed_claims
):
    drifted = replace(
        PACKAGES["stride"], control_unit_of=lambda claim: f"{claim.id}-elsewhere"
    )
    carrying(monkeypatch, drifted)

    strays = _stray_units(replayed_claims)

    assert strays
    assert {claim_id for claim_id, _ in strays} <= {
        claim.id for claim in replayed_claims if claim.framework == "stride"
    }


def test_a_requirement_sits_in_itself_its_section_and_its_chapter():
    requirement = next(req for req in REQUIREMENTS if req.id == "V2.1.1")

    holding = PACKAGES["asvs"].units_holding("V2.1.1")

    assert holding == {"V2.1.1", requirement.section, "V2"}


def test_a_stride_lane_sits_in_itself_alone():
    assert PACKAGES["stride"].units_holding("spoofing") == {"spoofing"}


def test_a_unit_the_package_does_not_carry_sits_in_nothing():
    assert PACKAGES["asvs"].units_holding("V99.1.1") == frozenset()
