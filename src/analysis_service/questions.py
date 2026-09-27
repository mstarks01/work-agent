"""The open facts a report's conditional findings rest on, asked of the submitter.

A ``needs-info`` verdict names the facts an answer would settle. This module
turns them into questions, ranks them, and writes the answers back (#1225).

**Every open fact is asked, in the order that settles the most.** With the
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
* A **subject** with no place in the model, such as whether queries are
  parameterized. Nothing structured can hold the answer, so it reaches the
  lanes only as its line of the answers Source.

An answer to a question this service asked settles the fact, even against the
sources (the maintainer's decision of 2026-09-25 on #1225).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from analysis_service.sources import plain_name
from analysis_service.system_model import SystemModel

__all__ = [
    "MAX_FACT_ANSWERS",
    "FactAnswer",
    "FactQuestion",
    "answered_model",
    "check_fact_answers",
    "fact_questions",
    "fact_rows",
]

#: How many fact answers one submission carries. The largest report measured
#: raised 85 open facts, so this bounds the body above any real use.
MAX_FACT_ANSWERS = 200

FactKind = Literal["attribute", "assertion", "subject"]


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
        return _kind_of(self.key)


def _kind_of(key: UnknownKey) -> FactKind:
    element_id, attribute, assertion, _ = key
    if assertion:
        return "assertion"
    return "attribute" if element_id or attribute else "subject"


@dataclass(frozen=True)
class FactQuestion:
    """One open fact, the findings that wait on it, and the answers it takes."""

    key: UnknownKey
    kind: FactKind
    #: What a reader sees: an element's name and the attribute, or the subject.
    label: str
    #: How many still-open findings cite it when it is asked.
    cited_by: int
    #: How many findings are settled once it and every question before it is
    #: answered.
    settled_so_far: int
    #: The answers it takes, or empty where the answer is free text.
    choices: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "label": self.label,
            "cited_by": self.cited_by,
            "settled_so_far": self.settled_so_far,
            "choices": list(self.choices),
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


def _choices(key: UnknownKey, model: SystemModel, catalog: AssertionCatalog | None):
    element_id, attribute, assertion, _ = key
    kind = _kind_of(key)
    if kind == "attribute":
        special = _ATTRIBUTE_CHOICES.get(attribute)
        return (
            special(model, element_id)
            if special
            else _closed(model, element_id, attribute)
        )
    if kind == "assertion" and catalog is not None:
        row = _open_row(catalog, assertion)
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


def _open_row(catalog: AssertionCatalog, identity: str) -> Assertion | None:
    return next(
        (entry for entry in catalog.entries if assertion_id(entry) == identity), None
    )


def fact_questions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    catalog: AssertionCatalog | None,
) -> tuple[FactQuestion, ...]:
    """Every open fact a report's needs-info findings cite, the most settling first.

    Greedy over the findings that are still open: the next question is the fact
    the most of them cite, which is the order in which answering from the top
    settles the most. The findings of every framework count together, because
    one answer settles a fact for every framework that cites it.
    """
    needs: dict[tuple[str, str], set[UnknownKey]] = {}
    refs: dict[UnknownKey, UnknownRef] = {}
    for block in analyses:
        for claim in block.claims:
            if claim.verdict.status != "needs-info":
                continue
            keys = set()
            for ref in claim.verdict.related_unknowns:
                keys.add(ref.key)
                refs.setdefault(ref.key, ref)
            needs[block.framework, claim.id] = keys
    open_findings = {finding: set(keys) for finding, keys in needs.items() if keys}
    names = element_names(model)
    asked: list[FactQuestion] = []
    settled = 0
    while open_findings:
        cites = Counter(key for keys in open_findings.values() for key in keys)
        key, count = min(cites.items(), key=lambda item: (-item[1], item[0]))
        for finding in list(open_findings):
            open_findings[finding].discard(key)
            if not open_findings[finding]:
                del open_findings[finding]
                settled += 1
        asked.append(
            FactQuestion(
                key=key,
                kind=_kind_of(key),
                label=label_of(refs[key], names),
                cited_by=count,
                settled_so_far=settled,
                choices=_choices(key, model, catalog),
            )
        )
    return tuple(asked)


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
        element_id, attribute, assertion, subject = answer.key
        kind = answer.kind
        if kind == "attribute":
            element = model.get(element_id)
            if element is None or attribute not in type(element).model_fields:
                raise ValueError(f"no attribute {attribute!r} on {element_id!r}")
        elif kind == "assertion":
            if catalog is None or _open_row(catalog, assertion) is None:
                raise ValueError(f"no open assertion row {assertion!r}")
        elif not subject:
            raise ValueError("an answer names an element, an assertion or a subject")
        choices = _choices(answer.key, model, catalog)
        if choices and answer.value not in choices:
            raise ValueError(
                f"{answer.value!r} is not one of {', '.join(choices)} for"
                f" {attribute or assertion or subject!r}"
            )


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
        element_id, attribute, _, _ = answer.key
        element = by_id.get(element_id)
        if element is None:
            continue
        element[attribute] = answer.value
        note = f'The submitter answered {attribute}: "{answer.value}".'
        element["notes"] = f"{element.get('notes', '')} {note}".strip()[:2000]
    return SystemModel.model_validate(data)


def fact_rows(
    catalog: AssertionCatalog,
    answers: Sequence[FactAnswer],
    spans: Sequence[SupportSpan],
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with each assertion answer written over the open row it answers.

    ``spans`` are where each answer's line sits in the answers Source, in the
    order of ``answers``. The stated row keeps the open row's subject,
    predicate and scope, so it answers exactly what was asked.
    """
    replaced: dict[str, Assertion] = {}
    issues: list[CatalogIssue] = []
    for answer, span in zip(answers, spans, strict=True):
        if answer.kind != "assertion":
            continue
        row = _open_row(catalog, answer.key[2])
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
