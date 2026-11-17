"""What a missed must-find's lane was given, read from the archive (#2271).

:mod:`evals.harness.losses` charges a miss to ``place`` or ``unled`` when no
draft and no proposal sits at the reference's place, and
:func:`~evals.harness.triggers.archived_lead_rules` decides which of the two.
This module also reads what the archived lane was sent, and states how the
closest candidate stands to the reference's place.

For each such row it gives four readings:

* ``delivered``: the strongest :data:`~evals.harness.losses.Relation` between
  the reference's place and a candidate in the lane's captured
  ``<case>.lanes.json``, or ``absent``. ``None`` where the sweep captured no
  lane material.
* ``rebuilt``: the same reading over the candidates that today's rules write
  for the archived model and the archived catalog, with the case's options
  from today's corpus. The archive records no options, so a ``differs`` can
  come from an options edit as well as from a rule edit.
* ``capture``: whether the two candidate sets hold the same
  :func:`~analysis_service.candidates.leads`. A rule's question text is not
  part of a lead, because a question edit does not move one.
* ``archived_lead_rules``: the rules that
  :func:`~evals.harness.triggers.archived_lead_rules` reads as leading to the
  reference, the rule that decides ``place`` against ``unled``. A rule leads
  when one of its candidates names an element ID the reference names. The
  relation resolves a cited flow to its two
  endpoints first, so a row can read ``overlap`` with no lead rule: a
  candidate on a flow's endpoint shares a place with a reference that cites
  the flow, and names none of its element IDs.

The relation reads the candidate as the second side of
:func:`~evals.harness.losses.relation`, so ``draft-contains`` is a candidate
whose place contains the reference's, at a coarser grain. A candidate that
shares an element is a structural overlap only. It does not show that the
rule's question is the mechanism the reference describes.

A row of any other cause names a draft or a proposal at the place, so the
lane wrote there and its material reached it. This module does not trace
those rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from analysis_service.candidates import CandidateSet, leads
from analysis_service.graph import unfence
from analysis_service.report import Report
from evals.harness.artifact import EvalArtifact
from evals.harness.bundle import finished_file, read_report, stride_block
from evals.harness.identity import CaseFlows
from evals.harness.lane_replay import lanes_file, material_at
from evals.harness.losses import RELATIONS, SILENT, Relation, relation
from evals.harness.modes import EvalRunError
from evals.harness.reference import GoldenCase, flows_by_case
from evals.harness.scorecard import (
    ReferenceRow,
    RunIdentity,
    Scorecard,
    charged_misses,
)
from evals.harness.triggers import archived_lead_rules, rebuilt_candidates

#: How the closest candidate stands to the reference's place, or ``absent``
#: where no candidate in the lane shares an element with it.
PlaceRelation = Relation | Literal["absent"]

#: Whether the rebuilt candidates of a lane are the captured ones.
Capture = Literal["agrees", "differs", "uncaptured"]


@dataclass(frozen=True)
class LaneTrace:
    """One reference's lane, as the archive holds it and as today's rules rebuild it."""

    delivered: PlaceRelation | None
    rebuilt: PlaceRelation
    capture: Capture
    archived_lead_rules: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "rebuilt": self.rebuilt,
            "capture": self.capture,
            "archived_lead_rules": list(self.archived_lead_rules),
        }


def closest_relation(
    reference_ids: Sequence[str], candidates: CandidateSet, flows: CaseFlows
) -> PlaceRelation:
    """The strongest relation of one of ``candidates`` to the reference's place."""
    found = {
        relation(reference_ids, candidate.element_ids, flows)
        for candidate in candidates.candidates
    }
    return next((strength for strength in RELATIONS if strength in found), "absent")


def sent_candidates(material: Mapping[str, Any], lane: str) -> CandidateSet:
    """The STRIDE lane's candidates as the sweep sent them, from ``<case>.lanes.json``."""
    rendered = material["lanes"]["stride"][lane]["candidates"]
    return CandidateSet.model_validate_json(unfence(rendered))


