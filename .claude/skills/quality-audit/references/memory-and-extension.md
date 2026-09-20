# Memory and extension

## The ledger

`evals/experiments/` holds one JSONL file per audit. The loader, the closed
vocabulary and the staleness rule are in `evals/harness/audit.py`.

### Reading it

Do this **before** proposing a fix, every time:

```bash
uv run python -m evals.harness.run experiments --signature "<the failure>"
uv run python -m evals.harness.run experiments --phase criticism
uv run python -m evals.harness.run experiments --framework asvs
```

A row naming no framework is **neutral** and answers for every package. A row
naming one does not answer for another — which is the parity duty in
`docs/agents/framework-parity.md`, applied to the ledger.

Each match prints its outcome and one of four states:

| State | Means |
| --- | --- |
| `current` | none of the files the record depends on has changed |
| `stale` | at least one has, and the record names which |
| `unanchored` | the record named no file, so nothing can be checked |
| `undecidable` | `git` cannot reach the record's revision |

Then say, in the report, whether the prior evidence supports the proposal,
argues against it, or no longer applies. **Do not repeat a refuted approach
without a recorded reason** — changed code, a corrected measurement, or new
evidence. Name the reason.

### Writing it

Every experiment gets a row, whatever it concluded. Fill
`templates/experiment-record.md`, then:

```bash
uv run python -m evals.harness.run experiments --record /tmp/<experiment-id>.json
```

The ledger is append-only. A correction is a new row naming the row it
supersedes in `supersedes`; nothing rewrites an earlier row.

Six outcomes, and the loader refuses a seventh: `supported`, `refuted`, `null`,
`inconclusive`, `blocked`, `superseded`.

**`refuted` and `null` are the valuable rows.** A plausible prompt edit has
measured worse here twice, and the record of that is what stops the third
attempt. An audit that only records its successes teaches the next audit
nothing.

`reads` is the field that makes staleness work: list the repository paths the
conclusion actually depends on — the prompt, the scorer, the reference set. A
row with no `reads` can never be checked and reads `unanchored`.

## Diagnostic rules

When the same finding holds across several experiments, distil it into a rule
and record it as an experiment row whose `intervention` is the rule itself.
State five things:

1. the observable failure signature;
2. the checks that confirm or refute the suspected mechanism;
3. the smallest intervention;
4. the remedies that worked, and the conditions where they failed;
5. the supporting experiment IDs, and where the rule stops applying.

These are evidence-backed heuristics, not truths. Reassess every one after a
scorer, reference or contract change. One case succeeding is not repeatable
multi-case evidence, and the row says which it is.

Keep contradictions. Two rows that disagree are information; summarising them
into one is how the disagreement becomes invisible.

## Measuring the skill itself

Read these off the ledger, not off a count of edits:

- diagnoses later confirmed by an intervention;
- the rate of candidate changes that did not work;
- work an earlier audit had already done and this one repeated;
- cost per validated improvement;
- blockers raised versus blockers resolved;
- regressions introduced.

A high hypothesis count is not a good audit.

## Extension tickets

Every audit names its capability blockers. A blocker is **not** model
variability, an ordinary bug, a transient failure, an exhausted budget, or a
disproved hypothesis. It is a question the tooling cannot answer.

Use `templates/extension-ticket.md`. Say where the change belongs — the skill,
the harness, the instrumentation, the scorer, or the reference sets — because a
missing measurement fixed in the wrong place corrupts the measurement.

Examples of each:

| Symptom | Where the change belongs |
| --- | --- |
| no artifact for a stage | instrumentation |
| no way to replay a stage independently | harness |
| repeated expensive experiments that answer nothing | the skill's selection policy |
| work an earlier audit finished, rediscovered | memory retrieval |
| inconsistent semantic judgements | evaluation calibration |
| a spend limit nothing enforces | executable budget controls |

File it as a GitHub issue on `mstarks01/work-agent` — see
`docs/agents/issue-tracker.md` — and label it per
`docs/agents/triage-labels.md`. Apply `needs-sweep` when its next step is a
paid run.

## Authorised extension

Only under an explicit instruction. Then, in order:

1. reproduce the blocker and classify it;
2. check whether the capability already exists under another name;
3. implement the smallest isolated change;
4. verify its acceptance fixture and the regressions it can reach;
5. resume the original blocked experiment;
6. keep the change only if it resolved the blocker without changing what any
   existing measurement means.

Bound the depth: an extension may not raise its own authorisation, spend from
outside the total budget, or trigger a further extension. Extension spend
counts against the same cap.

Never, under any authorisation: loosen an acceptance criterion, edit a
reference answer or a human ruling to fit an output, expose a holdout answer to
generation, disable a gate, or merge the audit's own work. An evaluation
correction is proposed separately, with the before and after scores preserved
and any denominator change named.
