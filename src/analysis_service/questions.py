"""The open facts a report's conditional findings rest on, asked of the submitter.

A ``needs-info`` verdict names the facts an answer would settle. This module
turns them into questions, ranks them, and writes the answers back (#1225).

**Every open fact is asked, in the order that settles the most.** The facts a
finding's own grounds cite come first, in an order the critic cannot change;
the facts only the critic named follow (``QA-2026-09-26-03-E7``). With the
STRIDE lane closing a conditional finding waits on several facts, and six
questions settled about a quarter of them where asking every one settled all
(``QA-2026-09-26-03-E6``). So no cap is chosen here. The next question is the
one the most still-open findings cite, and each question states how many
findings are settled once it and every question before it is answered. A
submitter answers from the top as far as they choose.

**Three kinds of fact, and an answer to each is written by code.**

* An **attribute** of an element the model left ``unknown``. The answer is
  written onto the model the resumed run analyses, and the element's notes say
  the submitter answered it.
* An **assertion** row the sources left open. The answer replaces the row with
  a stated one, quoting its line of the answers Source.
* A **question** of one of the kinds in
  :data:`~analysis_service.question_kinds.QUESTION_KINDS`, about one element,
  or a free-text **subject** where no kind fits. Nothing structured can hold
  either answer, so it reaches the lanes only as its line of the answers
  Source.

An answer to a question this service asked settles the fact, even against the
sources (the maintainer's decision of 2026-09-25 on #1225).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from analysis_service.assertions import (
    ABSENT,
    REGISTRY,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    CatalogIssue,
    SupportSpan,
    assertion_id,
)
from analysis_service.claims import FrameworkAnalysis, UnknownKey, UnknownRef
from analysis_service.open_facts import element_names, label_of
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.sources import plain_name
from analysis_service.system_model import SystemModel, attribute_names
from analysis_service.validation import validate

__all__ = [
    "ANSWERS_LABEL",
    "MAX_FACT_ANSWERS",
    "FactAnswer",
    "FactQuestion",
    "Fallback",
    "answer_choices",
    "answered_model",
    "check_fact_answers",
    "fact_kind",
    "fact_questions",
    "fact_rows",
    "question_fallback",
]

#: How many fact answers one submission carries. The largest report measured
#: raised 85 open facts, so this bounds the body above any real use.
MAX_FACT_ANSWERS = 200

#: The label of the Source the answers become. A caller's own source may not
#: use it: the job refuses two sources that share a label.
ANSWERS_LABEL = "Answers to link questions"

#: The fields a flow's ID is built from. An answer cannot move one, because the
#: flow would then carry an ID its endpoints no longer derive.
_ENDPOINTS = frozenset({"source", "destination"})

FactKind = Literal["attribute", "assertion", "question", "subject"]
Basis = Literal["evidence", "critic"]
#: One finding: its framework and its claim ID.
Finding = tuple[str, str]


class FactAnswer(BaseModel):
    """One submitter answer to one open fact, named by the fact's key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: UnknownKey
    value: str = Field(min_length=1, max_length=1000)

    @field_validator("value")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return plain_name(value)

    @property
    def kind(self) -> FactKind:
        return fact_kind(self.key)


def fact_kind(key: UnknownKey) -> FactKind:
    """Which of the four kinds of fact this key names."""
    element_id, attribute, assertion, _, question = key
    if question:
        return "question"
    if assertion:
        return "assertion"
    return "attribute" if element_id or attribute else "subject"


@dataclass(frozen=True)
class FactQuestion:
    """One open fact, the findings that wait on it, and the answers it takes."""

    key: UnknownKey
    kind: FactKind
    #: ``evidence`` where a finding's own grounds cite the fact, which does not
    #: change when the critic is sampled again; ``critic`` where only the
    #: critic's verdict names it, which does.
    basis: Basis
    #: What a reader sees: an element's name and the attribute, or the subject.
    label: str
    #: How many still-open findings cite it when it is asked.
    cited_by: int
    #: How many findings are settled once it and every question before it is
    #: answered.
    settled_so_far: int
    #: The answers it takes, or empty where the answer is free text.
    choices: tuple[str, ...]
    #: Every finding that waits on it, as ``framework/claim``. A finding is
    #: settled once every question that names it has an answer, whichever
    #: questions those are.
    findings: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "basis": self.basis,
            "label": self.label,
            "cited_by": self.cited_by,
            "settled_so_far": self.settled_so_far,
            "choices": list(self.choices),
            "findings": list(self.findings),
        }


