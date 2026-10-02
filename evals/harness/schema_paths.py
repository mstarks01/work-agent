"""How each node's schema reached the provider, read off archived reports.

ADR 0058 lets a tier send a node schema natively, as a tool's parameters, or
stated in the request text, and records three facts on each node execution:
``schema_path``, ``schema_fallback`` and ``reasks``. This command is the eval
harness's reader of those three facts. It counts them per node over every
archived report under a root, so a paired run of the native and tool paths
(#1413) reads its re-ask rate and its fallbacks from the record rather than
from a log.

A node execution that sent no schema counts under the path ``none``. A report
written before the fields existed reads as ``none`` with no re-ask, because
that is what their defaults say, so read the counts only over runs made after
ADR 0058.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from analysis_service.report import NodeRun, Report

#: The path an execution that sent no schema counts under.
NO_SCHEMA = "none"


@dataclass
class NodeSchemaPaths:
    """One node's schema facts, summed over its executions."""

    executions: int = 0
    reasks: int = 0
    paths: Counter[str] = field(default_factory=Counter)
    fallbacks: Counter[str] = field(default_factory=Counter)

    def to_json(self) -> dict[str, object]:
        return {
            "executions": self.executions,
            "reasks": self.reasks,
            "paths": dict(sorted(self.paths.items())),
            "fallbacks": dict(sorted(self.fallbacks.items())),
        }


def schema_paths(nodes: Iterable[NodeRun]) -> dict[str, NodeSchemaPaths]:
    """Each node's schema path, fallbacks and re-asks, by node name."""
    totals: dict[str, NodeSchemaPaths] = {}
    for node in nodes:
        total = totals.setdefault(node.node, NodeSchemaPaths())
        total.executions += 1
        total.reasks += node.reasks
        total.paths[node.schema_path or NO_SCHEMA] += 1
        if node.schema_fallback is not None:
            total.fallbacks[node.schema_fallback] += 1
    return totals


def sweep(root: Path) -> dict[str, NodeSchemaPaths]:
    """:func:`schema_paths` over every archived report under ``root``."""
    nodes = []
    for path in sorted(root.rglob("*.report.json")):
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        nodes.extend(report.nodes)
    return schema_paths(nodes)


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="the directory whose archived *.report.json files are read",
    )


def command_schema_paths(args: argparse.Namespace) -> int:
    """Print each node's schema facts over the reports under ``--root``."""
    totals = sweep(args.root)
    if not totals:
        print(f"no archived report under {args.root}", file=sys.stderr)
        return 1
    table = {name: totals[name].to_json() for name in sorted(totals)}
    print(json.dumps(table, indent=2))
    return 0
