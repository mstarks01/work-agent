# 122. The first-run app installs the diagram reader

- **Status**: accepted
- **Date**: 2026-11-02
- **Relates to**: [ADR 0121](0121-mermaid-s-own-parsers-read-a-diagram-s-structure.md), [ADR 0110](0110-the-first-run-app-migrates-a-sqlite-job-store.md)

## Context

The diagram reader runs Mermaid's own parsers in Node (ADR 0121). It needs
the packages that `diagram_reader/package-lock.json` locks. Where they are
missing, a job with a `diagram` Source fails, and the job shows only the
general failure message. A person who set up a checkout before the reader
existed has no packages, and `uv sync` does not install them.

The reading of a diagram is a core feature, and a person should not have to
run command lines to set up the first-run app (ADR 0111).

## Decision

**The first-run app installs the packages at its startup.** Before it
serves, the app reads one small flowchart through `read_diagram`. Where the
reading raises `DiagramReaderMissing`, the app runs
`npm ci --ignore-scripts --prefix diagram_reader` and reads again. Where the
second reading also fails, the app logs a warning that names the install
step, and it still starts, because a job with no diagram does not need the
reader.

`npm ci` installs only the locked versions and checks each package against
the integrity hash in the lock file. `--ignore-scripts` runs no package's
own install script.

**Node stays a dependency of the machine.** The app does not install Node.
Where Node or npm is missing, the warning says to install Node.

**The `/v1` service keeps the manual step.** It installs nothing at its
startup, as it migrates nothing (ADR 0110).

## Considered options

- **Commit a bundled `read.mjs`.** One file with the packages built in would
  need Node only. Rejected: the repository would hold a large generated file,
  and each package upgrade would need a rebuild and a check that the bundle
  matches the lock file.
- **Install at the first job with a diagram.** Rejected: the first job would
  wait for the install, and a failed install would fail the job.

## Consequences

- The first startup after a checkout needs a network connection, and it
  takes longer while npm installs the packages.
- A later startup reads one flowchart, about 0.7 s.
