# Extension ticket template

Write the ticket when an audit hits a question the tooling cannot answer. A
blocker is a missing capability. It is **not** model variability, an ordinary
bug, a transient failure, an exhausted budget, or a hypothesis that lost.

The ticket lives in the `blocked` row's `ticket` block, and `--record` refuses
a `blocked` row without one. Each section below is one field of that block,
named in brackets. Then print the issue and file it:

```bash
uv run python -m evals.harness.run experiments --ticket <experiment-id>
```

The first line it prints is the issue title, and the rest is the body. File it
on `mstarks01/work-agent` with `gh issue create`. See
`docs/agents/issue-tracker.md` for the `gh` commands and
`docs/agents/triage-labels.md` for the label.

---

**Title** (`title`). The measurement that cannot be made.

## The blocked question

(`question`) What the audit was trying to decide, and which decision it
prevents.

## Evidence

(`evidence`) The exact commands that reproduce the block, and what came back
instead of an answer. A reader must be able to hit the same wall.

## The missing capability

(`missing`) What does not exist.

## Alternatives checked

(`alternatives`) The existing instruments you checked, and why each one does
not answer. Name them, because "no instrument exists" is wrong more often than
it is right in this repository.

## Where the change belongs

(`belongs`) One of `skill`, `harness`, `instrumentation`, `scorer` or
`references`, and the row refuses any other. Say why that one in `change`.

A missing measurement fixed in the scorer when it belonged in the
instrumentation changes what every past number meant.

## The smallest change

(`change`) What to build, and the interfaces it touches. If it adds an entry
per framework, per vendor, or per anything else, it is a table — see
`docs/agents/framework-parity.md`.

## Acceptance

(`acceptance`) The fixture or the check that says it works. A check that
cannot run on a half-built tree helps nobody.

## Cost

(`cost`) Implementation effort, and any inference cost. Apply `needs-sweep`
only when the next necessary step after this lands needs fresh paid model
output.

## Risk and rollback

(`risk`) What it could corrupt, and how to undo it.

## Resume

The command writes this section from the row: the audit ID and the experiment
ID, so the answer comes back to the question that asked it.
