"""Answer the early list from the top, as an owner would, and read what each
policy settles at matched choices (#1632).

The list before the analysis ranks, within a framework's turn, the question
whose answer settles the most other questions first (ADR 0078). What that
order is worth depends on the answers: a "no" to a broad parent removes its
parts, a "yes" lists them. :mod:`evals.harness.early_policies` reads an order
with every answer "I don't know", which moves nothing. This command answers
the first listed question, saves, and reads the list again, one question a
save, with each of several answerers, under the shipped policy and under the
order without the effects, which is the band-and-prior order of ADR 0055 and
ADR 0049.

**What an order is worth is read off archived reports**, as `early-policies`
reads it: a STRIDE report's conditional finding is complete when every open
fact it waits on is answered, and an ASVS report's unit is decided when the
framework's own applicability rule, read over the model with the answers
written in, no longer says ``unknown``. A "no" to a parent decides a unit
without an answer to the part, which is what the effects count. Both are
counts of dependency sets, never of findings an answer changes.

**Effort is choices**: one for each facet of a question with facets, and one
for every other question. The walk stops at the largest budget, and each
smaller budget reads a prefix of the same walk.

**The order without the effects is the shipped code with one reader stubbed.**
``answer_round.capability_effects`` answers with no effect, so
:func:`~analysis_service.answer_round.recommended` falls back to the list's
own order: the band and the units for a capability, the prior per choice for
a field question. Everything else, the limits, the floors, the turns and the
parts that wait on a parent, is the shipped code.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest import mock

from analysis_service import answer_round
from analysis_service.answer_round import Answers, AnswerState, SavedRound
from analysis_service.answer_sets import AnswerSet
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion
from analysis_service.fact_answers import FactAnswer, key_ref
from analysis_service.fact_writes import answered_model
from analysis_service.frameworks import PACKAGES
from analysis_service.jobs import Checkpoint
from analysis_service.open_facts import prepared_model
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.early_policies import Needs, Selection, needs_of
from evals.harness.early_rounds import dont_know

#: The owner effort, in choices, at which each walk is read.
BUDGETS = (5, 10, 20)

#: A walk that has not ended by this save is cut here.
MAX_SAVES = 60

#: An answerer's answer to one listed question.
Answerer = Callable[[EarlyQuestion], FactAnswer]


def _capabilities(value: str) -> Answerer:
    """``value`` to every capability question, "I don't know" to the rest."""

    def answer(question: EarlyQuestion) -> FactAnswer:
        if question.kind == "capability":
            return FactAnswer(key=question.key, value=value)
        return dont_know(question)

    return answer


def _alternate(question: EarlyQuestion) -> FactAnswer:
    """ "yes" and "no" by turns over the capability's name, the same on every
    walk, so a mixed path is read without a probability."""
    if question.kind != "capability":
        return dont_know(question)
    name = key_ref(question.key).capability
    return FactAnswer(key=question.key, value="yes" if len(name) % 2 else "no")


#: The answerers, keyed by name. ``unknown`` writes nothing, so it reads the
#: order alone, as `early-policies` does.
ANSWERERS: Mapping[str, Answerer] = {
    "yes": _capabilities("yes"),
    "no": _capabilities("no"),
    "unknown": dont_know,
    "alternate": _alternate,
}

#: The policies, keyed by name: whether the effects rank the list.
POLICIES: Mapping[str, bool] = {"effects": True, "no-effects": False}


@contextmanager
def policy(effects: bool) -> Iterator[None]:
    """The shipped order, or the order without the effects (see the module)."""
    if effects:
        yield
        return
    with mock.patch.object(answer_round, "capability_effects", lambda *a, **k: {}):
        yield


def _state(
    model: SystemModel, frameworks: Selection, answered: Sequence[FactAnswer]
) -> AnswerState:
    return AnswerState(
        checkpoint=Checkpoint(system_model=model, assertions=None),
        frameworks=frameworks,
        analyses=(),
        waiting=True,
        final=False,
        sources=(),
        answers=AnswerSet(facts=tuple(answered)),
        shown=(),
        skipped=(),
        corrections=(),
        revision=0,
        resumed_by=None,
    )


@dataclass(frozen=True)
class Step:
    """One answered question of a walk, and the choices spent with it."""

    key: UnknownKey
    answer: FactAnswer
    spent: int


@dataclass(frozen=True)
class Walk:
    """One list answered from the top, one question a save."""

    steps: tuple[Step, ...]
    saves: int
    ended: bool

    def answers(self, budget: int | None) -> list[FactAnswer]:
        """The answers given within ``budget`` choices, or all of them."""
        return [
            step.answer for step in self.steps if budget is None or step.spent <= budget
        ]


def walk(
    model: SystemModel,
    frameworks: Selection,
    answerer: Answerer,
    *,
    effects: bool,
    budget: int = max(BUDGETS),
) -> Walk:
    """Answer the first listed question and save, until ``budget`` choices are
    spent or the list ends. Each save reads the list again, as the page does."""
    answered: list[FactAnswer] = []
    steps: list[Step] = []
    spent = 0
    saves = 0
    ended = False
    with policy(effects):
        for saves in range(MAX_SAVES):
            state = _state(model, frameworks, answered)
            asked = state.questions
            if asked.done:
                ended = True
                break
            if spent >= budget:
                break
            question = asked.early[0]
            reply = answerer(question)
            saved = state.answer(
                Answers(
                    given=AnswerSet(facts=(reply,)),
                    save=True,
                    presented=[question.key],
                    revision=0,
                ),
                limits=None,
            )
            if not isinstance(saved, SavedRound):
                raise TypeError("a save starts no job")
            answered = list(saved.answers.facts)
            spent += question.decisions
            steps.append(Step(key=question.key, answer=reply, spent=spent))
    return Walk(steps=tuple(steps), saves=saves, ended=ended)


@dataclass(frozen=True)
class Reading:
    """What one policy and one answerer settle on one report at one budget."""

    case: str
    report: str
    framework: str
    policy: str
    answerer: str
    selected: str
    budget: int | None
    #: The choices spent and the questions answered within the budget.
    choices: int
    asked: int
    #: The report's conditional findings, and how many the answers complete,
    #: in all and by band.
    findings: int
    completed: int
    completed_bands: Mapping[str, int]
    #: The framework's units, and how many the answers decide.
    units: int
    decided: int
    #: Questions the pause first listed that the answers made unnecessary,
    #: and the answers of "I don't know".
    unnecessary: int
    unknown_answers: int
    saves: int
    ended: bool


def _read(
    model: SystemModel,
    frameworks: Selection,
    framework: FrameworkName,
    needs: Needs,
    answers: Sequence[FactAnswer],
    *,
    effects: bool,
) -> dict[str, Any]:
    """The figures the answers reach, through the production readers."""
    keys = {answer.key for answer in answers}
    done = [band for band, waits in needs.findings.values() if waits <= keys]
    prepared = prepared_model(answered_model(model, answers), None)
    entries = PACKAGES[framework].record.applicability(prepared, frameworks[framework])
    with policy(effects):
        asked = _state(model, frameworks, answers).questions
    return {
        "choices": 0,
        "asked": len(answers),
        "findings": len(needs.findings),
        "completed": len(done),
        "completed_bands": dict(Counter(done)),
        "units": len(entries),
        "decided": sum(1 for entry in entries if entry.state != "unknown"),
        "unnecessary": len(asked.unnecessary),
        "unknown_answers": sum(1 for answer in answers if not answer.known),
    }


def readings(
    case: str, path: str, report: Report, selections: Mapping[str, Selection]
) -> list[Reading]:
    """Every policy's and answerer's reading on one report, at every budget."""
    (framework,) = (selection.name for selection in report.job.frameworks)
    needs = needs_of(report)
    found = []
    for name, effects in POLICIES.items():
        for who, answerer in ANSWERERS.items():
            for selected, frameworks in selections.items():
                walked = walk(
                    report.system_model, frameworks, answerer, effects=effects
                )
                for budget in (*BUDGETS, None):
                    answers = walked.answers(budget)
                    figures = _read(
                        report.system_model,
                        frameworks,
                        framework,
                        needs,
                        answers,
                        effects=effects,
                    )
                    figures["choices"] = max(
                        (
                            step.spent
                            for step in walked.steps
                            if budget is None or step.spent <= budget
                        ),
                        default=0,
                    )
                    found.append(
                        Reading(
                            case=case,
                            report=path,
                            framework=framework,
                            policy=name,
                            answerer=who,
                            selected=selected,
                            budget=budget,
                            saves=walked.saves,
                            ended=walked.ended,
                            **figures,
                        )
                    )
    return found


def _readings_of(job: tuple[str, str, Mapping[str, Selection]]) -> list[dict[str, Any]]:
    """One report's readings, for a worker process."""
    case, path, selections = job
    report = Report.model_validate_json(Path(path).read_text(encoding="utf-8"))
    return [asdict(one) for one in readings(case, path, report, selections)]


