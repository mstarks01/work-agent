"""How a page takes an answer to one open fact: its choices, its form and its limit.

The early list, the report's list and the first-run app's correction rows all
build a question's form here, so a page offers what the admission check
accepts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Literal, get_args, get_origin

from pydantic import BaseModel

from analysis_service.assertions import (
    ABSENT,
    MAX_QUOTE_CHARS,
    REGISTRY,
    UNKNOWN,
    AssertionCatalog,
)
from analysis_service.claims import UnknownKey
from analysis_service.fact_answers import (
    FactAnswer,
    answer_facets,
    answered_row,
    fact_kind,
    fact_line,
    key_ref,
)
from analysis_service.question_kinds import QUESTION_KINDS, Facet
from analysis_service.system_model import SystemModel

__all__ = [
    "CONTROL_SUGGESTIONS",
    "YES_NO",
    "AnswerForm",
    "answer_choices",
    "answer_form",
    "answer_limit",
    "answer_suggestions",
    "facets_json",
]


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
    element_id, attribute, assertion, _, question, _ = key
    kind = fact_kind(key)
    if kind == "capability":
        return YES_NO
    if kind == "question":
        asked = QUESTION_KINDS.get(question)
        return YES_NO if asked is not None and asked.answer == "yes-no" else ()
    if kind == "attribute":
        special = _ATTRIBUTE_CHOICES.get(attribute)
        return (
            special(model, element_id)
            if special
            else _closed(model, element_id, attribute)
        )
    if kind == "assertion" and catalog is not None:
        row = answered_row(catalog, assertion)
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


#: The answers a ``yes-no`` question kind takes.
YES_NO: tuple[str, ...] = ("yes", "no")


#: Starting points for a control answered in free text: common mechanisms, so
#: a submitter can pick one and add what matters, such as how a key is rotated.
#: Keyed by every control attribute with no closed set, which
#: ``tests/test_questions.py`` derives from the element classes. The words come
#: from the corpus's signed models where they name one, and a suggestion is
#: never an answer until the submitter sends it.
CONTROL_SUGGESTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "authentication": (
            "session cookie after a password login",
            "password and a second factor",
            "company SSO",
            "OAuth 2.0 bearer token",
            "API key in a header",
            "service account",
            "mutual TLS",
            "pre-shared key",
        ),
        "encryption_in_transit": (
            "HTTPS",
            "TLS 1.3",
            "TLS 1.2",
            "mutual TLS",
            "SSH transport",
            "VPN tunnel",
        ),
        "encryption_at_rest": (
            "provider-managed key",
            "customer-managed key (CMEK)",
            "full-disk encryption",
            "database transparent data encryption",
        ),
    }
)


#: How a page takes an answer: one of ``choices``; a control, where the
#: submitter says there is none, does not know, or names the mechanism in
#: text; or free text.
AnswerForm = Literal["choice", "control", "facets", "text"]


def answer_form(
    key: UnknownKey, model: SystemModel, catalog: AssertionCatalog | None
) -> AnswerForm:
    """How a page takes an answer to this fact."""
    if answer_facets(key):
        return "facets"
    if answer_choices(key, model, catalog):
        return "choice"
    control = (
        fact_kind(key) == "attribute" and key_ref(key).attribute in CONTROL_SUGGESTIONS
    )
    return "control" if control else "text"


def answer_suggestions(key: UnknownKey) -> tuple[str, ...]:
    """Common mechanisms for a control answered in free text, or empty."""
    if fact_kind(key) != "attribute":
        return ()
    return CONTROL_SUGGESTIONS.get(key_ref(key).attribute, ())


def answer_limit(key: UnknownKey, model: SystemModel) -> int:
    """The longest answer this fact admits, in characters.

    **The one reader of "how long may this answer be".** A page's text box
    takes it, and :func:`~analysis_service.fact_writes.check_fact_answers`
    refuses one character more. It is the least of three bounds: an answer's
    own, the line the answer writes into the answers Source, and an
    attribute's field on its element.
    """
    bounds = [
        _max_length(FactAnswer, "value"),
        MAX_QUOTE_CHARS - (len(fact_line(FactAnswer(key=key, value="x"))) - 1),
    ]
    ref = key_ref(key)
    element = model.get(ref.element_id) if fact_kind(key) == "attribute" else None
    if element is not None and ref.attribute in type(element).model_fields:
        bounds.append(_max_length(type(element), ref.attribute))
    return min(bound for bound in bounds if bound is not None)


def _max_length(model_class: type[BaseModel], field: str) -> int | None:
    """The field's ``max_length``, which pydantic keeps in its metadata."""
    metadata = model_class.model_fields[field].metadata
    return next(
        (bound.max_length for bound in metadata if hasattr(bound, "max_length")),
        None,
    )


def facets_json(facets: Sequence[Facet]) -> list[dict[str, str]]:
    """The facets as a page reads them."""
    return [{"id": facet.id, "question": facet.question} for facet in facets]
