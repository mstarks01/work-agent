"""The open facts a job asks before its analysis, at the pause after extraction.

A question after the report is ranked by the findings that wait on it. Before
the analysis there are no findings, so this module ranks each element's open
attributes, its assumed zone and the question kinds by two facts the paused
job does hold (``QA-2026-09-26-03-E13``):

* **the prior**: how often findings in earlier runs cite that attribute or
  kind, per element of that type, for each framework the job selected;
* **the candidates**: how many of that framework's rules fire on the element.

A question's score is the prior times one plus the candidates, summed over
the job's frameworks. On 13 tuned cases that order settled about 60% of what
the report's own order settled, at six and at ten questions a case. The count
cannot see the main reason to ask early: the lanes then write their findings
with the answer in hand.

**Only what is open is asked.** An attribute is asked where the model holds
``unknown``, and a zone where the service inferred it. A question kind has no
field, so each kind the prior names for the element's type is asked.

**The prior is a table with its provenance.** ``question_prior.json`` holds one
row per framework in :data:`~analysis_service.frameworks.PACKAGES`, and each
row names the runs it was counted from. ``run.py question-prior`` writes a
row. A framework whose row counted no run asks nothing early.

An answer takes the path a fact answer after the report takes: the answers
route checks it with :func:`~analysis_service.questions.check_fact_answers`,
and the resumed job writes it (#1252).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from analysis_service.assertions import AssertionCatalog
from analysis_service.candidates import generate_candidates
from analysis_service.claims import FrameworkName, UnknownKey, UnknownRef
from analysis_service.frameworks import PACKAGES
from analysis_service.open_facts import element_names, label_of
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import FactKind, answer_choices, fact_kind
from analysis_service.system_model import UNKNOWN, ZONE_ATTRIBUTE, Element, SystemModel

__all__ = [
    "QUESTION_PRIOR",
    "QUESTION_PRIOR_PATH",
    "EarlyQuestion",
    "PriorRow",
    "early_questions",
    "element_type",
    "load_prior",
]

QUESTION_PRIOR_PATH = Path(__file__).with_name("question_prior.json")


@dataclass(frozen=True)
class PriorRow:
    """One framework's prior, and the runs it was counted from."""

    #: The archived runs the rates were counted from, as ``run.py`` was given them.
    runs: tuple[str, ...]
    #: The commit the command ran at, or ``None`` where no run was counted.
    revision: str | None
    #: The tuned cases counted. A holdout case is never counted.
    cases: int
    #: Citations of each attribute or kind, per element of each type.
    rates: Mapping[str, Mapping[str, float]]

    def to_json(self) -> dict[str, object]:
        return {
            "runs": list(self.runs),
            "revision": self.revision,
            "cases": self.cases,
            "rates": {kind: dict(fields) for kind, fields in self.rates.items()},
        }


def load_prior(path: Path = QUESTION_PRIOR_PATH) -> Mapping[FrameworkName, PriorRow]:
    """The prior table, one row per framework the file holds."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return MappingProxyType(
        {
            name: PriorRow(
                runs=tuple(row["runs"]),
                revision=row["revision"],
                cases=row["cases"],
                rates=MappingProxyType(
                    {
                        kind: MappingProxyType(fields)
                        for kind, fields in row["rates"].items()
                    }
                ),
            )
            for name, row in raw.items()
        }
    )


QUESTION_PRIOR = load_prior()


def element_type(element: Element) -> str:
    """The type an element's prior is keyed by: its class name, never its ID."""
    return type(element).__name__


@dataclass(frozen=True)
class EarlyQuestion:
    """One open fact asked before the analysis, and why it is asked."""

    key: UnknownKey
    kind: FactKind
    #: What a reader sees: an element's name and the attribute, or the kind's
    #: question about the element.
    label: str
    #: The questions of the rules that fire on the element, which say why the
    #: fact matters before any finding exists.
    reasons: tuple[str, ...]
    #: The answers it takes, or empty where the answer is free text.
    choices: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "label": self.label,
            "reasons": list(self.reasons),
            "choices": list(self.choices),
        }


def _open_key(model: SystemModel, element: Element, field: str) -> UnknownKey | None:
    """The fact this field asks about this element, or ``None`` where it is not open."""
    if field in QUESTION_KINDS:
        return UnknownRef(element_id=element.id, question=field).key
    if field == ZONE_ATTRIBUTE:
        assumed = element.id in model.assumed_zone_elements()
        return (
            UnknownRef(element_id=element.id, attribute=field).key if assumed else None
        )
    if getattr(element, field, None) == UNKNOWN:
        return UnknownRef(element_id=element.id, attribute=field).key
    return None


def early_questions(
    model: SystemModel,
    frameworks: Sequence[FrameworkName],
    catalog: AssertionCatalog | None,
    prior: Mapping[FrameworkName, PriorRow] = QUESTION_PRIOR,
) -> tuple[EarlyQuestion, ...]:
    """Every open fact the prior names for this model, the most likely cited first."""
    score: dict[UnknownKey, float] = {}
    reasons: dict[str, list[str]] = {}
    for name in frameworks:
        package = PACKAGES[name]
        asks = {rule.rule_id: rule.question for rule in package.rules}
        named: Counter[str] = Counter()
        for found in generate_candidates(
            model, package.lanes, package.rules, catalog
        ).values():
            for candidate in found.candidates:
                named.update(set(candidate.element_ids))
                for element_id in candidate.element_ids:
                    said = reasons.setdefault(element_id, [])
                    if asks[candidate.rule_id] not in said:
                        said.append(asks[candidate.rule_id])
        for element in model.elements():
            rates = prior[name].rates.get(element_type(element), {})
            for field, rate in rates.items():
                key = _open_key(model, element, field)
                if key is not None:
                    score[key] = score.get(key, 0.0) + rate * (1 + named[element.id])
    names = element_names(model)
    return tuple(
        EarlyQuestion(
            key=key,
            kind=fact_kind(key),
            label=label_of(
                UnknownRef(element_id=key[0], attribute=key[1], question=key[4]), names
            ),
            reasons=tuple(reasons.get(key[0], ())),
            choices=answer_choices(key, model, catalog),
        )
        for key in sorted(score, key=lambda key: (-score[key], key))
    )
