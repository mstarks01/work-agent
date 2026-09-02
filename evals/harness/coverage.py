"""What the lane agents were offered across a sweep, and what they cited.

:class:`~analysis_service.report.LaneCoverage` is computed per job and rides on
each **Framework Analysis**, one row per lane. One job's rows are close to
unreadable: an agent that examined a flow and correctly found nothing cites
nothing, and no observable separates it from one that never looked. The
aggregate is where the field was always meant to be read. A lane that cites two
of forty structural leads across twelve cases is a coverage signal, in a way
that one agent's zero on one case never is.

The numbers are pooled rather than averaged over cases, for the reason
:func:`~evals.harness.critic_yield.aggregate_yield` gives: these are small
per-case counts, and a mean of per-case rates lets a three-element case outweigh
a forty-element one.

They are keyed by ``(framework, lane)``, never by lane alone. A lane is a
**Framework Package**'s own vocabulary rather than a shared one, and two
packages may declare a lane of the same name, so a table keyed by the slug would
pool two unrelated agents' numbers the day that happens. STRIDE's six categories
and ASVS's 17 chapters do not collide today, and relying on that is the
one-package assumption ``docs/agents/framework-parity.md`` exists to catch.

It is credential-free, because it folds numbers the service already computed in
code, so it costs a sweep nothing. It does not gate. A citation rate is not a
quality bar: the rate a healthy lane runs at is a thing the baseline sweeps have
to establish before anybody writes a threshold over it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from analysis_service.frameworks import PACKAGES
from analysis_service.report import FrameworkName
from analysis_service.report import LaneCoverage as ReportLaneCoverage
from evals.harness.scorer import ratio

# The offered/cited pairs, in the order they read on a row. Named once because
# the fold, the rates and the printed table would otherwise each carry their
# own copy of the pairing and could disagree about which half is which.
CITED_PAIRS: tuple[tuple[str, str], ...] = (
    ("candidates", "candidates_cited"),
    ("elements", "elements_cited"),
    ("boundary_crossings", "boundary_crossings_cited"),
    ("unknown_controls", "unknown_controls_cited"),
)


#: One report row, tagged with the block it was read off. The report's own row
#: carries no framework — it does not need one, since it sits inside that
#: framework's block — so the sweep pairs them as it collects.
TaggedRow = tuple[FrameworkName, ReportLaneCoverage]


@dataclass(frozen=True)
class LaneCoverage:
    """One lane's coverage pooled over every case in the sweep."""

    framework: FrameworkName
    lane: str
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
            "framework": self.framework,
            "lane": self.lane,
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


def aggregate_coverage(
    rows: Iterable[TaggedRow], frameworks: Iterable[FrameworkName]
) -> list[LaneCoverage]:
    """Pool per-case coverage rows into one row per ``(framework, lane)``.

    **Every lane of every framework the sweep ran**, in each package's own
    declared order, including lanes the sweep saw no row for: a lane missing
    from the table would read as a lane with nothing to cite, and the two are
    opposite findings.

    ``frameworks`` is what the sweep *built*, not what the rows happen to
    mention, and that is what makes the guarantee hold in both directions. A
    package whose every lane went silent would vanish from the table if the list
    came off the rows — which is the exact finding the table exists to show. And
    a sweep that ran STRIDE alone prints six rows rather than twenty-three,
    because a framework nobody ran has no silent lanes to report.

    The lane list itself comes from
    :data:`~analysis_service.frameworks.PACKAGES`, so a package that gains a lane
    gains a row here with no edit.
    """
    collected: dict[tuple[FrameworkName, str], list[ReportLaneCoverage]] = {}
    for framework, row in rows:
        collected.setdefault((framework, row.lane), []).append(row)
    ran = set(frameworks)
    return [
        LaneCoverage(
            framework=framework,
            lane=lane,
            cases=len(collected.get((framework, lane), [])),
            drafts=sum(row.drafts for row in collected.get((framework, lane), [])),
            rules=sum(row.rules for row in collected.get((framework, lane), [])),
            rules_fired=sum(
                row.rules_fired for row in collected.get((framework, lane), [])
            ),
            totals={
                field: sum(
                    getattr(row, field) for row in collected.get((framework, lane), [])
                )
                for pair in CITED_PAIRS
                for field in pair
            },
        )
        for framework in PACKAGES
        if framework in ran
        for lane in PACKAGES[framework].lanes
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


def render(lanes: Sequence[LaneCoverage], offered: bool) -> None:
    """What each lane was offered and how much of it its drafts cite.

    Read as a rate over the whole sweep, never per case, and never as a score:
    an agent that examined a lead and correctly rejected it cites nothing, so a
    low rate is a question to ask rather than a failure. The number that is
    unambiguous is a lane whose rules fire nowhere at all: those rules read a
    shape this corpus does not have, or nothing at all.

    The rules column is **firings over evaluations** — one rule against one
    case — because pooling multiplies the lane's rules by the cases.
    """
    if not offered:
        print("coverage: no case produced a report to account for")
        return
    print("coverage (whole sweep, per lane — cited, not considered):")
    header = f"  {'framework':10} {'lane':30} {'drafts':>7} {'rules fired':>11}"
    print(f"{header} {'candidates':>12} {'elements':>12} {'crossings':>12}")
    for lane in lanes:
        cited = [
            f"{lane.totals[cited_field]}/{lane.totals[offered_field]}"
            for offered_field, cited_field in CITED_PAIRS[:3]
        ]
        print(
            f"  {lane.framework:10} {lane.lane:30} {lane.drafts:7,}"
            f" {lane.rules_fired:>4}/{lane.rules:<5}"
            f" {cited[0]:>12} {cited[1]:>12} {cited[2]:>12}"
        )
    totals = coverage_totals(lanes)
    print(
        f"coverage: {totals['rules_fired']}/{totals['rules']} rule evaluations fired,"
        f" candidates cited {totals['cited_rates']['candidates_cited']:.0%},"
        f" elements {totals['cited_rates']['elements_cited']:.0%},"
        f" unknown controls {totals['cited_rates']['unknown_controls_cited']:.0%}"
        " (instrument, non-gating)"
    )


def artifact(lanes: Sequence[LaneCoverage]) -> dict[str, Any]:
    """This instrument's artifact keys.

    Written whether or not anything was offered: an absent block and a block of
    zeroes would otherwise be indistinguishable to a reader comparing two
    sweeps.
    """
    return {
        "coverage": [lane.to_json() for lane in lanes],
        "coverage_totals": coverage_totals(lanes),
    }
