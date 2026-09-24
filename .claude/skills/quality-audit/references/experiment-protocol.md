# Experiment protocol

## Before an experiment exists

State all seven, in the audit report, before running anything:

1. the observed failure, and the artifact a reader can open to see it;
2. the suspected mechanism, and the competing explanations;
3. the likely fix, and which phases it touches;
4. which important outcomes it affects, and the **ceiling** on what it can
   recover, as a number read off the archived misses in its class;
5. what would disprove the hypothesis;
6. the smallest experiment that discriminates between the explanations;
7. the expected cost and the acceptance criterion.

A hypothesis with no falsifier is not an experiment. The ledger refuses a row
without one.

## The ladder

Climb one rung at a time. Each rung licenses a narrower claim than the next,
and a report that states a higher claim than its rung has reached is wrong.

| Rung | What it is | What it licenses you to say |
| --- | --- | --- |
| 1 | offline analysis and deterministic replay | the defect is reproduced |
| 2 | single-stage calls on archived inputs | the local behaviour changed |
| 3 | the changed stage plus its affected downstream consumers | the change survives its consumers |
| 4 | a small representative end-to-end suite | a downstream gain was measured |
| 5 | held-out confirmation | the gain holds on material the fix never saw |

A unit test is rung 1. It proves local behaviour and says nothing about how
often a model hits the path or whether the report improved.

A condition that feeds signed material to generation (`corrected-extraction`,
`direct-facts` in `references/three-conditions.md`) never reaches rung 4 or 5.
Its figures locate a loss, and only the shipped route can measure a gain.

Report each recommendation's position explicitly:
**suspected → reproduced → local fix verified → downstream gain measured →
held-out gain confirmed.**

## Money

The default budget is zero. Raise it only on an explicit amount the user states
in this conversation.

- **Ask every time.** A permission for one run is not a permission for the
  next.
- **"One run" means one case.** A corpus sweep is a separate ask with its own
  number.
- **Price before you ask.** A fix whose ceiling sits inside the run-to-run band
  gets no run. Batch it with the next fixes until the batch clears the band.
- **Pre-flight.** One case first; read its provenance before the sweep.
- **This file enforces nothing.** The estimate gate and the spend hold are in
  `evals/harness/consent.py`, and they are the only enforcement that exists.
  `--accept-cost unknown` removes the hold entirely — never pass it to raise a
  budget.
- **A cache hit is not free.** Meter paid requests.
- **Do not substitute a cheaper model in a confirmation run.** That is a
  changed condition, and the comparison stops being a comparison.

There is no dollar ceiling in the code, by decision: a contributor may spend
any amount. The control is informed consent, so your job is to state the
labelled amount — `recorded`, `estimated` or `unpriced` — and stop when the
answer is no.

## Execution identity

Reuse an earlier result only under a complete identity: the same inputs,
prompts, schemas, relevant code and dependencies, model and settings, and the
same reachable provider identity. Where the exact provider revision cannot be
read, record the ambiguity rather than assuming it away.

- A changed upstream artifact invalidates its descendants. Unrelated branches
  stay reusable.
- **A replay is not a repeat.** Re-scoring an archive under new code measures
  the code. Only a new run measures the run-to-run spread.
- An incomplete experiment that stopped on a budget is incomplete, not passing.

## Isolation

When implementation is authorised:

- one candidate change per branch, and one lever per change;
- run the regressions the change can reach, and say which;
- an unsuccessful change is reverted and its record kept;
- the skill's own files are candidate changes too, with reviewable diffs.

## Before changing any identity rule

Replay the archive first:

```bash
uv run python -m evals.harness.run replay evals/emissions/*.json --out /tmp/replay.json
```

A slug, identity or digest rule change moves figures silently, and the diff
against the archive is the only thing that says whether it did. This costs
nothing.
