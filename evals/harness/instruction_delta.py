"""What one prompt edit did: which node's instruction moved, and what moved with it.

## The question this answers

The token caps are drift alarms (ADR 0016). They say how far the static
instruction moved and claim nothing about how well it works. The claim nobody
could check was the retired envelope's — that a lane agent analyses worse above
some length — because a raise that improved findings and a deletion that cost
them looked alike to a sweep.

#279 recorded the independent variable: each node's built instruction size and
digest, in the artifact beside the scores. This is the reading over two of them.
Given the sweep before an edit and the sweep after, it prints which nodes'
instructions changed, by how many tokens, and every measurement that moved
beside them.

## What it does not do

**It does not establish causation, and nothing about two sweeps can.** Two runs
of one build already disagree — that spread is what
:mod:`evals.harness.stability` measures, and reading a score change smaller than
it as an effect of a prompt edit is reading noise. So this prints the
instruction delta and the measurement delta side by side and draws no
conclusion; the conclusion needs the stability spread beside it and more than
one pair.

It is **credential-free**. Both artifacts already exist, so the comparison is
arithmetic over records rather than a re-run.

## Keyed by instrument, not by a list of blocks

The measurement half reads :data:`~evals.harness.instruments.INSTRUMENTS` and
walks each entry's declared ``keys``. An instrument added to that table appears
here with no edit, and one whose package a sweep did not run is absent from both
sides rather than reported as a change to zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evals.harness.artifact import EvalArtifact
from evals.harness.instruments import INSTRUMENTS

#: What an instruction row is keyed by. The pair is unique within a sweep —
#: :func:`~evals.harness.instruction.collect` raises if one node was built twice
#: with different text — so it is safe to join two artifacts on it.
NodeKey = tuple[str, str]


@dataclass(frozen=True)
class NodeDelta:
    """One node's instruction, before and after.

    ``moved`` is the digest comparison rather than a token comparison, and the
    two are different questions. An edit that swaps a sentence for one the same
    length moves the digest and not the count, and it is still an edit whose
    effect somebody wants to read.
    """

    framework: str
    node: str
    before: int | None
    after: int | None
    moved: bool

    @property
    def delta(self) -> int | None:
        """Tokens gained, or ``None`` where the node is on only one side."""
        if self.before is None or self.after is None:
            return None
        return self.after - self.before

    def to_json(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "node": self.node,
            "before": self.before,
            "after": self.after,
            "delta": self.delta,
            "moved": self.moved,
        }


@dataclass(frozen=True)
class MeasurementDelta:
    """One instrument's number, before and after."""

    instrument: str
    path: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before

    def to_json(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "path": self.path,
            "before": self.before,
            "after": self.after,
            "delta": round(self.delta, 4),
        }


def _instruction_rows(artifact: EvalArtifact) -> dict[NodeKey, tuple[int, str]]:
    """One artifact's instruction rows, keyed by ``(framework, node)``."""
    return {
        (row["framework"], row["node"]): (row["tokens"], row["sha256"])
        for row in artifact.block("instruction")
    }


def node_deltas(before: EvalArtifact, after: EvalArtifact) -> list[NodeDelta]:
    """Every node either sweep instructed, with what changed.

    **The union, not the intersection.** A node present on one side only is a
    node the graph gained or lost — a package registered, a lane added — and
    reporting only the nodes both sweeps share would hide the largest change a
    prompt edit can make.
    """
    first, second = _instruction_rows(before), _instruction_rows(after)
    rows = []
    for key in sorted(set(first) | set(second)):
        was, now = first.get(key), second.get(key)
        rows.append(
            NodeDelta(
                framework=key[0],
                node=key[1],
                before=was[0] if was else None,
                after=now[0] if now else None,
                moved=(was[1] if was else None) != (now[1] if now else None),
            )
        )
    return rows


def _numbers(value: Any, prefix: str = "") -> Iterable[tuple[str, float]]:
    """Every numeric leaf of one artifact block, with its path.

    Generic because an aggregate's shape is the instrument's own business. A
    reader comparing two sweeps wants every number that moved, and enumerating
    them per instrument here would be the table-of-names this module exists to
    avoid.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        yield prefix, float(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _numbers(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for index, item in enumerate(value):
            yield from _numbers(item, f"{prefix}[{index}]")


def measurement_deltas(
    before: EvalArtifact, after: EvalArtifact
) -> list[MeasurementDelta]:
    """Every instrument number that differs between the two sweeps.

    The ``instruction`` instrument's own keys are skipped: they are the
    independent variable, reported by :func:`node_deltas` in the shape a reader
    can use rather than as flattened leaves beside the scores.
    """
    rows = []
    for name, instrument in INSTRUMENTS.items():
        if name == "instruction":
            continue
        for key in instrument.keys:
            first = dict(_numbers(before.block(key), key))
            second = dict(_numbers(after.block(key), key))
            for path in sorted(set(first) & set(second)):
                if first[path] != second[path]:
                    rows.append(
                        MeasurementDelta(
                            instrument=name,
                            path=path,
                            before=first[path],
                            after=second[path],
                        )
                    )
    return rows


def render(
    nodes: Sequence[NodeDelta], measurements: Sequence[MeasurementDelta]
) -> None:
    """The instruction change, then everything that moved beside it.

    Printed in that order on purpose. The instruction delta is what somebody
    did; the measurements are what happened afterwards, and reading the second
    without the first is how a prompt edit gets credited with a provider's bad
    afternoon.
    """
    changed = [row for row in nodes if row.moved]
    if not changed:
        print("instruction delta: no node's instruction changed between these sweeps")
    else:
        print(f"instruction delta: {len(changed)} of {len(nodes)} nodes moved")
        print(f"  {'framework':10} {'node':50} {'before':>7} {'after':>7} {'delta':>7}")
        for row in sorted(changed, key=lambda item: -abs(item.delta or 0)):
            before = "—" if row.before is None else f"{row.before:,}"
            after = "—" if row.after is None else f"{row.after:,}"
            delta = "—" if row.delta is None else f"{row.delta:+,}"
            print(
                f"  {row.framework:10} {row.node:50} {before:>7} {after:>7} {delta:>7}"
            )

    if not measurements:
        print("measurement delta: nothing an instrument reports moved")
        return
    print(f"measurement delta: {len(measurements)} numbers moved")
    print(f"  {'instrument':16} {'measurement':46} {'before':>9} {'after':>9}")
    for moved_number in measurements:
        print(
            f"  {moved_number.instrument:16} {moved_number.path:46}"
            f" {moved_number.before:9.3f} {moved_number.after:9.3f}"
        )
    print(
        "measurement delta: a change smaller than the run-to-run spread"
        " (`harness stability`) is not evidence of anything — this reports what"
        " moved, never why"
    )


def artifact(
    nodes: Sequence[NodeDelta], measurements: Sequence[MeasurementDelta]
) -> dict[str, Any]:
    """The comparison as a record, for a reader that wants it in a file."""
    return {
        "instruction_delta": [row.to_json() for row in nodes if row.moved],
        "instruction_nodes": len(nodes),
        "measurement_delta": [row.to_json() for row in measurements],
    }
