"""Compare orders of early questions when a job selects more than one framework.

The pause asks early questions before any finding exists (ADR 0053). Where a
job selects two frameworks, the shipped rounds ask a fixed number of
capability and field questions a round, with the frameworks taking turns. The
#1289 review of 2026-10-01 asked which order serves each selected framework
best at the same owner effort. This command answers that offline,
for an owner who answers from the top and stops after a number of choices.

**What an order is worth is read off archived reports.** No archived report
holds two frameworks, so each framework is measured on its own reports: the
order is built for that report's extracted model with every framework
selected, and scored against that report's findings. A conditional finding is
complete when every open fact it waits on is among the questions answered
(:func:`~analysis_service.questions.fact_questions` names them). A unit of a
framework whose units apply by a rule over capabilities is settled when every
capability its decision misses is answered. Both are counts of dependency
sets an order completes, never a count of findings an answer changes.

**Effort is choices**: one for each facet of a question with facets, and one
for every other question (:attr:`~analysis_service.early_questions.EarlyQuestion.decisions`).
The owner answers every question shown, the parts of a capability included.

**Each order is a policy in** :data:`POLICIES`, built from the production
readers: the early list, the floor and the round.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis_service.answer_round import passes_floor, question_set
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, early_questions
from analysis_service.fact_answers import FactAnswer
from analysis_service.frameworks import PACKAGES
from analysis_service.questions import fact_questions
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.early_rounds import MAX_ROUNDS, dont_know

#: The frameworks a job selects, each with its options.
Selection = Mapping[FrameworkName, Mapping[str, Any]]

#: The owner effort, in choices, at which each order is read.
BUDGETS = (5, 10, 15, 20, 30, 45, 60)


def _rounds(model: SystemModel, frameworks: Selection) -> list[list[EarlyQuestion]]:
    """Each round as the pause builds it, in turn.

    The owner answers "I don't know" to every question, which writes nothing,
    so the model and the ranking stay as they started and only the round
    moves on.
    """
    shown: list[list[EarlyQuestion]] = []
    answered: list[FactAnswer] = []
    for _ in range(MAX_ROUNDS):
        asked = question_set(
            model,
            None,
            frameworks,
            [],
            waiting=True,
            answered=answered,
            answered_links=[],
            final=False,
            shown=[],
        )
        if not asked.early:
            break
        shown.append(list(asked.early))
        answered.extend(dont_know(question) for question in asked.early)
    return shown


def _shipped_rounds(model: SystemModel, frameworks: Selection) -> list[EarlyQuestion]:
    """The shipped order: the rounds in turn, each in the order the page shows,
    which takes the frameworks in turn (:func:`~analysis_service.answer_round.by_turn`)."""
    return [question for shown in _rounds(model, frameworks) for question in shown]


def _own_lists(
    model: SystemModel, frameworks: Selection
) -> dict[FrameworkName, list[EarlyQuestion]]:
    """Each framework's own early list as a job selecting it alone ranks it,
    at or above its own floor."""
    lists = {}
    for name in sorted(frameworks):
        listed = early_questions(model, {name: frameworks[name]}, None)
        lists[name] = [q for q in listed if passes_floor(q, listed)]
    return lists


def _merge(
    model: SystemModel, frameworks: Selection, *, grouped: bool
) -> list[EarlyQuestion]:
    """Each framework's own order, taken in turn by the effort each has had.

    The next pick goes to the framework charged the fewest choices so far,
    the first by name on a tie. A question one framework's list shares with
    another is asked once, and charged to every framework that lists it.
    ``grouped`` takes a framework's next group whole, as the page shows a
    group, rather than its next question.
    """
    lists = _own_lists(model, frameworks)
    holders: dict[UnknownKey, list[FrameworkName]] = defaultdict(list)
    for name, listed in lists.items():
        for question in listed:
            holders[question.key].append(name)
    charged = dict.fromkeys(lists, 0)
    taken: set[UnknownKey] = set()
    order: list[EarlyQuestion] = []
    while True:
        waiting = {
            name: [q for q in listed if q.key not in taken]
            for name, listed in lists.items()
        }
        open_names = [name for name, left in waiting.items() if left]
        if not open_names:
            return order
        name = min(open_names, key=lambda each: (charged[each], each))
        first = waiting[name][0]
        picks = (
            [q for q in waiting[name] if q.group == first.group] if grouped else [first]
        )
        for question in picks:
            order.append(question)
            taken.add(question.key)
            for holder in holders[question.key]:
                charged[holder] += question.decisions


#: Every order compared, by name.
POLICIES: Mapping[str, Callable[[SystemModel, Selection], list[EarlyQuestion]]] = {
    "shipped-rounds": _shipped_rounds,
    "framework-merge": lambda model, chosen: _merge(model, chosen, grouped=False),
    "grouped-merge": lambda model, chosen: _merge(model, chosen, grouped=True),
}


def _prefix(order: Sequence[EarlyQuestion], budget: int | None) -> set[UnknownKey]:
    """The questions an owner answers before ``budget`` choices run out."""
    spent = 0
    answered = set()
    for question in order:
        if budget is not None and spent + question.decisions > budget:
            break
        spent += question.decisions
        answered.add(question.key)
    return answered


@dataclass(frozen=True)
class Needs:
    """What one report's framework waits on: its findings and its units."""

    #: Each conditional finding's open facts, with the finding's band label.
    findings: Mapping[str, tuple[str, frozenset[UnknownKey]]]
    #: Each unknown unit's missing capabilities.
    units: Mapping[str, frozenset[str]]


