"""A ``diagram`` Source's structure, as Mermaid's own parsers read it (ADR 0121).

Mermaid has no specification apart from its grammars, so the service does not
write a parser: ``diagram_reader/read.mjs`` runs Mermaid's flowchart grammar
or the ``architecture-beta`` grammar of ``@mermaid-js/parser`` in Node, and
:func:`read_diagram` calls it as a subprocess.

**The structure is syntax, never meaning.** A node keeps its shape or icon as
written. Whether a rectangle is an External Entity, or a group a trust zone,
is extraction's judgement, because a person can draw the wrong shape as they
can write the wrong word.

**Two readings, and an install fault that is neither.** A diagram the parsers
read is a :class:`DiagramStructure`. One they cannot read, through a syntax
error or another diagram type, is a :class:`DiagramRefusal`, and extraction
reads that diagram as text. A missing Node or package raises
:class:`DiagramReaderMissing`, because reading the diagram another way would
make the same job run two ways on two deployments.
"""

from __future__ import annotations

import json
import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DiagramEdge",
    "DiagramGroup",
    "DiagramNode",
    "DiagramReaderMissing",
    "DiagramReading",
    "DiagramRefusal",
    "DiagramStructure",
    "read_diagram",
]

#: The longest a reading may take, and the most memory Node may hold for it.
#: Mermaid's own limit of 500 edges bounds the work far below both.
READ_TIMEOUT_SECONDS = 20
READ_MEMORY_MB = 256

#: The exit code ``read.mjs`` gives when a package is missing.
_MISSING_PACKAGE = 3

#: What the ends of an edge say about its direction: ``forward`` from source
#: to target, ``backward`` from target to source, ``both`` and ``none`` either.
Arrow = Literal["forward", "backward", "both", "none"]


class _Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagramGroup(_Reading):
    """A flowchart subgraph or an architecture group."""

    id: str
    label: str
    parent: str | None
    line: int | None


class DiagramNode(_Reading):
    """A flowchart node or an architecture service. ``shape`` is the
    flowchart shape or the architecture icon, as written."""

    id: str
    label: str
    shape: str
    group: str | None
    line: int | None


class DiagramEdge(_Reading):
    """An edge between two nodes or groups, by their diagram IDs."""

    source: str
    target: str
    label: str
    arrow: Arrow
    line: int | None


class DiagramStructure(_Reading):
    """What Mermaid's parser read from a diagram."""

    kind: Literal["flowchart", "architecture"]
    groups: tuple[DiagramGroup, ...]
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]


class DiagramRefusal(_Reading):
    """A diagram Mermaid's parsers cannot read, and the reason they gave."""

    refused: str


DiagramReading = DiagramStructure | DiagramRefusal


class DiagramReaderMissing(RuntimeError):
    """Node or the reader's packages are not installed."""


def read_diagram(text: str) -> DiagramReading:
    """The structure of one diagram, or why it has none.

    Raises :class:`DiagramReaderMissing` where Node or a package is missing. A
    reading that runs past :data:`READ_TIMEOUT_SECONDS`, or that Node ends with
    no reading, is a refusal: the fault is in the diagram, and extraction can
    still read its text.
    """
    # Imported here: the deployment module reaches the gate, which reads the
    # types above, so a module-level import closes a cycle.
    from analysis_service.deployment import DEFAULT_DIAGRAM_READER_DIR

    script = DEFAULT_DIAGRAM_READER_DIR / "read.mjs"
    install = f"npm ci --ignore-scripts --prefix {DEFAULT_DIAGRAM_READER_DIR}"
    try:
        done = subprocess.run(
            ["node", f"--max-old-space-size={READ_MEMORY_MB}", str(script)],
            input=text,
            capture_output=True,
            text=True,
            timeout=READ_TIMEOUT_SECONDS,
            cwd=DEFAULT_DIAGRAM_READER_DIR,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DiagramReaderMissing(
            f"a diagram Source needs Node to read it; install Node, then run: {install}"
        ) from exc
    except subprocess.TimeoutExpired:
        return DiagramRefusal(
            refused=f"the reading took longer than {READ_TIMEOUT_SECONDS} s"
        )
    if done.returncode == _MISSING_PACKAGE:
        raise DiagramReaderMissing(
            f"the diagram reader's packages are not installed; run: {install}"
        )
    try:
        found = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return DiagramRefusal(
            refused=f"the reader ended with code {done.returncode} and no reading"
        )
    if "structure" in found:
        return DiagramStructure.model_validate(found["structure"])
    return DiagramRefusal(refused=str(found["refused"]))