def _closed(model: SystemModel, element_id: str, attribute: str) -> tuple[str, ...]:
    """A literal-typed attribute's legal values, except ``unknown``."""
    element = model.get(element_id)
    if element is None:
        return ()
    annotation = type(element).model_fields[attribute].annotation
    if get_origin(annotation) is not Literal:
        return ()
    return tuple(value for value in get_args(annotation) if value != UNKNOWN)


#: The answers an attribute takes, where they are not free text. Keyed by
#: attribute, so an attribute with a closed set it does not name here falls
#: back to its own annotation rather than to free text.
_ATTRIBUTE_CHOICES: Mapping[str, Callable[[SystemModel, str], tuple[str, ...]]] = (
    MappingProxyType(
        {
            "data_classification": lambda model, element_id: tuple(
                sorted(REGISTRY["data-classification"].terms)
            ),
            "trust_zone": lambda model, element_id: tuple(
                boundary.id for boundary in model.trust_boundaries
            ),
        }
    )
)


def answer_choices(
    key: UnknownKey, model: SystemModel, catalog: AssertionCatalog | None
) -> tuple[str, ...]:
    """The values an answer to this fact may take, or empty for free text."""
    element_id, attribute, assertion, _, _ = key
    kind = fact_kind(key)
    if kind == "attribute":
        special = _ATTRIBUTE_CHOICES.get(attribute)
        return (
            special(model, element_id)
            if special
            else _closed(model, element_id, attribute)
        )
    if kind == "assertion" and catalog is not None:
        row = _answered_row(catalog, assertion)
        if row is None:
            return ()
        predicate = REGISTRY.get(row.predicate)
        if predicate is None:
            return ()
        if predicate.value == "term":
            return (*sorted(predicate.terms), ABSENT)
        if predicate.value == "reference":
            return tuple(
                subject.id
                for subject in catalog.subjects
                if subject.type in predicate.refers_to
            )
    return ()


def _answered_row(catalog: AssertionCatalog, identity: str) -> Assertion | None:
    """The row an answer to the open row ``identity`` writes over, if any.

    **The one reader of "which row does this answer settle".** That is the open
    row itself, or the stated row an earlier answer wrote in its place. The
    second is the row a resumed job's checkpoint holds, and a later round
    writes it again, so that its quote reads the answers Source this round
    composed. A row the sources settled is neither, so no answer names it.
    """
    for entry in catalog.entries:
        if entry.value == UNKNOWN:
            reopened = entry
        elif any(span.source_label == ANSWERS_LABEL for span in entry.support):
            reopened = entry.model_copy(update={"value": UNKNOWN})
        else:
            continue
        if assertion_id(reopened) == identity:
            return entry
    return None


def _greedy(open_facts: Mapping[Finding, set[UnknownKey]]) -> list[UnknownKey]:
    """The facts in the order that settles the most findings, asked one by one.

    The next fact is the one the most still-open findings cite; a tie goes to
    the lower key, so one input always gives one order.
    """
    left = {finding: set(keys) for finding, keys in open_facts.items() if keys}
    order: list[UnknownKey] = []
    while left:
        cites = Counter(key for keys in left.values() for key in keys)
        key = min(cites.items(), key=lambda item: (-item[1], item[0]))[0]
        order.append(key)
        left = {finding: keys - {key} for finding, keys in left.items() if keys - {key}}
    return order


