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

**Only what is open is asked.** An attribute or a zone is asked where
:func:`~analysis_service.questions.open_attribute` says it is open, the rule
the answer check reads too. A question kind has no field, so each kind the
prior names for the element's type is asked.

**The prior decides which fields are asked, as well as their order.** Asking
every open attribute and zone the prior does not name added about 18% to the
list and covered one more finding in 337 (``QA-2026-09-26-03-E17``).

**The prior is a table with its provenance.** ``question_prior.json`` holds one
row per framework in :data:`~analysis_service.frameworks.PACKAGES`, and each
row names the runs it was counted from. ``run.py question-prior`` writes a
row. A framework whose row counted no run asks nothing ranked by it.

**A capability question needs no prior.** A framework whose units apply by a
rule over capabilities counts how many units each unknown capability could
settle, and those questions come first (:func:`capability_questions`).

An answer takes the path a fact answer after the report takes: a
:class:`~analysis_service.answer_round.QuestionSet` admits it, and the resumed
job writes it (#1252).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from analysis_service.assertions import AssertionCatalog
from analysis_service.candidates import generate_candidates
from analysis_service.capabilities import CAPABILITIES, lineage
from analysis_service.claims import (
    FrameworkName,
    UnknownKey,
    UnknownRef,
)
from analysis_service.frameworks import PACKAGES
from analysis_service.open_facts import element_names, group_of, label_of
from analysis_service.question_kinds import QUESTION_KINDS, Facet
from analysis_service.questions import (
    YES_NO,
    AnswerForm,
    FactKind,
    answer_choices,
    answer_facets,
    answer_form,
    answer_limit,
    answer_suggestions,
    facets_json,
    fact_kind,
    open_attribute,
    prepared_model,
)
from analysis_service.system_model import Element, SystemModel

__all__ = [
    "QUESTION_PRIOR",
    "QUESTION_PRIOR_PATH",
    "EarlyQuestion",
    "PriorRow",
    "capability_questions",
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
    #: How a page takes the answer; see :data:`~analysis_service.questions.AnswerForm`.
    form: AnswerForm
    #: Common mechanisms a ``control`` answer may start from.
    suggestions: tuple[str, ...]
    #: The parts a ``facets`` answer is given in.
    facets: tuple[Facet, ...]
    #: The longest answer it admits (:func:`~analysis_service.questions.answer_limit`).
    max_length: int
    #: The group a page shows it in, the group's heading, and the element's
    #: name as its row in that group (:func:`~analysis_service.open_facts.group_of`).
    group: str
    group_heading: str
    element: str
    #: The value the list is ranked by. For a field, the prior's rate for the
    #: field on the element's type times one plus the candidates the rules
    #: raise on the element, summed over the frameworks: a ranking heuristic,
    #: not a measured count of the findings an answer changes. For a
    #: capability, how many units it could settle. The list is in this order,
    #: capabilities first.
    score: float = 0.0
    #: For a capability, the band of the most important unit it could settle,
    #: as its framework ranks units (level 1 highest for ASVS); 0 for every
    #: other question. :func:`~analysis_service.answer_round.passes_floor`
    #: reads it.
    band: int = 0
    #: The key of the question this one depends on: a capability's parent,
    #: whose "no" makes this one moot. ``None`` for every other question.
    parent: UnknownKey | None = None

    @property
    def decisions(self) -> int:
        """The choices a person makes to answer it: one a facet, else one."""
        return len(self.facets) or 1

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "label": self.label,
            "reasons": list(self.reasons),
            "choices": list(self.choices),
            "form": self.form,
            "suggestions": list(self.suggestions),
            "facets": facets_json(self.facets),
            "max_length": self.max_length,
            "decisions": self.decisions,
            "group": self.group,
            "group_heading": self.group_heading,
            "element": self.element,
            "parent": None if self.parent is None else list(self.parent),
        }


def _open_key(model: SystemModel, element: Element, field: str) -> UnknownKey | None:
    """The fact this field asks about this element, or ``None`` where it is not open."""
    if field in QUESTION_KINDS:
        return UnknownRef(element_id=element.id, question=field).key
    if open_attribute(model, element.id, field):
        return UnknownRef(element_id=element.id, attribute=field).key
    return None


def early_questions(
    model: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    catalog: AssertionCatalog | None,
    prior: Mapping[FrameworkName, PriorRow] = QUESTION_PRIOR,
) -> tuple[EarlyQuestion, ...]:
    """Every open fact the prior names for this model, the most likely cited first.

    ``frameworks`` maps each framework the job selected to its options. Read
    off the model with the catalog applied, which is the model the lanes will
    read, so a fact the catalog states is not asked. The capability questions
    come first (:func:`capability_questions`).
    """
    model = prepared_model(model, catalog)
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
    asked = []
    for key in sorted(score, key=lambda key: (-score[key], key)):
        ref = UnknownRef(element_id=key[0], attribute=key[1], question=key[4])
        group, heading = group_of(ref)
        asked.append(
            EarlyQuestion(
                key=key,
                kind=fact_kind(key),
                label=label_of(ref, names),
                reasons=tuple(reasons.get(key[0], ())),
                choices=answer_choices(key, model, catalog),
                form=answer_form(key, model, catalog),
                suggestions=answer_suggestions(key),
                facets=answer_facets(key),
                max_length=answer_limit(key, model),
                group=group,
                group_heading=heading,
                element=names.get(key[0], key[0]),
                score=score[key],
            )
        )
    return (*capability_questions(model, frameworks), *asked)


def capability_questions(
    model: SystemModel, frameworks: Mapping[FrameworkName, Mapping[str, Any]]
) -> tuple[EarlyQuestion, ...]:
    """Every unknown capability a selected framework's applicability rule needs.

    **No prior decides these.** A framework's own rule says which of its units
    an answer settles, so the need is proven before any run. Each question
    states how many units it could settle, summed over the frameworks.

    **The most important first, and a parent before its children.** Each
    question is ordered by the band of the most important unit it could
    settle, then by its count, as the report's follow-up is (ADR 0055). A
    parent's band and count are never below its child's, because it settles
    every unit the child does, and a tie goes to the shallower question, so
    every parent comes before its children without ordering by tree. A child
    names its parent, so a page can hide it until the parent is answered
    "yes", and the answer check refuses a "yes" under a "no".
    """
    counts: Counter[str] = Counter()
    bands: dict[str, int] = {}
    for name, options in frameworks.items():
        for key, need in (
            PACKAGES[name].record.open_capabilities(model, options).items()
        ):
            counts[key] += need.units
            bands[key] = max(bands.get(key, need.band), need.band)

    def asked_parent(key: str) -> str:
        return next((parent for parent in lineage(key) if parent in counts), "")

    def depth(key: str) -> int:
        parent = asked_parent(key)
        return depth(parent) + 1 if parent else 0

    order = sorted(
        counts,
        key=lambda key: (-bands[key], -counts[key], depth(key), key),
    )
    questions = []
    for key in order:
        ref = UnknownRef(capability=key)
        group, heading = group_of(ref)
        parent = asked_parent(key)
        questions.append(
            EarlyQuestion(
                key=ref.key,
                kind="capability",
                label=CAPABILITIES[key].question,
                reasons=(
                    f"An answer settles whether up to {counts[key]} units apply.",
                ),
                choices=YES_NO,
                form="choice",
                suggestions=(),
                facets=(),
                max_length=answer_limit(ref.key, model),
                group=group,
                group_heading=heading,
                element=CAPABILITIES[key].question,
                score=float(counts[key]),
                band=bands[key],
                parent=UnknownRef(capability=parent).key if parent else None,
            )
        )
    return tuple(questions)
