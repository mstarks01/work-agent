"""STRIDE's eleven deterministic candidate rules.

A **Candidate** is not a finding. It is a mechanically-evaluated condition over
the validated System Model — "this flow crosses a trust boundary and its
``authentication`` is unverified" — handed to the lane agent as something to
*look at*. Whether it is a threat, and what the attacker actually achieves, is
the agent's judgement and stays there.

**These rules are STRIDE's, which is why they live in STRIDE's package.** A rule
decides which lane sees a lead, and a lane is a framework's own unit; the
machinery that fires a rule and groups the results is neutral and stays in
:mod:`stride_service.candidates`. The retrieval tables that select a **Reference
Note** or a **Worked Case** key on the IDs below, and they are this package's
for the same reason.

The line this module does not cross: **a candidate is never evidence.** It
carries no severity, no attacker story, no claim that anything is wrong, and it
cannot become a :class:`~stride_service.frameworks.stride.record.Threat` —
nothing downstream of the prompt reads a candidate at all. What grounds a
finding is still the submitter's words, an ``unknown`` attribute, or a derived
crossing. A rule's whole contribution is *attention*.

The representation is deliberately small: a tuple of
:class:`~stride_service.candidates.Rule` values, each a rule ID, the lane it
belongs to, the question it puts to that agent, and a plain function from model
to matches. There is no rule DSL, no condition tree and no engine, because the
thing a maintainer needs to do most often is read one rule and decide whether it
is right — and a table of eleven functions is the representation that makes that
cheapest. Adding a rule is writing a function and appending to :data:`RULES`.

Rules fire on **structure**, never on prose. The attribute predicates come from
:mod:`stride_service.analysis`, which reads a control attribute's leading token
and nothing else, so no rule here is a natural-language classifier wearing a
rule's clothes. A rule that needed to understand what ``"company SSO"`` implies
would be a rule that belongs in the lane skill text instead.

Two rules may fire on one attribute from different lanes, and that is correct:
an unverified ``authentication`` on a boundary-crossing flow is a spoofing
question and an attribution question, and the two agents answer differently.
"""

from __future__ import annotations

from collections.abc import Iterator

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
from stride_service.candidates import Match, Rule, clip_fact
from stride_service.system_model import SystemModel

__all__ = ["PRIVILEGE_ZONE_KINDS", "RULES", "SHARED_DEPENDENCY_MIN"]

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

_clip = clip_fact


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
        lane="spoofing",
        question=(
            "This flow crosses a trust boundary and states no verified caller"
            " identity. Who in the source zone can originate it, and as whom"
            " would the destination treat them?"
        ),
        find=_unverified_boundary_auth,
    ),
    Rule(
        rule_id="spoofing-unverified-external-caller",
        lane="spoofing",
        question=(
            "An external system originates this flow and its authentication is"
            " unverified. Can anyone who learns the endpoint impersonate that"
            " party, and what does the receiver do with the events?"
        ),
        find=_unverified_external_caller,
    ),
    Rule(
        rule_id="tampering-unprotected-transit-crossing",
        lane="tampering",
        question=(
            "This flow crosses a trust boundary with no verified transport"
            " protection. What could an on-path attacker alter in it, and what"
            " acts on the altered data?"
        ),
        find=_unprotected_transit_crossing,
    ),
    Rule(
        rule_id="tampering-unverified-write-to-store",
        lane="tampering",
        question=(
            "This flow writes into a data store without a verified caller"
            " identity. What can be written, and what downstream reader trusts"
            " what it finds there?"
        ),
        find=_unverified_write_to_store,
    ),
    Rule(
        rule_id="repudiation-unattributable-action",
        lane="repudiation",
        question=(
            "An unverified caller acts on an element holding a graded asset."
            " What record names the actor, and would it survive a dispute?"
        ),
        find=_unattributable_action,
    ),
    Rule(
        rule_id="information-disclosure-unprotected-sensitive-transit",
        lane="information-disclosure",
        question=(
            "This flow has no verified transport protection and an endpoint"
            " holds a graded asset. Who can observe the channel, and what"
            " exactly would they read?"
        ),
        find=_unprotected_sensitive_transit,
    ),
    Rule(
        rule_id="information-disclosure-store-at-rest-unverified",
        lane="information-disclosure",
        question=(
            "This store states no verified protection at rest. Who reaches the"
            " storage layer beneath the application, and what is in it?"
        ),
        find=_store_at_rest_unverified,
    ),
    Rule(
        rule_id="denial-of-service-internet-exposed-process",
        lane="denial-of-service",
        question=(
            "This process is reachable from the internet. What does an"
            " unauthenticated request cost it, and what else stalls when it"
            " saturates?"
        ),
        find=_internet_exposed_process,
    ),
    Rule(
        rule_id="denial-of-service-shared-dependency",
        lane="denial-of-service",
        question=(
            "Several callers depend on this element. If it slows or stops,"
            " which of them fail, and does anything shed load or degrade?"
        ),
        find=_shared_dependency,
    ),
    Rule(
        rule_id="elevation-of-privilege-privilege-zone-crossing",
        lane="elevation-of-privilege",
        question=(
            "This flow crosses into a privilege or tenant boundary. What"
            " enforces the transition, and what would the caller command if"
            " nothing did?"
        ),
        find=_privilege_zone_crossing,
    ),
    Rule(
        rule_id="elevation-of-privilege-inbound-from-exposed-process",
        lane="elevation-of-privilege",
        question=(
            "An internet-facing process exercises authority across a boundary."
            " What does whoever compromises it inherit, and is that authority"
            " wider than the process's own job?"
        ),
        find=_inbound_from_exposed_process,
    ),
)
