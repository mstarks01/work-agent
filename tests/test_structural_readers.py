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

Each row of :data:`MUTATIONS` breaks one sound report one way and names which
readers refuse the result. ``BOTH`` is a rule the two share, and a change that
moves one of them fails here. ``APP`` is a rule only ``Report`` carries, so the
row is the written list of what the offline gate does not re-assert: it holds
because a report that reached the gate parsed as a ``Report`` already, and it
is what the gate would have to grow if that stopped being true.

Nothing is refused by the gate alone. A gate stricter than the service would
refuse a sweep of reports the service serves happily, and
:func:`test_the_gate_is_never_stricter_than_the_service` is what says so.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from analysis_service.report import (
    Report,
    ScopeEntry,
    UnknownRef,
    UnresolvedReference,
    UnverifiedGround,
)
from evals.harness.structural import report_issues
from tests.factories import sample_report

#: Refused by the shipped validator and by the offline gate.
BOTH = "both"
#: Refused by the shipped validator alone.
APP = "app"

Mutation = Callable[[Report], None]


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
    report.analyses[0].claims[0].severity.level = "low"


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
        UnverifiedGround(claim_id="S-99", index=0, reason="not in the source")
    )


def _reference_mark_naming_no_claim(report: Report) -> None:
    report.analyses[0].unresolved_references.append(
        UnresolvedReference(claim_id="S-99", element_id="process:ghost")
    )


def _claim_its_own_scope_rules_out(report: Report) -> None:
    block = report.analyses[0]
    block.scope.append(
        ScopeEntry(
            unit=block.claims[0].id,
            state="not-applicable",
            reason="the lane does not apply to this system",
            needs="",
        )
    )


def _no_block_for_the_selected_framework(report: Report) -> None:
    report.analyses.clear()


#: One way a report can be unsound, and the readers that refuse it.
#:
#: A row is the whole of what this file asserts, so adding a check to either
#: reader means adding a row or moving one. That is the point: the two cannot
#: drift apart without a line here going stale.
MUTATIONS: dict[str, tuple[Mutation, str]] = {
    # The shared rules. Each of these is written twice, once in
    # `analysis_service.report` and once in `evals.harness.structural`.
    "a claim names an element the model does not hold": (_dangling_reference, BOTH),
    "a needs-info verdict hangs on an element the model does not hold": (
        _dangling_unknown,
        BOTH,
    ),
    "two claims in one block share an ID": (_repeated_claim_id, BOTH),
    "a severity band contradicts the matrix": (_severity_off_the_matrix, BOTH),
    "a summary does not count its own block": (_summary_miscounts, BOTH),
    "elements_analyzed is not the embedded model's count": (
        _elements_analyzed_wrong,
        BOTH,
    ),
    "boundary_crossings are not the model's own crossings": (
        _crossings_not_derived,
        BOTH,
    ),
    # The rules only the shipped validator carries. Each holds offline because
    # a report the gate reads parsed as a `Report` first.
    "a rejected verdict sits in claims": (_rejected_verdict_in_claims, APP),
    "a mark names a claim the block does not carry": (_mark_naming_no_claim, APP),
    "a reference mark names a claim the block does not carry": (
        _reference_mark_naming_no_claim,
        APP,
    ),
    "a claim is about a unit its own scope list rules out": (
        _claim_its_own_scope_rules_out,
        APP,
    ),
    "the blocks do not answer the frameworks the job selected": (
        _no_block_for_the_selected_framework,
        APP,
    ),
}


def test_a_sound_report_passes_both_readers():
    """The baseline every row below is a mutation of."""
    report = sample_report()

    assert not app_refuses(report)
    assert not gate_refuses(report)


@pytest.mark.parametrize("fault", sorted(MUTATIONS))
def test_the_readers_agree_about_this_fault(fault):
    """One broken report, both readers, and the row that says what to expect."""
    mutate, refused_by = MUTATIONS[fault]
    report = sample_report()
    mutate(report)

    assert app_refuses(report), (
        f"the shipped validator accepts a report where {fault}. Every row here"
        " is a rule Report enforces; if this one moved, move the row."
    )
    assert gate_refuses(report) == (refused_by == BOTH), (
        f"the offline gate and the shipped validator now disagree about"
        f" whether {fault} is a fault. Give the gate the rule, or move this"
        f" row between {BOTH!r} and {APP!r} and say in the docstring why the"
        " gate stays silent."
    )


@pytest.mark.parametrize("fault", sorted(MUTATIONS))
def test_the_gate_is_never_stricter_than_the_service(fault):
    """Nothing the gate refuses may be a report the service serves happily.

    The other direction of the same rule. A gate that refuses what ``Report``
    accepts fails a sweep over reports that are sound, and the sweep is what
    every published quality number rests on.
    """
    mutate, _ = MUTATIONS[fault]
    report = sample_report()
    mutate(report)

    if gate_refuses(report):
        assert app_refuses(report), (
            f"the offline gate refuses a report where {fault}, and the service"
            " serves it. A gate stricter than the thing it grades throws away"
            " sound runs."
        )
