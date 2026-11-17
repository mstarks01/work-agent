"""What a missed must-find's lane was given, read from the archive (#2271).

:mod:`evals.harness.losses` charges a miss to ``place`` or ``unled`` when no
draft and no proposal sits at the reference's place. Those two causes read
today's rules on today's corpus model. This module reads what the archived lane
was sent instead, and states how the closest candidate stands to the
reference's place.

For each such row it gives four readings:

* ``delivered``: the strongest :data:`~evals.harness.losses.Relation` between
  the reference's place and a candidate in the lane's captured
  ``<case>.lanes.json``, or ``absent``. ``None`` where the sweep captured no
  lane material.
* ``rebuilt``: the same reading over the candidates that today's rules write
  for the archived model and the archived catalog.
* ``capture``: whether the two candidate sets agree, compared by rule and
  elements. A rule's question text is not compared, because a question edit
  does not move a lead.
* ``archived_lead_rules``: the rebuilt rules with a candidate on the
  reference's elements that the archived block also lists in ``fired_rules``.
  The block lists rule IDs only, so today's rule code says which elements
  each rule fired on.

The relation reads the candidate as the second side of
:func:`~evals.harness.losses._relation`, so ``draft-contains`` is a candidate
whose place contains the reference's, at a coarser grain. A candidate that
shares an element is a structural overlap only. It does not show that the
rule's question is the mechanism the reference describes.

A row of any other cause names a draft or a proposal at the place, so the
lane wrote there and its material reached it. This module does not trace
those rows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from analysis_service.candidates import CandidateSet, generate_candidates
from analysis_service.frameworks import package_for
from analysis_service.graph import unfence
from analysis_service.report import Report
from evals.harness.artifact import EvalArtifact
from evals.harness.bundle import finished_file, read_report, stride_block
from evals.harness.identity import CaseFlows
from evals.harness.losses import RELATIONS, Cause, Relation, _relation
from evals.harness.modes import EvalRunError
from evals.harness.reference import GoldenCase, flows_by_case
from evals.harness.scorecard import (
    ReferenceRow,
    RunIdentity,
    Scorecard,
    charged_misses,
)
from evals.harness.triggers import lead_rules

#: How the closest candidate stands to the reference's place, or ``absent``
#: where no candidate in the lane shares an element with it.
Lead = Relation | Literal["absent"]

#: Whether the rebuilt candidates of a lane are the captured ones.
Capture = Literal["agrees", "differs", "uncaptured"]

#: The causes that name no draft and no proposal at the reference's place,
#: and so the only rows this module traces.
TRACED: frozenset[Cause] = frozenset({"place", "unled"})


@dataclass(frozen=True)
class LaneTrace:
    """One reference's lane, as the archive holds it and as today's rules rebuild it."""

    delivered: Lead | None
    rebuilt: Lead
    capture: Capture
    archived_lead_rules: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "rebuilt": self.rebuilt,
            "capture": self.capture,
            "archived_lead_rules": list(self.archived_lead_rules),
        }


def lead(reference_ids: Sequence[str], fired: CandidateSet, flows: CaseFlows) -> Lead:
    """The strongest relation of a candidate in ``fired`` to the reference's place."""
    found = {
        _relation(reference_ids, candidate.element_ids, flows)
        for candidate in fired.candidates
    }
    return next((relation for relation in RELATIONS if relation in found), "absent")


def captured(material: Mapping[str, Any], lane: str) -> CandidateSet:
    """The STRIDE lane's candidates as the sweep sent them, from ``<case>.lanes.json``."""
    rendered = material["lanes"]["stride"][lane]["candidates"]
    return CandidateSet.model_validate_json(unfence(rendered))


def trace_lane(
    reference_ids: Sequence[str],
    lane: str,
    report: Report,
    sent: CandidateSet | None,
    flows: CaseFlows,
) -> LaneTrace:
    """Trace one reference through its lane: ``sent`` is ``None`` where uncaptured."""
    package = package_for("stride")
    catalog = report.assertions.catalog if report.assertions else None
    rebuilt = generate_candidates(
        report.system_model, package.lanes, package.rules, catalog
    )[lane]
    fired = set(stride_block(report).fired_rules)
    capture: Capture = "uncaptured"
    if sent is not None:
        capture = "agrees" if _keys(sent) == _keys(rebuilt) else "differs"
    return LaneTrace(
        delivered=None if sent is None else lead(reference_ids, sent, flows),
        rebuilt=lead(reference_ids, rebuilt, flows),
        capture=capture,
        archived_lead_rules=tuple(
            rule for rule in lead_rules(rebuilt, reference_ids) if rule in fired
        ),
    )


def _keys(fired: CandidateSet) -> frozenset[tuple[str, tuple[str, ...]]]:
    return frozenset(
        (candidate.rule_id, candidate.element_ids) for candidate in fired.candidates
    )


@dataclass(frozen=True)
class DeliveryRow:
    """One charged miss, and its lane's trace where its cause has one."""

    reference: ReferenceRow
    #: ``None`` for a cause outside :data:`TRACED`: the lane wrote at the
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
    traced_cases = sorted({row.case for row in misses if row.cause in TRACED})
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
            path = _required(finished_file(artifact, case, "lanes"), case)
        except EvalRunError as error:
            uncaptured[case] = str(error)
            continue
        material[case] = json.loads(path.read_text(encoding="utf-8"))
    rows = tuple(
        DeliveryRow(
            reference=row,
            lane=trace_lane(
                row.elements,
                row.lane,
                reports[row.case],
                captured(material[row.case], row.lane)
                if row.case in material
                else None,
                flows[row.case],
            )
            if row.cause in TRACED
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
        f" ({', '.join(sorted(TRACED))}), {len(result.rows) - len(traced)} where"
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
