# Experiment ledger

One JSONL file per quality audit, plus that audit's report beside it. The
loader, the closed outcome vocabulary and the staleness rule live in
`evals/harness/audit.py`; `.claude/skills/quality-audit/SKILL.md` is the
procedure that writes them.

Read the ledger before proposing a fix:

```bash
python -m evals.harness.run experiments --signature "<the observed failure>"
python -m evals.harness.run experiments --phase extraction
python -m evals.harness.run experiments --framework stride
```

Append a row after running an experiment, whatever it concluded:

```bash
python -m evals.harness.run experiments --record /tmp/<experiment-id>.json
```

## What belongs here

Every experiment an audit ran: supported, refuted, null, inconclusive, blocked
or superseded. **A refuted row is the valuable one.** It is what stops the next
audit paying for the same answer.

A row is never rewritten. A correction is a new row naming the row it corrects
in `supersedes`, so the state of the ledger reconstructs at any past date.

## What does not belong here

A measurement. A number a scorer printed belongs in the artifact that printed
it, and a row here cites that artifact. The ledger records what somebody
claimed, how they tried to be wrong about it, and what came back.

A row also never carries a reference answer, a holdout, or anything a run would
be graded against.

## Staleness

Each row names the repository paths its conclusion reads. The lookup compares
them against the tree and reports one of four states:

| State | Means |
| --- | --- |
| `current` | none of the named paths has changed since the row's revision |
| `stale` | at least one has, and the row names which |
| `unanchored` | the row named no path, so nothing can be checked |
| `undecidable` | `git` cannot reach the row's revision |

`stale` is a reason to re-read a row, not a verdict that it is wrong.