def fact_questions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    catalog: AssertionCatalog | None,
) -> tuple[FactQuestion, ...]:
    """Every open fact a report's findings rest on, the most settling first.

    **Two sections, and the first does not depend on the critic.** The
    evidence section ranks the open facts each finding's own grounds cite, for
    every finding the lanes wrote, rejected ones included. A critic that is
    sampled again rules differently about a third of the time, and a list read
    from its verdicts shared 7 of 18 questions with the list from a second
    sample; a list read from the grounds is the same for both, and settled 82%
    as many findings as each sample's own list (``QA-2026-09-26-03-E7``). The
    critic section follows, with the facts only the critic named: its
    free-text subjects, mostly, which change from one sample to the next.

    Each question counts the findings of every framework together, because one
    answer settles a fact for every framework that cites it. ``cited_by`` and
    ``settled_so_far`` count findings waiting on the fact, by their grounds or,
    for a conditional finding, by its verdict too.
    """
    evidence: dict[Finding, set[UnknownKey]] = {}
    named: dict[Finding, set[UnknownKey]] = {}
    refs: dict[UnknownKey, UnknownRef] = {}
    for block in analyses:
        for claim in block.all_claims():
            finding = (block.framework, claim.id)
            grounds = claim.unknown_grounds()
            evidence[finding] = {ref.key for ref in grounds}
            cited = list(grounds)
            if claim.verdict.status == "needs-info":
                named[finding] = {ref.key for ref in claim.verdict.related_unknowns}
                cited += claim.verdict.related_unknowns
            for ref in cited:
                refs.setdefault(ref.key, ref)
    first = _greedy(evidence)
    asked_first = set(first)
    later = _greedy({f: keys - asked_first for f, keys in named.items()})
    waiting = {
        finding: evidence.get(finding, set()) | named.get(finding, set())
        for finding in evidence.keys() | named.keys()
    }
    waiting = {finding: keys for finding, keys in waiting.items() if keys}
    names = element_names(model)
    asked: list[FactQuestion] = []
    answered: set[UnknownKey] = set()
    sections: tuple[tuple[Basis, list[UnknownKey]], ...] = (
        ("evidence", first),
        ("critic", later),
    )
    for basis, keys in sections:
        for key in keys:
            cited_by = sum(
                1
                for facts in waiting.values()
                if key in facts and not facts <= answered
            )
            answered.add(key)
            asked.append(
                FactQuestion(
                    key=key,
                    kind=fact_kind(key),
                    basis=basis,
                    label=label_of(refs[key], names),
                    cited_by=cited_by,
                    settled_so_far=sum(
                        1 for facts in waiting.values() if facts <= answered
                    ),
                    choices=answer_choices(key, model, catalog),
                    findings=tuple(
                        sorted(
                            f"{framework}/{claim}"
                            for (framework, claim), facts in waiting.items()
                            if key in facts
                        )
                    ),
                )
            )
    return tuple(asked)


@dataclass(frozen=True)
class Fallback:
    """How the critic named the open facts that have no place in the model.

    ``typed`` names a kind from the question table and an element; ``free_text``
    fell back to a ``subject``. The share that fell back is what says whether
    the table is enough (``QA-2026-09-26-03-E8``): a low, steady share means it
    is, and the fallback texts name the kinds it lacks.
    """

    typed: int
    free_text: int

    @property
    def rate(self) -> float | None:
        total = self.typed + self.free_text
        return None if total == 0 else round(self.free_text / total, 3)

    def to_json(self) -> dict[str, object]:
        return {"typed": self.typed, "free_text": self.free_text, "rate": self.rate}


def question_fallback(analyses: Sequence[FrameworkAnalysis]) -> Fallback:
    """Count a report's typed and free-text open facts across its verdicts."""
    refs = [
        ref
        for block in analyses
        for claim in block.all_claims()
        for ref in claim.verdict.related_unknowns
    ]
    return Fallback(
        typed=sum(1 for ref in refs if ref.spellings == ("question",)),
        free_text=sum(1 for ref in refs if ref.spellings == ("subject",)),
    )


