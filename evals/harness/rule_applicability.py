"""The applicability benchmark: the rule's decisions, and the questions that settle them.

``applicability.py`` in this directory scores which requirements a run's claims
applied, and a claim's verdict mixes in whether the input shows the control.
This module scores the applicability rule alone (ADR 0051): given what a
fixture's sources and answers state about each **Capability**, does each
requirement come out ``applicable``, ``not-applicable`` or ``unknown`` as the
fixture's label says?

**The labels are a person's.** A fixture under ``evals/applicability/`` names
who drafted it and who signed it. An unsigned fixture is scored and reported
as unsigned, because a figure against labels nobody reviewed measures the
drafter's reading and not the rule.

**False exclusions are the safety figure.** A requirement ruled out that the
label says applies, or leaves open, is analysis the job silently skipped.

Every requirement coverage and fact coverage figure the issue asks for holds
by construction: each package checks its table against its catalog at import,
and every rule term names a capability with a question. So they are not
figures here; ``covered`` checks that each fixture got one entry per unit.

The question simulation asks a fixture's early capability questions in the
rounds a paused job serves them, answers each from the fixture's ``truth``, and
counts how many unknown requirements each answer settled. A later round is read
off the model with the earlier answers in, and a part whose parent its round
answers "no" is not asked, as the pause page hides it. Nothing here calls a
model, so every figure costs nothing.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis_service.answer_round import next_round
from analysis_service.capabilities import CAPABILITIES
from analysis_service.claims import FrameworkName, UnknownKey, UnknownRef
from analysis_service.early_questions import capability_questions
from analysis_service.fact_answers import FactAnswer, key_ref
from analysis_service.fact_writes import answered_model
from analysis_service.frameworks import PACKAGES
from analysis_service.system_model import CapabilityStatement, SystemModel

__all__ = [
    "FIXTURES_DIR",
    "Fixture",
    "FixtureScore",
    "QuestionRun",
    "arguments",
    "command_rule_applicability",
    "load_fixtures",
    "score_fixture",
    "simulate_questions",
]

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "applicability"

#: The label a fixture's source statements cite.
_LABEL = "Fixture source"

#: How a truth state answers a yes-or-no capability question.
_ANSWER = {"present": "yes", "absent": "no", "unknown": "unknown"}


@dataclass(frozen=True)
class Fixture:
    """One labelled situation: what is stated, what is answered, what should follow."""

    name: str
    test_class: str
    drafted_by: str
    signed_by: str | None
    framework: FrameworkName
    options: Mapping[str, Any]
    statements: tuple[CapabilityStatement, ...]
    answers: Mapping[str, str]
    expected: Mapping[str, str]
    truth: Mapping[str, str]

    @property
    def signed(self) -> bool:
        return bool(self.signed_by)


def load_fixtures(directory: Path = FIXTURES_DIR) -> tuple[Fixture, ...]:
    """Every fixture in ``directory``, in file-name order."""
    fixtures = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        fixtures.append(
            Fixture(
                name=path.stem,
                test_class=raw["test_class"],
                drafted_by=raw["drafted_by"],
                signed_by=raw["signed_by"],
                framework=raw["framework"],
                options=raw["options"],
                statements=tuple(
                    CapabilityStatement(
                        capability=row["capability"],
                        state=row["state"],
                        source_excerpt=row["quote"],
                        source_label=_LABEL,
                    )
                    for row in raw.get("statements", [])
                ),
                answers=raw.get("answers", {}),
                expected=raw["expected"],
                truth=raw.get("truth", {}),
            )
        )
    return tuple(fixtures)


def _answers(values: Mapping[str, str]) -> list[FactAnswer]:
    return [
        FactAnswer(key=UnknownRef(capability=key).key, value=value)
        for key, value in values.items()
    ]


def _model(fixture: Fixture) -> SystemModel:
    stated = SystemModel(capabilities=list(fixture.statements))
    return answered_model(stated, _answers(fixture.answers))


def _decisions(fixture: Fixture, model: SystemModel) -> dict[str, str]:
    record = PACKAGES[fixture.framework].record
    entries = record.applicability(model, fixture.options)
    return {entry.unit: entry.state for entry in entries}


def _selected(fixture: Fixture) -> set[str]:
    package = PACKAGES[fixture.framework]
    return {
        unit
        for lane in package.lanes
        for unit in package.record.units_for(fixture.options, lane) or ()
    }


@dataclass(frozen=True)
class FixtureScore:
    """One fixture's labelled rows against the rule's decisions."""

    name: str
    signed: bool
    adjudicated: int
    correct: int
    #: Labelled applicable or unknown, and ruled not-applicable.
    false_exclusions: tuple[str, ...]
    #: Labelled not-applicable, and ruled applicable.
    false_inclusions: tuple[str, ...]
    expected_unknown: int
    correct_unknown: int
    #: Every selected requirement got an entry.
    covered: bool
    #: Two evaluations of the same facts agreed.
    repeatable: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "signed": self.signed,
            "adjudicated": self.adjudicated,
            "correct": self.correct,
            "false_exclusions": list(self.false_exclusions),
            "false_inclusions": list(self.false_inclusions),
            "expected_unknown": self.expected_unknown,
            "correct_unknown": self.correct_unknown,
            "covered": self.covered,
            "repeatable": self.repeatable,
        }


def score_fixture(fixture: Fixture) -> FixtureScore:
    """Score one fixture's labelled rows."""
    got = _decisions(fixture, _model(fixture))
    missing = sorted(set(fixture.expected) - set(got))
    if missing:
        raise ValueError(
            f"{fixture.name} labels units its options do not select: {missing}"
        )
    rows = sorted(fixture.expected)
    return FixtureScore(
        name=fixture.name,
        signed=fixture.signed,
        adjudicated=len(rows),
        correct=sum(got[row] == fixture.expected[row] for row in rows),
        false_exclusions=tuple(
            row
            for row in rows
            if got[row] == "not-applicable"
            and fixture.expected[row] != "not-applicable"
        ),
        false_inclusions=tuple(
            row
            for row in rows
            if got[row] == "applicable" and fixture.expected[row] == "not-applicable"
        ),
        expected_unknown=sum(fixture.expected[row] == "unknown" for row in rows),
        correct_unknown=sum(
            fixture.expected[row] == "unknown" and got[row] == "unknown" for row in rows
        ),
        covered=set(got) == _selected(fixture),
        repeatable=got == _decisions(fixture, _model(fixture)),
    )


