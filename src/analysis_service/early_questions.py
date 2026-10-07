"""The open facts a job asks before its analysis, at the pause after extraction.

A question after the report is ranked by the findings that wait on it. Before
the analysis there are no findings, so this module ranks each element's open
attributes, its assumed zone and the question kinds by two facts the paused
job does hold (``QA-2026-09-26-03-E13``):

* **the prior**: how often findings in earlier runs cite that attribute or
  kind, per element of that type, for each framework the job selected;
* **the candidates**: how many of that framework's rules fire on the element,
  in the lanes the job runs. A lane the job's options leave idle
  (:meth:`~analysis_service.claims.Claim.idle`) leads nothing.

A question's score is the prior times one plus the candidates, summed over
the job's frameworks. On 13 tuned cases that order settled about 60% of what
the report's own order settled, at six and at ten questions a case. The count
cannot see the main reason to ask early: the lanes then write their findings
with the answer in hand.

**The list ranks a question by its score per choice.** A question answered in
facets costs a choice a facet, so a table of four facets must score four
times a one-choice question to come before it. Ranked by score alone, a
STRIDE-only pause opened with tables, and an owner's first ten choices
completed 517 of 2,744 conditional findings on the archive; ranked per
choice, 1,488 (``QA-2026-09-26-03-E33``). The floor still reads the score.

**Only what is open is asked.** An attribute or a zone is asked where
:func:`~analysis_service.open_facts.open_attribute` says it is open, the rule
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

**A framework that will not run asks nothing** (ADR 0067). Each selected
framework's precondition is read first, through the gate ``prepare`` runs
(:func:`framework_gates`). A refuted framework adds no question and no score.
An undecidable one asks only the facts its package says decide it
(``precondition_facts``), ahead of every other question; its own questions
wait until an answer satisfies it. A question another framework asks is still
asked for that framework.

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

from analysis_service.answer_forms import (
    YES_NO,
    AnswerForm,
    answer_choices,
    answer_form,
    answer_limit,
    answer_suggestions,
    facets_json,
)
from analysis_service.assertions import AssertionCatalog
from analysis_service.candidates import generate_candidates
from analysis_service.capabilities import CAPABILITIES, lineage
from analysis_service.claims import (
    FrameworkName,
    UnknownKey,
    UnknownRef,
)
from analysis_service.fact_answers import FactKind, answer_facets, fact_kind, key_ref
from analysis_service.frameworks import PACKAGES, PreconditionResult, run_precondition
from analysis_service.open_facts import (
    element_names,
    group_of,
    label_of,
    open_attribute,
    prepared_model,
)
from analysis_service.question_kinds import QUESTION_KINDS, Facet
from analysis_service.system_model import Element, SystemModel

__all__ = [
    "GATE_REASON",
    "PRIOR_REASON",
    "QUESTION_PRIOR",
    "QUESTION_PRIOR_PATH",
    "EarlyQuestion",
    "PriorRow",
    "capability_questions",
    "early_questions",
    "element_type",
    "framework_gates",
    "load_prior",
]

QUESTION_PRIOR_PATH = Path(__file__).with_name("question_prior.json")

#: The reason a field question gives where no rule reads its fact: the prior,
#: which is why it is asked at all.
PRIOR_REASON = "Findings in earlier analyses often depend on this fact."

#: The reason a gate question gives, once for each framework it decides.
GATE_REASON = "Whether the {framework} analysis runs depends on this fact."


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
    #: Why the fact matters before any finding exists: the questions of the
    #: rules that read this fact on this element, or :data:`PRIOR_REASON`
    #: where no rule reads it. Never a rule that only names the element.
    reasons: tuple[str, ...]
    #: The selected frameworks an answer serves, by name, in name order.
    frameworks: tuple[FrameworkName, ...]
    #: The answers it takes, or empty where the answer is free text.
    choices: tuple[str, ...]
    #: How a page takes the answer; see :data:`~analysis_service.answer_forms.AnswerForm`.
    form: AnswerForm
    #: Common mechanisms a ``control`` answer may start from.
    suggestions: tuple[str, ...]
    #: The parts a ``facets`` answer is given in.
    facets: tuple[Facet, ...]
    #: The longest answer it admits (:func:`~analysis_service.answer_forms.answer_limit`).
    max_length: int
    #: The group a page shows it in, the group's heading, and the element's
    #: name as its row in that group (:func:`~analysis_service.open_facts.group_of`).
    group: str
    group_heading: str
    element: str
    #: The value the list is ranked from. For a field, the prior's rate for the
    #: field on the element's type times one plus the candidates the rules
    #: raise on the element, summed over the frameworks: a ranking heuristic,
    #: not a measured count of the findings an answer changes. For a
    #: capability, how many units it could settle. The list ranks a field
    #: question by this value per choice (:attr:`decisions`), capabilities
    #: first; a round reorders its own questions with the
    #: frameworks in turn (:func:`~analysis_service.answer_round.by_turn`).
    score: float = 0.0
    #: For a capability, the band of the most important unit it could settle,
    #: as its framework ranks units (level 1 highest for ASVS); 0 for every
    #: other question. :func:`~analysis_service.answer_round.passes_floor`
    #: reads it.
    band: int = 0
    #: The key of the question this one depends on: a capability's parent,
    #: whose "no" makes this one moot. ``None`` for every other question.
    parent: UnknownKey | None = None
    #: The selected frameworks whose undecidable precondition this fact can
    #: decide, by name. A round asks every such question first, outside the
    #: limits (:func:`~analysis_service.answer_round.next_round`). Empty for
    #: every other question.
    gates: tuple[FrameworkName, ...] = ()

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
            "frameworks": list(self.frameworks),
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
            "gates": list(self.gates),
        }


def _open_key(model: SystemModel, element: Element, field: str) -> UnknownKey | None:
    """The fact this field asks about this element, or ``None`` where it is not open."""
    if field in QUESTION_KINDS:
        return UnknownRef(element_id=element.id, question=field).key
    if open_attribute(model, element.id, field):
        return UnknownRef(element_id=element.id, attribute=field).key
    return None


def framework_gates(
    model: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    catalog: AssertionCatalog | None,
) -> Mapping[FrameworkName, PreconditionResult]:
    """Each selected framework's precondition, read off the model the lanes will read.

    **The gate ``prepare`` runs**, through the same
    :func:`~analysis_service.frameworks.run_precondition`, over the model with
    the answers written in and the catalog applied, which is the model a
    resumed job's ``prepare`` reads.
    """
    return _gates(prepared_model(model, catalog), frameworks)


def _gates(
    prepared: SystemModel, frameworks: Mapping[FrameworkName, Mapping[str, Any]]
) -> dict[FrameworkName, PreconditionResult]:
    return {
        name: run_precondition(PACKAGES[name], prepared) for name in sorted(frameworks)
    }


def early_questions(
    model: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    catalog: AssertionCatalog | None,
    prior: Mapping[FrameworkName, PriorRow] = QUESTION_PRIOR,
) -> tuple[EarlyQuestion, ...]:
    """Every open fact the prior names for this model, the most likely cited first.

    ``frameworks`` maps each framework the job selected to its options. Read
    off the model with the catalog applied, which is the model the lanes will
    read, so a fact the catalog states is not asked. The questions that decide
    an undecidable framework come first (:func:`framework_gates`), then the
    capability questions (:func:`capability_questions`). Only a framework
    whose precondition is satisfied ranks the capability and field questions.
    """
    model = prepared_model(model, catalog)
    gates = _gates(model, frameworks)
    runnable = {
        name: frameworks[name] for name, state in gates.items() if state == "satisfied"
    }
    names = element_names(model)
    deciding = _gate_questions(model, gates, catalog, names)
    decided = {question.key for question in deciding}
    score: dict[UnknownKey, float] = {}
    helps: dict[UnknownKey, list[FrameworkName]] = {}
    reasons: dict[tuple[str, str], list[str]] = {}
    for name in runnable:
        package = PACKAGES[name]
        asks = {rule.rule_id: rule.question for rule in package.rules}
        named: Counter[str] = Counter()
        for lane, found in generate_candidates(
            model, package.lanes, package.rules, catalog
        ).items():
            if package.record.idle(model, frameworks[name], lane):
                continue
            for candidate in found.candidates:
                named.update(set(candidate.element_ids))
                for element_id in candidate.element_ids:
                    for field in candidate.facts:
                        said = reasons.setdefault((element_id, field), [])
                        if asks[candidate.rule_id] not in said:
                            said.append(asks[candidate.rule_id])
        for element in model.elements():
            rates = prior[name].rates.get(element_type(element), {})
            for field, rate in rates.items():
                key = _open_key(model, element, field)
                if key is not None and key not in decided:
                    score[key] = score.get(key, 0.0) + rate * (1 + named[element.id])
                    helps.setdefault(key, []).append(name)
    asked = []
    for key, value in score.items():
        ref = key_ref(key)
        asked.append(
            _question(
                key,
                model,
                catalog,
                names,
                reasons=tuple(
                    reasons.get((ref.element_id, ref.attribute), ()) or (PRIOR_REASON,)
                ),
                frameworks=tuple(helps[key]),
                score=value,
            )
        )
    asked.sort(
        key=lambda question: (-question.score / question.decisions, question.key)
    )
    return (*deciding, *capability_questions(model, runnable), *asked)


def _question(
    key: UnknownKey,
    model: SystemModel,
    catalog: AssertionCatalog | None,
    names: Mapping[str, str],
    *,
    reasons: tuple[str, ...],
    frameworks: tuple[FrameworkName, ...],
    score: float = 0.0,
    gates: tuple[FrameworkName, ...] = (),
) -> EarlyQuestion:
    """One early question about an element's fact, with the form its answer takes."""
    ref = key_ref(key)
    group, heading = group_of(ref)
    return EarlyQuestion(
        key=key,
        kind=fact_kind(key),
        label=label_of(ref, names),
        reasons=reasons,
        frameworks=frameworks,
        choices=answer_choices(key, model, catalog),
        form=answer_form(key, model, catalog),
        suggestions=answer_suggestions(key),
        facets=answer_facets(key),
        max_length=answer_limit(key, model),
        group=group,
        group_heading=heading,
        element=names.get(ref.element_id, ref.element_id),
        score=score,
        gates=gates,
    )


