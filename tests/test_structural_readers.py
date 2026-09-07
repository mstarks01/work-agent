"""The two readers of a report's structural rules, held against each other.

One rule has two readers here, on purpose.
:class:`~analysis_service.report.Report` refuses an unsound payload at
validation, and :func:`~evals.harness.structural.report_issues` refuses one
again over a report that already parsed.
:mod:`evals.harness.structural` states why the second exists: a payload has to
be gradeable before it is known to parse, and a gate that would silently weaken
if somebody relaxed ``Report`` is not a gate.

Two readers of one rule eventually answer it differently, and each one's own
test agrees with it, so neither notices. ``evals/harness/structural.py`` names
the risk where it takes it: its reference check "re-derives
``Report._reference_issues`` one seam later, so it has to read both spellings
exactly as that check does". A sweep does not exercise the difference, because
the scripted critic never emits the second spelling. This file does.

Each row of :data:`FAULTS` breaks one sound report one way and names which
readers refuse the result. ``BOTH`` is a rule the two share, and a change that
moves one of them fails here. ``APP`` is a rule only ``Report`` carries, so the
row is the written list of what the offline gate does not re-assert: it holds
because a report that reached the gate parsed as a ``Report`` already, and it
is what the gate would have to grow if that stopped being true.

Nothing is refused by the gate alone. A gate stricter than the service would
refuse a sweep of reports the service serves happily, and
:func:`test_the_gate_is_never_stricter_than_the_service` is what says so.

**Every fault runs against every package.** The rules under test read
:class:`~analysis_service.report.Claim` and nothing a package declares, so they
are neutral — and a neutral-looking rule that diverged for one package is
exactly what cost the first ASVS corpus sweep eleven correctly shaped claims.
:data:`BLOCKS` is therefore a table keyed by framework, checked against
``PACKAGES``, and a fault a package cannot spell says so as a property of the
package rather than as its name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from analysis_service import FrameworkName
from analysis_service.frameworks import PACKAGES, FrameworkPackage, package_for
from analysis_service.frameworks.asvs import ASVS
from analysis_service.frameworks.asvs.record import AsvsAnalysis, RequirementRuling
from analysis_service.frameworks.stride.record import Threat
from analysis_service.report import (
    FrameworkAnalysis,
    Ground,
    Report,
    ScopeEntry,
    UnknownRef,
    UnresolvedReference,
    UnverifiedGround,
    Verdict,
)
from evals.harness.structural import report_issues
from tests.factories import PROJECT_ROOT, sample_analysis, sample_report

#: Refused by the shipped validator and by the offline gate.
BOTH = "both"
#: Refused by the shipped validator alone.
APP = "app"

Mutation = Callable[[Report], None]


def _stride_block() -> FrameworkAnalysis:
    """One sound STRIDE block, from the shared factory."""
    return sample_analysis()


def _asvs_block() -> FrameworkAnalysis:
    """One sound ASVS block, built the way ``assemble`` builds it.

    ``scope`` and ``summary`` come from the block type's own hooks rather than
    from a literal, so a mutation below breaks a block the graph would produce.
    """
    claim = RequirementRuling(
        id="v5.0.0-6.2.1",
        framework="asvs",
        framework_version=ASVS.version,
        chapter="authentication",
        title="No password length policy is stated",
        description="The requirement applies and the input does not settle it.",
        affected_element_ids=["process:web-app"],
        grounds=[Ground(kind="derived-fact", flow_id="flow:customer-to-web-app:login")],
        verdict=Verdict(status="confirmed"),
    )
    return AsvsAnalysis(
        framework="asvs",
        framework_version=ASVS.version,
        disclaimer=(PROJECT_ROOT / "frameworks" / "asvs" / "disclaimer.md")
        .read_text(encoding="utf-8")
        .strip(),
        level=1,
        claims=[claim],
        scope=AsvsAnalysis.scope_entries(
            lanes=ASVS.lanes, claims=[claim], options={"level": 1}
        ),
        summary=AsvsAnalysis.summarize([claim], []),
    )


#: One sound block per package, so every fault below runs against every
#: package's own record. Keyed rather than branched, and
#: :func:`test_every_package_has_a_block` holds it against ``PACKAGES``.
BLOCKS: Mapping[FrameworkName, Callable[[], FrameworkAnalysis]] = {
    "stride": _stride_block,
    "asvs": _asvs_block,
}


def sound_report(framework: FrameworkName) -> Report:
    """A report carrying one package's sound block, and nothing else."""
    return sample_report(analyses=[BLOCKS[framework]()])


