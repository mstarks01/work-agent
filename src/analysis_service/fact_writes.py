"""Where fact answers are written, and the check that runs the writer first.

**Only an open fact is asked, and only an asked fact takes an answer.** An
attribute is open where :func:`~analysis_service.open_facts.open_attribute`
says so. An answer to an attribute the model states is refused, unless an
earlier round answered it, so a submission cannot overwrite what the sources
said. An answer to a fact the job did not ask is refused by
:func:`~analysis_service.links.check_answers`, so a submission cannot write a
question of its own into every lane prompt.

**The check runs the writer it guards.** :func:`check_fact_answers` builds
the answered model with :func:`answered_model` and asks the validity gate of
it, so it admits only what the writer then accepts.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from analysis_service.analysis import control_state
from analysis_service.answer_forms import answer_choices
from analysis_service.assertions import (
    ABSENT,
    MAX_QUOTE_CHARS,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    CatalogIssue,
    SupportSpan,
    answered,
    assertion_id,
    projected_attribute,
)
from analysis_service.capabilities import CAPABILITIES, lineage
from analysis_service.fact_answers import (
    FactAnswer,
    answer_facets,
    answered_row,
    fact_label,
    fact_line,
    key_ref,
    merged_facts,
)
from analysis_service.open_facts import open_attribute, prepared_model
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import (
    SystemModel,
    attribute_names,
)
from analysis_service.validation import validate

__all__ = [
    "answered_model",
    "check_fact_answers",
    "fact_rows",
]


#: The fields a flow's ID is built from. An answer cannot move one, because the
#: flow would then carry an ID its endpoints no longer derive.
_ENDPOINTS = frozenset({"source", "destination"})


def check_fact_answers(
    answers: Sequence[FactAnswer],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    earlier: Sequence[FactAnswer] = (),
    *,
    reopen: bool = False,
) -> None:
    """Refuse an answer that names no open fact here, or a value the fact cannot hold.

    Checked before a resumed run is admitted, against the model and catalog
    the questions were asked about, so a wrong answer costs nothing. A key
    names one fact in one of the five spellings, as
    :attr:`~analysis_service.claims.UnknownRef.spellings` reads them.

    ``earlier`` is what the earlier rounds answered. An attribute one of them
    answered is stated now, and may be answered again with another value. An
    ``unknown`` answer to a fact an earlier round settled reopens the fact, so
    it is refused unless ``reopen``: a job waiting at its pause lets a
    submitter take back an answer they guessed, and a report's follow-up does
    not. **The rule holds for each facet:** a facet answered ``unknown`` is
    refused where an earlier answer gave it a known answer, whatever the other
    facets say.
    """
    prepared = prepared_model(model, catalog)
    answered_before = {answer.key for answer in earlier}
    settled_before = {answer.key: answer for answer in earlier if answer.known}
    stated = prepared.capability_facts()
    for answer in answers:
        element_id, attribute, assertion, subject, question, capability = answer.key
        ref = key_ref(answer.key)
        if len(ref.spellings) != 1:
            raise ValueError(f"an answer's key names one fact, not {answer.key!r}")
        kind = answer.kind
        if kind == "question":
            if question not in QUESTION_KINDS or model.get(element_id) is None:
                raise ValueError(f"no question {question!r} about {element_id!r}")
            if answer_facets(answer.key) and answer.facets is None and answer.known:
                raise ValueError(f"{question!r} is answered in its facets")
        elif kind == "attribute":
            element = model.get(element_id)
            answerable = () if element is None else attribute_names(element)
            if attribute not in answerable or attribute in _ENDPOINTS:
                raise ValueError(f"no attribute {attribute!r} on {element_id!r}")
            if answer.key not in answered_before and not open_attribute(
                prepared, element_id, attribute
            ):
                raise ValueError(
                    f"{attribute!r} on {element_id!r} is stated, so it takes no answer"
                )
        elif kind == "assertion" and (
            catalog is None or answered_row(catalog, assertion) is None
        ):
            raise ValueError(f"no open assertion row {assertion!r}")
        elif kind == "capability":
            if capability not in CAPABILITIES:
                raise ValueError(f"no capability {capability!r}")
            known = stated.get(capability)
            if (
                answer.key not in answered_before
                and known is not None
                and known.state != UNKNOWN
            ):
                raise ValueError(
                    f"the sources state {capability!r}, so it takes no answer"
                )
        if len(fact_line(answer)) > MAX_QUOTE_CHARS:
            raise ValueError(
                f"an answer's line may hold {MAX_QUOTE_CHARS} characters; shorten"
                f' the answer to "{fact_label(answer.key, model)}"'
            )
        if not reopen and answer.key in settled_before:
            _refuse_reopening(answer, settled_before[answer.key])
        if not answer.known:
            continue
        choices = answer_choices(answer.key, model, catalog)
        if choices and answer.value not in choices:
            raise ValueError(
                f"{answer.value!r} is not one of {', '.join(choices)} for"
                f" {attribute or assertion or subject or capability!r}"
            )
    _check_attribute_values(answers, model)
    _check_capability_lineage(answers, prepared, earlier)


def _refuse_reopening(answer: FactAnswer, before: FactAnswer) -> None:
    """Refuse an ``unknown`` answer to a fact, or to a facet, ``before`` settled."""
    if answer.facets is None or before.facets is None:
        if not answer.known:
            raise ValueError(
                "an earlier answer settled this fact; send a value to change it"
            )
        return
    for facet in answer_facets(answer.key):
        reopened = answer.facets.get(facet.id) == UNKNOWN
        if reopened and before.facets.get(facet.id, UNKNOWN) != UNKNOWN:
            raise ValueError(
                f"an earlier answer settled {facet.question!r}; send a value to"
                " change it"
            )


def _check_capability_lineage(
    answers: Sequence[FactAnswer],
    prepared: SystemModel,
    earlier: Sequence[FactAnswer],
) -> None:
    """Refuse a round that would leave a capability present under an absent ancestor.

    **Checked on the model the round produces**, with every earlier answer and
    every source statement in it, so a contradiction is refused whether it
    arrives in one round, across two, or against what the sources state. A
    contradiction this round's answers take no part in is left as it is:
    :func:`~analysis_service.capabilities.resolve` keeps it for a person to
    settle.
    """
    answering = {
        key_ref(answer.key).capability
        for answer in answers
        if answer.kind == "capability" and answer.known
    }
    if not answering:
        return
    held = answered_model(prepared, merged_facts(earlier, answers)).capability_facts()
    for key, fact in held.items():
        if fact.state != "present" or key not in CAPABILITIES:
            continue
        for ancestor in lineage(key):
            absent = ancestor in held and held[ancestor].state == "absent"
            if absent and answering & {key, ancestor}:
                raise ValueError(
                    f"{key!r} is part of {ancestor!r}, and these answers would"
                    f" leave {key!r} present while {ancestor!r} is absent;"
                    " answer both"
                )


def _check_attribute_values(answers: Sequence[FactAnswer], model: SystemModel) -> None:
    """Refuse an attribute answer the validity gate would refuse in that field.

    A resumed run starts at ``prepare`` and never meets the gate, so the gate's
    own rules are asked here: a field's length, and a control that is blank or
    opens with a negation other than ``none``, which
    :func:`~analysis_service.analysis.control_state` would read as stated.
    """
    attributes = [
        answer for answer in answers if answer.kind == "attribute" and answer.known
    ]
    if not attributes:
        return
    try:
        answered = answered_model(model, attributes)
    except ValidationError as error:
        first = error.errors()[0]
        raise ValueError(
            f"an answer does not fit {first['loc'][-1]!r}: {first['msg']}"
        ) from None
    refs = [key_ref(answer.key) for answer in attributes]
    asked = {(ref.element_id, ref.attribute) for ref in refs}
    for issue in validate(answered):
        if (issue.element_id, issue.field) in asked:
            raise ValueError(issue.message)


def answered_model(model: SystemModel, answers: Sequence[FactAnswer]) -> SystemModel:
    """The model with every attribute and capability answer written in.

    An attribute answer settles the attribute, so an
    :class:`~analysis_service.system_model.Assumption` that named it as inferred
    is removed. The note keeps who settled it. An ``unknown`` answer writes only
    the note, so the attribute stays open.

    A capability answer replaces every statement about that capability with
    one that quotes the answer's line of the answers Source: "yes" is
    ``present`` and "no" is ``absent``. An ``unknown`` answer writes nothing,
    so the capability stays open.
    """
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
        element_id, attribute, *_ = answer.key
        element = by_id.get(element_id)
        if element is None:
            continue
        if answer.known:
            element[attribute] = answer.value
            note = f'The submitter answered {attribute}: "{answer.value}".'
        else:
            note = f"The submitter does not know {attribute}."
        # A resumed job's checkpoint already holds an earlier round's answers.
        notes = element.get("notes", "")
        if note not in notes:
            element["notes"] = f"{notes} {note}".strip()[:2000]
    answered = {
        answer.key[:2]
        for answer in answers
        if answer.kind == "attribute" and answer.known
    }
    data["assumptions"] = [
        assumption
        for assumption in data.get("assumptions", [])
        if (assumption["element_id"], assumption["attribute"]) not in answered
    ]
    capabilities = {
        key_ref(answer.key).capability: answer
        for answer in answers
        if answer.kind == "capability" and answer.known
    }
    data["capabilities"] = [
        statement
        for statement in data.get("capabilities", [])
        if statement["capability"] not in capabilities
    ] + [
        {
            "capability": key,
            "state": "present" if answer.value == "yes" else "absent",
            "source_excerpt": fact_line(answer),
            "source_label": ANSWERS_LABEL,
        }
        for key, answer in capabilities.items()
    ]
    return SystemModel.model_validate(data)


def fact_rows(
    catalog: AssertionCatalog,
    answers: Sequence[FactAnswer],
    spans: Sequence[SupportSpan],
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with each answer written over the rows it settles.

    ``spans`` are where each answer's line sits in the answers Source, in the
    order of ``answers``. An assertion answer replaces the open row it answers,
    and the stated row keeps the open row's subject, predicate and scope, so it
    answers exactly what was asked. An earlier round's answer is written again
    over the row it wrote, so its quote reads this round's answers Source.

    **An attribute answer removes every unscoped row that reaches its
    attribute.** The answer settles the attribute, and such a row left beside it
    would reach the lanes as the opposite fact. Each removed row is named by a
    ``superseded-by-answer`` issue, which later rounds keep. **A scoped row
    stays**, because it states a narrower fact that the answer does not settle.
    Where one stays, the answer is written as an unscoped row beside it, which
    :func:`~analysis_service.assertions.project` lets outrank it.
    """
    replaced: dict[str, Assertion] = {}
    issues: list[CatalogIssue] = []
    attributes = {
        answer.key[:2]: (answer, span)
        for answer, span in zip(answers, spans, strict=True)
        if answer.kind == "attribute" and answer.known
    }

    def reached(entry: Assertion) -> tuple[str, str]:
        return entry.subject, projected_attribute(entry.predicate, entry.subject)

    superseded = {
        assertion_id(entry)
        for entry in catalog.entries
        if reached(entry) in attributes and not entry.scope
    }
    issues.extend(
        CatalogIssue(
            code="superseded-by-answer",
            message="the submitter's answer to this row's attribute replaced it",
            subject=entry.subject,
            assertion=assertion_id(entry),
        )
        for entry in catalog.entries
        if assertion_id(entry) in superseded and not answered(entry)
    )
    scoped = sorted(
        (
            entry
            for entry in catalog.entries
            if reached(entry) in attributes and entry.scope
        ),
        key=assertion_id,
    )
    written: dict[tuple[str, str], Assertion] = {}
    for entry in scoped:
        answer, span = attributes[reached(entry)]
        value = ABSENT if control_state(answer.value) == "absent" else answer.value
        written.setdefault(
            reached(entry),
            Assertion(
                subject=entry.subject,
                predicate=entry.predicate,
                value=value,
                basis="stated",
                support=[span],
            ),
        )
    for answer, span in zip(answers, spans, strict=True):
        if answer.kind != "assertion" or not answer.known:
            continue
        assertion = key_ref(answer.key).assertion
        row = answered_row(catalog, assertion)
        if row is None:
            issues.append(
                CatalogIssue(
                    code="unmatched-answer",
                    message=f"no open row {assertion!r} in this catalog, so"
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
    entries = [
        replaced.get(assertion_id(entry), entry)
        for entry in catalog.entries
        if assertion_id(entry) not in superseded
    ]
    return (
        catalog.model_copy(update={"entries": [*entries, *written.values()]}),
        issues,
    )
