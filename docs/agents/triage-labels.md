# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the
actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding
label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Not triage labels

Two other label families live on this tracker and are **orthogonal** to the five roles above —
never substitute one for a triage label, and never read one as a triage state:

- **`wayfinder:*`** (`map`, `research`, `prototype`, `grilling`, `task`) — the *kind* of a
  wayfinding ticket, not its readiness. See `docs/agents/issue-tracker.md`. A wayfinder ticket's
  state is carried by its assignee, its open blockers and whether it is closed; a `wayfinder:*`
  issue does not want a triage label on top.
- **GitHub's stock set** (`bug`, `enhancement`, `documentation`, `question`, `duplicate`,
  `invalid`, `good first issue`, `help wanted`) — subject-matter labels. `question` is not
  `needs-info`, and `help wanted` is not `ready-for-human`; the pairs mean different things and
  the triage skills look for the strings in the table above.
