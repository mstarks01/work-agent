"""Answer the early list from the top, as a submitter would, and read what each
policy settles at matched choices (#1632).

The list before the analysis ranks, within a framework's turn, a capability
question by its band and the units it could settle (ADR 0055), and a field
question by the prior per choice (ADR 0049). What an answer settles, the
effects, breaks a tie between two capabilities (ADR 0078). What that order is
worth depends on the answers: a "no" to a broad parent removes its parts, a
"yes" lists them. :mod:`evals.harness.early_policies` reads an order with every
answer "I don't know", which moves nothing. This command answers the first
listed question, saves, and reads the list again, one question a save, with
each of several answerers, under the shipped policy and under the order
without the effects, where the question's place in the early list breaks the
tie.

**What an order is worth is read off archived reports**, as `early-policies`
reads it: a STRIDE report's conditional finding is complete when every open
fact it waits on is answered, and an ASVS report's unit is decided when the
framework's own applicability rule, read over the model with the answers
written in, no longer says ``unknown``. A "no" to a parent decides a unit
without an answer to the part, which is what the effects count. Both are
counts of dependency sets, never of findings an answer changes.

**Effort is choices**: one for each facet of a question with facets, one for
every other question, and one for a skip. Each save spends at least one
choice, so the largest budget bounds the walk, and each smaller budget reads a
prefix of the same walk.

**A reversal changes the walk's first "yes" or "no"**, through the same save,
and reads which questions the change reopens, which it makes unnecessary, and
how the list moves.

**The order without the effects is the shipped code with one reader stubbed.**
``answer_round.capability_effects`` answers with no effect, so
:func:`~analysis_service.answer_round.recommended` breaks a tie by the
question's place in the early list. Everything else, the limits, the floors, the turns and the
parts that wait on a parent, is the shipped code.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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
from analysis_service.capabilities import lineage
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion
from analysis_service.fact_answers import FactAnswer, SkipKey, key_ref
from analysis_service.fact_writes import answered_model
from analysis_service.frameworks import PACKAGES
from analysis_service.open_facts import prepared_model
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.early_policies import Needs, Selection, needs_of, paired
from evals.harness.early_rounds import dont_know, paused_state
from evals.harness.rule_applicability import TRUTH_ANSWERS, load_fixtures

#: The submitter effort, in choices, at which each walk is read.
BUDGETS = (5, 10, 20)

#: An answerer's answer to one listed question, or ``None`` to skip it.
Answerer = Callable[[EarlyQuestion], FactAnswer | None]


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


def _skip(question: EarlyQuestion) -> None:
    """A submitter who skips every question for now."""
    del question


def _truth(truth: Mapping[str, str]) -> Answerer:
    """A signed fixture's truth for each capability it names, as
    ``rule-applicability`` answers it, and "I don't know" to the rest."""

    def answer(question: EarlyQuestion) -> FactAnswer:
        state = truth.get(key_ref(question.key).capability)
        if question.kind != "capability" or state is None:
            return dont_know(question)
        return FactAnswer(key=question.key, value=TRUTH_ANSWERS[state])

    return answer


#: The answerers, keyed by name. ``unknown`` writes nothing, so it reads the
#: order alone, as `early-policies` does. Each signed fixture with a truth
#: answers as the submitter it describes, a mix of "yes", "no" and "I don't know".
ANSWERERS: Mapping[str, Answerer] = {
    "yes": _capabilities("yes"),
    "no": _capabilities("no"),
    "unknown": dont_know,
    "alternate": _alternate,
    "skip": _skip,
    **{
        f"fixture {fixture.name}": _truth(fixture.truth)
        for fixture in load_fixtures()
        if fixture.signed and fixture.truth
    },
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


def _save(
    state: AnswerState,
    given: Sequence[FactAnswer],
    *,
    skips: Sequence[SkipKey] = (),
    presented: Sequence[UnknownKey] = (),
) -> SavedRound:
    """One save through the service's own admission, which runs no model."""
    saved = state.answer(
        Answers(
            given=AnswerSet(facts=tuple(given)),
            save=True,
            skips=skips,
            presented=presented,
            revision=0,
        ),
        limits=None,
    )
    if not isinstance(saved, SavedRound):
        raise TypeError("a save starts no job")
    return saved


