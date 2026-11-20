"""What the extraction read from the sources, as a person reviews it at the pause.

A paused job asks about the facts its model leaves open. The facts the model
states are the other half: a wrong one is fixed by an amendment (ADR 0072),
and a person can see it only where a page lists it. :func:`detected_facts`
lists each element with the attributes it states, the words of the source it
was read from, and which values extraction inferred rather than read.

**Read-only.** Nothing here writes a fact. A stated value that is wrong is
corrected through an amendment, which reads the sources again.

**Only what is stated.** An attribute the model holds as ``unknown``, or
leaves empty, is an open fact and is asked, so it is not listed here.
"""

from __future__ import annotations

from typing import Any

from analysis_service.open_facts import element_names
from analysis_service.system_model import UNKNOWN, SystemModel, attribute_names

__all__ = ["detected_facts"]


def _shown(value: Any, names: dict[str, str]) -> str:
    """A stated value as a person reads it: an element's name for its ID, and
    a list joined."""
    if isinstance(value, list):
        return ", ".join(_shown(each, names) for each in value)
    text = str(value)
    return names.get(text, text)


def detected_facts(model: SystemModel) -> list[dict[str, object]]:
    """Each element with the facts the model states about it, in model order.

    A fact is an attribute whose value is neither ``unknown`` nor empty, and
    the element's assets. ``type`` is the element's
    :data:`~analysis_service.system_model.ElementPrefix`. ``inferred`` holds
    the basis where extraction recorded the value as an assumption, and is
    empty where a source states it.
    """
    names = dict(element_names(model))
    basis = {(a.element_id, a.attribute): a.basis for a in model.assumptions}
    listed: list[dict[str, object]] = []
    for element in model.elements():
        facts: list[dict[str, str]] = []
        if element.assets:
            facts.append(
                {
                    "attribute": "assets",
                    "value": _shown(element.assets, names),
                    "inferred": "",
                }
            )
        for attribute in attribute_names(element):
            value = getattr(element, attribute)
            if value in (None, "", [], UNKNOWN):
                continue
            facts.append(
                {
                    "attribute": attribute,
                    "value": _shown(value, names),
                    "inferred": basis.get((element.id, attribute), ""),
                }
            )
        listed.append(
            {
                "element": element.id,
                "name": names.get(element.id, element.name),
                "type": element.id_prefix,
                "excerpt": element.source_excerpt,
                "source_label": element.source_label,
                "facts": facts,
            }
        )
    return listed
