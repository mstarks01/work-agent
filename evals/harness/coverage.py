"""What the category agents were offered across a sweep, and what they cited.

:class:`~stride_service.report.LaneCoverage` is computed per job and rides on
the report, one row per lane — which for STRIDE is one per category. One job's
rows are close to
unreadable — an agent that examined a flow and correctly found nothing cites
nothing, and no observable separates it from one that never looked. The
aggregate is where the field was always meant to be read: a lane citing two of
forty structural leads across twelve cases is a coverage signal in a way that
one agent's zero on one case never is.

Pooled rather than averaged over cases, for the reason
:func:`~evals.harness.critic_yield.aggregate_yield` gives: these are small
per-case counts, and a mean of per-case rates lets a three-element case
outweigh a forty-element one.

Credential-free — it folds numbers the service already computed in code — so
it costs a sweep nothing and is **non-gating**. A citation rate is not a
quality bar: the rate a healthy lane runs at is a thing the baseline sweeps
have to establish before anyone writes a threshold over it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from evals.harness.scorer import ratio
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES, StrideCategory
from stride_service.report import LaneCoverage as ReportLaneCoverage

# The offered/cited pairs, in the order they read on a row. Named once because
# the fold, the rates and the printed table would otherwise each carry their
# own copy of the pairing and could disagree about which half is which.
CITED_PAIRS: tuple[tuple[str, str], ...] = (
    ("candidates", "candidates_cited"),
    ("elements", "elements_cited"),
    ("boundary_crossings", "boundary_crossings_cited"),
    ("unknown_controls", "unknown_controls_cited"),
)


@dataclass(frozen=True)
class LaneCoverage:
    """One category's coverage pooled over every case in the sweep."""

    category: StrideCategory
    cases: int
    drafts: int
    rules: int
    rules_fired: int
    totals: dict[str, int]

    @property
    def rules_fired_rate(self) -> float:
        """Of this lane's rule *evaluations*, the share that produced a candidate.

        Pooling makes the denominator the lane's rules times the cases, so this
        is one rule against one case rather than one rule against the corpus —
        a rule that fires on half the corpus and one that fires always on half
        the lane's rules read the same here. The number that is unambiguous is
        0.0: a lane whose rules never fire anywhere reads a shape this corpus
        does not contain, or reads nothing at all.
        """
        return ratio(self.rules_fired, self.rules)

    def cited_rate(self, offered_field: str, cited_field: str) -> float:
        return ratio(self.totals[cited_field], self.totals[offered_field])

    def to_json(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "cases": self.cases,
            "drafts": self.drafts,
            "rules": self.rules,
            "rules_fired": self.rules_fired,
            "rules_fired_rate": round(self.rules_fired_rate, 3),
            **self.totals,
            "cited_rates": {
                cited: round(self.cited_rate(offered, cited), 3)
                for offered, cited in CITED_PAIRS
            },
        }


def _as_category(lane: str) -> StrideCategory:
    """A report row's lane slug as the STRIDE category it names.

    A report row is keyed by a plain string, because a lane belongs to whichever
    package declares it. This fold is STRIDE's, where the six lane slugs *are*
    the category names — so the narrowing is a real lookup against that package's
    own list rather than a cast, and a lane no STRIDE category matches is a
    caller feeding this fold another framework's rows.
    """
    for category in STRIDE_CATEGORIES:
        if category == lane:
            return category
    raise ValueError(f"{lane!r} is not a STRIDE lane; these rows are not STRIDE's")


def aggregate_coverage(rows: Iterable[ReportLaneCoverage]) -> list[LaneCoverage]:
    """Pool per-case coverage rows into one row per category.

    Always all six lanes, in the report's own category order, including any the
    sweep never produced a row for: a lane missing from the table would read as
    a lane with nothing to cite, and the two are opposite findings.

    STRIDE's lane slugs *are* its category names, so a report row's ``lane``
    keys this table directly. That identity is this package's and not a fact
    about the report, which is why the fold names the framework it is folding.
    """
    by_category: dict[StrideCategory, list[ReportLaneCoverage]] = {
        category: [] for category in STRIDE_CATEGORIES
    }
    for row in rows:
        by_category[_as_category(row.lane)].append(row)
    return [
        LaneCoverage(
            category=category,
            cases=len(lane),
            drafts=sum(row.drafts for row in lane),
            rules=sum(row.rules for row in lane),
            rules_fired=sum(row.rules_fired for row in lane),
            totals={
                field: sum(getattr(row, field) for row in lane)
                for pair in CITED_PAIRS
                for field in pair
            },
        )
        for category, lane in by_category.items()
    ]


def coverage_totals(lanes: Sequence[LaneCoverage]) -> dict[str, Any]:
    """The whole-sweep line under the per-lane table."""
    totals = {
        field: sum(lane.totals[field] for lane in lanes)
        for pair in CITED_PAIRS
        for field in pair
    }
    return {
        "cases": max((lane.cases for lane in lanes), default=0),
        "drafts": sum(lane.drafts for lane in lanes),
        "rules_fired": sum(lane.rules_fired for lane in lanes),
        "rules": sum(lane.rules for lane in lanes),
        **totals,
        "cited_rates": {
            cited: round(ratio(totals[cited], totals[offered]), 3)
            for offered, cited in CITED_PAIRS
        },
    }
