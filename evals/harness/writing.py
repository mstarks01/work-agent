"""What reviewers said about how a finding is *written*, kept off the analysis.

A style down-vote moves no analysis number, by construction.
:data:`~evals.harness.ledger.STYLE_REASONS` leaves the finding in the reference
pool, and ``Vote.counts_against_analysis`` is false for every one of them. That
split is the whole control for a reviewer's taste, and it stops "I dislike this
sentence" reading as "the tool found a threat that is not there".

The split only works if the objection lands somewhere. This module is where it
lands. The ledger kept the reason, and until this instrument no command read it,
so a reviewer's answer about the prose changed nothing anybody could see.

It is neutral over frameworks. How a claim reads is not a property of a claim
set, so this walks whatever blocks the sweep produced and reports one row per
``(case, framework)`` pair. A package that composes an identity and a package
that carries a catalog identifier are graded on their prose the same way, and a
package nobody has written yet needs no entry here.

The denominator is what a person answered, never what the sweep produced. A
sweep of four hundred findings over a ledger holding nine votes has an objection
rate over those nine. Dividing by the produced count would report a number that
falls whenever the tool writes more, which is the opposite of what this
measures. ``answered`` rides beside the rate for the same reason ``unvoted``
rides beside ``rejected_rate``: a rate over three answers is a number to act on
only after reading the three.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from analysis_service.report import Claim, FrameworkName, Report
from evals.harness.fingerprint import identifier_of, key_claim, lane_field
from evals.harness.identity import FlowMap
from evals.harness.ledger import STYLE_REASONS, Ledger
from evals.harness.reference import GoldenCase


@dataclass(frozen=True)
class CaseWriting:
    """One case's prose, as its reviewers rated it, for one framework."""

    case_id: str
    framework: FrameworkName
    produced: int
    answered: int
    objections: int
    by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def objection_rate(self) -> float:
        """Of the findings a person answered, the share drawing a style reason."""
        return self.objections / self.answered if self.answered else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "framework": self.framework,
            "produced": self.produced,
            "answered": self.answered,
            "objections": self.objections,
            "objection_rate": round(self.objection_rate, 3),
            "by_reason": dict(sorted(self.by_reason.items())),
        }


def measure_case(
    case_id: str,
    framework: FrameworkName,
    claims: Iterable[Claim],
    flows: FlowMap,
    votes: Ledger,
) -> CaseWriting:
    """Every claim of one block, looked up in the ledger by its fingerprint.

    Keyed exactly as :mod:`evals.harness.queue` keys it, from the same
    components under the same per-framework version, so a vote cast from the
    queue reaches the finding counted here.

    A claim carries its lane in its own package's field —
    :func:`~evals.harness.fingerprint.lane_field` names which — because the
    lane is half of the key and a package with no entry there would key every
    finding of one place alike.
    """
    lane_of = lane_field(framework)
    live = votes.current_by_finding()
    produced = 0
    answered = 0
    objections = 0
    reasons: Counter[str] = Counter()

    for claim in claims:
        produced += 1
        value, _ = key_claim(
            framework,
            getattr(claim, lane_of),
            tuple(claim.affected_element_ids),
            flows,
            verb=claim.verb,
            identifier=identifier_of(framework, claim.id),
        )
        current = live.get(value, ())
        if not current:
            continue
        answered += 1
        objected = [
            vote.reason
            for vote in current
            if vote.verdict == "down" and vote.reason in STYLE_REASONS
        ]
        if objected:
            objections += 1
            reasons.update(objected)

    return CaseWriting(
        case_id=case_id,
        framework=framework,
        produced=produced,
        answered=answered,
        objections=objections,
        by_reason=dict(reasons),
    )


def measure(
    cases: Sequence[GoldenCase],
    reports: Mapping[str, Report],
    votes: Ledger,
) -> tuple[CaseWriting, ...]:
    """One row per ``(case, framework)`` the sweep produced a block for.

    A case that did not finish carries no report and no row. An empty ledger
    gives every row an ``answered`` of zero, which is a sweep nobody has
    reviewed rather than a sweep whose prose nobody objected to.
    """
    rows = []
    for case in cases:
        report = reports.get(case.id)
        if report is None:
            continue
        flows = {
            flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
        }
        rows += [
            measure_case(case.id, block.framework, block.claims, flows, votes)
            for block in report.analyses
        ]
    return tuple(rows)


def published(blocks: Mapping[str, Any], framework: str) -> float | None:
    """This framework's objection count, for the comparison table."""
    aggregate = blocks.get("writing_aggregate")
    if not isinstance(aggregate, Mapping):
        return None
    totals = aggregate.get("by_framework", {}).get(framework)
    return float(totals["objections"]) if isinstance(totals, Mapping) else None


def aggregate(rows: Sequence[CaseWriting]) -> dict[str, Any]:
    """The sweep's totals, per framework and over all of them."""
    by_framework: dict[str, dict[str, int]] = {}
    for row in rows:
        totals = by_framework.setdefault(
            row.framework, {"produced": 0, "answered": 0, "objections": 0}
        )
        totals["produced"] += row.produced
        totals["answered"] += row.answered
        totals["objections"] += row.objections

    answered = sum(row.answered for row in rows)
    objections = sum(row.objections for row in rows)
    reasons: Counter[str] = Counter()
    for row in rows:
        reasons.update(row.by_reason)
    return {
        "answered": answered,
        "objections": objections,
        "objection_rate": round(objections / answered, 3) if answered else 0.0,
        "by_framework": dict(sorted(by_framework.items())),
        "by_reason": dict(sorted(reasons.items())),
    }


def render(rows: Sequence[CaseWriting]) -> None:
    """Print the rate beside the count it was computed over, never alone."""
    print("\nWriting (style objections, per case and framework)")
    if not rows:
        print("  no block produced a claim")
        return

    for row in sorted(rows, key=lambda row: (row.case_id, row.framework)):
        print(
            f"  {row.case_id:<34} {row.framework:<8}"
            f" {row.objections}/{row.answered} answered"
            f" ({row.objection_rate:.2f}), {row.produced} produced"
        )

    totals = aggregate(rows)
    if not totals["answered"]:
        print("  nobody has voted on this sweep's findings yet")
        return
    print(
        f"  all: {totals['objections']}/{totals['answered']} answered"
        f" ({totals['objection_rate']:.2f})"
    )
    for reason, count in totals["by_reason"].items():
        print(f"    {reason:<22} {count}")


def artifact(rows: Sequence[CaseWriting]) -> dict[str, Any]:
    return {
        "writing": [row.to_json() for row in rows],
        "writing_aggregate": aggregate(rows),
    }
