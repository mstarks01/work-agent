"""Deterministic candidate triggers: structural conditions worth investigating.

A **candidate** is not a finding. It is a mechanically-evaluated condition over
the validated System Model — "this flow crosses a trust boundary and its
``authentication`` is unverified" — handed to the category agent as something
to *look at*. Whether it is a threat, and what the attacker actually achieves,
is the agent's judgement and stays there.

The line this module does not cross: **a candidate is never evidence.** It
carries no severity, no attacker story, no claim that anything is wrong, and it
cannot become a :class:`~stride_service.report.Threat` — nothing downstream of
the prompt reads a candidate at all. What grounds a finding is still the
submitter's words, an ``unknown`` attribute, or a derived crossing, exactly as
before. A rule's whole contribution is *attention*.

The representation is deliberately small: a tuple of :class:`Rule` values, each
a rule ID, the category whose lane it belongs to, the question it puts to that
agent, and a plain function from model to matches. There is no rule DSL, no
condition tree and no engine, because the thing a maintainer needs to do most
often is read one rule and decide whether it is right — and a table of twelve
functions is the representation that makes that cheapest. Adding a rule is
writing a function and appending to :data:`RULES`.

Rules fire on **structure**, never on prose. The attribute predicates come from
:mod:`stride_service.analysis`, which reads a control attribute's leading token
and nothing else, so no rule here is a natural-language classifier wearing a
rule's clothes. A rule that needed to understand what ``"company SSO"`` implies
would be a rule that belongs in the skill text instead.

Two rules may fire on one attribute from different lanes, and that is correct:
an unverified ``authentication`` on a boundary-crossing flow is a spoofing
question and an attribution question, and the two agents answer differently.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from stride_service.analysis import (
    control_state,
    crossing_flow_ids,
    inbound_flows,
    internet_exposed_elements,
    is_unverified,
    reachable_from,
    sensitive_assets,
    zone_kinds,
)
from stride_service.report import STRIDE_CATEGORIES, StrideCategory
from stride_service.system_model import SystemModel

__all__ = [
    "RULES",
    "Candidate",
    "CandidateSet",
    "Rule",
    "generate_candidates",
    "rules_for",
]

# How much of a caller-authored attribute value a fact carries. Facts exist to
# say which state an attribute is in, not to re-render the model — the agent
# has the whole thing under ``{system_model}``.
MAX_FACT_CHARS = 200

# A shared dependency is an element two or more distinct elements flow into.
# Two rather than three: on the small models this service sees, the second
# dependent is already the point — one queue behind two producers is a
# chokepoint, and the agent decides whether it matters.
SHARED_DEPENDENCY_MIN = 2

# The zone kinds whose crossing is a privilege transition rather than a network
# hop. ``network`` and ``other`` are excluded: every boundary crossing is
# already surfaced to every agent, and a rule that fires on all of them adds no
# attention anywhere.
PRIVILEGE_ZONE_KINDS = frozenset({"privilege", "tenant"})

Fact = str | int | bool
Match = tuple[tuple[str, ...], dict[str, Fact]]
"""One rule hit: the element IDs it is about, and the facts that made it fire."""


class Candidate(BaseModel):
    """One fired trigger: a condition to investigate, with the facts behind it.

    ``facts`` are the model values the rule read, so the agent can see the
    trigger without taking the rule's word for it — an ``authentication`` of
    ``unknown`` is a question to ask, one of ``none`` is a control the
    submitter said is not there, and the two produce different findings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1, max_length=100)
    category: StrideCategory
    element_ids: tuple[str, ...] = Field(min_length=1)
    facts: dict[str, Fact] = Field(default_factory=dict)


