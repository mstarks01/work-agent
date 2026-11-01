# 121. Mermaid's own parsers read a diagram's structure

- **Status**: accepted
- **Date**: 2026-11-01
- **Effort**: the `diagram` Source (#2056) and QA-2026-10-31-03 to -06
- **Relates to**: [ADR 0001](0001-sources-replace-description.md), whose
  Sources carry equal weight and whose extraction alone writes elements;
  [ADR 0120](0120-one-format-table-serves-both-uploads.md) (on the Owner File
  map, not yet on `main`), whose check admits only a flowchart as a `diagram`

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
group, node, junction and edge with the diagram line it came from. An
architecture service keeps its icon as written, because an icon names a
component kind. A flowchart node carries no shape: a flowchart shape is a
drawing choice with no fixed meaning, and the diagram text still shows it.
An edge end marked `{group}` is the group that holds the node. The versions are pinned in
`diagram_reader/package-lock.json`. `analysis_service.diagrams` calls the
reader as a subprocess with a time and memory limit.

**The structure is evidence beside the text, and extraction still writes the
model.** `render_sources` puts the structure inside the diagram Source's own
block, in a fence, after the diagram text. Code writes no element, so ADR
0001's two rules stand: the Sources carry equal weight, and extraction alone
writes elements. The model decides each element's type and each zone, and it
treats a shape or an icon in the diagram as evidence beside the label.

**The gate holds extraction to the structure.** Each node must be an element
whose name is the node's label, or the part of the label before its first
colon or bracket, as in `Orders DB (PostgreSQL)`. Each edge must be a flow between the
elements of its two nodes, in the direction of its arrow. An edge with no
arrow, or with two, takes either direction. A gap is a validity issue, so the
repair pass gets it as it gets any other issue. Groups are not checked,
because a group is a trust zone only where its label says so. An edge with a
group or a junction at an end is not checked, because which elements a
junction joins is extraction's judgement.

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
- ADR 0120's check must also admit `architecture-beta`, when the Owner File
  map reaches `main`.
- QA-2026-11-01-01 measured the structure against a text reading. On
  `architecture-beta` diagrams written from the corpus models, elements lost
  fell from 8.75 to 5.0 a sweep. On loose flowcharts, the first design raised
  them from 80.25 to 87.75. A printed flowchart shape and a whole label as
  the name caused the rise, so the reader prints no flowchart shape and a
  name can stop before a label's detail. With both changes, the loss fell
  from 80.5 to 74.25.

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