def app_refuses(report: Report) -> bool:
    """Whether the shipped validator refuses this report's own payload.

    Through :meth:`Report.model_validate` rather than through the private check
    methods, because that is the reader the service actually runs: a payload
    reaches a caller by parsing, and the checks are inside the parse.
    """
    try:
        Report.model_validate(report.model_dump(mode="json"))
    except (ValidationError, ValueError):
        return True
    return False


def gate_refuses(report: Report) -> bool:
    """Whether the offline gate refuses this report."""
    return bool(report_issues(report))


def _dangling_reference(report: Report) -> None:
    report.analyses[0].claims[0].affected_element_ids = ["process:ghost"]


def _dangling_unknown(report: Report) -> None:
    claim = report.analyses[0].claims[0]
    claim.verdict.status = "needs-info"
    claim.verdict.related_unknowns = [
        UnknownRef(element_id="process:ghost", attribute="authentication")
    ]


def _repeated_claim_id(report: Report) -> None:
    block = report.analyses[0]
    block.claims.append(block.claims[0])


def _severity_off_the_matrix(report: Report) -> None:
    """The band is a judgement of a package whose record grades harm.

    The same reason the offline gate reads the field off the package rather
    than off the annotation: a block's claims validate as their own package's
    record, and only a record that grades harm declares ``severity``.
    """
    claim = report.analyses[0].claims[0]
    assert isinstance(claim, Threat)
    claim.severity.level = "low"


def _summary_miscounts(report: Report) -> None:
    report.analyses[0].summary.claim_count = 99


def _elements_analyzed_wrong(report: Report) -> None:
    report.elements_analyzed = 99


def _crossings_not_derived(report: Report) -> None:
    report.boundary_crossings = []


def _rejected_verdict_in_claims(report: Report) -> None:
    report.analyses[0].claims[0].verdict.status = "rejected"


def _mark_naming_no_claim(report: Report) -> None:
    report.analyses[0].unverified_grounds.append(
        UnverifiedGround(claim_id="no-such-claim", index=0, reason="not in the source")
    )


def _reference_mark_naming_no_claim(report: Report) -> None:
    report.analyses[0].unresolved_references.append(
        UnresolvedReference(claim_id="no-such-claim", element_id="process:ghost")
    )


def _claim_its_own_scope_rules_out(report: Report) -> None:
    block = report.analyses[0]
    block.scope.append(
        ScopeEntry(
            unit=block.claims[0].id,
            state="not-applicable",
            reason="this unit does not apply to a system of this shape",
            needs="",
        )
    )


def _no_block_for_the_selected_framework(report: Report) -> None:
    report.analyses.clear()


def _grades_harm(package: FrameworkPackage) -> bool:
    return package.carries_severity()


def _every_package(package: FrameworkPackage) -> bool:
    return True


@dataclass(frozen=True)
class Fault:
    """One way a report can be unsound, and the readers that refuse it."""

    #: Breaks a sound report in place.
    mutate: Mutation
    #: :data:`BOTH` or :data:`APP`.
    refused_by: str
    #: Which packages can spell this fault, as a property of the package rather
    #: than as its name. A fault every record can carry takes every package.
    spelled_by: Callable[[FrameworkPackage], bool] = field(default=_every_package)