class CandidateSet(BaseModel):
    """One category's candidates, plus the questions their rules put.

    The questions ride beside the candidates rather than inside each one: a
    rule that fires eight times would otherwise spend its sentence eight
    times, and the agent reads the question once per rule either way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: StrideCategory
    questions: dict[str, str] = Field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True)
class Rule:
    """One deterministic trigger: its lane, its question, and how it fires.

    ``find`` yields :data:`Match` values rather than :class:`Candidate` ones,
    so a rule cannot file under a category that is not its own — the ID and
    the category are stamped by :meth:`fire`, from the rule's own fields.
    """

    rule_id: str
    category: StrideCategory
    question: str
    find: Callable[[SystemModel], Iterator[Match]]

    def fire(self, model: SystemModel) -> list[Candidate]:
        """Every candidate this rule produces for this model, in model order."""
        return [
            Candidate(
                rule_id=self.rule_id,
                category=self.category,
                element_ids=element_ids,
                facts=facts,
            )
            for element_ids, facts in self.find(model)
        ]


def _clip(value: str) -> str:
    return value[:MAX_FACT_CHARS]


# --- Spoofing ---------------------------------------------------------------


def _unverified_boundary_auth(model: SystemModel) -> Iterator[Match]:
    crossing_ids = crossing_flow_ids(model)
    zones = {crossing.flow_id: crossing for crossing in model.boundary_crossings()}
    for flow in model.data_flows:
        if flow.id not in crossing_ids or not is_unverified(flow.authentication):
            continue
        crossing = zones[flow.id]
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "crosses_boundary": True,
                "source_zone": crossing.source_zone,
                "destination_zone": crossing.destination_zone,
            },
        )


def _unverified_external_caller(model: SystemModel) -> Iterator[Match]:
    external = {
        entity.id
        for entity in model.external_entities
        if entity.kind == "external-system"
    }
    for flow in model.data_flows:
        if flow.source not in external or not is_unverified(flow.authentication):
            continue
        yield (
            (flow.id, flow.source),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "source_kind": "external-system",
            },
        )


# --- Tampering --------------------------------------------------------------


def _unprotected_transit_crossing(model: SystemModel) -> Iterator[Match]:
    crossing_ids = crossing_flow_ids(model)
    for flow in model.data_flows:
        if flow.id not in crossing_ids or not is_unverified(flow.encryption_in_transit):
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "encryption_in_transit": _clip(flow.encryption_in_transit),
                "encryption_state": control_state(flow.encryption_in_transit),
                "protocol": _clip(flow.protocol),
                "crosses_boundary": True,
            },
        )


def _unverified_write_to_store(model: SystemModel) -> Iterator[Match]:
    stores = {store.id: store for store in model.data_stores}
    for flow in model.data_flows:
        store = stores.get(flow.destination)
        if store is None or not is_unverified(flow.authentication):
            continue
        yield (
            (flow.id, store.id),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "data_classification": _clip(store.data_classification),
            },
        )


# --- Repudiation ------------------------------------------------------------


def _shared_authentication(model: SystemModel) -> Iterator[Match]:
    """Flows from distinct sources presenting the identical stated credential.

    String equality, not similarity: two flows whose ``authentication`` reads
    the same are describing one mechanism, and one mechanism shared by several
    callers is an attribution question by construction. Unverified values are
    excluded — six flows reading ``unknown`` share nothing but silence.
    """
    by_value: dict[str, list[str]] = {}
    sources: dict[str, set[str]] = {}
    for flow in model.data_flows:
        if is_unverified(flow.authentication):
            continue
        by_value.setdefault(flow.authentication, []).append(flow.id)
        sources.setdefault(flow.authentication, set()).add(flow.source)
    for value, flow_ids in by_value.items():
        if len(sources[value]) < SHARED_DEPENDENCY_MIN:
            continue
        yield (
            tuple(flow_ids),
            {
                "authentication": _clip(value),
                "flow_count": len(flow_ids),
                "distinct_sources": len(sources[value]),
            },
        )


def _unattributable_action(model: SystemModel) -> Iterator[Match]:
    """Unverified callers writing into an element that holds a graded asset."""
    by_id = {element.id: element for element in model.elements()}
    for flow in model.data_flows:
        destination = by_id.get(flow.destination)
        if destination is None or not is_unverified(flow.authentication):
            continue
        assets = sensitive_assets(destination)
        if not assets:
            continue
        yield (
            (flow.id, destination.id),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "destination_assets": ", ".join(assets),
            },
        )


# --- Information disclosure -------------------------------------------------


def _unprotected_sensitive_transit(model: SystemModel) -> Iterator[Match]:
    by_id = {element.id: element for element in model.elements()}
    for flow in model.data_flows:
        if not is_unverified(flow.encryption_in_transit):
            continue
        endpoints = [by_id.get(flow.source), by_id.get(flow.destination)]
        assets = sorted(
            {
                asset
                for endpoint in endpoints
                if endpoint is not None
                for asset in sensitive_assets(endpoint)
            }
        )
        if not assets:
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "encryption_in_transit": _clip(flow.encryption_in_transit),
                "encryption_state": control_state(flow.encryption_in_transit),
                "endpoint_assets": ", ".join(assets),
            },
        )


def _store_at_rest_unverified(model: SystemModel) -> Iterator[Match]:
    for store in model.data_stores:
        if not is_unverified(store.encryption_at_rest):
            continue
        yield (
            (store.id,),
            {
                "encryption_at_rest": _clip(store.encryption_at_rest),
                "encryption_state": control_state(store.encryption_at_rest),
                "data_classification": _clip(store.data_classification),
                "assets": ", ".join(store.assets),
            },
        )


# --- Denial of service ------------------------------------------------------


def _internet_exposed_process(model: SystemModel) -> Iterator[Match]:
    for process in internet_exposed_elements(model):
        yield (
            (process.id,),
            {
                "exposure": "internet-facing",
                "inbound_flows": len(inbound_flows(model, process.id)),
                "reachable_elements": len(reachable_from(model, process.id)),
            },
        )


def _shared_dependency(model: SystemModel) -> Iterator[Match]:
    """Elements several distinct callers flow into: one stall stalls them all."""
    for element in model.elements():
        flows = inbound_flows(model, element.id)
        callers = {flow.source for flow in flows}
        if len(callers) < SHARED_DEPENDENCY_MIN:
            continue
        yield (
            (element.id, *sorted(callers)),
            {
                "inbound_flows": len(flows),
                "distinct_callers": len(callers),
                "assets": ", ".join(element.assets),
            },
        )


# --- Elevation of privilege -------------------------------------------------


def _privilege_zone_crossing(model: SystemModel) -> Iterator[Match]:
    kinds = zone_kinds(model)
    # A crossing's flow ID names a Data Flow in the model it was derived from,
    # so this lookup resolves. Indexed by type rather than reached for with a
    # defaulted ``getattr``: the default would answer a missing flow with the
    # empty string, whose ``control_state`` is ``stated`` — a rule reporting a
    # control the model never named, which is the one thing this module may not
    # do. A flow that somehow does not resolve yields no candidate instead.
    flows = {flow.id: flow for flow in model.data_flows}
    for crossing in model.boundary_crossings():
        kind = kinds.get(crossing.destination_zone, "")
        if kind not in PRIVILEGE_ZONE_KINDS:
            continue
        flow = flows.get(crossing.flow_id)
        if flow is None:
            continue
        authentication = flow.authentication
        yield (
            (crossing.flow_id, crossing.destination_zone),
            {
                "source_zone": crossing.source_zone,
                "destination_zone": crossing.destination_zone,
                "destination_zone_kind": kind,
                "authentication": _clip(authentication),
                "authentication_state": control_state(authentication),
            },
        )


def _inbound_from_exposed_process(model: SystemModel) -> Iterator[Match]:
    """What an internet-facing process can command in another zone.

    The structural half of a foothold: whoever holds the exposed process holds
    every authority it exercises across a boundary. The rule states the reach;
    whether the authority is excessive is the agent's call.
    """
    exposed = {process.id for process in internet_exposed_elements(model)}
    crossing_ids = crossing_flow_ids(model)
    for flow in model.data_flows:
        if flow.source not in exposed or flow.id not in crossing_ids:
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "source_exposure": "internet-facing",
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "crosses_boundary": True,
            },
        )


RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="spoofing-unverified-boundary-auth",
        category="spoofing",
        question=(
            "This flow crosses a trust boundary and states no verified caller"
            " identity. Who in the source zone can originate it, and as whom"
            " would the destination treat them?"
        ),
        find=_unverified_boundary_auth,
    ),
    Rule(
        rule_id="spoofing-unverified-external-caller",
        category="spoofing",
        question=(
            "An external system originates this flow and its authentication is"
            " unverified. Can anyone who learns the endpoint impersonate that"
            " party, and what does the receiver do with the events?"
        ),
        find=_unverified_external_caller,
    ),
    Rule(
        rule_id="tampering-unprotected-transit-crossing",
        category="tampering",
        question=(
            "This flow crosses a trust boundary with no verified transport"
            " protection. What could an on-path attacker alter in it, and what"
            " acts on the altered data?"
        ),
        find=_unprotected_transit_crossing,
    ),
    Rule(
        rule_id="tampering-unverified-write-to-store",
        category="tampering",
        question=(
            "This flow writes into a data store without a verified caller"
            " identity. What can be written, and what downstream reader trusts"
            " what it finds there?"
        ),
        find=_unverified_write_to_store,
    ),
    Rule(
        rule_id="repudiation-shared-authentication",
        category="repudiation",
        question=(
            "Several flows from different sources present the same credential."
            " If one of them acts, can the logs say which? Who could deny it?"
        ),
        find=_shared_authentication,
    ),
    Rule(
        rule_id="repudiation-unattributable-action",
        category="repudiation",
        question=(
            "An unverified caller acts on an element holding a graded asset."
            " What record names the actor, and would it survive a dispute?"
        ),
        find=_unattributable_action,
    ),
    Rule(
        rule_id="information-disclosure-unprotected-sensitive-transit",
        category="information-disclosure",
        question=(
            "This flow has no verified transport protection and an endpoint"
            " holds a graded asset. Who can observe the channel, and what"
            " exactly would they read?"
        ),
        find=_unprotected_sensitive_transit,
    ),
    Rule(
        rule_id="information-disclosure-store-at-rest-unverified",
        category="information-disclosure",
        question=(
            "This store states no verified protection at rest. Who reaches the"
            " storage layer beneath the application, and what is in it?"
        ),
        find=_store_at_rest_unverified,
    ),
    Rule(
        rule_id="denial-of-service-internet-exposed-process",
        category="denial-of-service",
        question=(
            "This process is reachable from the internet. What does an"
            " unauthenticated request cost it, and what else stalls when it"
            " saturates?"
        ),
        find=_internet_exposed_process,
    ),
    Rule(
        rule_id="denial-of-service-shared-dependency",
        category="denial-of-service",
        question=(
            "Several callers depend on this element. If it slows or stops,"
            " which of them fail, and does anything shed load or degrade?"
        ),
        find=_shared_dependency,
    ),
    Rule(
        rule_id="elevation-of-privilege-privilege-zone-crossing",
        category="elevation-of-privilege",
        question=(
            "This flow crosses into a privilege or tenant boundary. What"
            " enforces the transition, and what would the caller command if"
            " nothing did?"
        ),
        find=_privilege_zone_crossing,
    ),
    Rule(
        rule_id="elevation-of-privilege-inbound-from-exposed-process",
        category="elevation-of-privilege",
        question=(
            "An internet-facing process exercises authority across a boundary."
            " What does whoever compromises it inherit, and is that authority"
            " wider than the process's own job?"
        ),
        find=_inbound_from_exposed_process,
    ),
)


def rules_for(category: StrideCategory) -> tuple[Rule, ...]:
    """The rules in one category's lane, in :data:`RULES` order."""
    return tuple(rule for rule in RULES if rule.category == category)


def generate_candidates(model: SystemModel) -> dict[StrideCategory, CandidateSet]:
    """Every rule evaluated against the model, grouped into the six lanes.

    Every category gets an entry, including one whose rules all found nothing:
    an empty candidate set is the honest statement that deterministic analysis
    surfaced no structural lead here, and it is a different thing from a lane
    that was never offered any.
    """
    return {category: _candidate_set(category, model) for category in STRIDE_CATEGORIES}


def _candidate_set(category: StrideCategory, model: SystemModel) -> CandidateSet:
    fired = [
        candidate for rule in rules_for(category) for candidate in rule.fire(model)
    ]
    return CandidateSet(
        category=category,
        questions={
            rule.rule_id: rule.question
            for rule in rules_for(category)
            if any(candidate.rule_id == rule.rule_id for candidate in fired)
        },
        candidates=tuple(fired),
    )
