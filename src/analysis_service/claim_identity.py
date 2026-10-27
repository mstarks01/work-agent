"""Which finding a claim is, read from the fields the claim carries.

A **Claim**'s identity is its framework, its lane, its verb and its place, or
its framework and the catalog unit it rules on (:func:`finding_key`). Two
claims under one key are two findings where their mechanisms are apart
(:func:`distinct_mechanisms`, ADRs 0060 to 0062). Nothing here reads prose or
calls a model.

Each caller supplies the flow map that a claim's cited flows resolve through,
because the caller knows which graph the claim was reached in: a follow-up
report reads its own **System Model**, and the eval harness reads the corpus
case graph. ``docs/agents/claim-identity.md`` states the rule.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from analysis_service.assertions import (
    REGISTRY,
    AssertionCatalog,
    assertion_id,
    projection_fields,
)
from analysis_service.claims import ASSERTION_GROUNDS, ATTRIBUTE_GROUNDS, Claim
from analysis_service.frameworks import lane_of
from analysis_service.report import Report
from analysis_service.system_model import DataFlow, is_zone


def endpoint_targets(
    element_ids: Iterable[str], flows: Mapping[str, tuple[str, str]]
) -> frozenset[str]:
    """The cited elements with every flow replaced by its two endpoints.

    One place in the graph, spelled one way: a claim citing a flow and one
    citing the process at its end name the same place. Trust boundaries are
    dropped, since a zone is the context a claim sits in rather than what it is
    about. The same fold ``evals/harness/identity.py`` applies when it scores,
    kept in step by ``tests/test_evals_identity.py``.

    ``flows`` is each flow's ID against its two endpoints — a
    :attr:`~analysis_service.system_model.ModelIndex.flow_endpoints` — rather
    than the model, and the eval side's ``endpoint_form`` takes the same map.
    A caller folding many claims builds it once: derived per call, the fold
    would walk every flow in the model for every claim it was asked about.
    """
    targets: set[str] = set()
    for element_id in element_ids:
        if is_zone(element_id):
            continue
        endpoints = flows.get(element_id)
        if endpoints is None:
            targets.add(element_id)
        else:
            targets.update(endpoints)
    return frozenset(targets)


#: A finding's identity, as :func:`finding_key` spells it.
FindingKey = tuple[object, ...]


#: No catalog: the rows a job without an assertion pass carries.
NO_ROWS: Mapping[str, str] = MappingProxyType({})


def row_controls(assertions: AssertionCatalog | None) -> Mapping[str, str]:
    """Each settled row's ID against the control it names, built once per job.

    The control is the attribute the row's predicate projects into, so a row
    and an attribute ground about one control read alike, or the predicate's
    own name where it projects into nothing. Looked up by the row's exact ID,
    which a reader never splits.
    """
    if assertions is None:
        return NO_ROWS
    fields = projection_fields()
    return MappingProxyType(
        {
            assertion_id(entry): fields.get(entry.predicate) or entry.predicate
            for entry in assertions.entries
            if entry.predicate in REGISTRY
        }
    )


def report_rows(report: Report) -> Mapping[str, str]:
    """The row controls of a **Report**'s own catalog, or :data:`NO_ROWS`.

    **The one reader of a Report's rows.** A report whose job ran no assertion
    pass carries no catalog, so it reads no row.
    """
    return row_controls(report.assertions.catalog if report.assertions else None)


def grounded_mechanism(
    claim: Claim, rows: Mapping[str, str] = NO_ROWS
) -> frozenset[str]:
    """The controls a claim's own grounds say are unstated, missing or settled.

    Read from fields, never prose: the attribute of each unknown or absent
    ground, and the control of each assertion row it cites, from ``rows``
    (:func:`row_controls`). A fake server rests on the client never checking
    the server (``encryption_in_transit``) and a fake caller on the server
    never checking the caller (``authentication``), and the two share a lane,
    a verb and a place. A leaked password rests on stated rows
    (``credential-custody``) and an offline copy on an unstated attribute
    (``encryption_at_rest``).
    """
    return frozenset(
        ground.attribute
        for ground in claim.grounds
        if ground.kind in ATTRIBUTE_GROUNDS and ground.attribute
    ) | frozenset(
        rows[ground.assertion]
        for ground in claim.grounds
        if ground.kind in ASSERTION_GROUNDS and ground.assertion in rows
    )


def sibling_flows(flows: Mapping[str, tuple[str, str]]) -> frozenset[str]:
    """Every flow that runs between the same two elements as another flow,
    in either direction."""
    pairs = Counter(frozenset(ends) for ends in flows.values())
    return frozenset(flow for flow, ends in flows.items() if pairs[frozenset(ends)] > 1)


def cited_channels(
    element_ids: Iterable[str], flows: Mapping[str, tuple[str, str]]
) -> frozenset[str]:
    """The sibling flows a claim cites: the channel its place cannot name.

    :func:`endpoint_targets` folds a flow to its two endpoints, so two flows
    between one pair of elements are one place, as case 13's REST requests
    and its WebSocket are. The cited flow ID is what still tells them apart
    (ADR 0061). A flow with no sibling is fully named by its place and is not
    a channel.
    """
    cited = frozenset(element_ids) & frozenset(flows)
    return cited & sibling_flows(flows) if cited else frozenset()


def mechanism_of(
    claim: Claim,
    flows: Mapping[str, tuple[str, str]],
    rows: Mapping[str, str] = NO_ROWS,
) -> frozenset[str]:
    """A claim's whole mechanism: its grounded controls and its channels."""
    return grounded_mechanism(claim, rows) | cited_channels(
        claim.affected_element_ids, flows
    )


def _apart(one: frozenset[str], other: frozenset[str]) -> bool:
    return bool(one) and bool(other) and not one & other


def distinct_mechanisms(first: Iterable[str], second: Iterable[str]) -> bool:
    """True where two claims under one key are two findings.

    **The one reader of "two mechanisms at one place"** (ADRs 0060 and 0061).
    A mechanism holds controls and channels: an attribute name, or a sibling
    flow's ID, which carries the flow prefix. Two claims are apart where their
    controls do not overlap at all, or their channels do not. Each side must
    name something, so a side that one claim leaves empty sets nothing apart,
    and a claim with no mechanism is one finding with each claim under its
    key. Overlap
    rather than equality, because one finding cites a slightly different set
    of controls from one run to the next: equality split 39 of 165 archived
    cross-run pairs of one finding, and non-overlap splits 3 (ledger
    ``QA-2026-10-03-02-E7``).
    """
    one, other = frozenset(first), frozenset(second)
    prefix = f"{DataFlow.id_prefix}:"
    channels = (
        frozenset(m for m in one if m.startswith(prefix)),
        frozenset(m for m in other if m.startswith(prefix)),
    )
    return _apart(one - channels[0], other - channels[1]) or _apart(*channels)


def finding_key(claim: Claim, flows: Mapping[str, tuple[str, str]]) -> FindingKey:
    """Which finding a claim is, from its fields and never its prose.

    A claim with a verb is one action at one place in one lane: its
    framework, its lane, its verb and its endpoint-resolved targets. A claim
    with no verb belongs to a package whose identity is a catalog identifier,
    so it is its framework and the unit it rules on. Read by
    :mod:`analysis_service.report_changes`, which matches one report's
    findings to an earlier report's.
    """
    if claim.verb is None:
        return (claim.framework, type(claim).unit_of(claim))
    return (
        claim.framework,
        lane_of(claim),
        claim.verb,
        endpoint_targets(claim.affected_element_ids, flows),
    )