#: Every fault either reader names.
#:
#: A row is the whole of what this file asserts, so adding a check to either
#: reader means adding a row or moving one. That is the point: the two cannot
#: drift apart without a line here going stale.
FAULTS: Mapping[str, Fault] = {
    # The shared rules. Each of these is written twice, once in
    # `analysis_service.report` and once in `evals.harness.structural`.
    "a claim names an element the model does not hold": Fault(
        _dangling_reference, BOTH
    ),
    "a needs-info verdict hangs on an element the model does not hold": Fault(
        _dangling_unknown, BOTH
    ),
    "two claims in one block share an ID": Fault(_repeated_claim_id, BOTH),
    "a severity band contradicts the matrix": Fault(
        _severity_off_the_matrix, BOTH, spelled_by=_grades_harm
    ),
    "a summary does not count its own block": Fault(_summary_miscounts, BOTH),
    "elements_analyzed is not the embedded model's count": Fault(
        _elements_analyzed_wrong, BOTH
    ),
    "boundary_crossings are not the model's own crossings": Fault(
        _crossings_not_derived, BOTH
    ),
    # The rules only the shipped validator carries. Each holds offline because
    # a report the gate reads parsed as a `Report` first.
    "a rejected verdict sits in claims": Fault(_rejected_verdict_in_claims, APP),
    "a mark names a claim the block does not carry": Fault(_mark_naming_no_claim, APP),
    "a reference mark names a claim the block does not carry": Fault(
        _reference_mark_naming_no_claim, APP
    ),
    "a claim is about a unit its own scope list rules out": Fault(
        _claim_its_own_scope_rules_out, APP
    ),
    "the blocks do not answer the frameworks the job selected": Fault(
        _no_block_for_the_selected_framework, APP
    ),
}

#: Every (package, fault) pair the package can spell.
CASES = [
    (framework, fault)
    for framework in sorted(PACKAGES)
    for fault in sorted(FAULTS)
    if FAULTS[fault].spelled_by(package_for(framework))
]


def test_every_package_has_a_block():
    """A package with no sound block runs none of the faults below.

    The table check every framework-keyed table in this repo carries: a package
    that joins ``PACKAGES`` and not this table would be silently unexercised
    rather than loudly missing.
    """
    assert set(BLOCKS) == set(PACKAGES), (
        "BLOCKS and PACKAGES disagree: add a sound block for"
        f" {sorted(set(PACKAGES) - set(BLOCKS))}, and drop"
        f" {sorted(set(BLOCKS) - set(PACKAGES))}."
    )


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_a_sound_report_passes_both_readers(framework):
    """The baseline every row below is a mutation of."""
    report = sound_report(framework)

    assert not app_refuses(report)
    assert not gate_refuses(report)


@pytest.mark.parametrize(("framework", "fault"), CASES)
def test_the_readers_agree_about_this_fault(framework, fault):
    """One broken report, both readers, and the row that says what to expect."""
    row = FAULTS[fault]
    report = sound_report(framework)
    row.mutate(report)

    assert app_refuses(report), (
        f"the shipped validator accepts a {framework} report where {fault}."
        " Every row here is a rule Report enforces; if this one moved, move"
        " the row."
    )
    assert gate_refuses(report) == (row.refused_by == BOTH), (
        f"over {framework}, the offline gate and the shipped validator now"
        f" disagree about whether {fault} is a fault. Give the gate the rule,"
        f" or move this row between {BOTH!r} and {APP!r} and say in the"
        " docstring why the gate stays silent."
    )


@pytest.mark.parametrize(("framework", "fault"), CASES)
def test_the_gate_is_never_stricter_than_the_service(framework, fault):
    """Nothing the gate refuses may be a report the service serves happily.

    The other direction of the same rule. A gate that refuses what ``Report``
    accepts fails a sweep over reports that are sound, and the sweep is what
    every published quality number rests on.
    """
    report = sound_report(framework)
    FAULTS[fault].mutate(report)

    if gate_refuses(report):
        assert app_refuses(report), (
            f"the offline gate refuses a {framework} report where {fault}, and"
            " the service serves it. A gate stricter than the thing it grades"
            " throws away sound runs."
        )