@dataclass(frozen=True)
class Step:
    """One listed question of a walk, its answer or ``None`` for a skip, and
    the choices spent with it."""

    key: UnknownKey
    answer: FactAnswer | None
    spent: int


@dataclass(frozen=True)
class Walk:
    """One list answered from the top, one question a save."""

    steps: tuple[Step, ...]
    #: The list asked nothing more before the walk spent its budget.
    ended: bool

    def within(self, budget: int) -> tuple[Step, ...]:
        """The steps taken within ``budget`` choices."""
        return tuple(step for step in self.steps if step.spent <= budget)


def walk(
    model: SystemModel,
    frameworks: Selection,
    answerer: Answerer,
    *,
    effects: bool,
    budget: int = max(BUDGETS),
) -> Walk:
    """Answer or skip the first listed question and save, until ``budget``
    choices are spent or the list ends. Each save reads the list again, as the
    page does, and spends at least one choice, so ``budget`` bounds the saves."""
    answered: list[FactAnswer] = []
    skipped: list[SkipKey] = []
    steps: list[Step] = []
    spent = 0
    with policy(effects):
        while spent < budget:
            state = paused_state(model, frameworks, answered, skipped)
            asked = state.questions
            if asked.done:
                return Walk(steps=tuple(steps), ended=True)
            question = asked.early[0]
            reply = answerer(question)
            if reply is None:
                saved = _save(state, [], skips=[question.key], presented=[question.key])
                spent += 1
            else:
                saved = _save(state, [reply], presented=[question.key])
                spent += question.decisions
            answered = list(saved.answers.facts)
            skipped = list(saved.skipped)
            steps.append(Step(key=question.key, answer=reply, spent=spent))
    return Walk(steps=tuple(steps), ended=False)


@dataclass(frozen=True)
class Reading:
    """What one policy and one answerer settle on one report at one budget."""

    case: str
    report: str
    framework: str
    policy: str
    answerer: str
    selected: str
    budget: int
    #: The choices spent, the questions answered and the questions skipped
    #: within the budget.
    choices: int
    asked: int
    skipped: int
    #: The report's conditional findings, and how many the answers complete,
    #: in all and by band.
    findings: int
    finding_bands: Mapping[str, int]
    completed: int
    completed_bands: Mapping[str, int]
    #: The framework's units, and how many the answers decide.
    units: int
    decided: int
    #: Questions the pause first listed that the answers made unnecessary,
    #: and the answers of "I don't know".
    unnecessary: int
    unknown_answers: int
    #: The list asked nothing more within the budget.
    ended: bool
    #: Whether the walk held a "yes" or "no" to reverse; whether the service
    #: refused that one change alone, and how many answers the submitter turned.
    reversed: bool
    reversal_refused: bool
    reversal_answers: int
    #: After the reversal: the questions the list holds and did not hold
    #: before, and those it no longer holds; the questions an answer had made
    #: unnecessary and no longer has, and those it newly has; and the units
    #: decided.
    listed_reversed: int
    withdrawn_reversed: int
    reopened: int
    closed: int
    decided_reversed: int


def _decided(
    model: SystemModel,
    frameworks: Selection,
    framework: FrameworkName,
    answers: Sequence[FactAnswer],
) -> tuple[int, int]:
    """The framework's units with ``answers`` written in, and how many are decided."""
    prepared = prepared_model(answered_model(model, answers), None)
    entries = PACKAGES[framework].record.applicability(prepared, frameworks[framework])
    return len(entries), sum(1 for entry in entries if entry.state != "unknown")


#: The other answer to a capability question.
TURNED = {"yes": "no", "no": "yes"}


def _reversal(answers: Sequence[FactAnswer]) -> tuple[FactAnswer, ...]:
    """The first "yes" or "no" of ``answers`` turned to the other, then each
    answer the lineage check makes the submitter turn with it: a part answered
    "yes" under a parent turned to "no", or a parent answered "no" over a part
    turned to "yes". Empty where ``answers`` hold no "yes" or "no"."""
    first = next(
        (one for one in answers if one.kind == "capability" and one.value in TURNED),
        None,
    )
    if first is None:
        return ()
    name = key_ref(first.key).capability

    def bound(other: FactAnswer) -> bool:
        other_name = key_ref(other.key).capability
        return (
            other is not first
            and other.kind == "capability"
            and other.value == first.value
            and (name in lineage(other_name) or other_name in lineage(name))
        )

    return tuple(
        FactAnswer(key=one.key, value=TURNED[one.value])
        for one in (first, *filter(bound, answers))
    )