def trace_lane(
    reference_ids: Sequence[str],
    lane: str,
    report: Report,
    sent: CandidateSet | None,
    flows: CaseFlows,
    options: Mapping[str, object],
) -> LaneTrace:
    """Trace one reference through its lane: ``sent`` is ``None`` where uncaptured.

    The rebuild is :func:`~evals.harness.triggers.rebuilt_candidates` over the
    archived model and catalog with the case's ``options``.
    """
    rebuilt = rebuilt_candidates("stride", report, options)[lane]
    capture: Capture = "uncaptured"
    if sent is not None:
        same = leads({lane: sent}) == leads({lane: rebuilt})
        capture = "agrees" if same else "differs"
    return LaneTrace(
        delivered=None
        if sent is None
        else closest_relation(reference_ids, sent, flows),
        rebuilt=closest_relation(reference_ids, rebuilt, flows),
        capture=capture,
        archived_lead_rules=archived_lead_rules(
            rebuilt, reference_ids, stride_block(report).fired_rules
        ),
    )


@dataclass(frozen=True)
class DeliveryRow:
    """One charged miss, and its lane's trace where its cause has one."""

    reference: ReferenceRow
    #: ``None`` for a cause outside :data:`SILENT`: the lane wrote at the
    #: place, so its material reached it.
    lane: LaneTrace | None

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference.to_json(),
            "lane": None if self.lane is None else self.lane.to_json(),
        }


@dataclass(frozen=True)
class Delivery:
    """One run's charged misses, each traced through its lane where it can be."""

    run: RunIdentity
    rows: tuple[DeliveryRow, ...]
    #: The must-finds the scorecard reads as ``re-keyed``. They are outside
    #: :func:`~evals.harness.scorecard.charged_misses`, and are counted here so
    #: the traced population is read beside them.
    re_keyed: int
    #: Each case whose lane material the sweep did not capture, and why.
    uncaptured: Mapping[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "run": self.run.to_json(),
            "re_keyed": self.re_keyed,
            "uncaptured": dict(self.uncaptured),
            "rows": [row.to_json() for row in self.rows],
        }


def trace(
    card: Scorecard, artifact: EvalArtifact, cases: Sequence[GoldenCase]
) -> Delivery:
    """Trace each charged miss of ``card`` through the lane material ``artifact`` holds."""
    by_id = {case.id: case for case in cases}
    misses = charged_misses(card.rows)
    traced_cases = sorted({row.case for row in misses if row.cause in SILENT})
    reports = {
        case: read_report(_required(finished_file(artifact, case, "report"), case))
        for case in traced_cases
    }
    flows = flows_by_case(
        [by_id[case] for case in traced_cases],
        {case: report.system_model for case, report in reports.items()},
    )
    material: dict[str, Mapping[str, Any]] = {}
    uncaptured: dict[str, str] = {}
    for case in traced_cases:
        try:
            material[case] = material_at(lanes_file(artifact, case))
        except EvalRunError as error:
            uncaptured[case] = str(error)
    rows = tuple(
        DeliveryRow(
            reference=row,
            lane=trace_lane(
                row.elements,
                row.lane,
                reports[row.case],
                sent_candidates(material[row.case], row.lane)
                if row.case in material
                else None,
                flows[row.case],
                by_id[row.case].declaration("stride").options,
            )
            if row.cause in SILENT
            else None,
        )
        for row in misses
    )
    return Delivery(
        run=card.run,
        rows=rows,
        re_keyed=sum(row.compatibility == "re-keyed" for row in card.rows),
        uncaptured=uncaptured,
    )


def _required(path: Path | None, case: str) -> Path:
    """``path``, which a charged miss's case always has: a failed case has no row."""
    if path is None:
        raise EvalRunError(f"{case}: the sweep records the case as failed")
    return path


def render(result: Delivery) -> None:
    """Print each charged miss, its lane's trace, and the populations beside them."""
    traced = [row for row in result.rows if row.lane is not None]
    print(
        f"{len(result.rows)} charged miss(es): {len(traced)} traced"
        f" ({', '.join(sorted(SILENT))}), {len(result.rows) - len(traced)} where"
        f" the lane wrote at the place; {result.re_keyed} re-keyed must-find(s)"
        " outside the population"
    )
    for case, reason in sorted(result.uncaptured.items()):
        print(f"  lane material uncaptured: {case}: {reason}")
    for row in traced:
        reference, lane = row.reference, row.lane
        assert lane is not None
        print(
            f"  {reference.case} #{reference.reference_index} {reference.lane}"
            f" {reference.cause} [{reference.compatibility}]: delivered"
            f" {lane.delivered or '-'}, rebuilt {lane.rebuilt}, capture"
            f" {lane.capture}; lead rules today"
            f" {', '.join(reference.lead_rules) or '-'}, archived"
            f" {', '.join(lane.archived_lead_rules) or '-'}"
        )
