"""How much instruction each node was given, per framework, per sweep.

## Why this is measured

The token caps over the static instruction are drift alarms (ADR 0016). They say
how far the text has moved, and claim nothing about how well it works. The claim
nobody could check was the one the retired 6-8K envelope made, that a lane agent
analyses worse above some length. No instrument read prompt size, so a raise
that improved findings and a deletion that cost them looked alike to a sweep.

This is the instrument that makes the comparison possible. It records, beside
the scores in the same artifact, what each node was told: the size of its
composed instruction, and that instruction's own digest. Two artifacts either
side of a prompt edit now answer which node's instruction moved, by how much,
and what the scores did. That is what a raise costs, as against a deletion.

## What it does not do

It does not establish that a longer instruction analyses worse, and no single
sweep can. It records the thing that changed, next to the numbers that may have
moved with it. Reading a trend out of that needs sweeps on both sides, and more
than one case. A rate here does not gate, for the same reason coverage does not:
no baseline says what size a healthy lane runs at.

## Keyed by ``(framework, node)``

The framework comes from ``Pipeline.tier_nodes``, which the graph already builds
per selection, and which maps a graph node name to its tier key — ``analyze/asvs``
rather than a name this module would have to parse. Attribution is therefore
read off the registry the graph itself used, and a package that names its nodes
some new way cannot silently land under the wrong heading. ``extract`` and
``repair`` carry no framework, because one extraction serves every framework a
job selected.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from analysis_service.graph import Pipeline
from analysis_service.report import FrameworkName

#: What a node with no framework half in its tier key is filed under. A string
#: rather than ``None`` so the artifact's rows are one shape, and one nothing
#: reads as a framework name: ``PACKAGES`` is keyed by lowercase slugs.
SHARED = "(shared)"


@dataclass(frozen=True)
class NodeInstruction:
    """One LLM node's static instruction, as this sweep built it."""

    framework: FrameworkName | str
    node: str
    tokens: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "node": self.node,
            "tokens": self.tokens,
            "sha256": self.sha256,
        }


def _framework_of(tier_key: str) -> FrameworkName | str:
    """The framework half of a tier key, or :data:`SHARED` when it has none."""
    _, separator, name = tier_key.partition("/")
    return name if separator else SHARED


def collect(pipelines: Iterable[Pipeline]) -> list[NodeInstruction]:
    """Every distinct node instruction across the graphs a sweep built.

    A sweep builds one graph per distinct framework set, so a shared node —
    ``extract``, or a framework's own nodes under two different selections —
    is built more than once. It is recorded **once**, keyed by name, because
    the instruction is a property of the built node rather than of the
    selection that caused it to be built.

    That deduplication is checked rather than assumed: two graphs disagreeing
    about one node's instruction would mean the composition depends on the
    selection, which is the property the cacheable prefix rests on. It raises
    here rather than reporting whichever it saw last.
    """
    seen: dict[str, NodeInstruction] = {}
    for pipeline in pipelines:
        for node, size in pipeline.node_instructions.items():
            row = NodeInstruction(
                framework=_framework_of(pipeline.tier_nodes[node]),
                node=node,
                tokens=size.tokens,
                sha256=size.sha256,
            )
            if seen.setdefault(node, row) != row:
                raise ValueError(
                    f"node {node!r} was built with two different instructions:"
                    f" {seen[node].sha256} and {row.sha256}"
                )
    return sorted(seen.values(), key=lambda row: (row.framework, row.node))


def totals(rows: Sequence[NodeInstruction]) -> dict[str, Any]:
    """The per-framework fold under the table.

    ``tokens`` is the sum over that framework's nodes, which is what the
    framework costs a job in static instruction — not what one node was given,
    and not an average, since the nodes are different sizes on purpose.
    """
    by_framework: dict[str, list[NodeInstruction]] = {}
    for row in rows:
        by_framework.setdefault(str(row.framework), []).append(row)
    return {
        framework: {
            "nodes": len(group),
            "tokens": sum(row.tokens for row in group),
            "largest": max((row.tokens for row in group), default=0),
        }
        for framework, group in sorted(by_framework.items())
    }


def render(rows: Sequence[NodeInstruction]) -> None:
    """What each node was told, in the unit the caps are written in.

    Printed largest first within a framework, because the question this answers
    is which node carries the most static text — not what order the graph
    happens to build them in.
    """
    if not rows:
        print("instruction: no graph was built, so no node was instructed")
        return
    print("instruction (static text per node, coarse tokens — instrument, non-gating):")
    print(f"  {'framework':10} {'node':50} {'tokens':>7}  digest")
    ordered = sorted(rows, key=lambda row: (row.framework, -row.tokens))
    for row in ordered:
        print(f"  {row.framework:10} {row.node:50} {row.tokens:7,}  {row.sha256[:12]}")
    for framework, fold in totals(rows).items():
        print(
            f"instruction: {framework} {fold['tokens']:,} tokens over"
            f" {fold['nodes']} nodes, largest {fold['largest']:,}"
        )


def artifact(rows: Sequence[NodeInstruction]) -> dict[str, Any]:
    """This instrument's artifact keys.

    Written whether or not a graph was built, for the reason every instrument
    writes its keys unconditionally: an absent block and an empty one are
    different claims to a reader comparing two sweeps, and only the second is
    true of a sweep that built nothing.
    """
    return {
        "instruction": [row.to_json() for row in rows],
        "instruction_totals": totals(rows),
    }
