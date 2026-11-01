# 121. Mermaid's own parsers read a diagram's structure

- **Status**: accepted
- **Date**: 2026-11-01
- **Effort**: the `diagram` Source (#2056) and QA-2026-10-31-03 to -06
- **Relates to**: [ADR 0001](0001-sources-replace-description.md), whose
  Sources carry equal weight and whose extraction alone writes elements;
  [ADR 0120](0120-one-format-table-serves-both-uploads.md), whose check admits
  a flowchart and an `architecture-beta` diagram as a `diagram`

## Context

A `diagram` Source holds Mermaid text, and the extraction model reads it as
text. QA-2026-10-31-03 to -06 measured what that loses on the 16 corpus
cases:

- From a diagram that the export wrote, extraction still loses about 2.75
  elements a sweep: an element typed against its shape, and a flow merged
  with a second flow between the same two elements.
- From a diagram drawn loosely, the loss is far higher, because shapes do not
  follow a convention and labels differ.

A prototype (branch `prototype/mermaid-parser`) read each diagram with
Mermaid's own parsers. It found every node, edge and subgraph of every
exported and drawn flowchart, and of 14 of 16 `architecture-beta` diagrams.
The other two hold a title that Mermaid's architecture grammar refuses, so
Mermaid cannot draw them either. Mermaid has no specification apart from its
grammars: `flow.jison` for flowcharts, and a Langium grammar in
`@mermaid-js/parser` for `architecture-beta`.

The syntax of a diagram is certain. What it means is not: a person can draw
a process as a rectangle, as a person can use the wrong word in prose.

## Decision

**Mermaid's own parsers read the structure.** `diagram_reader/read.mjs` runs
in Node. It reads a flowchart through Mermaid's flowchart grammar and an
`architecture-beta` diagram through `@mermaid-js/parser`, and prints each
group, node and edge with the diagram line it came from. A node keeps its
shape or icon as written. The versions are pinned in
`diagram_reader/package-lock.json`. `analysis_service.diagrams` calls the
reader as a subprocess with a time and memory limit.

**The structure is evidence beside the text, and extraction still writes the
model.** `render_sources` puts the structure inside the diagram Source's own
block, in a fence, after the diagram text. Code writes no element, so ADR
0001's two rules stand: the Sources carry equal weight, and extraction alone
writes elements. The model decides each element's type and each zone, and it
treats a shape or an icon as evidence beside the label.

**The gate holds extraction to the structure.** Each node must be an element
whose name is the node's label, and each edge must be a flow between the
elements of its two nodes, in the direction of its arrow. An edge with no
arrow, or with two, takes either direction. A gap is a validity issue, so the
repair pass gets it as it gets any other issue. Groups are not checked,
because a group is a trust zone only where its label says so.

**A diagram the parser refuses is read as text.** A syntax error, or a
diagram type other than a flowchart or `architecture-beta`, gives no
structure. The block says why, and the gate checks nothing for that Source.

**Node is a dependency of a job with a diagram.** The reader needs Node and
the locked packages. Where they are missing, a job with a `diagram` Source
fails with a message that names the install step. A job with no diagram does
not start Node.

## Consequences

- The model input changes only for a job with a `diagram` Source. A job
  without one renders the same bytes as before.
- Each diagram costs one Node start, about 0.7 s.
- ADR 0120's check admits the two diagram types that the reader reads.
- How much the structure helps extraction is unmeasured until a paid run
  compares it with the archived diagram arms.

## Considered options

- **A parser for the export's own format only.** The prototype read every
  exported diagram exactly, but refused most of each drawn one. Rejected:
  the service must read any diagram a person writes.
- **A Python port of `flow.jison`.** Rejected: the grammar is large, has lexer
  modes, and changes with each Mermaid release, so a port drifts.
- **Code writes the elements from the structure.** Rejected: a wrong shape
  would become a wrong type with no judgement, and ADR 0001 would need a
  source that outranks the others.
- **Read the diagram without Node when Node is missing.** Rejected: the same
  job would then run two ways, depending on the deployment.