def _gate_questions(
    model: SystemModel,
    gates: Mapping[FrameworkName, PreconditionResult],
    catalog: AssertionCatalog | None,
    names: Mapping[str, str],
) -> tuple[EarlyQuestion, ...]:
    """The open facts that can decide each undecidable framework, in its package's order.

    **No prior and no floor decide these.** The framework runs only once one
    of them is answered, so each is asked whatever its rank would be. An
    attribute is asked only where :func:`~analysis_service.open_facts.open_attribute`
    says it is open, the rule the answer check reads too.
    """
    deciding: dict[UnknownKey, list[FrameworkName]] = {}
    for name, state in gates.items():
        if state != "undecidable":
            continue
        for ref in PACKAGES[name].precondition_facts(model):
            if ref.attribute and not open_attribute(
                model, ref.element_id, ref.attribute
            ):
                continue
            deciding.setdefault(ref.key, []).append(name)
    return tuple(
        _question(
            key,
            model,
            catalog,
            names,
            reasons=tuple(GATE_REASON.format(framework=name) for name in held),
            frameworks=tuple(held),
            gates=tuple(held),
        )
        for key, held in deciding.items()
    )


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
    helps: dict[str, list[FrameworkName]] = {}
    for name in sorted(frameworks):
        for key, need in (
            PACKAGES[name].record.open_capabilities(model, frameworks[name]).items()
        ):
            counts[key] += need.units
            bands[key] = max(bands.get(key, need.band), need.band)
            helps.setdefault(key, []).append(name)

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
                frameworks=tuple(helps[key]),
                score=float(counts[key]),
                band=bands[key],
                parent=UnknownRef(capability=parent).key if parent else None,
            )
        )
    return tuple(questions)
