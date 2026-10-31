"""The owner's mapping of a carried answer's element to an element of the new model.

A carried answer names its element by an ID, and an ID follows the element's
name (ADR 0079). Where the new extraction names an element differently, every
answer about it is dropped. An **Element Rename** is the owner's statement
that an element of the earlier job is an element of this one, under another
name (ADR 0116). The service never guesses one from a similar name.

**A rename keeps the element's type.** An ID's prefix names its type, so a
process maps only to a process, and a flow only to a flow.

**A rename reaches the flows of its element.** A flow's ID holds its two
endpoints' IDs, so a renamed endpoint renames each carried flow ID that names
it. A flow whose own label changed takes a rename of its own.

**A rename is one to one.** Each earlier element maps to one element, each
element takes one earlier element, and an element that a carried answer
already names takes none, so a rename never puts two answers on one fact.

The job keeps its carried answers as they were carried and the renames beside
them. :func:`renamed` applies the renames each time a reader reads the carried
answers, so the record keeps both the answer and the mapping.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.answer_sets import AnswerSet
from analysis_service.claims import UNKNOWN_KEY_FIELDS, UnknownKey
from analysis_service.fact_answers import MAX_HELD_FACTS, key_ref
from analysis_service.system_model import (
    ELEMENT_ID,
    ELEMENT_ID_MAX_CHARS,
    FlowIdError,
    SystemModel,
    flow_id_version,
    make_flow_id,
    parse_flow_id,
)

__all__ = [
    "MAX_RENAMES",
    "ElementRename",
    "check_renames",
    "missing_elements",
    "rename_choices",
    "renamed",
]

#: The most renames one job holds: one for each element a held carried
#: answer can name.
MAX_RENAMES = MAX_HELD_FACTS


class ElementRename(BaseModel):
    """One element of the earlier job, and the element of this model it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    earlier: str = Field(max_length=ELEMENT_ID_MAX_CHARS, pattern=ELEMENT_ID)
    element: str = Field(max_length=ELEMENT_ID_MAX_CHARS, pattern=ELEMENT_ID)


def _type_of(element_id: str) -> str:
    """The type an element ID names: its prefix."""
    return element_id.partition(":")[0]


def _renamed_id(element_id: str, renames: Sequence[ElementRename]) -> str:
    """``element_id`` under ``renames``: its own rename, else a flow with each
    renamed endpoint replaced, else itself."""
    to = {rename.earlier: rename.element for rename in renames}
    if element_id in to:
        return to[element_id]
    if _type_of(element_id) != "flow":
        return element_id
    try:
        version = flow_id_version(element_id)
        parts = parse_flow_id(element_id, version)
    except FlowIdError:
        return element_id
    source = to.get(parts.source, parts.source)
    destination = to.get(parts.destination, parts.destination)
    if (source, destination) == (parts.source, parts.destination):
        return element_id
    return make_flow_id(source, destination, parts.label, version)


def _rekeyed(key: UnknownKey, element_id: str) -> UnknownKey:
    parts = dict(zip(UNKNOWN_KEY_FIELDS, key, strict=True))
    parts["element_id"] = element_id
    return tuple(parts[name] for name in UNKNOWN_KEY_FIELDS)  # type: ignore[return-value]


def renamed(carried: AnswerSet, renames: Sequence[ElementRename]) -> AnswerSet:
    """The carried answers with each element under ``renames``.

    **The one reader of a rename.** A fact answer about an element takes the
    element's new ID; every other answer is kept as it was carried. Where a
    renamed answer would land on a fact another carried answer names, the
    other answer is kept and the renamed one is not.
    """
    if not renames:
        return carried
    keys = {fact.key for fact in carried.facts}
    facts = []
    for fact in carried.facts:
        element_id = key_ref(fact.key).element_id
        to = _renamed_id(element_id, renames) if element_id else element_id
        if to == element_id:
            facts.append(fact)
            continue
        key = _rekeyed(fact.key, to)
        if key not in keys:
            facts.append(fact.model_copy(update={"key": key}))
    return carried.model_copy(update={"facts": tuple(facts)})


def missing_elements(carried: AnswerSet, model: SystemModel) -> tuple[str, ...]:
    """Each element a carried answer names that ``model`` does not have, in
    the order the answers name them."""
    present = {element.id for element in model.elements()}
    named = (key_ref(fact.key).element_id for fact in carried.facts)
    return tuple(dict.fromkeys(e for e in named if e and e not in present))


def _named(carried: AnswerSet) -> set[str]:
    return {key_ref(fact.key).element_id for fact in carried.facts} - {""}


def check_renames(
    renames: Sequence[ElementRename], carried: AnswerSet, model: SystemModel
) -> None:
    """Refuse a set of renames the job cannot hold, naming the first fault.

    Each rename maps an element a carried answer names and the model does
    not have, to an element of the model of the same type. Each side is
    named once, and no carried answer names the element already.
    """
    if len(renames) > MAX_RENAMES:
        raise ValueError(f"a job holds at most {MAX_RENAMES} renames")
    missing = set(missing_elements(carried, model))
    present = {element.id for element in model.elements()}
    named = _named(carried)
    earlier = [rename.earlier for rename in renames]
    elements = [rename.element for rename in renames]
    if len(set(earlier)) < len(earlier) or len(set(elements)) < len(elements):
        raise ValueError("each element is renamed once, and takes one rename")
    for rename in renames:
        if rename.earlier not in missing:
            raise ValueError(
                f"{rename.earlier} is not an element that a carried answer names"
                " and this model does not have"
            )
        if rename.element not in present:
            raise ValueError(f"{rename.element} is not an element of this model")
        if _type_of(rename.earlier) != _type_of(rename.element):
            raise ValueError(
                f"{rename.earlier} and {rename.element} are elements of two types"
            )
        if rename.element in named:
            raise ValueError(f"a carried answer already names {rename.element}")


def rename_choices(
    carried: AnswerSet, model: SystemModel, renames: Sequence[ElementRename]
) -> dict[str, tuple[str, ...]]:
    """For each element that the carried answers under ``renames`` still name
    and the model does not have, the elements it can map to: those of its
    type that no carried answer names."""
    held = renamed(carried, renames)
    named = _named(held)
    return {
        missing: tuple(
            element.id
            for element in model.elements()
            if _type_of(element.id) == _type_of(missing) and element.id not in named
        )
        for missing in missing_elements(held, model)
    }
