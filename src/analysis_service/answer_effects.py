"""What an answer to one early question proves about the others (#1632).

A paused job publishes, for each listed capability question, what each of its
answers would settle, so a page can say "a 'no' may remove 19 questions", and
the list breaks a tie between two questions by it. "Settles" means that a
rule the service already runs proves it:
an absent capability makes each of its parts absent
(:func:`~analysis_service.capabilities.resolve`), and a unit whose rule an
answer decides no longer needs the other capabilities its rule reads
(``open_capabilities`` of each **Framework Package**). A question that only
names the same concept proves nothing.

**The effect is read with the production rules, on a copy.** For each
possible answer to a capability question, the capability statement the
answer would write (:func:`~analysis_service.fact_writes.capability_statements`)
is written onto a copy of the model, and each runnable framework's open
capabilities are read again. The questions that leave the pool are what the
answer settles; the questions that join the recommended list are what it
unlocks. A field question has no rule that removes another question, so it has
no effect here and keeps its ranking (:mod:`analysis_service.early_questions`).

**This is potential, not expectation.** A question's potential is the most
it settles over its answers, and its least is the fewest. No probability is
assumed for either answer, and a lower score or a move under a floor earns no
credit.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis_service.answer_forms import YES_NO
from analysis_service.claims import FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, waiting_parts
from analysis_service.fact_answers import FactAnswer, key_ref
from analysis_service.fact_writes import capability_statements
from analysis_service.frameworks import PACKAGES
from analysis_service.memo import Memo, remembered
from analysis_service.system_model import SystemModel

__all__ = ["AnswerEffect", "Effect", "capability_effects"]


@dataclass(frozen=True)
class AnswerEffect:
    """What one possible answer proves about the other questions."""

    #: Other open questions the answer proves settled or unnecessary: each
    #: one a choice the submitter no longer makes.
    settles: int
    #: Questions the answer brings into the recommended list, such as the
    #: parts of a capability its "yes" shows.
    unlocks: int

    def to_json(self) -> dict[str, int]:
        return {"settles": self.settles, "unlocks": self.unlocks}


@dataclass(frozen=True)
class Effect:
    """The effect of each possible answer to one question, by answer."""

    answers: Mapping[str, AnswerEffect]

    @property
    def potential(self) -> int:
        """The most other questions an answer settles."""
        return max(effect.settles for effect in self.answers.values())

    @property
    def least(self) -> int:
        """The fewest other questions an answer settles."""
        return min(effect.settles for effect in self.answers.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "settles_at_most": self.potential,
            "settles_at_least": self.least,
            "answers": {
                answer: effect.to_json() for answer, effect in self.answers.items()
            },
        }


def _pool(
    model: SystemModel, frameworks: Mapping[FrameworkName, Mapping[str, Any]]
) -> frozenset[str]:
    """Every capability a runnable framework still needs an answer to."""
    return frozenset(
        key
        for name in frameworks
        for key in PACKAGES[name].record.open_capabilities(model, frameworks[name])
    )


def _listed(pool: frozenset[str], settled: Collection[str]) -> frozenset[str]:
    """The pool's capabilities the list holds: each one that waits on no parent."""
    return pool - waiting_parts(pool, settled)


_effects: Memo[dict[UnknownKey, Effect]] = OrderedDict()


def capability_effects(
    prepared: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    questions: Sequence[EarlyQuestion],
    settled: Collection[str],
) -> dict[UnknownKey, Effect]:
    """The effect of each possible answer to each of ``questions``.

    ``prepared`` is the model the lanes read, with the saved answers in;
    ``frameworks`` the selected frameworks that will run, with their options;
    ``questions`` the capability questions to read; and ``settled`` the
    capabilities a save answered or skipped, which say which parts are listed
    now. Each effect is read against the pool as it stands, so a part an
    earlier answer settled is not counted again. The table is kept by the
    content it was read from (:func:`~analysis_service.memo.remembered`),
    so a read that changes no answer reads it again for nothing.
    """
    return remembered(
        _effects,
        (
            prepared.model_dump_json(),
            json.dumps(frameworks, sort_keys=True, default=str),
            json.dumps([list(question.key) for question in questions]),
            json.dumps(sorted(settled)),
        ),
        lambda: _capability_effects(prepared, frameworks, questions, settled),
        held=(PACKAGES,),
    )


def _capability_effects(
    prepared: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    questions: Sequence[EarlyQuestion],
    settled: Collection[str],
) -> dict[UnknownKey, Effect]:
    """The rule :func:`capability_effects` keeps the result of."""
    before = _pool(prepared, frameworks)
    listed = _listed(before, settled)
    effects = {}
    for question in questions:
        key = key_ref(question.key).capability
        others = before - {key}
        by_answer = {}
        for value in YES_NO:
            answer = FactAnswer(key=question.key, value=value)
            view = prepared.model_copy(
                update={
                    "capabilities": capability_statements(
                        prepared.capabilities, [answer]
                    )
                }
            )
            after = _pool(view, frameworks)
            by_answer[value] = AnswerEffect(
                settles=len(others - after),
                unlocks=len(_listed(after, {key, *settled}) - listed - {key}),
            )
        effects[question.key] = Effect(by_answer)
    return effects
