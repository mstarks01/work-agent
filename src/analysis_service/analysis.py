"""Deterministic traversal of a validated System Model.

Exhaustive enumeration is mechanical work, and mechanical work belongs in
code. Everything here answers a *structural* question — what flows touch this
element, what does it reach, which controls are unverified — from the
validated :class:`~analysis_service.system_model.SystemModel` and nothing else.
No source text is read, no security claim is introduced, and identical input
gives identical output down to list order.

That last property is the point rather than a nicety. These results become
:mod:`analysis_service.candidates` triggers, which become prompt bytes; a helper
that reordered its output between runs would make two otherwise identical jobs
send two different instructions and would break the cacheable prefix for
nothing.

**What a helper may not do.** It may not decide that a control is *absent*.
The System Model's security attributes are free-form strings whose one
reserved value is ``unknown``, so this module reads only the leading token of
an attribute (:func:`control_state`) and classifies it as ``unverified``,
``absent`` or ``stated``. ``"none; accepted by network position"`` is absent
because it says so; ``"company SSO"`` is stated and this module has nothing
further to say about whether SSO is any good. That judgement is the category
agent's, and pushing it here would be the thing this design exists to avoid.

Reachability is over Data Flows in their stated direction, which is who
*initiates* — see :class:`~analysis_service.system_model.DataFlow`. A flow is
therefore not a channel an attacker can only ride forwards, and the second-order
reach a category agent reasons about is wider than what :func:`reachable_from`
returns. The helper answers the narrow structural question; the prompt says so.

Every function takes the model as its first argument rather than living on
:class:`SystemModel`, because the model is a schema shared with the report and
this is analysis over it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.system_model import (
    UNKNOWN,
    BoundaryCrossing,
    DataFlow,
    Element,
    SystemModel,
)

__all__ = [
    "CONTROL_ATTRIBUTES",
    "TEXT_ATTRIBUTES",
    "WHOLE_WORD",
    "ControlState",
    "UnknownControl",
    "control_state",
    "cross_boundary_flows",
    "crossing_flow_ids",
    "inbound_flows",
    "internet_exposed_elements",
    "is_unverified",
    "matches_term",
    "names_term",
    "outbound_flows",
    "reachable_from",
    "sensitive_assets",
    "unknown_controls",
    "zone_kinds",
]

ControlState = Literal["unverified", "absent", "stated"]

# The five attributes ``prompts/extract.md`` names as security-relevant and
# defaults to ``unknown``. The list is here rather than derived from the
# schema because "is a control" is a fact about what the attribute means, not
# about its type: ``protocol`` and ``technology`` are strings too, and neither
# is a control whose absence is a finding.
CONTROL_ATTRIBUTES: tuple[str, ...] = (
    "authentication",
    "encryption_in_transit",
    "encryption_at_rest",
    "exposure",
    "data_classification",
)

# Asset tags whose exposure is a confidentiality loss on its own. A subset of
# ``CORE_ASSET_TAGS``: ``availability-critical`` and ``reputation`` are real
# assets and are deliberately not here, because neither is disclosed.
SENSITIVE_ASSET_TAGS = frozenset(
    {"credentials", "pii", "financial", "health", "secrets", "business-critical-data"}
)

# A control attribute states its own absence or its own unverifiability in its
# first token, which is the only position this module reads. Everything after
# it is prose for the agent: ``"none; accepted by network position"`` is
# absent, ``"unknown; possibly a shared group account"`` is unverified, and
# ``"no MFA on the password login"`` is *stated* — it describes a mechanism
# that exists, and reading "no" as absence would delete a control the text
# actually named.
_LEADING_CONTROL_TOKEN_RE = re.compile(r"^\s*(unknown|none)\b", re.IGNORECASE)


class UnknownControl(BaseModel):
    """One security-relevant attribute that the model does not verify.

    Deliberately shaped like the ``unknown-attribute`` ground branch
    (:class:`~analysis_service.report.Ground`) it will most often justify: an
    element ID and an attribute name, no free text. ``value`` carries the raw
    attribute so an agent can see *which* of the two states it is in without
    re-deriving it — ``unknown`` is a question to ask, ``none`` is a control
    the submitter said is not there.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str = Field(min_length=1, max_length=300)
    attribute: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=200)
    state: ControlState


def control_state(value: str) -> ControlState:
    """Classify one control attribute from its leading token alone.

    ``unverified`` for ``unknown``, ``absent`` for ``none``, ``stated`` for
    everything else — including the empty string, which no validated model
    carries for these attributes and which is not evidence of anything if it
    somehow arrives. Reading only the leading token is what keeps this from
    becoming a natural-language classifier with a security opinion.
    """
    match = _LEADING_CONTROL_TOKEN_RE.match(value)
    if match is None:
        return "stated"
    return "unverified" if match.group(1).lower() == UNKNOWN else "absent"


def is_unverified(value: str) -> bool:
    """True when a control attribute is unverified *or* stated absent.

    The union, because it is what a trigger asks about: both states mean the
    model carries no verified control, and the difference between them is
    reported in the facts rather than in whether the candidate exists.
    """
    return control_state(value) != "stated"


def inbound_flows(model: SystemModel, element_id: str) -> list[DataFlow]:
    """Every Data Flow whose ``destination`` is this element, in model order."""
    return [flow for flow in model.data_flows if flow.destination == element_id]