def check_fact_answers(
    answers: Sequence[FactAnswer],
    model: SystemModel,
    catalog: AssertionCatalog | None,
) -> None:
    """Refuse an answer that names no open fact here, or a value the fact cannot hold.

    Checked before a resumed run is admitted, against the model and catalog
    the questions were asked about, so a wrong answer costs nothing.
    """
    for answer in answers:
        element_id, attribute, assertion, subject, question = answer.key
        kind = answer.kind
        if kind == "question":
            if question not in QUESTION_KINDS or model.get(element_id) is None:
                raise ValueError(f"no question {question!r} about {element_id!r}")
        elif kind == "attribute":
            element = model.get(element_id)
            answerable = () if element is None else attribute_names(element)
            if attribute not in answerable or attribute in _ENDPOINTS:
                raise ValueError(f"no attribute {attribute!r} on {element_id!r}")
        elif kind == "assertion":
            if catalog is None or _answered_row(catalog, assertion) is None:
                raise ValueError(f"no open assertion row {assertion!r}")
        elif not subject:
            raise ValueError("an answer names an element, an assertion or a subject")
        choices = answer_choices(answer.key, model, catalog)
        if choices and answer.value not in choices:
            raise ValueError(
                f"{answer.value!r} is not one of {', '.join(choices)} for"
                f" {attribute or assertion or subject!r}"
            )
    _check_attribute_values(answers, model)


def _check_attribute_values(answers: Sequence[FactAnswer], model: SystemModel) -> None:
    """Refuse an attribute answer the validity gate would refuse in that field.

    A resumed run starts at ``prepare`` and never meets the gate, so the gate's
    own rules are asked here: a field's length, and a control that is blank or
    opens with a negation other than ``none``, which
    :func:`~analysis_service.analysis.control_state` would read as stated.
    """
    attributes = [answer for answer in answers if answer.kind == "attribute"]
    if not attributes:
        return
    try:
        answered = answered_model(model, attributes)
    except ValidationError as error:
        first = error.errors()[0]
        raise ValueError(
            f"an answer does not fit {first['loc'][-1]!r}: {first['msg']}"
        ) from None
    asked = {(answer.key[0], answer.key[1]) for answer in attributes}
    for issue in validate(answered):
        if (issue.element_id, issue.field) in asked:
            raise ValueError(issue.message)


def answered_model(model: SystemModel, answers: Sequence[FactAnswer]) -> SystemModel:
    """The model with every attribute answer written in, and noted on its element."""
    data = model.model_dump(mode="json")
    by_id = {
        element["id"]: element
        for group in (
            "external_entities",
            "processes",
            "data_stores",
            "data_flows",
            "trust_boundaries",
        )
        for element in data.get(group, [])
    }
    for answer in answers:
        if answer.kind != "attribute":
            continue
        element_id, attribute, _, _, _ = answer.key
        element = by_id.get(element_id)
        if element is None:
            continue
        element[attribute] = answer.value
        note = f'The submitter answered {attribute}: "{answer.value}".'
        # A resumed job's checkpoint already holds an earlier round's answers.
        notes = element.get("notes", "")
        if note not in notes:
            element["notes"] = f"{notes} {note}".strip()[:2000]
    return SystemModel.model_validate(data)


def fact_rows(
    catalog: AssertionCatalog,
    answers: Sequence[FactAnswer],
    spans: Sequence[SupportSpan],
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with each assertion answer written over the open row it answers.

    ``spans`` are where each answer's line sits in the answers Source, in the
    order of ``answers``. The stated row keeps the open row's subject,
    predicate and scope, so it answers exactly what was asked. An earlier
    round's answer is written again over the row it wrote, so its quote reads
    this round's answers Source.
    """
    replaced: dict[str, Assertion] = {}
    issues: list[CatalogIssue] = []
    for answer, span in zip(answers, spans, strict=True):
        if answer.kind != "assertion":
            continue
        row = _answered_row(catalog, answer.key[2])
        if row is None:
            issues.append(
                CatalogIssue(
                    code="unmatched-answer",
                    message=f"no open row {answer.key[2]!r} in this catalog, so"
                    " the answer placed nothing",
                )
            )
            continue
        replaced[assertion_id(row)] = row.model_copy(
            update={
                "value": answer.value,
                "basis": "stated",
                "reason": None,
                "support": [span],
            }
        )
    entries = [replaced.get(assertion_id(entry), entry) for entry in catalog.entries]
    return catalog.model_copy(update={"entries": entries}), issues
