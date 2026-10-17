"""Compare orders of early questions when a job selects more than one framework.

The pause asks early questions before any finding exists (ADR 0078). Where a
job selects two frameworks, the shipped list takes the frameworks in turn, and
within a turn ranks a capability question by its band and the units it could
settle, with what an answer settles as the tiebreak, and a field question by
the prior per choice. The #1289 review of 2026-10-01 asked which order serves each selected
framework best at the same submitter effort. This command answers that offline,
for a submitter who answers from the top and stops after a number of choices.

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
The submitter answers every question shown, the parts of a capability included.

**Each order is a policy in** :data:`POLICIES`, built from the production
readers: the early list, the floor and the list's selection. A policy returns
charges: each one costs some choices and answers some questions. Every order
but one charges each question on its own. ``group-question`` asks each group
of a list once for every element it lists (ADR 0081): one charge for the
group's parts answers each of its rows. A capability question is charged on
its own, because every capability shares one group.

The ``no-to-the-rest`` policies keep each capability question at its place
and answer "yes" to some of them, each on its own; one choice then answers
"no" to every capability question of the list left. The walk does not know
which capabilities an application has, so each policy reads one share of
"yes" answers (:data:`YES_SHARES`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from itertools import groupby
from pathlib import Path
from typing import Any

from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import passes_floor, question_set, whose_turn
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, early_questions
from analysis_service.fact_answers import FactAnswer, key_ref
from analysis_service.frameworks import PACKAGES
from analysis_service.open_facts import CAPABILITY_GROUP
from analysis_service.questions import fact_questions
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness import audit
from evals.harness.archive import case_suffix
from evals.harness.early_rounds import MAX_SAVES, dont_know

#: The frameworks a job selects, each with its options.
Selection = Mapping[FrameworkName, Mapping[str, Any]]

#: The submitter effort, in choices, at which each order is read.
BUDGETS = (5, 10, 15, 20, 30, 45, 60)


@dataclass(frozen=True)
class Charge:
    """One answer a submitter gives: what it costs and what it answers."""

    choices: int
    keys: frozenset[UnknownKey]


def _each(order: Sequence[EarlyQuestion]) -> list[Charge]:
    """Each question answered on its own."""
    return [Charge(question.decisions, frozenset({question.key})) for question in order]


def _group_question(
    model: SystemModel, frameworks: Selection, yes_share: float | None = None
) -> list[Charge]:
    """Each group of each list asked once for every element it lists.

    A run of a list's rows that share a group is one charge: the most parts
    any of its rows has, for all its rows. A capability is its own charge,
    unless ``yes_share`` is set (:func:`_no_to_the_rest`).
    """
    charges: list[Charge] = []
    for shown in _lists(model, frameworks):
        capabilities = [q for q in shown if q.group == CAPABILITY_GROUP]
        for group, rows in groupby(shown, key=lambda question: question.group):
            run = list(rows)
            if group != CAPABILITY_GROUP:
                keys = frozenset(question.key for question in run)
                charges.append(Charge(max(q.decisions for q in run), keys))
            elif yes_share is None:
                charges += _each(run)
            else:
                charges += _no_to_the_rest(run, capabilities, yes_share)
    return charges


#: The shares of a list's capability questions a submitter answers "yes",
#: one policy each. A third is about what the example description states of
#: its capabilities, by one reading (``QA-2026-10-13-09``).
YES_SHARES: Mapping[str, float] = {"none": 0.0, "third": 1 / 3, "all": 1.0}


def _no_to_the_rest(
    run: Sequence[EarlyQuestion],
    capabilities: Sequence[EarlyQuestion],
    yes_share: float,
) -> list[Charge]:
    """A run of a list's capability questions, each at its place: the first
    ``yes_share`` of the list's capability questions are "yes", each on its
    own, and the first one after them is one charge answering "no" to it and
    every capability question of the list after it."""
    count = round(len(capabilities) * yes_share)
    charges = []
    for question in run:
        place = capabilities.index(question)
        if place < count:
            charges += _each([question])
        elif place == count:
            charges.append(Charge(1, frozenset(q.key for q in capabilities[count:])))
    return charges


def _lists(model: SystemModel, frameworks: Selection) -> list[list[EarlyQuestion]]:
    """The list after each save, in turn.

    The submitter answers "I don't know" to every listed question, which writes
    nothing, so the model and the ranking stay as they started and only the
    saved answers move the list on.
    """
    shown: list[list[EarlyQuestion]] = []
    answered: list[FactAnswer] = []
    for _ in range(MAX_SAVES):
        asked = question_set(
            model,
            None,
            frameworks,
            [],
            waiting=True,
            answered=AnswerSet(facts=tuple(answered)),
            final=False,
            shown=[],
        )
        if not asked.early:
            break
        shown.append(list(asked.early))
        answered.extend(dont_know(question) for question in asked.early)
    return shown


def _shipped_list(model: SystemModel, frameworks: Selection) -> list[EarlyQuestion]:
    """The shipped order: the list after each save, in the order the page
    shows it (:func:`~analysis_service.answer_state.recommended`)."""
    return [question for shown in _lists(model, frameworks) for question in shown]


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

    The next pick goes to the framework that
    :func:`~analysis_service.answer_state.whose_turn` names. A question one framework's list shares with
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
        name = whose_turn(open_names, charged)
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
POLICIES: Mapping[str, Callable[[SystemModel, Selection], list[Charge]]] = {
    "shipped-list": lambda model, chosen: _each(_shipped_list(model, chosen)),
    "framework-merge": lambda model, chosen: _each(
        _merge(model, chosen, grouped=False)
    ),
    "grouped-merge": lambda model, chosen: _each(_merge(model, chosen, grouped=True)),
    "group-question": _group_question,
    **{
        f"no-to-the-rest-{name}": partial(_group_question, yes_share=share)
        for name, share in YES_SHARES.items()
    },
}


def _spend(order: Sequence[Charge], budget: int | None) -> tuple[set[UnknownKey], int]:
    """The questions a submitter answers before ``budget`` choices run out,
    and the choices spent."""
    spent = 0
    answered: set[UnknownKey] = set()
    for charge in order:
        if budget is not None and spent + charge.choices > budget:
            break
        spent += charge.choices
        answered |= charge.keys
    return answered, spent


def _prefix(order: Sequence[EarlyQuestion], budget: int | None) -> set[UnknownKey]:
    """The questions a submitter answers before ``budget`` choices run out,
    each question charged on its own."""
    return _spend(_each(order), budget)[0]


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
                answered, spent = _spend(order, budget)
                capabilities = {key_ref(key).capability for key in answered} - {""}
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
                        choices=spent,
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


@dataclass(frozen=True)
class Paired:
    """One archived report whose case another framework's report shares."""

    case: str
    path: Path
    report: Report
    #: The report's own framework (``alone``), and every framework the
    #: case's reports hold (``all``), each with the options its report chose.
    selections: Mapping[str, Selection]