@dataclass(frozen=True)
class QuestionRun:
    """What asking a fixture's capability questions from its truth settled."""

    name: str
    unknown_before: int
    unknown_after: int
    asked: int
    #: Rounds the pause showed before no question was left.
    rounds: int
    #: Unknown requirements each asked question settled, in order.
    settled: tuple[int, ...]
    #: The capability each asked question named, in the same order.
    keys: tuple[str, ...]
    #: Parts not asked because their parent was answered "no".
    avoided: int
    #: Questions that named no capability of the table. Zero by construction,
    #: and counted so a change that let one through is seen.
    conformance: int

    @property
    def redundant(self) -> int:
        return sum(1 for count in self.settled if count == 0)

    def to_reach(self, share: float) -> int | None:
        """How many questions settle ``share`` of what the answers can settle."""
        answerable = self.unknown_before - self.unknown_after
        if answerable == 0:
            return None
        total = 0
        for asked, count in enumerate(self.settled, start=1):
            total += count
            if total >= share * answerable:
                return asked
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unknown_before": self.unknown_before,
            "unknown_after": self.unknown_after,
            "asked": self.asked,
            "rounds": self.rounds,
            "settled": list(self.settled),
            "redundant": self.redundant,
            "avoided": self.avoided,
            "conformance": self.conformance,
            "to_reach": {
                str(share): self.to_reach(share) for share in (0.5, 0.75, 0.9, 1.0)
            },
        }


def simulate_questions(fixture: Fixture) -> QuestionRun:
    """Answer the paused job's capability questions from the fixture's truth.

    Round by round, as :func:`~analysis_service.answer_round.next_round` serves
    them: each round is read off the model with every earlier answer in, so an
    answer drops a question it settled from every later round. Within a round,
    a part whose parent the round asks is hidden until the parent is "yes".
    """
    model = _model(fixture)
    options = {fixture.framework: fixture.options}

    def unknown(current: SystemModel) -> int:
        return sum(
            state == "unknown" for state in _decisions(fixture, current).values()
        )

    before = unknown(model)
    held: dict[UnknownKey, tuple[FrameworkName, ...]] = {}
    settled, keys, avoided, conformance, rounds = [], [], 0, 0, 0
    while True:
        shown, _, _ = next_round(
            capability_questions(model, options), frozenset(held), held
        )
        if not shown:
            break
        rounds += 1
        asked = {question.key for question in shown}
        given: dict[UnknownKey, str] = {}
        for question in shown:
            key = key_ref(question.key).capability
            if key not in CAPABILITIES:
                conformance += 1
                held[question.key] = question.frameworks
                continue
            if question.parent in asked and given.get(question.parent) != "yes":
                avoided += 1
                continue
            answer = FactAnswer(
                key=question.key, value=_ANSWER[fixture.truth.get(key, "unknown")]
            )
            given[question.key] = answer.value
            held[question.key] = question.frameworks
            count = unknown(model)
            model = answered_model(model, [answer])
            settled.append(count - unknown(model))
            keys.append(key)
    return QuestionRun(
        name=fixture.name,
        unknown_before=before,
        unknown_after=unknown(model),
        asked=len(settled),
        rounds=rounds,
        settled=tuple(settled),
        keys=tuple(keys),
        avoided=avoided,
        conformance=conformance,
    )


def report(fixtures: Sequence[Fixture]) -> dict[str, Any]:
    """Every figure over ``fixtures``, pooled apart for signed and unsigned ones."""
    scores = [score_fixture(fixture) for fixture in fixtures]
    runs = [simulate_questions(fixture) for fixture in fixtures if fixture.truth]
    pooled = {}
    for signed in (True, False):
        group = [score for score in scores if score.signed is signed]
        rows = sum(score.adjudicated for score in group)
        pooled["signed" if signed else "unsigned"] = {
            "fixtures": len(group),
            "adjudicated": rows,
            "accuracy": sum(score.correct for score in group) / rows if rows else None,
            "false_exclusions": sum(len(score.false_exclusions) for score in group),
            "false_inclusions": sum(len(score.false_inclusions) for score in group),
            "unknown_accuracy": (
                sum(score.correct_unknown for score in group)
                / sum(score.expected_unknown for score in group)
                if any(score.expected_unknown for score in group)
                else None
            ),
        }
    return {
        "pooled": pooled,
        "fixtures": [score.to_json() for score in scores],
        "questions": [run.to_json() for run in runs],
    }


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fixtures",
        default=str(FIXTURES_DIR),
        help="the directory of labelled fixtures",
    )


def command_rule_applicability(args: argparse.Namespace) -> int:
    """Print the figures. It runs no model and reads no credential."""
    print(json.dumps(report(load_fixtures(Path(args.fixtures))), indent=2))
    return 0
