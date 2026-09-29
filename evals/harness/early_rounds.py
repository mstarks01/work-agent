"""Replay rounds of early questions on archived models, and count how the list moves.

The bounded plan before the analysis asks a paused job's early questions in
rounds (#1289). This command answers them offline, round after round, and
records how many questions reach the floor at the start, how many a
submitter answers in all, and how many the answers add or remove. No model
runs: each round is built by
:func:`~analysis_service.answer_round.question_set`, admitted by
:meth:`~analysis_service.answer_round.QuestionSet.admit` and written by
:func:`~analysis_service.questions.answered_model`, as the service does.

**The checkpoint is an archived report's extracted model**, with no catalog,
because no archived run pairs an extracted model with its catalog.

**The answerer reads the case's blessed model.** An attribute takes the blessed
value where the blessed model holds the element and states the attribute, and
"I don't know" otherwise. A question kind takes "I don't know": its answer
writes nothing onto the model, so its content cannot move a later round. No
blessed model states a capability, so the two answerers bound it: one answers
every capability "yes", which shows every child, and one answers "no", which
hides them.

**Two more answerers bound the attributes.** Few extracted elements carry the
blessed model's IDs, so the blessed answerer leaves most attributes open. One
answerer says every control is ``none``, which keeps each lead, and one names
a mechanism for every control, which closes it. Each takes a closed
attribute's first or last choice, and answers every capability "yes".
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from analysis_service.analysis import control_state
from analysis_service.answer_round import EARLY_RULES, QuestionSet, question_set
from analysis_service.assertions import UNKNOWN
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, early_questions
from analysis_service.questions import FactAnswer, merged_facts
from analysis_service.report import Report
from analysis_service.system_model import SystemModel

#: A replay that has not ended by this round is reported as not ending.
MAX_ROUNDS = 50

Answerer = Callable[[EarlyQuestion, SystemModel], FactAnswer]


def _dont_know(question: EarlyQuestion) -> FactAnswer:
    if question.facets:
        facets = {facet.id: UNKNOWN for facet in question.facets}
        return FactAnswer.model_validate({"key": question.key, "facets": facets})
    return FactAnswer(key=question.key, value=UNKNOWN)


def _blessed(question: EarlyQuestion, blessed: SystemModel) -> FactAnswer:
    """The blessed value of an attribute question, else "I don't know"."""
    element_id, attribute = question.key[:2]
    element = blessed.get(element_id)
    value = getattr(element, attribute, None) if attribute else None
    stated = isinstance(value, str) and control_state(value) != "unverified"
    fits = not question.choices or value in question.choices
    if question.kind == "attribute" and isinstance(value, str) and stated and fits:
        return FactAnswer(key=question.key, value=value)
    return _dont_know(question)


def _capabilities(said: str) -> Answerer:
    def answer(question: EarlyQuestion, blessed: SystemModel) -> FactAnswer:
        if question.kind == "capability":
            return FactAnswer(key=question.key, value=said)
        return _blessed(question, blessed)

    return answer


def _every_control(stated: bool) -> Answerer:
    def answer(question: EarlyQuestion, blessed: SystemModel) -> FactAnswer:
        del blessed
        if question.kind == "capability":
            return FactAnswer(key=question.key, value="yes")
        if question.kind != "attribute":
            return _dont_know(question)
        if question.choices:
            value = question.choices[0 if stated else -1]
        elif question.form == "control":
            value = question.suggestions[0] if stated else "none"
        else:
            return _dont_know(question)
        return FactAnswer(key=question.key, value=value)

    return answer


#: The answerers, keyed by name.
ANSWERERS: Mapping[str, Answerer] = {
    "capability-yes": _capabilities("yes"),
    "capability-no": _capabilities("no"),
    "controls-none": _every_control(stated=False),
    "controls-stated": _every_control(stated=True),
}


@dataclass(frozen=True)
class Replay:
    """One archived model answered by one answerer, round after round."""

    case: str
    framework: str
    answerer: str
    #: Every early question at the start, and those at or above the floor.
    listed: int
    above_floor: int
    #: How many questions the rounds showed and the submitter answered.
    asked: int
    rounds: int
    #: The choices the rounds asked of a person in all, and the most in one
    #: round: a facet is one choice, and any other question one.
    decisions: int
    widest: int
    #: Questions that reached the floor only after an answer, and questions at
    #: the floor at the start that were never asked: an answer took them away,
    #: or their kind's limit cut them.
    added: int
    removed: int
    ended: bool


def _eligible(listed: Sequence[EarlyQuestion]) -> set[UnknownKey]:
    """The questions at or above their kind's floor, before any answer."""
    return {
        question.key
        for question in listed
        if question.score
        >= EARLY_RULES["capability" if question.kind == "capability" else "field"].floor
    }