def paired(root: Path) -> list[Paired]:
    """Every archived report under ``root`` whose case holds a report of
    another framework, with the two selections it is read under. A holdout
    case's report is not opened (:func:`~evals.harness.audit.held_out`); an
    exhausted one is a development case and is read."""
    held = audit.still_held_out(audit.holdout_cases())
    by_case: dict[str, list[tuple[Path, Report]]] = defaultdict(list)
    for path in sorted(root.rglob(f"*{case_suffix('report')}")):
        case = path.name.removesuffix(case_suffix("report"))
        if case in held:
            continue
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        if len(report.job.frameworks) == 1:
            by_case[case].append((path, report))
    found = []
    for case, reports in sorted(by_case.items()):
        every = {
            selection.name: dict(selection.options)
            for _, report in reports
            for selection in report.job.frameworks
        }
        if len(every) < 2:
            continue
        for path, report in reports:
            own = {s.name: dict(s.options) for s in report.job.frameworks}
            found.append(Paired(case, path, report, {"alone": own, "all": every}))
    return found


def sweep(root: Path) -> list[Reading]:
    """Every policy's reading on every :func:`paired` report, under both selections."""
    return [
        reading
        for one in paired(root)
        for reading in readings(one.case, str(one.path), one.report, one.selections)
    ]


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