def _read(
    model: SystemModel,
    frameworks: Selection,
    framework: FrameworkName,
    needs: Needs,
    steps: Sequence[Step],
    *,
    effects: bool,
) -> dict[str, Any]:
    """The figures ``steps`` reach, through the production readers."""
    answers = [step.answer for step in steps if step.answer is not None]
    skips = [step.key for step in steps if step.answer is None]
    keys = {answer.key for answer in answers}
    done = [band for band, waits in needs.findings.values() if waits <= keys]
    units, decided = _decided(model, frameworks, framework, answers)
    turned = _reversal(answers)
    refused = False
    with policy(effects):
        state = paused_state(model, frameworks, answers, skips)
        before = after = state.questions
        decided_reversed = decided
        if turned:
            # The submitter changes an answer the page shows as answered, which
            # the list no longer holds, so the save presents nothing. Where the
            # service refuses the one change, the submitter turns the lineage too.
            try:
                saved = _save(state, turned[:1])
            except ValueError:
                refused = True
                saved = _save(state, turned)
            after = paused_state(
                model, frameworks, saved.answers.facts, skips
            ).questions
            decided_reversed = _decided(
                model, frameworks, framework, saved.answers.facts
            )[1]
    listed = {question.key for question in before.early}
    listed_after = {question.key for question in after.early}
    unnecessary = {question.key for question, _ in before.unnecessary}
    unnecessary_after = {question.key for question, _ in after.unnecessary}
    return {
        "choices": steps[-1].spent if steps else 0,
        "asked": len(answers),
        "skipped": len(skips),
        "findings": len(needs.findings),
        "finding_bands": dict(Counter(band for band, _ in needs.findings.values())),
        "completed": len(done),
        "completed_bands": dict(Counter(done)),
        "units": units,
        "decided": decided,
        "unnecessary": len(before.unnecessary),
        "unknown_answers": sum(1 for answer in answers if not answer.known),
        "reversed": bool(turned),
        "reversal_refused": refused,
        "reversal_answers": len(turned),
        "listed_reversed": len(listed_after - listed),
        "withdrawn_reversed": len(listed - listed_after),
        "reopened": len(unnecessary - unnecessary_after),
        "closed": len(unnecessary_after - unnecessary),
        "decided_reversed": decided_reversed,
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
                for budget in BUDGETS:
                    steps = walked.within(budget)
                    found.append(
                        Reading(
                            case=case,
                            report=path,
                            framework=framework,
                            policy=name,
                            answerer=who,
                            selected=selected,
                            budget=budget,
                            ended=walked.ended and steps == walked.steps,
                            **_read(
                                report.system_model,
                                frameworks,
                                framework,
                                needs,
                                steps,
                                effects=effects,
                            ),
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
    """Every :func:`~evals.harness.early_policies.paired` report, with the
    named selections, for a worker process."""
    return [
        (one.case, str(one.path), {name: one.selections[name] for name in selections})
        for one in paired(root)
    ]


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
    "skipped",
    "findings",
    "completed",
    "units",
    "decided",
    "unnecessary",
    "unknown_answers",
    "ended",
    "reversed",
    "reversal_refused",
    "reversal_answers",
    "listed_reversed",
    "withdrawn_reversed",
    "reopened",
    "closed",
    "decided_reversed",
)


def summary(found: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """Sums over reports, by framework and selection, then policy, answerer and budget."""
    sums: dict[str, dict[str, Counter[str]]] = {}
    for one in found:
        group = sums.setdefault(f"{one['framework']} / {one['selected']}", {})
        row = group.setdefault(
            f"{one['policy']} / {one['answerer']} @ {one['budget']}", Counter()
        )
        row["reports"] += 1
        for name in SUMMED:
            row[name] += one[name]
        for band, count in one["finding_bands"].items():
            row[f"findings {band}"] += count
        for band, count in one["completed_bands"].items():
            row[f"completed {band}"] += count
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