def _admitted(asked: QuestionSet, answered: list[FactAnswer], given: list[FactAnswer]):
    """``given`` as the service saves it; a refused answer becomes "I don't know"."""
    kept: list[FactAnswer] = []
    for answer in given:
        try:
            asked.admit(
                sources=[],
                earlier_links=[],
                earlier_facts=answered,
                links=[],
                facts=[*kept, answer],
                save=True,
            )
        except ValueError:
            question = next(q for q in asked.early if q.key == answer.key)
            answer = _dont_know(question)
        kept.append(answer)
    return kept


def replay(
    case: str,
    extracted: SystemModel,
    blessed: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    name: str,
) -> Replay:
    """Answer ``extracted``'s early rounds, as the service shows them, with one answerer."""
    answerer = ANSWERERS[name]
    answered: list[FactAnswer] = []
    listed = early_questions(extracted, frameworks, None)
    first = _eligible(listed)
    seen: set[UnknownKey] = set()
    asked_keys: set[UnknownKey] = set()
    per_round: list[int] = []
    rounds = 0
    for rounds in range(MAX_ROUNDS):
        asked = question_set(
            extracted,
            None,
            frameworks,
            [],
            waiting=True,
            answered=answered,
            answered_links=[],
            final=False,
            shown=[],
        )
        if asked.done:
            break
        seen |= {question.key for question in asked.early}
        per_round.append(sum(question.decisions for question in asked.early))
        given = [answerer(question, blessed) for question in asked.early]
        answered = merged_facts(answered, _admitted(asked, answered, given))
        asked_keys |= {question.key for question in asked.early}
    else:
        rounds = MAX_ROUNDS
    return Replay(
        case=case,
        framework=",".join(frameworks),
        answerer=name,
        listed=len(listed),
        above_floor=len(first),
        asked=len(asked_keys),
        rounds=rounds,
        decisions=sum(per_round),
        widest=max(per_round, default=0),
        added=len(seen - first),
        removed=len(first - asked_keys),
        ended=rounds < MAX_ROUNDS,
    )


def replays(root: Path, corpus: Path) -> list[Replay]:
    """Every archived report under ``root`` whose case has a blessed model."""
    found: list[Replay] = []
    for path in sorted(root.rglob("*.report.json")):
        case = path.name.removesuffix(".report.json")
        blessed_path = corpus / case / "model.json"
        if not blessed_path.is_file():
            continue
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        blessed = SystemModel.model_validate_json(
            blessed_path.read_text(encoding="utf-8")
        )
        frameworks = {
            selection.name: selection.options for selection in report.job.frameworks
        }
        found.extend(
            replay(case, report.system_model, blessed, frameworks, name)
            for name in ANSWERERS
        )
    return found


#: The figures a summary reports, each read off a replay.
FIGURES: Mapping[str, Callable[[Replay], int]] = {
    "listed": lambda row: row.listed,
    "above_floor": lambda row: row.above_floor,
    "asked": lambda row: row.asked,
    "rounds": lambda row: row.rounds,
    "decisions": lambda row: row.decisions,
    "widest": lambda row: row.widest,
    "added": lambda row: row.added,
    "removed": lambda row: row.removed,
}


def summary(found: list[Replay]) -> dict[str, dict[str, object]]:
    """Each framework and answerer's figures, as medians and maxima."""
    groups: dict[str, list[Replay]] = {}
    for one in found:
        groups.setdefault(f"{one.framework} / {one.answerer}", []).append(one)
    table = {}
    for group, rows in sorted(groups.items()):
        figures: dict[str, object] = {"models": len(rows)}
        for field, read in FIGURES.items():
            values = [read(row) for row in rows]
            figures[field] = {
                "median": statistics.median(values),
                "max": max(values),
            }
        drift = [abs(row.asked - row.above_floor) for row in rows]
        figures["asked_within_2_of_start"] = round(
            sum(1 for value in drift if value <= 2) / len(rows), 3
        )
        figures["not_ended"] = sum(1 for row in rows if not row.ended)
        table[group] = figures
    return table


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("evals"),
        help="the directory whose archived *.report.json files are replayed",
    )
    parser.add_argument(
        "--corpus", type=Path, default=Path("evals/corpus"), help="the corpus"
    )
    parser.add_argument(
        "--out", type=Path, help="write every replay and the summary here as JSON"
    )


def command_early_rounds(args: argparse.Namespace) -> int:
    """Replay the rounds on every archived model, and print the summary."""
    found = replays(args.root, args.corpus)
    if not found:
        print("no archived report has a case with a blessed model", file=sys.stderr)
        return 1
    table = summary(found)
    print(json.dumps(table, indent=2))
    if args.out is not None:
        args.out.write_text(
            json.dumps(
                {"summary": table, "replays": [asdict(one) for one in found]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0
