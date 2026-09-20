# Extension ticket template

File this as a GitHub issue on `mstarks01/work-agent` when an audit hits a
question the tooling cannot answer. See `docs/agents/issue-tracker.md` for the
`gh` commands and `docs/agents/triage-labels.md` for the label.

A blocker is a missing capability. It is **not** model variability, an ordinary
bug, a transient failure, an exhausted budget, or a hypothesis that lost.

---

**Title.** `<the measurement that cannot be made>`

## The blocked question

What the audit was trying to decide, and which decision it prevents.

## Evidence

The exact commands that reproduce the block, and what came back instead of an
answer. A reader must be able to hit the same wall.

## The missing capability

What does not exist. Then: the existing alternatives you checked and why each
one does not answer — name them, because "no instrument exists" is wrong more
often than it is right in this repository.

## Where the change belongs

One of: the skill, the harness, the instrumentation, the scorer, or the
reference sets. Say why that one.

A missing measurement fixed in the scorer when it belonged in the
instrumentation changes what every past number meant.

## The smallest change

What to build, and the interfaces it touches. If it adds an entry per
framework, per vendor, or per anything else, it is a table — see
`docs/agents/framework-parity.md`.

## Acceptance

The fixture or the check that says it works. A check that cannot run on a
half-built tree helps nobody.

## Cost

Implementation effort, and any inference cost. Apply `needs-sweep` when the
next step after this lands is a paid run.

## Risk and rollback

What it could corrupt, and how to undo it.

## Resume

The audit ID and the experiment ID this blocked, so the answer comes back to
the question that asked it.
