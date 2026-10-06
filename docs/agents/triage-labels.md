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

## No AI attribution

Post no AI disclaimer or other AI attribution in an issue, a comment or a pull request, even
where a triage skill asks for one. The No AI attribution rule in `AGENTS.md` overrides that
skill.

## Not triage labels

Two other label families live on this tracker and are **orthogonal** to the five roles above —
never substitute one for a triage label, and never read one as a triage state:

- **`wayfinder:*`** (`map`, `research`, `prototype`, `grilling`, `task`) — the *kind* of a
  wayfinding ticket, not its readiness. See `docs/agents/issue-tracker.md`. A wayfinder ticket's
  state is carried by its assignee, its open blockers and whether it is closed; a `wayfinder:*`
  issue does not want a triage label on top.
- **`needs-sweep`** — the next necessary step needs fresh paid model output: a sweep, a
  spread of repeated runs, or a command that sends a request again, such as `lane-replay` or
  `critic-replay`. Classify a command by what it does, not by its name: `score` and `replay`
  re-read an archive and are offline. The label marks a spend decision, never a readiness
  state, so it sits beside a triage label rather than in place of one. A run needs explicit
  approval every time; the label records that the ask is pending, and it authorises nothing.

  Apply it only when no offline step remains before the paid one. Offline work on the issue —
  an implementation, a fixture review, an archive measurement — keeps the issue without the
  label. When an issue mixes the two, do one of two things. Remove the label until the offline
  work is done, and record in a comment the paid step, its unmet criterion and the condition
  that puts the label back. Or split the issue: the offline work stays visible on its own
  issue, and the measurement issue keeps the label and its empirical acceptance criterion.
  Remove the label when the run lands or the step is ruled out. A deferred run keeps the label
  only while the deferral says what would reopen it.
- **GitHub's stock set** (`bug`, `enhancement`, `documentation`, `question`, `duplicate`,
  `invalid`, `good first issue`, `help wanted`) — subject-matter labels. `question` is not
  `needs-info`, and `help wanted` is not `ready-for-human`; the pairs mean different things and
  the triage skills look for the strings in the table above.
