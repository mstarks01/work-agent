"""Read which **Data Flow** identity version a graph spells, and lift old IDs (#989).

ADR 0037 changes what a flow ID is built from: version 1 dropped the endpoints'
type prefixes, so an entity and a process sharing one name derived one flow ID.
Version 2 carries both endpoints' full **Element ID**s. Rule 4 keeps version 1
computable, because an archived artifact spells its flows the way the version it
ran under did.

## The table comes from the graph, never from the recorded ID

An old flow ID cannot say what types its endpoints had, because that is exactly
the fact a version 1 ID omits. So :func:`table_between` derives each flow's ID
under both versions from the endpoints and the label the graph records.

## What a rename reaches

A reference is a string somewhere in a JSON value, and the sites are of two
kinds. A field holds an ID alone — a claim's ``affected_element_ids``, a fact's
``subject`` — or it holds the ID inside something longer: a rationale, or an
evidence reference such as ``crossing:flow:...``. :func:`rewrite_text` is the
one reader of "where does an old ID occur", so no caller has to know which
fields are which.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from analysis_service.system_model import (
    FLOW_ID_VERSION,
    FlowIdError,
    flow_id_rule,
    flow_id_version,
)

#: Which collections of a ``model.json`` hold the elements a flow's endpoints
#: may name. Read off the file's own keys rather than the schema, because this
#: reads a graph as recorded, and an archived graph can hold a shape the current
#: schema refuses.
_ENDPOINT_COLLECTIONS = ("external_entities", "processes", "data_stores")


class MigrationError(Exception):
    """A graph names no one flow identity version, and the message says why."""


def _elements_by_id(raw: Mapping[str, Any]) -> dict[str, str]:
    """Every endpoint-eligible element's ID, against the collection it sits in."""
    found: dict[str, str] = {}
    for collection in _ENDPOINT_COLLECTIONS:
        for element in raw.get(collection) or ():
            if isinstance(element, Mapping) and isinstance(element.get("id"), str):
                found[element["id"]] = collection
    return found


def flow_id_version_of_graph(raw: Mapping[str, Any]) -> int:
    """Which flow identity version one recorded graph's IDs are at.

    Read off the IDs by shape, and a graph whose flows disagree raises: a
    mixture names no one rule, and a lift must not guess one. A graph with no
    flows is at the version this code writes, because there is nothing in it
    that another version would spell differently. An ID no rule wrote carries
    no version and does not decide the graph's: the replay names it as
    ``malformed-flow-id``, and the lift resolves no endpoint for it.
    """
    versions: set[int] = set()
    for flow in raw.get("data_flows") or ():
        if not isinstance(flow.get("id"), str):
            continue
        try:
            versions.add(flow_id_version(flow["id"]))
        except FlowIdError:
            continue
    if len(versions) > 1:
        raise MigrationError(
            f"this graph's flow IDs sit at versions {sorted(versions)}; a"
            " mixture names no one rule, so nothing here reads it"
        )
    return versions.pop() if versions else FLOW_ID_VERSION


def table_between(
    raw: Mapping[str, Any], from_version: int, to_version: int = FLOW_ID_VERSION
) -> dict[str, str]:
    """Each flow's ID under one version against its ID under another.

    Derived from the endpoints and the label the graph records, so it never asks
    which version the graph's *own* IDs are at. That is what makes it the lift
    for an archived artifact read against a graph at any version: an archived
    assertion proposal names a flow the way the version it ran under spelled it,
    and this says what the same flow is called now.

    Only the entries that differ, and only the flows whose endpoints the graph
    resolves.
    """
    old_rule, new_rule = flow_id_rule(from_version), flow_id_rule(to_version)
    known = _elements_by_id(raw)
    table: dict[str, str] = {}
    for flow in raw.get("data_flows") or ():
        source, destination, name = (
            flow.get("source"),
            flow.get("destination"),
            flow.get("name"),
        )
        if not all(isinstance(part, str) for part in (source, destination, name)):
            continue
        if source not in known or destination not in known:
            continue
        try:
            before = old_rule.build(source, destination, name)
            after = new_rule.build(source, destination, name)
        except (ValueError, FlowIdError):
            continue
        if before != after:
            table[before] = after
    return table


def lift_value(value: Any, table: Mapping[str, str]) -> Any:
    """One JSON value with every old flow ID inside it replaced.

    The in-memory counterpart of :func:`rewrite_text`, over the same bounded
    match: an archived emission is a parsed payload rather than a file. Dict
    keys move too, because a table keyed by element ID is
    how an artifact records per-element data.
    """
    if isinstance(value, str):
        return rewrite_text(value, table)
    if isinstance(value, Mapping):
        return {
            lift_value(key, table): lift_value(item, table)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [lift_value(item, table) for item in value]
    return value


def _bounded(old: str) -> re.Pattern[str]:
    """``old`` where the character after it cannot extend a slug.

    Every element ID ends in a slug, so one ID is routinely a prefix of
    another: ``flow:a-to-b:read`` of ``flow:a-to-b:read-more``, and
    ``entity:shopper`` of ``entity:shopper-group``. Without the boundary a
    rename of the first corrupts the second into a mixture of both, and the
    corpus holds label pairs exactly that shape (``read-orders`` beside
    ``read-write-orders``).

    The rule is a flow's by origin and every ID's by use: a version 2 flow ID
    carries its endpoints, so renaming an endpoint moves every flow through it,
    and ``tests/test_metamorphic.py`` drives this over element renames for that
    reason.

    **Bounded on both sides.** The left guard has nothing to catch today: no
    prefix in use ends with another, so no ID is a *suffix* of a longer one.
    It is here because ``_ID_PREFIX`` admits ``[a-z_]+``, which a prefix ending
    in an existing one would satisfy — and a fence sized to one side is safe
    only while its neighbours are fenced too. The intended sites still match:
    an ID inside ``crossing:flow:...`` or a JSON string is preceded by ``:``
    or ``"``, neither of which is a slug character.
    """
    return re.compile(r"(?<![a-z0-9_-])" + re.escape(old) + r"(?![a-z0-9-])")


def rewrite_text(text: str, renames: Mapping[str, str]) -> str:
    """Every old flow ID occurring in ``text`` replaced by its new one.

    The one reader of "where does an old ID occur". A field holding an ID alone
    and a sentence mentioning one are the same substitution, so nothing here
    holds a list of which fields are which — a list that would go stale the day
    a case gains a field.

    Longest old ID first, so a table holding one ID and a longer ID starting
    with it cannot have the short one applied inside the long one. The
    replacement goes through :func:`_constant`, never a template.
    """
    for old, new in sorted(renames.items(), key=lambda pair: -len(pair[0])):
        text = _bounded(old).sub(_constant(new), text)
    return text


def _constant(value: str) -> Callable[[re.Match[str]], str]:
    """``value``, as the replacement function :meth:`re.Pattern.sub` takes.

    A function rather than a template string, so a character ``re`` reads in a
    template — a backslash, a group reference — cannot be interpreted. Bound
    here rather than in a closure over the loop variable, which is the defect
    the ``B023`` lint names.
    """
    return lambda _match: value
