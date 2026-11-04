"""What the Controls page reads, and what its routes answer (#2157, ADR 0127).

The page renders each value from here: the units each Framework Package
offers, with the labels its catalog gives at run time, the facts each scope
type takes, the evidence classes and the limits. A save sends a document
with the text of each new evidence file, and :func:`saved_control` builds the
Control the store keeps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analysis_service.answer_forms import YES_NO, attribute_choices
from analysis_service.capabilities import CAPABILITIES
from analysis_service.controls import (
    EVIDENCE_ID,
    Control,
    ControlDocument,
    ControlLimits,
    ControlRow,
    ControlScope,
    EvidenceFile,
    ScopeType,
)
from analysis_service.errors import refusal_text
from analysis_service.fact_answers import FACET_ANSWERS, FactAnswer
from analysis_service.frameworks import PACKAGES
from analysis_service.owner_files import NOT_UTF_8
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.sources import MAX_LABEL_CHARS, EvidenceClass, text_digest
from analysis_service.system_model import (
    Element,
    ZonedElement,
    attribute_names,
)

#: How the page names each scope type.
SCOPE_LABELS: Mapping[ScopeType, str] = MappingProxyType(
    {
        "application": "The whole application",
        "boundary": "A trust boundary",
        "process": "A process",
        "store": "A data store",
        "entity": "An external entity",
    }
)

#: How the page names each evidence class.
EVIDENCE_LABELS: Mapping[EvidenceClass, str] = MappingProxyType(
    {"config": "Configuration", "code": "Code", "people": "People"}
)

#: The attribute a Control does not write: a zone scope is how a Control
#: reaches a zone, and a Control places no element.
_PLACEMENT = "trust_zone"


def _element_types(scope_type: ScopeType) -> tuple[type[Element], ...]:
    """The element classes whose facts a scope of ``scope_type`` writes: its
    own class, each class a zone holds, or none for the whole application."""
    zoned = get_args(ZonedElement)
    if scope_type == "boundary":
        return zoned
    return tuple(kind for kind in zoned if kind.id_prefix == scope_type)


def _key(*, attribute: str = "", question: str = "", capability: str = "") -> list:
    """A fact's key with no element: the scope supplies it."""
    return ["", attribute, "", "", question, capability]


def _attribute_facts(scope_type: ScopeType) -> list[dict[str, object]]:
    facts: dict[str, dict[str, object]] = {}
    for element_type in _element_types(scope_type):
        fields = element_type.model_construct()
        for attribute in attribute_names(fields):
            if attribute == _PLACEMENT or attribute in facts:
                continue
            facts[attribute] = {
                "key": _key(attribute=attribute),
                "kind": "attribute",
                "label": attribute.replace("_", " "),
                "choices": list(attribute_choices(element_type, attribute, None)),
                "facets": [],
            }
    return list(facts.values())


def _question_facts() -> list[dict[str, object]]:
    return [
        {
            "key": _key(question=name),
            "kind": "question",
            "label": kind.template.replace("{element}", "it"),
            "choices": list(YES_NO) if kind.answer == "yes-no" else [],
            "facets": [
                {"id": facet.id, "question": facet.question} for facet in kind.facets
            ],
        }
        for name, kind in QUESTION_KINDS.items()
    ]


def _capability_facts() -> list[dict[str, object]]:
    return [
        {
            "key": _key(capability=name),
            "kind": "capability",
            "label": capability.question,
            "choices": list(YES_NO),
            "facets": [],
        }
        for name, capability in CAPABILITIES.items()
    ]


def scope_types() -> list[dict[str, object]]:
    """Each scope type, with the facts a Control of that scope may write.

    An element scope writes its type's attributes and a Question Kind about
    the element; the whole application writes a Capability, which names no
    element.
    """
    return [
        {
            "type": scope_type,
            "label": label,
            "facts": (
                _capability_facts()
                if scope_type == "application"
                else [*_attribute_facts(scope_type), *_question_facts()]
            ),
        }
        for scope_type, label in SCOPE_LABELS.items()
    ]


