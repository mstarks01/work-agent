"""The System Model as a Mermaid flowchart, for a person to paste elsewhere.

The report page draws the model itself. :func:`model_mermaid` writes the same
facts as Mermaid text: a ``subgraph`` for each trust zone that holds an element,
an element with no stated zone outside every subgraph, and an edge for each
data flow. A flow that crosses a boundary, or may cross one, takes a link
style, read from :func:`~analysis_service.analysis.crossings_by_flow`.

**Every name is submitted text.** A name reaches Mermaid only inside a quoted
label, and each character that Mermaid or HTML reads as syntax is written as a
Mermaid entity code. A node takes an ID from its position (``n0``, ``z0``),
never from its element ID or its name, so nothing submitted is ever a Mermaid
identifier or keyword.
"""

from __future__ import annotations

from analysis_service.analysis import crossings_by_flow
from analysis_service.system_model import (
    DataStore,
    ExternalEntity,
    SystemModel,
    ZonedElement,
)

__all__ = ["model_mermaid"]

#: The characters a label writes as an entity code: the quote that closes the
#: label, the start of an entity code, the edge label delimiter, the backtick
#: that opens a Markdown string, the percent sign that opens a directive, and
#: the characters HTML reads.
_ENCODED = frozenset('"#%&<>`|')

#: The link style of a flow that crosses a boundary, and of one that may cross
#: one because a zone is not stated, as the report page draws them.
_CROSSING_STYLE = {
    True: "stroke:#c0392b,stroke-width:2px",
    False: "stroke-dasharray:3 3",
}


def _label(text: str) -> str:
    """``text`` as a quoted Mermaid label, on one line."""
    flat = " ".join(text.split())
    return '"{}"'.format(
        "".join(f"#{ord(char)};" if char in _ENCODED else char for char in flat)
    )


def _node(node_id: str, element: ZonedElement) -> str:
    """One element as a Mermaid node: an external entity is a rectangle, a
    process a rounded box and a data store a cylinder."""
    label = _label(element.name)
    if isinstance(element, ExternalEntity):
        return f"{node_id}[{label}]"
    if isinstance(element, DataStore):
        return f"{node_id}[({label})]"
    return f"{node_id}({label})"


def model_mermaid(model: SystemModel) -> str:
    """``model`` as Mermaid ``flowchart`` text, or ``""`` for a model with no
    element to draw."""
    elements = model.zoned_elements()
    if not elements:
        return ""
    ids = {element.id: f"n{at}" for at, element in enumerate(elements)}
    lines = ["flowchart LR"]
    zoned: set[str] = set()
    for at, zone in enumerate(model.trust_boundaries):
        members = [element for element in elements if element.trust_zone == zone.id]
        if not members:
            continue
        lines.append(f"    subgraph z{at}[{_label(zone.name)}]")
        lines.extend(f"        {_node(ids[each.id], each)}" for each in members)
        lines.append("    end")
        zoned.update(each.id for each in members)
    lines.extend(
        f"    {_node(ids[each.id], each)}" for each in elements if each.id not in zoned
    )
    crossings = crossings_by_flow(model)
    for at, flow in enumerate(model.data_flows):
        lines.append(
            f"    {ids[flow.source]} -->|{_label(flow.name)}| {ids[flow.destination]}"
        )
        if flow.id in crossings:
            style = _CROSSING_STYLE[crossings[flow.id].decided]
            lines.append(f"    linkStyle {at} {style}")
    return "\n".join(lines) + "\n"