def jobs(
    root: Path, selections: Collection[str] = ("alone", "all")
) -> list[tuple[str, str, Mapping[str, Selection]]]:
    """Every archived report whose case another framework's report shares,
    read alone and with every framework the case's reports hold, as
    :func:`evals.harness.early_policies.sweep` pairs them."""
    by_case: dict[str, list[tuple[Path, Report]]] = defaultdict(list)
    for path in sorted(root.rglob("*.report.json")):
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        if len(report.job.frameworks) == 1:
            by_case[path.name.removesuffix(".report.json")].append((path, report))
    found: list[tuple[str, str, Mapping[str, Selection]]] = []
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
            chosen: dict[str, Selection] = {"alone": own, "all": every}
            found.append((case, str(path), {name: chosen[name] for name in selections}))
    return found


def sweep(
    root: Path, workers: int, selections: Collection[str] = ("alone", "all")
) -> list[dict[str, Any]]:
    """Every reading over the archive, read in ``workers`` processes."""
    found: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(_readings_of, jobs(root, selections)):
            found.extend(rows)
    return found


#: The counts a summary sums over reports.
SUMMED = (
    "choices",
    "asked",
    "findings",
    "completed",
    "units",
    "decided",
    "unnecessary",
    "unknown_answers",
)


def summary(found: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """Sums over reports, by framework and selection, then policy, answerer and budget."""
    sums: dict[str, dict[str, Counter[str]]] = {}
    for one in found:
        group = sums.setdefault(f"{one['framework']} / {one['selected']}", {})
        budget = "all" if one["budget"] is None else one["budget"]
        row = group.setdefault(
            f"{one['policy']} / {one['answerer']} @ {budget}", Counter()
        )
        row["reports"] += 1
        for name in SUMMED:
            row[name] += one[name]
        for band, count in one["completed_bands"].items():
            row[f"completed {band}"] += count
        row["not_ended"] += 0 if one["ended"] or one["budget"] is not None else 1
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
    parser.add_argument("--workers", type=int, default=4, help="worker processes")
    parser.add_argument(
        "--selections",
        default="alone,all",
        help="which selections to read: the report's own framework ('alone'),"
        " every framework the case holds ('all'), or both",
    )
    parser.add_argument(
        "--out", type=Path, help="write the summary and every reading as JSON"
    )


def command_early_answerers(args: argparse.Namespace) -> int:
    """Walk every policy with every answerer over the archive, and print the sums."""
    found = sweep(args.root, args.workers, args.selections.split(","))
    if not found:
        print("no case has archived reports of two frameworks", file=sys.stderr)
        return 1
    table = summary(found)
    print(json.dumps(table, indent=2))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # The summary is laid out to read; each reading is one line, so the
        # file stays a few megabytes over thousands of walks.
        rows = ",\n".join(json.dumps(row, separators=(",", ":")) for row in found)
        args.out.write_text(
            json.dumps({"summary": table}, indent=2)[:-2]
            + ',\n  "readings": [\n'
            + rows
            + "\n  ]\n}\n",
            encoding="utf-8",
        )
    return 0