def outbound_flows(model: SystemModel, element_id: str) -> list[DataFlow]:
    """Every Data Flow whose ``source`` is this element, in model order."""
    return [flow for flow in model.data_flows if flow.source == element_id]


def reachable_from(model: SystemModel, element_id: str) -> list[str]:
    """Element IDs reachable by following outbound flows, excluding the start.

    Breadth-first, so the order is by distance and then by model order — a
    stable order that also reads as "what is one hop away, then two". The
    start element appears only if a cycle leads back to it.
    """
    seen: set[str] = set()
    order: list[str] = []
    frontier = [element_id]
    while frontier:
        next_frontier: list[str] = []
        for current in frontier:
            for flow in outbound_flows(model, current):
                if flow.destination in seen or flow.destination == element_id:
                    continue
                seen.add(flow.destination)
                order.append(flow.destination)
                next_frontier.append(flow.destination)
        frontier = next_frontier
    return order


def sensitive_assets(element: Element) -> tuple[str, ...]:
    """The element's asset tags whose disclosure is itself a loss."""
    return tuple(tag for tag in element.assets if tag in SENSITIVE_ASSET_TAGS)


def cross_boundary_flows(model: SystemModel) -> list[BoundaryCrossing]:
    """The derived boundary crossings.

    A thin alias for :meth:`SystemModel.boundary_crossings`, so this module is
    the one import a caller doing structural analysis needs, and so the
    crossing derivation stays defined in exactly one place.
    """
    return model.boundary_crossings()


def crossing_flow_ids(model: SystemModel) -> frozenset[str]:
    """The IDs of the flows that cross a trust boundary."""
    return frozenset(crossing.flow_id for crossing in cross_boundary_flows(model))


def internet_exposed_elements(model: SystemModel) -> list[Element]:
    """Processes whose ``exposure`` is ``internet-facing``.

    Exposure is a ``Literal`` on :class:`~analysis_service.system_model.Process`
    and on nothing else, so this is the whole of what the model states about
    internet reachability. ``unknown`` exposure is *not* included: it is a
    question, and it is reported as an unknown control rather than assumed
    into an attacker population.
    """
    return [
        process for process in model.processes if process.exposure == "internet-facing"
    ]


def unknown_controls(model: SystemModel) -> list[UnknownControl]:
    """Every control attribute across the model that states no verified control.

    Walks elements in :meth:`SystemModel.elements` order and attributes in
    :data:`CONTROL_ATTRIBUTES` order, so the list is stable and complete.
    Elements that carry none of the five — trust boundaries, external
    entities — simply contribute nothing.
    """
    return [
        UnknownControl(
            element_id=element.id,
            attribute=attribute,
            value=value[:200],
            state=state,
        )
        for element in model.elements()
        for attribute, value, state in _control_values(element)
        if state != "stated"
    ]


def zone_kinds(model: SystemModel) -> dict[str, str]:
    """Trust boundary ID -> its ``kind``, for zone-aware triggers.

    A zoned element's ``trust_zone`` is a boundary ID by the validity gate's
    rule, so this is the one lookup that turns "these zones differ" into
    "this crossing is a *privilege* transition".
    """
    return {boundary.id: boundary.kind for boundary in model.trust_boundaries}


def _control_values(element: Element) -> Iterator[tuple[str, str, ControlState]]:
    """This element's control attributes, with each one's state."""
    for attribute in CONTROL_ATTRIBUTES:
        value = getattr(element, attribute, None)
        if isinstance(value, str):
            yield attribute, value, control_state(value)


#: The free-text attributes a term is matched against, per element type. Every
#: one is a ``str`` the submitter authored, which is why a search here matches a
#: term rather than looking a value up: #162 ruled that controls stay string
#: attributes, so no attribute in the System Model is a closed enum to test.
TEXT_ATTRIBUTES: tuple[str, ...] = (
    "name",
    "description",
    "notes",
    "technology",
    "protocol",
    "authentication",
    "data_description",
    "data_classification",
    "encryption_at_rest",
    "encryption_in_transit",
)

#: Marks a term that matches a whole word only: ``java$`` does not reach
#: ``javascript`` and ``log$`` does not reach ``login``. A term without it
#: matches at the start of a word, because most terms are stems —
#: ``authenticat`` reaches ``authenticated`` and ``http`` reaches ``https``.
#: The mode is data in the term, per term, rather than a rule in the matcher.
WHOLE_WORD = "$"


def matches_term(term: str, text: str) -> bool:
    """Whether ``term`` appears in ``text`` at the start of a word, or as one.

    ``text`` is matched as given, so a caller comparing against a submitter's
    prose lowercases it first — the terms this reads are written lowercase.
    """
    stem = re.escape(term.rstrip(WHOLE_WORD))
    tail = r"(?!\w)" if term.endswith(WHOLE_WORD) else ""
    return re.search(rf"(?<!\w){stem}{tail}", text) is not None


def names_term(model: SystemModel, term: str) -> bool:
    """Whether any element's free text names ``term``.

    The structural question behind "the model contains no such thing". It reads
    only :data:`TEXT_ATTRIBUTES`, so an element *named* for a technology answers
    for it and a control value mentioning one does too — which is the whole of
    what a submitter's prose offers. Answering ``False`` is not proof of
    absence from the *system*; it is proof of absence from the description,
    which is the only thing this service ever has.
    """
    return any(
        matches_term(term, value.lower())
        for element in model.elements()
        for attribute in TEXT_ATTRIBUTES
        if isinstance(value := getattr(element, attribute, ""), str)
    )