def units() -> dict[str, dict[str, object]]:
    """Each package's version and units, with the label its catalog gives
    each unit at run time."""
    return {
        name: {
            "version": package.version,
            "units": [
                {
                    "id": unit.id,
                    "grain": unit.grain,
                    "inside": unit.inside,
                    "label": package.record.text_of_unit(unit.id),
                }
                for unit in package.units
            ],
        }
        for name, package in PACKAGES.items()
    }


def controls_full(limits: ControlLimits) -> str:
    """The refusal of a new Control where the owner holds the most Controls
    the deployment keeps."""
    return (
        f"Controls holds {limits.max_controls} Controls, the most it can keep."
        " Delete a Control before you add another."
    )


def page_data(limits: ControlLimits) -> dict[str, object]:
    """Everything the Controls page reads of the service."""
    return {
        "units": units(),
        "scope_types": scope_types(),
        "evidence_classes": [
            {"id": evidence_class, "label": label}
            for evidence_class, label in EVIDENCE_LABELS.items()
        ],
        "limits": {
            "max_controls": limits.max_controls,
            "max_evidence": limits.max_evidence,
            "max_evidence_bytes": limits.max_evidence_bytes,
            "name_chars": MAX_LABEL_CHARS,
        },
        "facet_answers": list(FACET_ANSWERS),
    }


class NewEvidence(BaseModel):
    """An evidence file the save adds, with its text. The page reads the file
    and sends its text, so no route takes a file."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    evidence_class: EvidenceClass = Field(alias="class")
    text: str


class HeldEvidence(BaseModel):
    """An evidence file the Control holds, which the save keeps as it is."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=EVIDENCE_ID)


class ControlSave(BaseModel):
    """The body of ``POST /workspace/controls``: a Control as the editor wrote
    it. The service gives each new Control and each new evidence file its
    ID."""

    model_config = ConfigDict(extra="forbid")

    name: str
    scope: ControlScope
    facts: list[FactAnswer]
    rows: list[ControlRow]
    evidence: list[NewEvidence]


class ControlEdit(ControlSave):
    """The body of ``PUT /workspace/controls/{id}``: the Control as the editor
    wrote it, each held file by its ID, and the ``updated_at`` it read."""

    evidence: list[HeldEvidence | NewEvidence]  # type: ignore[assignment]
    updated_at: datetime


def saved_control(
    save: ControlSave,
    *,
    owner_id: str,
    control_id: str | None = None,
    held: Sequence[EvidenceFile] = (),
) -> tuple[Control, dict[str, str]]:
    """The Control a save writes, and the text of each new evidence file by
    its ID. ``control_id`` is the edited Control's ID, or ``None`` for a new
    one, and ``held`` is the evidence the store holds for it. Raises
    :class:`ValueError` where the save names a file the Control does not hold,
    or where the document breaks a rule of :class:`ControlDocument`."""
    kept = {file.id: file for file in held}
    files: list[EvidenceFile] = []
    texts: dict[str, str] = {}
    for sent in save.evidence:
        if isinstance(sent, HeldEvidence):
            if sent.id not in kept:
                raise ValueError("The save names an evidence file this Control lacks.")
            files.append(kept[sent.id])
            continue
        try:
            data = sent.text.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError(NOT_UTF_8) from None
        evidence_id = f"evidence-{uuid4().hex}"
        files.append(
            EvidenceFile.model_validate(
                {
                    "id": evidence_id,
                    "name": sent.name,
                    "class": sent.evidence_class,
                    "sha256": text_digest(sent.text),
                    "size_bytes": len(data),
                }
            )
        )
        texts[evidence_id] = sent.text
    document = ControlDocument(
        id=control_id or f"control-{uuid4().hex}",
        name=save.name,
        scope=save.scope,
        facts=save.facts,
        rows=save.rows,
        evidence=files,
    )
    return Control.create(owner_id=owner_id, document=document), texts


def save_refusal(exc: ValueError) -> str:
    """What the page shows for a refused save of a Control: the sentence of
    the first rule that a validator of the document states for the owner,
    else each place and rule of :func:`refusal_text`."""
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False):
            if error["type"] == "value_error":
                return str(error["ctx"]["error"])
    return refusal_text(exc)


def control_view(control: Control) -> dict[str, object]:
    """What the page shows of a Control: the document and its last save, and
    no evidence text."""
    return control.document.model_dump(mode="json") | {
        "updated_at": control.updated_at.isoformat()
    }
