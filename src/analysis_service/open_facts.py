"""How a reader sees an open fact: the question it asks, and the group it is in.

A ``needs-info`` verdict names what has to be answered in ``related_unknowns``.
Every surface that shows such a fact words it here, so the early list, the
report, ``/v1`` and a refusal ask it alike: :func:`label_of` for the question,
:func:`group_of` for the group a page lists it in. This module also names the
elements and references a report writes (:func:`reference_labels`), lists the
attributes the model leaves unstated (:func:`gaps`), and decides which
attribute may be asked (:func:`open_attribute`).

**Deterministic, and derived from the report alone.** No model is asked, and
nothing here is stored on the report: it is computed wherever a reader needs
it, so it cannot drift from the verdicts it describes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from analysis_service.analysis import control_state
from analysis_service.assertions import (
    REGISTRY,
    AssertionCatalog,
    assertion_id,
    identity_parts,
    read_assertion_id,
)
from analysis_service.capabilities import CAPABILITIES
from analysis_service.claims import UnknownRef
from analysis_service.evidence import evidence_catalog, prepared_view
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.system_model import (
    ZONE_ATTRIBUTE,
    DataFlow,
    SystemModel,
    assumable_attribute_names,
    attribute_names,
)

#: The one group every capability question is shown in.
CAPABILITY_GROUP = "capabilities"

#: What a reader sees for an assertion the catalog does not hold, in place of
#: its reference.
UNWORDED_ASSERTION = "An open fact from the description that has no readable label"

#: What a reader sees for each asset tag in
#: :data:`~analysis_service.system_model.CORE_ASSET_TAGS`.
ASSET_WORDS = {
    "credentials": "credentials",
    "pii": "personal data",
    "financial": "financial data",
    "health": "health data",
    "secrets": "secrets",
    "business-critical-data": "business-critical data",
}


def attribute_words(attribute: str) -> str:
    """An attribute or a control as a reader sees it: words, not a field name."""
    return attribute.replace("_", " ").replace("-", " ")


def element_names(model: SystemModel) -> Mapping[str, str]:
    """Each element's display name; a flow reads as its two endpoints.

    Read by :mod:`analysis_service.questions` too, so a question and its open
    fact name an element alike.
    """
    names = {element.id: element.name for element in model.elements()}
    for flow in model.data_flows:
        source = names.get(flow.source, flow.source)
        destination = names.get(flow.destination, flow.destination)
        names[flow.id] = f"{source} → {destination}"
    return names


#: How a reader sees each attribute state the evidence catalog offers.
_GROUND_STATE = {"unknown-attribute": "unstated", "absent-attribute": "stated absent"}


def reference_labels(
    model: SystemModel, assertions: AssertionCatalog | None
) -> Mapping[str, str]:
    """A display label for each element ID, each evidence reference, and each
    other name a model writes in backticks.

    The references are the keys of
    :func:`~analysis_service.evidence.evidence_catalog`, the one writer of
    their spelling, so a page that looks a cited reference up here never
    parses one. An attribute reads as ``element: attribute, state`` and a
    crossing as ``flow crosses a trust boundary``. An open assertion row reads
    as the question it asks (:func:`assertion_label`), and a stated one as its
    subject, the predicate's meaning and its value.

    A model also writes an attribute, an asset tag, ``element.attribute`` and
    ``attribute: value`` in backticks. Each of these gets its words too, for
    each element and value in the model, so the page parses none of them.
    """
    names = element_names(model)
    labels = dict(names)
    labels.update(ASSET_WORDS)
    labels.update({name: attribute_words(name) for name in assumable_attribute_names()})
    for element in model.elements():
        for name in attribute_names(element):
            words = attribute_words(name)
            labels[f"{element.id}.{name}"] = f"{names[element.id]}: {words}"
            labels[f"{name}: {getattr(element, name)}"] = (
                f"{words}: {getattr(element, name)}"
            )
    for ref, ground in evidence_catalog(model, assertions).items():
        if ground.kind in _GROUND_STATE:
            where = names.get(ground.element_id, ground.element_id)
            attribute = attribute_words(ground.attribute)
            labels[ref] = f"{where}: {attribute}, {_GROUND_STATE[ground.kind]}"
        elif ground.flow_id:
            where = names.get(ground.flow_id, ground.flow_id)
            labels[ref] = f"{where} crosses a trust boundary"
        elif ground.kind == "unknown-assertion":
            labels[ref] = assertion_label(ref, names, assertions)
        elif ground.kind == "assertion" and assertions is not None:
            labels[ref] = _assertion_statement(ref, names, assertions)
    return labels


def _assertion_statement(
    identity: str, names: Mapping[str, str], catalog: AssertionCatalog
) -> str:
    """A stated assertion row as words: its subject, its meaning and its value.

    A reference value names a subject, so it reads as that subject's label.
    """
    subjects = {subject.id: subject.label for subject in catalog.subjects}
    row = next(entry for entry in catalog.entries if assertion_id(entry) == identity)
    predicate = REGISTRY[row.predicate]
    subject = subjects.get(row.subject) or names.get(row.subject, row.subject)
    value = row.value
    if predicate.value == "reference":
        value = subjects.get(value) or names.get(value, value)
    return f"{subject}: {predicate.meaning} \u2014 {value}"


#: The question an attribute asks where the plain form reads badly: of one
#: element, and of each element its group lists. Each of these attributes
#: belongs to one element type: a flow is in transit and a store is at rest.
_ATTRIBUTE_QUESTIONS = {
    "encryption_in_transit": (
        "Is the connection {element} encrypted?",
        "Is each connection encrypted? If so, how?",
    ),
    "encryption_at_rest": (
        "Is data stored in {element} encrypted?",
        "Is the data in each store encrypted? If so, how?",
    ),
}


def _attribute_questions(attribute: str) -> tuple[str, str]:
    """The question an attribute asks of one element, and of each one listed."""
    name = attribute_words(attribute)
    return _ATTRIBUTE_QUESTIONS.get(
        attribute,
        (f"What is the {name} for {{element}}?", f"What is the {name} for each part?"),
    )


def flow_words(flow: DataFlow, names: Mapping[str, str]) -> str:
    """How a **Data Flow** reads as a place: the connection its source initiates.

    The one wording of a flow as a place; every label that names one calls
    this. The direction is the flow's own (``GLOSSARY.md``): the source starts
    the connection.
    """
    source = names.get(flow.source, flow.source)
    destination = names.get(flow.destination, flow.destination)
    return f"the connection initiated by {source} to {destination}"


def assertion_label(
    identity: str, names: Mapping[str, str], catalog: AssertionCatalog | None
) -> str:
    """The question an assertion reference asks, worded from the catalog.

    The label is the predicate's question about the subject's label, with the
    scope after it. It reads only the predicate, the subject and the scope, so
    each row that shares these three parts gives one label, whatever its value.
    A reference the catalog does not hold gets :data:`UNWORDED_ASSERTION`,
    never the reference itself.
    """
    asked = read_assertion_id(identity)
    if catalog is None or asked is None:
        return UNWORDED_ASSERTION
    subjects = {subject.id: subject.label for subject in catalog.subjects}
    for entry in catalog.entries:
        if entry.predicate not in REGISTRY:
            continue
        if identity_parts(entry)[:3] != asked[:3]:
            continue
        subject = subjects.get(entry.subject) or names.get(entry.subject, entry.subject)
        question = REGISTRY[entry.predicate].question.format(subject=subject)
        if not entry.scope:
            return question
        scope = "; ".join(f"{each.kind}: {each.value}" for each in entry.scope)
        return f"{question} ({scope})"
    return UNWORDED_ASSERTION


def label_of(
    ref: UnknownRef,
    names: Mapping[str, str],
    model: SystemModel | None = None,
    catalog: AssertionCatalog | None = None,
) -> str:
    """What a reader sees for one open fact: the question it asks.

    Every surface words a fact through this, so the form page, the report page,
    ``/v1`` and a refusal ask it alike. The kind is read through
    :attr:`~analysis_service.claims.UnknownRef.kind`. An assertion is worded
    from ``catalog`` (:func:`assertion_label`).
    """
    where = names.get(ref.element_id, ref.element_id)
    flow = model.get(ref.element_id) if model is not None else None
    if isinstance(flow, DataFlow):
        source = names.get(flow.source, flow.source)
        destination = names.get(flow.destination, flow.destination)
        where = flow_words(flow, names)
        if ref.kind == "attribute":
            match ref.attribute:
                case "authentication":
                    return (
                        f"When {source} initiates a connection to {destination}, "
                        "what authentication, if any, is used?"
                    )
                case "protocol":
                    return (
                        f"What protocol does {source} use to connect to {destination}?"
                    )
                case "encryption_in_transit":
                    return f"Is {where} encrypted?"
                case "data_description":
                    return f"What data passes over {where}?"
                case "operations":
                    return f"What does {source} do when it connects to {destination}?"
    match ref.kind:
        case "capability":
            capability = CAPABILITIES.get(ref.capability)
            return capability.question if capability else ref.capability
        case "question":
            kind = QUESTION_KINDS.get(ref.question)
            return kind.template.format(element=where) if kind else ref.question
        case "assertion":
            return assertion_label(ref.assertion, names, catalog)
        case "attribute":
            return _attribute_questions(ref.attribute)[0].format(element=where)
        case "subject":
            return ref.subject


def group_of(ref: UnknownRef) -> tuple[str, str]:
    """The group a page shows this fact in, and the group's heading.

    One group for each question kind and each attribute, so a page asks each
    once and lists the elements under it. Every capability question is about
    the whole application, so they share one group. The heading is the
    question the group asks, with no element named: the kind's question with
    its element made a pronoun, or the attribute's question of each element.
    The kind is read through :attr:`~analysis_service.claims.UnknownRef.kind`.
    An assertion and a free-text subject each ask their own question, so every
    assertion shares one group, and every subject shares another.
    """
    match ref.kind:
        case "capability":
            return CAPABILITY_GROUP, "About the application"
        case "assertion":
            return "assertions", "What the description leaves open"
        case "subject":
            return "subjects", "Other questions"
        case "question" if ref.question in QUESTION_KINDS:
            template = QUESTION_KINDS[ref.question].template
            heading = template.replace("{element}'s", "its").replace("{element}", "it")
            return ref.question, heading
        case "attribute":
            return ref.attribute, _attribute_questions(ref.attribute)[1]
    label = label_of(ref, {})
    return label, label


@dataclass(frozen=True)
class Gap:
    """One attribute the System Model leaves unstated, and where."""

    attribute: str
    #: Each element the attribute is unstated on, in the model's order.
    element_ids: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "attribute": self.attribute,
            "elements": [
                {
                    "id": element_id,
                    "key": list(
                        UnknownRef(element_id=element_id, attribute=self.attribute).key
                    ),
                }
                for element_id in self.element_ids
            ],
        }


def gaps(model: SystemModel) -> tuple[Gap, ...]:
    """Every attribute the model leaves unstated, on the most elements first.

    Read from the ``unknown-attribute`` entries of
    :func:`~analysis_service.evidence.evidence_catalog`, the one reader of
    which attributes a model leaves open. Each element carries the key of its
    open fact, so a page can link it to the question that asks it.
    """
    unstated: dict[str, list[str]] = defaultdict(list)
    for ground in evidence_catalog(model).values():
        if ground.kind == "unknown-attribute":
            unstated[ground.attribute].append(ground.element_id)
    order = sorted(
        unstated, key=lambda attribute: (-len(unstated[attribute]), attribute)
    )
    return tuple(Gap(attribute, tuple(unstated[attribute])) for attribute in order)


def open_attribute(model: SystemModel, element_id: str, attribute: str) -> bool:
    """True where this attribute is a fact the model leaves open.

    **The one reader of "may this attribute be asked, and answered".** Open is
    unverified by :func:`~analysis_service.analysis.control_state`, the
    reading the evidence catalog uses, or a zone the service inferred. The
    early list, the report list and the answer check all ask it.
    """
    element = model.get(element_id)
    value = getattr(element, attribute, None)
    unverified = isinstance(value, str) and control_state(value) == "unverified"
    assumed = (
        attribute == ZONE_ATTRIBUTE and element_id in model.assumed_zone_elements()
    )
    return element is not None and (unverified or assumed)


def prepared_model(model: SystemModel, catalog: AssertionCatalog | None) -> SystemModel:
    """The model with the catalog's projection applied, as the lanes read it.

    **The model every question reader asks :func:`open_attribute` of.** A
    paused job's checkpoint holds the model before ``prepare`` projects the
    catalog, so a fact the catalog states can still read ``unknown`` there.
    Asked of that model, the early list would offer the fact and the check
    would admit an answer that then supersedes what the sources stated. A
    report's model is projected already, and projecting it again changes
    nothing.
    """
    return model if catalog is None else prepared_view(model, catalog)[0]