def needs_of(report: Report) -> Needs:
    """Every dependency set the report's own framework holds open."""
    bands = {
        f"{block.framework}/{claim.id}": PACKAGES[block.framework].rank(claim).label
        for block in report.analyses
        for claim in block.all_claims()
    }
    waiting: dict[str, set[UnknownKey]] = defaultdict(set)
    for question in fact_questions(report.analyses, report.system_model, None):
        for finding in question.findings:
            waiting[finding].add(question.key)
    units = {}
    for selection in report.job.frameworks:
        record = PACKAGES[selection.name].record
        for entry in record.applicability(report.system_model, selection.options):
            if entry.state == "unknown":
                units[entry.unit] = frozenset(entry.missing)
    return Needs(
        findings={
            finding: (bands[finding], frozenset(keys))
            for finding, keys in waiting.items()
        },
        units=units,
    )


@dataclass(frozen=True)
class Reading:
    """One order's worth to one report's framework at one budget."""

    case: str
    report: str
    framework: str
    policy: str
    selected: str
    #: The budget in choices, or ``None`` for the whole order.
    budget: int | None
    choices: int
    findings: int
    completed: int
    #: Findings and completed findings by the band label of the finding.
    finding_bands: Mapping[str, int]
    completed_bands: Mapping[str, int]
    units: int
    settled: int


def readings(
    case: str, path: str, report: Report, selections: Mapping[str, Selection]
) -> list[Reading]:
    """Every policy's reading on one report, at every budget, per selection."""
    (framework,) = (selection.name for selection in report.job.frameworks)
    needs = needs_of(report)
    bands = Counter(band for band, _ in needs.findings.values())
    found = []
    for policy, build in POLICIES.items():
        for selected, frameworks in selections.items():
            order = build(report.system_model, frameworks)
            for budget in (*BUDGETS, None):
                answered = _prefix(order, budget)
                capabilities = {key[5] for key in answered if key[5]}
                done = [
                    band for band, keys in needs.findings.values() if keys <= answered
                ]
                found.append(
                    Reading(
                        case=case,
                        report=path,
                        framework=framework,
                        policy=policy,
                        selected=selected,
                        budget=budget,
                        choices=sum(q.decisions for q in order if q.key in answered),
                        findings=len(needs.findings),
                        completed=len(done),
                        finding_bands=dict(bands),
                        completed_bands=dict(Counter(done)),
                        units=len(needs.units),
                        settled=sum(
                            1
                            for missing in needs.units.values()
                            if missing <= capabilities
                        ),
                    )
                )
    return found


def sweep(root: Path) -> list[Reading]:
    """Every archived report whose case another framework's report shares.

    Each is read twice: with its own framework selected, and with every
    framework the case's reports hold, each with the options its report chose.
    """
    by_case: dict[str, list[tuple[Path, Report]]] = defaultdict(list)
    for path in sorted(root.rglob("*.report.json")):
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        if len(report.job.frameworks) == 1:
            by_case[path.name.removesuffix(".report.json")].append((path, report))
    found = []
    for case, reports in sorted(by_case.items()):
        every = {
            selection.name: selection.options
            for _, report in reports
            for selection in report.job.frameworks
        }
        if len(every) < 2:
            continue
        for path, report in reports:
            own = {s.name: s.options for s in report.job.frameworks}
            selections = {"alone": own, "all": every}
            found.extend(readings(case, str(path), report, selections))
    return found


#: The counts a summary sums over reports, each read off a reading.
SUMMED = ("choices", "findings", "completed", "units", "settled")


def summary(found: Sequence[Reading]) -> dict[str, dict[str, dict[str, object]]]:
    """Sums over reports, by framework and selection, policy, and budget."""
    sums: dict[str, dict[str, Counter[str]]] = {}
    for one in found:
        group = sums.setdefault(f"{one.framework} / {one.selected}", {})
        row = group.setdefault(
            f"{one.policy} @ {'all' if one.budget is None else one.budget}", Counter()
        )
        row["reports"] += 1
        for field in SUMMED:
            row[field] += getattr(one, field)
        for band, count in one.completed_bands.items():
            row[f"completed {band}"] += count
        for band, count in one.finding_bands.items():
            row[f"findings {band}"] += count
    return {
        group: {key: dict(sorted(row.items())) for key, row in rows.items()}
        for group, rows in sums.items()
    }


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("evals"),
        help="the directory whose archived *.report.json files are read",
    )
    parser.add_argument(
        "--out", type=Path, help="write the summary, and one for each case, as JSON"
    )


def command_early_policies(args: argparse.Namespace) -> int:
    """Read every policy on every paired archived report, and print the sums."""
    found = sweep(args.root)
    if not found:
        print("no case has archived reports of two frameworks", file=sys.stderr)
        return 1
    table = summary(found)
    print(json.dumps(table, indent=2))
    if args.out is not None:
        args.out.write_text(
            json.dumps(
                {
                    "summary": table,
                    "by_case": {
                        case: summary([one for one in found if one.case == case])
                        for case in sorted({one.case for one in found})
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0
